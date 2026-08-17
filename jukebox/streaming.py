from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


_LOCK = threading.RLock()
_QUEUE: list[tuple[str, Path]] = []
_QUEUED: set[str] = set()
_ACTIVE = ""
_WORKER: threading.Thread | None = None
_ROOT = Path.cwd() / ".sym-data" / "streams"


def configure(root: Path) -> None:
    global _ROOT
    _ROOT = Path(root).resolve() / "streams"
    _ROOT.mkdir(parents=True, exist_ok=True)


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"hls-h264-v1:{stat.st_size}:{stat.st_mtime_ns}"


def _track_dir(track_id: str) -> Path:
    if not track_id or any(character not in "0123456789abcdef" for character in track_id.casefold()):
        raise ValueError("Invalid stream track")
    return _ROOT / track_id.casefold()


def status(track_id: str, source: Path) -> dict[str, object]:
    source = Path(source).resolve()
    directory = _track_dir(track_id)
    manifest = directory / "stream.m3u8"
    metadata = directory / "source.json"
    ready = False
    if manifest.is_file() and metadata.is_file():
        try:
            ready = json.loads(metadata.read_text(encoding="utf-8")).get("fingerprint") == _fingerprint(source)
        except (OSError, json.JSONDecodeError):
            ready = False
    with _LOCK:
        state = "ready" if ready else "preparing" if track_id in _QUEUED or _ACTIVE == track_id else "not_ready"
    return {
        "state": state,
        "ready": ready,
        "kind": "video" if source.suffix.casefold() == ".mp4" else "audio",
        "segment_seconds": 6,
    }


def prepare(track_id: str, source: Path) -> dict[str, object]:
    global _WORKER
    source = Path(source).resolve()
    current = status(track_id, source)
    if current["ready"] or not source.is_file() or not shutil.which("ffmpeg"):
        return current
    with _LOCK:
        if track_id not in _QUEUED and _ACTIVE != track_id:
            _QUEUE.append((track_id, source))
            _QUEUED.add(track_id)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_run_queue, name="jukebox-hls", daemon=True)
            _WORKER.start()
    return status(track_id, source)


def _run_queue() -> None:
    global _ACTIVE
    while True:
        with _LOCK:
            if not _QUEUE:
                _ACTIVE = ""
                return
            track_id, source = _QUEUE.pop(0)
            _QUEUED.discard(track_id)
            _ACTIVE = track_id
        try:
            _build(track_id, source)
        except Exception:
            # Streaming is a derivative optimization. The original media remains
            # available through the byte-range endpoint if preparation fails.
            pass
        finally:
            with _LOCK:
                _ACTIVE = ""


def _build(track_id: str, source: Path) -> None:
    source = source.resolve()
    fingerprint = _fingerprint(source)
    destination = _track_dir(track_id)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    manifest = temporary / "stream.m3u8"
    segment_pattern = temporary / "segment-%05d.ts"
    if source.suffix.casefold() == ".mp4":
        codecs = [
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=w='min(1280,iw)':h=-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*6)", "-c:a", "aac", "-b:a", "160k",
        ]
    else:
        codecs = ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k"]
    command = [
        shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        *codecs,
        "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments", "-hls_segment_filename", str(segment_pattern), str(manifest),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
    if not manifest.is_file() or not list(temporary.glob("segment-*.ts")):
        raise RuntimeError("HLS preparation produced no stream")
    (temporary / "source.json").write_text(json.dumps({"fingerprint": fingerprint, "created_at": int(time.time())}), encoding="utf-8")
    backup = destination.with_name(f".{destination.name}.old")
    shutil.rmtree(backup, ignore_errors=True)
    if destination.exists():
        os.replace(destination, backup)
    os.replace(temporary, destination)
    shutil.rmtree(backup, ignore_errors=True)


def artifact(track_id: str, name: str) -> tuple[Path, str]:
    if name not in {"stream.m3u8", "source.json"} and not name.startswith("segment-"):
        raise KeyError("Stream artifact not found")
    path = (_track_dir(track_id) / name).resolve()
    if not path.is_file() or not path.is_relative_to(_track_dir(track_id).resolve()):
        raise KeyError("Stream artifact not found")
    if path.suffix == ".m3u8":
        content_type = "application/vnd.apple.mpegurl"
    elif path.suffix == ".ts":
        # Linux distributions may register .ts as a Qt translation file. HLS
        # transport-stream segments must use the media type browsers expect.
        content_type = "video/mp2t"
    else:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path, content_type


def manifest_with_ticket(path: Path, ticket: str, generation: str = "") -> bytes:
    suffix = f"?ticket={ticket}"
    if generation:
        suffix += f"&generation={generation}"
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        lines.append(f"{line}{suffix}" if line and not line.startswith("#") else line)
    return ("\n".join(lines) + "\n").encode("utf-8")
