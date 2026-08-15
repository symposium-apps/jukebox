from __future__ import annotations

import ipaddress
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESULT_BYTES = 4 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class CompanionUnavailable(RuntimeError):
    pass


class CompanionDownloadFailed(RuntimeError):
    pass


class CompanionCancelled(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


OPENER = build_opener(_NoRedirect())


def _configuration() -> tuple[str, str] | None:
    raw_url = str(os.environ.get("JUKEBOX_IMPORT_COMPANION_URL") or "").strip()
    token = str(os.environ.get("JUKEBOX_IMPORT_COMPANION_TOKEN") or "").strip()
    if not raw_url and not token:
        return None
    if not raw_url or len(token) < 32 or len(token) > 4096 or any(character in token for character in "\r\n"):
        raise CompanionUnavailable("The local downloader is not configured correctly")
    parsed = urlparse(raw_url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise CompanionUnavailable("The local downloader address is invalid") from exc
    allowed_address = address.is_loopback or address in TAILSCALE_V4 or address in TAILSCALE_V6
    if parsed.scheme not in {"http", "https"} or not allowed_address or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CompanionUnavailable("The local downloader address is invalid")
    if parsed.path not in {"", "/"} or port is None:
        raise CompanionUnavailable("The local downloader address is invalid")
    return raw_url.rstrip("/"), token


def configured() -> bool:
    try:
        return _configuration() is not None
    except CompanionUnavailable:
        return False


def _request(path: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 60) -> Any:
    configuration = _configuration()
    if not configuration:
        raise CompanionUnavailable("The local downloader is not configured")
    base_url, token = configuration
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
    request = Request(
        f"{base_url}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            data = response.read(MAX_JSON_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CompanionUnavailable("The local downloader could not be reached") from exc
    if len(data) > MAX_JSON_BYTES:
        raise CompanionUnavailable("The local downloader returned an invalid response")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanionUnavailable("The local downloader returned an invalid response") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise CompanionDownloadFailed("The public media source was rejected or unavailable")
    return value.get("data")


def inspect_source(source_url: str) -> dict[str, Any]:
    data = _request("/v1/inspect", method="POST", body={"url": source_url}, timeout=180)
    if not isinstance(data, dict):
        raise CompanionUnavailable("The local downloader returned invalid metadata")
    return data


def _download_result(job_id: str, destination: Path, progress: Callable[[int, int], None]) -> Path:
    configuration = _configuration()
    if not configuration:
        raise CompanionUnavailable("The local downloader is not configured")
    base_url, token = configuration
    request = Request(
        f"{base_url}/v1/jobs/{job_id}/result",
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/zip"},
    )
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        with OPENER.open(request, timeout=1800) as response, temporary.open("wb") as target:
            try:
                expected = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                expected = 0
            if expected > MAX_RESULT_BYTES:
                raise CompanionDownloadFailed("The local download exceeded the Jukebox upload limit")
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_RESULT_BYTES:
                    raise CompanionDownloadFailed("The local download exceeded the Jukebox upload limit")
                target.write(chunk)
                progress(downloaded, expected)
        os.replace(temporary, destination)
        return destination
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CompanionUnavailable("The local downloader result could not be transferred") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _unpack_bundle(bundle: Path, destination: Path, output_format: str) -> tuple[Path, dict[str, Any]]:
    expected_media = f"media.{output_format}"
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        if expected_media not in names or "info.json" not in names:
            raise CompanionDownloadFailed("The local downloader returned an incomplete result")
        allowed = {expected_media, "info.json", "thumbnail.jpg", "thumbnail.jpeg", "thumbnail.png", "thumbnail.webp"}
        if not names.issubset(allowed):
            raise CompanionDownloadFailed("The local downloader returned an unsafe result")
        media_info = archive.getinfo(expected_media)
        if media_info.file_size <= 0 or media_info.file_size > MAX_RESULT_BYTES:
            raise CompanionDownloadFailed("The local downloader returned an invalid media file")
        media_path = destination / expected_media
        with archive.open(media_info) as source, media_path.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        with archive.open("info.json") as source:
            raw_info = source.read(MAX_JSON_BYTES + 1)
        if len(raw_info) > MAX_JSON_BYTES:
            raise CompanionDownloadFailed("The local downloader returned invalid metadata")
        try:
            metadata = json.loads(raw_info)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanionDownloadFailed("The local downloader returned invalid metadata") from exc
        for name in sorted(names - {expected_media, "info.json"}):
            thumbnail_path = destination / name
            with archive.open(name) as source, thumbnail_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    if not isinstance(metadata, dict):
        raise CompanionDownloadFailed("The local downloader returned invalid metadata")
    return media_path, metadata


def download_source(
    source_url: str,
    *,
    output_format: str,
    quality: str,
    artwork: bool,
    destination: Path,
    progress: Callable[[int | None, int, int, str], None],
    cancelled: Callable[[], bool],
) -> tuple[Path, dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True)
    data = _request(
        "/v1/jobs",
        method="POST",
        body={"url": source_url, "format": output_format, "quality": quality, "artwork": artwork},
        timeout=30,
    )
    if not isinstance(data, dict) or not str(data.get("id") or ""):
        raise CompanionUnavailable("The local downloader did not create a job")
    job_id = str(data["id"])
    try:
        while True:
            if cancelled():
                try:
                    _request(f"/v1/jobs/{job_id}", method="DELETE", timeout=10)
                except Exception:
                    pass
                raise CompanionCancelled
            status = _request(f"/v1/jobs/{job_id}", timeout=30)
            if not isinstance(status, dict):
                raise CompanionUnavailable("The local downloader returned an invalid job status")
            state = str(status.get("status") or "")
            progress(
                int(status["progress"]) if status.get("progress") is not None else None,
                int(status.get("downloaded_bytes") or 0),
                int(status.get("total_bytes") or 0),
                str(status.get("stage") or "Downloading"),
            )
            if state == "complete":
                break
            if state == "cancelled":
                raise CompanionCancelled
            if state == "failed":
                raise CompanionDownloadFailed("The public media download was rejected or unavailable")
            time.sleep(1)
        bundle = _download_result(job_id, destination / "result.zip", lambda done, total: progress(round(done * 100 / total) if total else None, done, total, "Transferring to Jukebox"))
        return _unpack_bundle(bundle, destination, output_format)
    finally:
        try:
            _request(f"/v1/jobs/{job_id}", method="DELETE", timeout=10)
        except Exception:
            pass
