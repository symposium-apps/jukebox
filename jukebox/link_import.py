from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import import_companion
from .youtube_urls import YOUTUBE_HOSTS, canonicalize_source_url


ALLOWED_SOURCE_HOSTS = YOUTUBE_HOSTS
MAX_INSPECTION_ITEMS = 500
MAX_ACTIVE_JOBS = 2
MAX_RETAINED_JOBS = 40
INSPECTION_TTL_SECONDS = 30 * 60
TERMINAL_JOB_STATES = {"complete", "partial", "failed", "cancelled"}
AUDIO_QUALITIES = {"best": "0", "320": "320", "256": "256", "192": "192", "128": "128"}
VIDEO_QUALITIES = {"best": 1080, "1080": 1080, "720": 720, "480": 480, "360": 360}

IMPORT_LOCK = threading.RLock()
INDEX_LOCK = threading.RLock()
INSPECTIONS: dict[str, dict[str, Any]] = {}
JOBS: dict[str, dict[str, Any]] = {}


class ImportCancelled(Exception):
    pass


YOUTUBE_NETWORK_BLOCKED_MESSAGE = (
    "YouTube is blocking this server and the accountless local downloader is unavailable. "
    "Start the Jukebox Import Companion on the linked AI Computer, then try again"
)


class QuietLogger:
    def __init__(self) -> None:
        self.youtube_network_blocked = False

    def _classify(self, message: str) -> None:
        text = str(message or "").casefold()
        if "sign in to confirm you're not a bot" in text or "sign in to confirm you’re not a bot" in text:
            self.youtube_network_blocked = True

    def debug(self, message: str) -> None:
        self._classify(message)

    def info(self, message: str) -> None:
        return

    def warning(self, message: str) -> None:
        self._classify(message)

    def error(self, message: str) -> None:
        self._classify(message)


def validate_source_url(value: object) -> str:
    return canonicalize_source_url(value)


def _private_ytdlp_options() -> dict[str, object]:
    root = str(os.environ.get("SYM_APP_USER_DATA_DIR") or "").strip()
    if not root:
        return {}
    user_data_root = Path(root).resolve()
    config_dir = (user_data_root / "Jukebox API").resolve()
    if not config_dir.is_relative_to(user_data_root):
        return {}
    options: dict[str, object] = {}
    cookie_file = config_dir / "youtube-cookies.txt"
    if cookie_file.is_file() and not cookie_file.is_symlink():
        try:
            cookie_file.chmod(0o600)
        except OSError:
            pass
        options["cookiefile"] = str(cookie_file)
    proxy_file = config_dir / "youtube-proxy.txt"
    if proxy_file.is_file() and not proxy_file.is_symlink():
        try:
            proxy_file.chmod(0o600)
            proxy = proxy_file.read_text(encoding="utf-8").strip()
        except OSError:
            proxy = ""
        parsed_proxy = urlparse(proxy)
        if (
            len(proxy) <= 4096
            and not any(character in proxy for character in "\r\n")
            and parsed_proxy.scheme in {"http", "https", "socks4", "socks5", "socks5h"}
            and parsed_proxy.hostname
        ):
            options["proxy"] = proxy
    return options


def _inspection_failure(logger: QuietLogger, exc: Exception | None = None) -> ValueError:
    if exc is not None:
        logger._classify(str(exc))
    if logger.youtube_network_blocked:
        return ValueError(YOUTUBE_NETWORK_BLOCKED_MESSAGE)
    return ValueError("Jukebox could not inspect this link. It may be private, unavailable, or account-restricted")


def _load_ydl_class() -> type:
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("YouTube import is unavailable because yt-dlp is not installed") from exc
    return YoutubeDL


def dependency_status() -> dict[str, object]:
    try:
        _load_ydl_class()
        yt_dlp_ready = True
    except RuntimeError:
        yt_dlp_ready = False
    return {
        "yt_dlp": yt_dlp_ready,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "node": bool(shutil.which("node")),
        "local_companion": import_companion.configured(),
        "youtube_account_authentication": False,
        "formats": ["mp3", "mp4"],
        "mp4_status": "ready",
    }


def _thumbnail(info: dict[str, Any]) -> str:
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        candidates = [item for item in thumbnails if isinstance(item, dict) and str(item.get("url") or "").startswith("https://")]
        if candidates:
            candidates.sort(key=lambda item: (int(item.get("width") or 0) * int(item.get("height") or 0), int(item.get("preference") or 0)))
            return str(candidates[-1].get("url") or "")
    candidate = str(info.get("thumbnail") or "")
    return candidate if candidate.startswith("https://") else ""


def _duration_text(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _item_url(info: dict[str, Any]) -> str:
    for key in ("webpage_url", "original_url"):
        candidate = str(info.get(key) or "")
        try:
            return validate_source_url(candidate)
        except ValueError:
            pass
    source_id = str(info.get("id") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", source_id):
        return f"https://www.youtube.com/watch?v={source_id}"
    candidate = str(info.get("url") or "")
    try:
        return validate_source_url(candidate)
    except ValueError as exc:
        raise ValueError("The source returned an invalid track link") from exc


def _normalize_item(info: dict[str, Any], index: int) -> dict[str, Any]:
    source_id = str(info.get("id") or "").strip() or f"item-{index}"
    availability = str(info.get("availability") or "").casefold()
    title = str(info.get("title") or info.get("track") or f"Track {index}").strip()
    unavailable = availability in {"private", "premium_only", "subscriber_only", "needs_auth"} or title.casefold() in {"[private video]", "[deleted video]"}
    reason = "Unavailable" if unavailable else ""
    return {
        "id": source_id,
        "index": index,
        "title": title[:300],
        "artist": str(info.get("artist") or info.get("creator") or info.get("uploader") or info.get("channel") or "Unknown artist").strip()[:200],
        "album": str(info.get("album") or "").strip()[:200],
        "duration": int(float(str(info.get("duration") or 0))) if info.get("duration") else 0,
        "duration_text": _duration_text(info.get("duration")),
        "thumbnail": _thumbnail(info),
        "explicit": bool(info.get("age_limit") and int(info.get("age_limit") or 0) >= 18),
        "unavailable": unavailable,
        "unavailable_reason": reason,
        "url": "" if unavailable else _item_url(info),
    }


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in item.items() if key != "url"}


def _prune_locked() -> None:
    now = time.time()
    expired = [key for key, value in INSPECTIONS.items() if float(value.get("created_at") or 0) < now - INSPECTION_TTL_SECONDS]
    for key in expired:
        INSPECTIONS.pop(key, None)
    if len(JOBS) > MAX_RETAINED_JOBS:
        terminal = sorted(
            (value for value in JOBS.values() if value.get("status") in TERMINAL_JOB_STATES),
            key=lambda value: float(value.get("updated_at") or 0),
        )
        for value in terminal[: max(0, len(JOBS) - MAX_RETAINED_JOBS)]:
            JOBS.pop(str(value.get("id") or ""), None)


def inspect_source(url: object, *, ydl_class: type | None = None) -> dict[str, Any]:
    source_url = validate_source_url(url)
    logger = QuietLogger()
    if ydl_class is None and import_companion.configured():
        try:
            info = import_companion.inspect_source(source_url)
        except (import_companion.CompanionUnavailable, import_companion.CompanionDownloadFailed) as exc:
            raise ValueError(YOUTUBE_NETWORK_BLOCKED_MESSAGE) from exc
    else:
        YoutubeDL = ydl_class or _load_ydl_class()
        options: dict[str, Any] = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "extract_flat": "in_playlist",
            "playlistend": MAX_INSPECTION_ITEMS,
            "logger": logger,
            **_private_ytdlp_options(),
        }
        if shutil.which("node"):
            options["js_runtimes"] = {"node": {}}
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(source_url, download=False)
        except Exception as exc:
            raise _inspection_failure(logger, exc) from exc
    if not isinstance(info, dict):
        raise _inspection_failure(logger)
    raw_entries = info.get("entries")
    if isinstance(raw_entries, list):
        candidates = [item for item in raw_entries[:MAX_INSPECTION_ITEMS] if isinstance(item, dict)]
        source_type = "playlist"
    else:
        candidates = [info]
        source_type = "track"
    if not candidates:
        raise ValueError("This playlist has no accessible tracks")
    items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        try:
            items.append(_normalize_item(candidate, index))
        except ValueError:
            fallback = dict(candidate)
            fallback["availability"] = "needs_auth"
            items.append(_normalize_item(fallback, index))
    available = sum(1 for item in items if not item["unavailable"])
    total_duration = sum(int(item.get("duration") or 0) for item in items)
    inspection_id = uuid.uuid4().hex
    record = {
        "id": inspection_id,
        "created_at": time.time(),
        "source_type": source_type,
        "title": str(info.get("title") or items[0]["title"] or "YouTube import").strip()[:300],
        "creator": str(info.get("uploader") or info.get("channel") or info.get("creator") or items[0]["artist"] or "").strip()[:200],
        "thumbnail": _thumbnail(info) or str(items[0].get("thumbnail") or ""),
        "duration": total_duration or int(info.get("duration") or 0),
        "items": items,
    }
    with IMPORT_LOCK:
        _prune_locked()
        INSPECTIONS[inspection_id] = record
    return {
        "inspection_id": inspection_id,
        "source_type": source_type,
        "title": record["title"],
        "creator": record["creator"],
        "thumbnail": record["thumbnail"],
        "duration": record["duration"],
        "duration_text": _duration_text(record["duration"]),
        "count": len(items),
        "available_count": available,
        "items": [_public_item(item) for item in items],
        "formats": [{"id": "mp3", "label": "MP3 Audio", "enabled": True}, {"id": "mp4", "label": "MP4 Video", "enabled": True}],
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = {key: copy.deepcopy(value) for key, value in job.items() if key not in {"cancel_requested", "inspection_id", "destination"}}
    for item in public.get("items", []):
        item.pop("url", None)
        item.pop("work_dir", None)
    return public


def list_jobs() -> list[dict[str, Any]]:
    with IMPORT_LOCK:
        _prune_locked()
        return [_public_job(job) for job in sorted(JOBS.values(), key=lambda value: float(value.get("created_at") or 0), reverse=True)]


def get_job(job_id: object) -> dict[str, Any]:
    with IMPORT_LOCK:
        job = JOBS.get(str(job_id or ""))
        if not job:
            raise KeyError("Import job not found")
        return _public_job(job)


def _destination(payload: dict[str, Any], inspection: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("destination")
    data = raw if isinstance(raw, dict) else {}
    destination_type = str(data.get("type") or ("playlist_new" if inspection["source_type"] == "playlist" else "detected_album"))
    if destination_type not in {"detected_album", "playlist_new", "playlist_existing", "album"}:
        raise ValueError("Unsupported import destination")
    destination = {
        "type": destination_type,
        "name": str(data.get("name") or inspection.get("title") or "Imported music")[:100],
        "slug": str(data.get("slug") or "")[:120],
    }
    if destination_type == "playlist_existing" and not destination["slug"]:
        raise ValueError("Choose an existing playlist")
    return destination


def create_job(
    payload: dict[str, Any],
    *,
    library_dir: Path,
    state_dir: Path,
    quota_bytes: int,
    scan_callback: Callable[..., list[dict[str, Any]]],
    playlist_callback: Callable[[dict[str, str], list[str]], object],
    ydl_class: type | None = None,
) -> dict[str, Any]:
    inspection_id = str(payload.get("inspection_id") or "")
    with IMPORT_LOCK:
        _prune_locked()
        inspection = INSPECTIONS.get(inspection_id)
        if not inspection:
            raise ValueError("This inspection expired. Inspect the link again")
        if sum(1 for job in JOBS.values() if job.get("status") not in TERMINAL_JOB_STATES) >= MAX_ACTIVE_JOBS:
            raise ValueError("Jukebox is already running the maximum number of imports")
        requested = payload.get("item_ids")
        if not isinstance(requested, list) or not requested:
            raise ValueError("Select at least one track")
        selected_ids = list(dict.fromkeys(str(item) for item in requested if str(item)))
        by_id = {str(item["id"]): item for item in inspection["items"]}
        selected = [copy.deepcopy(by_id[item_id]) for item_id in selected_ids if item_id in by_id and not by_id[item_id]["unavailable"]]
        if not selected:
            raise ValueError("Select at least one available track")
        if len(selected) != len(selected_ids):
            raise ValueError("The selected tracks do not match this inspection")
        active_source_ids = {
            str(item.get("id") or "")
            for active_job in JOBS.values()
            if active_job.get("status") not in TERMINAL_JOB_STATES
            for item in active_job.get("items", [])
        }
        if active_source_ids.intersection(selected_ids):
            raise ValueError("One or more selected tracks are already queued")
        output_format = str(payload.get("format") or "mp3").casefold()
        if output_format not in {"mp3", "mp4"}:
            raise ValueError("Choose MP3 Audio or MP4 Video")
        quality = str(payload.get("quality") or "best")
        allowed_qualities = AUDIO_QUALITIES if output_format == "mp3" else VIDEO_QUALITIES
        if quality not in allowed_qualities:
            raise ValueError(f"Unsupported {output_format.upper()} quality")
        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
            "id": job_id,
            "inspection_id": inspection_id,
            "created_at": now,
            "updated_at": now,
            "status": "queued",
            "stage": "Waiting",
            "title": str(inspection.get("title") or "YouTube import"),
            "thumbnail": str(inspection.get("thumbnail") or ""),
            "format": output_format,
            "quality": quality,
            "artwork": payload.get("artwork") is not False,
            "destination": _destination(payload, inspection),
            "total": len(selected),
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "progress": 0,
            "cancel_requested": False,
            "items": [
                {
                    **item,
                    "status": "waiting",
                    "stage": "Waiting",
                    "progress": None,
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "error": "",
                    "track_id": "",
                }
                for item in selected
            ],
        }
        JOBS[job_id] = job
    worker = threading.Thread(
        target=_run_job,
        args=(job_id,),
        kwargs={
            "library_dir": Path(library_dir).resolve(),
            "state_dir": Path(state_dir).resolve(),
            "quota_bytes": int(quota_bytes),
            "scan_callback": scan_callback,
            "playlist_callback": playlist_callback,
            "ydl_class": ydl_class,
        },
        name=f"jukebox-import-{job_id[:8]}",
        daemon=True,
    )
    worker.start()
    return get_job(job_id)


def cancel_job(job_id: object) -> dict[str, Any]:
    with IMPORT_LOCK:
        job = JOBS.get(str(job_id or ""))
        if not job:
            raise KeyError("Import job not found")
        if job.get("status") in TERMINAL_JOB_STATES:
            return _public_job(job)
        job["cancel_requested"] = True
        job["stage"] = "Cancelling"
        job["updated_at"] = time.time()
        return _public_job(job)


def clear_finished_jobs() -> int:
    with IMPORT_LOCK:
        keys = [key for key, job in JOBS.items() if job.get("status") in TERMINAL_JOB_STATES]
        for key in keys:
            JOBS.pop(key, None)
        return len(keys)


def _safe_component(value: object, fallback: str, limit: int = 90) -> str:
    clean = re.sub(r'[<>:"|?*\x00-\x1f]+', "", str(value or "")).replace("/", "").replace("\\", "").strip().strip(".")
    return clean[:limit] or fallback


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
    except OSError:
        return total
    return total


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not choose a unique library filename")


def _job_cancelled(job_id: str) -> bool:
    with IMPORT_LOCK:
        job = JOBS.get(job_id)
        return not job or bool(job.get("cancel_requested"))


def _update_item(job_id: str, index: int, **updates: object) -> None:
    with IMPORT_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        item = job["items"][index]
        item.update(updates)
        job["updated_at"] = time.time()
        total = max(1, int(job["total"]))
        completed_fraction = int(job["completed"]) + int(job["failed"]) + int(job["cancelled"])
        active_progress = float(item.get("progress") or 0) / 100 if item.get("status") == "downloading" else 0
        job["progress"] = min(100, round((completed_fraction + active_progress) * 100 / total))
        job["stage"] = str(item.get("stage") or job.get("stage") or "Waiting")


def _persist_index(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _load_index(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _cover_from_work(work: Path, destination_dir: Path) -> None:
    if any((destination_dir / name).exists() for name in ("cover.jpg", "cover.png", "cover.webp", "folder.jpg", "folder.png")):
        return
    images = [path for path in work.rglob("*") if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}]
    if not images:
        return
    try:
        from PIL import Image  # type: ignore[import-not-found]

        with Image.open(images[0]) as image:
            converted = image.convert("RGB")
            cover = destination_dir / "cover.jpg"
            temporary = cover.with_name(f".{cover.name}.{uuid.uuid4().hex}.tmp")
            converted.save(temporary, format="JPEG", quality=92, optimize=True)
            os.replace(temporary, cover)
    except Exception:
        return


def _cover_from_audio(audio_path: Path, destination_dir: Path) -> None:
    if any((destination_dir / name).exists() for name in ("cover.jpg", "cover.png", "cover.webp", "folder.jpg", "folder.png")):
        return
    try:
        from io import BytesIO

        from mutagen import File as MutagenFile  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]

        tagged = MutagenFile(audio_path)
        tags = getattr(tagged, "tags", None)
        artwork = next((bytes(value.data) for value in (tags.values() if tags else []) if hasattr(value, "data") and value.__class__.__name__ == "APIC"), b"")
        if not artwork:
            return
        with Image.open(BytesIO(artwork)) as image:
            converted = image.convert("RGB")
            cover = destination_dir / "cover.jpg"
            temporary = cover.with_name(f".{cover.name}.{uuid.uuid4().hex}.tmp")
            converted.save(temporary, format="JPEG", quality=92, optimize=True)
            os.replace(temporary, cover)
    except Exception:
        return


def _extract_mp3(video_path: Path, output_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to create the audio copy")
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp.mp3")
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2", "-map_metadata", "0", str(temporary)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=900,
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("Audio extraction produced no MP3")
        os.replace(temporary, output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _download_one(job_id: str, item_index: int, *, library_dir: Path, work_root: Path, quota_bytes: int, index_path: Path, ydl_class: type | None) -> str:
    with IMPORT_LOCK:
        job = JOBS[job_id]
        item = copy.deepcopy(job["items"][item_index])
        output_format = str(job["format"])
        quality = str(job["quality"])
        include_artwork = bool(job["artwork"])
        destination = copy.deepcopy(job["destination"])
    if _job_cancelled(job_id):
        raise ImportCancelled
    with INDEX_LOCK:
        source_index = _load_index(index_path)
    index_key = f"{item['id']}:{output_format}"
    existing_relative = source_index.get(index_key) or (source_index.get(str(item["id"])) if output_format == "mp3" else None)
    if existing_relative:
        existing_path = (library_dir / existing_relative).resolve()
        if existing_path.is_file() and existing_path.is_relative_to(library_dir):
            _update_item(job_id, item_index, status="complete", stage="Already in Jukebox", progress=100, track_id="")
            return existing_relative
    if _directory_size(library_dir.parent) >= quota_bytes:
        raise RuntimeError("Jukebox does not have enough storage for this import")
    work = work_root / f"{item_index + 1:03d}-{_safe_component(item['id'], 'track', 40)}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    def progress_hook(data: dict[str, Any]) -> None:
        if _job_cancelled(job_id):
            raise ImportCancelled
        status = str(data.get("status") or "")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            progress = round(downloaded * 100 / total) if total else None
            _update_item(job_id, item_index, status="downloading", stage="Downloading", progress=progress, downloaded_bytes=downloaded, total_bytes=total)
        elif status == "finished":
            stage = "Converting to MP3" if output_format == "mp3" else "Merging MP4 video"
            _update_item(job_id, item_index, status="processing", stage=stage, progress=None)

    def postprocessor_hook(data: dict[str, Any]) -> None:
        if _job_cancelled(job_id):
            raise ImportCancelled
        if str(data.get("status") or "") != "started":
            return
        name = str(data.get("postprocessor") or "")
        stage = "Adding to Jukebox"
        if "ExtractAudio" in name:
            stage = "Converting to MP3"
        elif "Merger" in name:
            stage = "Merging MP4 video"
        elif "Metadata" in name:
            stage = "Embedding metadata"
        elif "Thumbnail" in name:
            stage = "Downloading artwork"
        _update_item(job_id, item_index, status="processing", stage=stage, progress=None)

    if ydl_class is None and import_companion.configured():
        try:
            media_path, downloaded_info = import_companion.download_source(
                str(item["url"]),
                output_format=output_format,
                quality=quality,
                artwork=include_artwork,
                destination=work,
                progress=lambda value, downloaded, total, stage: _update_item(
                    job_id,
                    item_index,
                    status="processing" if stage in {"Processing", "Transferring to Jukebox"} else "downloading",
                    stage=stage,
                    progress=value,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                ),
                cancelled=lambda: _job_cancelled(job_id),
            )
        except import_companion.CompanionCancelled as exc:
            raise ImportCancelled from exc
        except (import_companion.CompanionUnavailable, import_companion.CompanionDownloadFailed) as exc:
            raise RuntimeError("Download interrupted or unavailable") from exc
    else:
        YoutubeDL = ydl_class or _load_ydl_class()
        logger = QuietLogger()
        postprocessors: list[dict[str, Any]] = [{"key": "FFmpegMetadata", "add_metadata": True}]
        if output_format == "mp3":
            postprocessors.insert(0, {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": AUDIO_QUALITIES[quality]})
            if include_artwork:
                postprocessors.append({"key": "EmbedThumbnail"})
        height = VIDEO_QUALITIES.get(quality, 1080)
        options: dict[str, Any] = {
            "format": "bestaudio/best" if output_format == "mp3" else f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "logger": logger,
            "paths": {"home": str(work), "temp": str(work)},
            "outtmpl": {"default": "source.%(ext)s", "thumbnail": "source.%(ext)s"},
            "retries": 3,
            "fragment_retries": 3,
            "continuedl": True,
            "writethumbnail": include_artwork,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "postprocessors": postprocessors,
            **({"merge_output_format": "mp4"} if output_format == "mp4" else {}),
            **_private_ytdlp_options(),
        }
        if shutil.which("node"):
            options["js_runtimes"] = {"node": {}}
        try:
            with YoutubeDL(options) as ydl:
                downloaded_info = ydl.extract_info(str(item["url"]), download=True)
        except ImportCancelled:
            raise
        except Exception as exc:
            logger._classify(str(exc))
            if logger.youtube_network_blocked:
                raise RuntimeError(YOUTUBE_NETWORK_BLOCKED_MESSAGE) from exc
            raise RuntimeError("Download interrupted or unavailable") from exc
        media_files = sorted(path for path in work.rglob(f"*.{output_format}") if path.is_file())
        if not media_files:
            raise RuntimeError(f"The download did not produce an {output_format.upper()} file")
        media_path = media_files[0]
    if _job_cancelled(job_id):
        raise ImportCancelled

    if isinstance(downloaded_info, dict):
        artist = str(downloaded_info.get("artist") or downloaded_info.get("creator") or downloaded_info.get("uploader") or item["artist"])
        album = str(downloaded_info.get("album") or item["album"])
        title = str(downloaded_info.get("track") or downloaded_info.get("title") or item["title"])
    else:
        artist, album, title = str(item["artist"]), str(item["album"]), str(item["title"])
    paired_audio: Path | None = None
    if output_format == "mp4":
        _update_item(job_id, item_index, status="processing", stage="Creating audio copy", progress=None)
        paired_audio = work / "audio-copy.mp3"
        _extract_mp3(media_path, paired_audio)
    required_bytes = media_path.stat().st_size + (paired_audio.stat().st_size if paired_audio is not None else 0)
    if _directory_size(library_dir.parent) + required_bytes > quota_bytes:
        raise RuntimeError("Jukebox does not have enough storage for this import")
    if destination["type"] == "album":
        folder_name = _safe_component(destination["name"], "Imported music")
    else:
        clean_artist = _safe_component(artist, "Unknown artist")
        clean_album = _safe_component(album, str(job_id)[:8]) if album else "Singles"
        folder_name = _safe_component(f"{clean_artist} - {clean_album}", "Imported music")
    destination_dir = (library_dir / folder_name).resolve()
    if not destination_dir.is_relative_to(library_dir):
        raise RuntimeError("Import destination escaped the music library")
    destination_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{int(item['index']):02d} " if int(item.get("index") or 0) else ""
    final_path = _unique_destination(destination_dir / f"{prefix}{_safe_component(title, 'Track', 140)}.{output_format}")
    os.replace(media_path, final_path)
    paired_final: Path | None = None
    if paired_audio is not None:
        paired_final = _unique_destination(destination_dir / f"{prefix}{_safe_component(title, 'Track', 140)}.mp3")
        os.replace(paired_audio, paired_final)
    if include_artwork:
        _cover_from_work(work, destination_dir)
        if output_format == "mp3":
            _cover_from_audio(final_path, destination_dir)
        elif paired_final is not None:
            _cover_from_audio(paired_final, destination_dir)
    relative = final_path.relative_to(library_dir).as_posix()
    with INDEX_LOCK:
        source_index = _load_index(index_path)
        source_index[index_key] = relative
        if paired_final is not None:
            source_index[f"{item['id']}:mp3"] = paired_final.relative_to(library_dir).as_posix()
        _persist_index(index_path, source_index)
    return relative


def _run_job(
    job_id: str,
    *,
    library_dir: Path,
    state_dir: Path,
    quota_bytes: int,
    scan_callback: Callable[..., list[dict[str, Any]]],
    playlist_callback: Callable[[dict[str, str], list[str]], object],
    ydl_class: type | None,
) -> None:
    work_root = state_dir / "youtube-import-work" / job_id
    index_path = state_dir / "youtube-import-index.json"
    library_dir.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    successful_paths: list[str] = []
    try:
        with IMPORT_LOCK:
            job = JOBS[job_id]
            job["status"] = "running"
            job["stage"] = "Downloading"
            job["updated_at"] = time.time()
        for item_index in range(int(JOBS[job_id]["total"])):
            if _job_cancelled(job_id):
                with IMPORT_LOCK:
                    job = JOBS[job_id]
                    for remaining in job["items"][item_index:]:
                        if remaining["status"] == "waiting":
                            remaining.update({"status": "cancelled", "stage": "Cancelled", "progress": None})
                            job["cancelled"] += 1
                break
            try:
                relative = _download_one(
                    job_id,
                    item_index,
                    library_dir=library_dir,
                    work_root=work_root,
                    quota_bytes=quota_bytes,
                    index_path=index_path,
                    ydl_class=ydl_class,
                )
                successful_paths.append(relative)
                with IMPORT_LOCK:
                    job = JOBS[job_id]
                    item = job["items"][item_index]
                    item.update({"status": "complete", "stage": "Added to Jukebox", "progress": 100, "relative_path": relative})
                    job["completed"] += 1
                    job["updated_at"] = time.time()
            except ImportCancelled:
                with IMPORT_LOCK:
                    job = JOBS[job_id]
                    item = job["items"][item_index]
                    item.update({"status": "cancelled", "stage": "Cancelled", "progress": None})
                    job["cancelled"] += 1
                continue
            except Exception as exc:
                allowed_messages = {
                    "Jukebox does not have enough storage for this import",
                    "The download did not produce an MP3 file",
                    "The download did not produce an MP4 file",
                    "Download interrupted or unavailable",
                    YOUTUBE_NETWORK_BLOCKED_MESSAGE,
                }
                message = str(exc) if str(exc) in allowed_messages else "Could not download this track"
                with IMPORT_LOCK:
                    job = JOBS[job_id]
                    item = job["items"][item_index]
                    item.update({"status": "failed", "stage": "Failed", "progress": None, "error": message})
                    job["failed"] += 1
                    job["updated_at"] = time.time()
        tracks = scan_callback(force=True)
        by_relative = {str(track.get("relative_path") or ""): str(track.get("id") or "") for track in tracks}
        track_ids = [by_relative[path] for path in successful_paths if by_relative.get(path)]
        with IMPORT_LOCK:
            job = JOBS[job_id]
            for item in job["items"]:
                relative = str(item.get("relative_path") or "")
                if relative in by_relative:
                    item["track_id"] = by_relative[relative]
            destination = copy.deepcopy(job["destination"])
        if track_ids and destination["type"] in {"playlist_new", "playlist_existing"}:
            playlist_callback(destination, track_ids)
        with IMPORT_LOCK:
            job = JOBS[job_id]
            if job["cancel_requested"]:
                status = "cancelled"
                stage = "Cancelled"
            elif job["failed"] and job["completed"]:
                status = "partial"
                stage = "Import complete with problems"
            elif job["failed"]:
                status = "failed"
                stage = "Import failed"
            else:
                status = "complete"
                stage = "Import complete"
            job.update({"status": status, "stage": stage, "progress": 100, "updated_at": time.time()})
    except Exception:
        with IMPORT_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({"status": "failed", "stage": "Import failed", "error": "The import could not be completed", "updated_at": time.time()})
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
