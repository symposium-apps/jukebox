#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hmac
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from youtube_urls import YOUTUBE_HOSTS, canonicalize_source_url

VERSION = "1.4.9"
MAX_BODY_BYTES = 32 * 1024
MAX_JOBS = 2
MAX_JOB_AGE_SECONDS = 60 * 60
ALLOWED_HOSTS = YOUTUBE_HOSTS
AUDIO_QUALITIES = {"best": "0", "320": "320", "256": "256", "192": "192", "128": "128"}
VIDEO_QUALITIES = {"best": 1080, "1080": 1080, "720": 720, "480": 480, "360": 360}


class DownloadCancelled(Exception):
    pass


class QuietLogger:
    def debug(self, _message: str) -> None:
        return

    info = debug
    warning = debug
    error = debug


def safe_diagnostic(exc: Exception) -> str:
    text = re.sub(r"https?://\S+", "[URL_REDACTED]", str(exc or ""))
    text = re.sub(r"\b[A-Za-z0-9_-]{11}\b", "[MEDIA_ID_REDACTED]", text)
    return f"{type(exc).__name__}: {text[:500]}"


def validate_source_url(value: object) -> str:
    return canonicalize_source_url(value)


def _thumbnail_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("https://"):
            continue
        result.append({
            "url": url,
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
            "preference": int(item.get("preference") or 0),
        })
    return result[-20:]


def public_info(info: dict[str, Any], *, include_entries: bool = True) -> dict[str, Any]:
    keys = (
        "id", "title", "track", "artist", "creator", "uploader", "channel", "album",
        "duration", "age_limit", "availability", "thumbnail", "webpage_url", "original_url",
    )
    result = {key: copy.deepcopy(info.get(key)) for key in keys if info.get(key) is not None}
    thumbnails = _thumbnail_list(info.get("thumbnails"))
    if thumbnails:
        result["thumbnails"] = thumbnails
    if include_entries and isinstance(info.get("entries"), list):
        result["entries"] = [public_info(item, include_entries=False) for item in info["entries"][:500] if isinstance(item, dict)]
    return result


def browser_path() -> str:
    configured = str(os.environ.get("JUKEBOX_COMPANION_BROWSER") or "").strip()
    candidates = [
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("Google Chrome or Chromium is required by the accountless proof provider")


def node_path() -> str:
    configured = str(os.environ.get("JUKEBOX_COMPANION_NODE") or "").strip()
    candidates = [
        configured,
        *[str(path) for path in sorted((Path.home() / ".nvm" / "versions" / "node").glob("v*/bin/node"), reverse=True)],
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        str(shutil.which("node") or ""),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            version = subprocess.check_output([candidate, "--version"], text=True, timeout=5).strip().lstrip("v")
            if int(version.split(".", 1)[0]) >= 20:
                return candidate
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    raise RuntimeError("Node.js 20 or newer is required by the YouTube challenge solver")


def common_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "js_runtimes": {"node": {"path": node_path()}},
        "extractor_args": {
            "youtube": {"player_client": ["mweb"], "fetch_pot": ["always"]},
            "youtubepot-wpc": {"browser_path": [browser_path()]},
        },
    }


def inspect_source(source_url: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    options = {
        **common_options(),
        "skip_download": True,
        "ignoreerrors": False,
        "extract_flat": "in_playlist",
        "playlistend": 500,
    }
    with YoutubeDL(options) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(validate_source_url(source_url), download=False)
    if not isinstance(info, dict):
        raise RuntimeError("The source is unavailable")
    return public_info(dict(info))


def inspect_with_worker(source_url: str) -> dict[str, Any]:
    process = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().with_name("worker.py")), "inspect"],
        input=json.dumps({"url": validate_source_url(source_url)}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if process.returncode != 0:
        diagnostic = str(process.stderr or "").strip().splitlines()
        if diagnostic:
            print(diagnostic[-1][:500], flush=True)
        raise RuntimeError("The source is unavailable")
    try:
        event = json.loads(process.stdout.splitlines()[-1])
        data = event.get("data") if isinstance(event, dict) and event.get("event") == "result" else None
    except (IndexError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        raise RuntimeError("The source returned invalid metadata")
    return data


@dataclass
class Job:
    id: str
    source_url: str
    output_format: str
    quality: str
    artwork: bool
    root: Path
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "queued"
    stage: str = "Waiting"
    progress: int | None = None
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    result_path: Path | None = None
    cancel_requested: bool = False

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result_ready": bool(self.result_path and self.result_path.is_file()),
        }


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.slots = threading.Semaphore(MAX_JOBS)

    def prune(self) -> None:
        now = time.time()
        with self.lock:
            stale = [job_id for job_id, job in self.jobs.items() if job.updated_at < now - MAX_JOB_AGE_SECONDS]
        for job_id in stale:
            self.remove(job_id)

    def create(self, source_url: str, output_format: str, quality: str, artwork: bool) -> Job:
        self.prune()
        if output_format not in {"mp3", "mp4"}:
            raise ValueError("Unsupported output format")
        allowed = AUDIO_QUALITIES if output_format == "mp3" else VIDEO_QUALITIES
        if quality not in allowed:
            raise ValueError("Unsupported output quality")
        job_id = uuid.uuid4().hex
        root = self.root / job_id
        root.mkdir(mode=0o700)
        job = Job(job_id, validate_source_url(source_url), output_format, quality, artwork, root)
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job,), name=f"companion-{job_id[:8]}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise KeyError("Job not found")
        return job

    def remove(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job:
            job.cancel_requested = True
            shutil.rmtree(job.root, ignore_errors=True)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        job.cancel_requested = True
        job.stage = "Cancelling"
        job.updated_at = time.time()
        return job

    def _run(self, job: Job) -> None:
        with self.slots:
            try:
                if job.cancel_requested:
                    raise DownloadCancelled
                job.status = "running"
                job.stage = "Downloading"
                job.updated_at = time.time()
                self._download(job)
                if job.cancel_requested:
                    raise DownloadCancelled
                job.status = "complete"
                job.stage = "Ready"
                job.progress = 100
            except DownloadCancelled:
                job.status = "cancelled"
                job.stage = "Cancelled"
                job.error = ""
                shutil.rmtree(job.root, ignore_errors=True)
            except Exception:
                job.status = "failed"
                job.stage = "Failed"
                job.error = "The public media download was rejected or unavailable"
                shutil.rmtree(job.root, ignore_errors=True)
            finally:
                job.updated_at = time.time()

    def _download(self, job: Job) -> None:
        payload = json.dumps({
            "url": job.source_url,
            "format": job.output_format,
            "quality": job.quality,
            "artwork": job.artwork,
            "root": str(job.root),
        })
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().with_name("worker.py")), "download"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(payload)
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        result_path: Path | None = None
        try:
            while True:
                if job.cancel_requested:
                    process.terminate()
                    raise DownloadCancelled
                for _key, _mask in selector.select(timeout=0.5):
                    line = process.stdout.readline()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") == "progress":
                        job.stage = str(event.get("stage") or "Downloading")
                        job.progress = int(event["progress"]) if event.get("progress") is not None else None
                        job.downloaded_bytes = int(event.get("downloaded_bytes") or 0)
                        job.total_bytes = int(event.get("total_bytes") or 0)
                        job.updated_at = time.time()
                    elif event.get("event") == "result":
                        candidate = Path(str(event.get("path") or "")).resolve()
                        expected = (job.root / "result.zip").resolve()
                        if candidate == expected and candidate.is_file():
                            result_path = candidate
                if process.poll() is not None:
                    break
            if process.returncode != 0 or result_path is None:
                raise RuntimeError("Download worker failed")
            job.result_path = result_path
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


STORE: JobStore | None = None
TOKEN = ""


class Handler(BaseHTTPRequestHandler):
    server_version = "JukeboxImportCompanion"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        value = str(self.headers.get("Authorization") or "")
        scheme, separator, candidate = value.partition(" ")
        return bool(separator and scheme.casefold() == "bearer" and TOKEN and hmac.compare_digest(candidate.encode(), TOKEN.encode()))

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
        return False

    def _body(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Invalid request") from exc
        if size <= 0 or size > MAX_BODY_BYTES:
            raise ValueError("Invalid request")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("Invalid request")
        return value

    def do_GET(self) -> None:
        assert STORE is not None
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "version": VERSION, "account_authentication": False})
            return
        if not self._require_auth():
            return
        match = re.fullmatch(r"/v1/jobs/([a-f0-9]{32})(/result)?", path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        try:
            job = STORE.get(match.group(1))
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Job not found"})
            return
        if match.group(2):
            if job.status != "complete" or not job.result_path or not job.result_path.is_file():
                self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "Result is not ready"})
                return
            size = job.result_path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with job.result_path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
            return
        self._json(HTTPStatus.OK, {"ok": True, "data": job.public()})

    def do_POST(self) -> None:
        assert STORE is not None
        if not self._require_auth():
            return
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/v1/inspect":
                self._json(HTTPStatus.OK, {"ok": True, "data": inspect_with_worker(str(body.get("url") or ""))})
                return
            if path == "/v1/jobs":
                job = STORE.create(
                    str(body.get("url") or ""),
                    str(body.get("format") or "mp3").casefold(),
                    str(body.get("quality") or "best").casefold(),
                    body.get("artwork") is not False,
                )
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "data": job.public()})
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            print(safe_diagnostic(exc), flush=True)
            self._json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "The public media source was unavailable"})

    def do_DELETE(self) -> None:
        assert STORE is not None
        if not self._require_auth():
            return
        match = re.fullmatch(r"/v1/jobs/([a-f0-9]{32})", self.path.split("?", 1)[0])
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        try:
            job = STORE.get(match.group(1))
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Job not found"})
            return
        if job.status in {"complete", "failed", "cancelled"}:
            payload = job.public()
            STORE.remove(job.id)
            self._json(HTTPStatus.OK, {"ok": True, "data": payload})
            return
        job = STORE.cancel(job.id)
        self._json(HTTPStatus.OK, {"ok": True, "data": job.public()})


def token_from_environment() -> str:
    token = str(os.environ.get("JUKEBOX_COMPANION_TOKEN") or "").strip()
    token_file = str(os.environ.get("JUKEBOX_COMPANION_TOKEN_FILE") or "").strip()
    if not token and token_file:
        path = Path(token_file).expanduser().resolve()
        if path.is_file() and not path.is_symlink():
            token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or len(token) > 4096 or any(character in token for character in "\r\n"):
        raise RuntimeError("A strong companion token is required")
    return token


def main() -> None:
    global STORE, TOKEN
    parser = argparse.ArgumentParser(description="Accountless local YouTube media downloader for Jukebox")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47321)
    parser.add_argument("--state-dir", default=str(Path.home() / "Library" / "Caches" / "Samos Labs" / "Jukebox Import Companion"))
    args = parser.parse_args()
    TOKEN = token_from_environment()
    STORE = JobStore(Path(args.state_dir))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    raw_pid_file = str(os.environ.get("JUKEBOX_COMPANION_PID_FILE") or "").strip()
    pid_file = Path(raw_pid_file).expanduser() if raw_pid_file else None
    if pid_file:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = pid_file.with_suffix(".tmp")
        temporary.write_text(str(os.getpid()), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, pid_file)
    try:
        server.serve_forever()
    finally:
        if pid_file:
            pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
