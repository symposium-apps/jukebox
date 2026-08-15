#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import server


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def inspect(payload: dict[str, Any]) -> None:
    emit({"event": "result", "data": server.inspect_source(str(payload.get("url") or ""))})


def download(payload: dict[str, Any]) -> None:
    from yt_dlp import YoutubeDL

    source_url = server.validate_source_url(payload.get("url"))
    output_format = str(payload.get("format") or "mp3").casefold()
    quality = str(payload.get("quality") or "best").casefold()
    artwork = payload.get("artwork") is not False
    root = Path(str(payload.get("root") or "")).resolve()
    if output_format not in {"mp3", "mp4"} or not root.is_dir() or root.is_symlink():
        raise ValueError("Invalid download job")
    allowed = server.AUDIO_QUALITIES if output_format == "mp3" else server.VIDEO_QUALITIES
    if quality not in allowed:
        raise ValueError("Invalid download quality")

    def progress(data: dict[str, Any]) -> None:
        status = str(data.get("status") or "")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            emit({
                "event": "progress",
                "stage": "Downloading",
                "progress": round(downloaded * 100 / total) if total else None,
                "downloaded_bytes": downloaded,
                "total_bytes": total,
            })
        elif status == "finished":
            emit({"event": "progress", "stage": "Processing", "progress": None, "downloaded_bytes": 0, "total_bytes": 0})

    height = server.VIDEO_QUALITIES.get(quality, 1080)
    options: dict[str, Any] = {
        **server.common_options(),
        "noplaylist": True,
        "paths": {"home": str(root), "temp": str(root)},
        "outtmpl": {"default": "source.%(ext)s", "thumbnail": "source.%(ext)s"},
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "writethumbnail": artwork,
        "progress_hooks": [progress],
    }
    if output_format == "mp3":
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": server.AUDIO_QUALITIES[quality]},
                {"key": "FFmpegMetadata", "add_metadata": True},
                *([{"key": "EmbedThumbnail"}] if artwork else []),
            ],
        })
    else:
        options.update({
            "format": f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]",
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}],
        })
    with YoutubeDL(options) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(source_url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("Download returned no metadata")
    media = next((path for path in root.glob(f"*.{output_format}") if path.is_file()), None)
    if not media:
        raise RuntimeError("Download returned no media")
    info_path = root / "info.json"
    info_path.write_text(json.dumps(server.public_info(dict(info), include_entries=False), ensure_ascii=False), encoding="utf-8")
    archive = root / "result.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        bundle.write(media, f"media.{output_format}")
        bundle.write(info_path, "info.json")
        if artwork:
            thumbnail = next((path for path in root.glob("source.*") if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}), None)
            if thumbnail:
                bundle.write(thumbnail, f"thumbnail{thumbnail.suffix.casefold()}")
    emit({"event": "result", "path": str(archive)})


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"inspect", "download"}:
        raise SystemExit(2)
    raw = sys.stdin.buffer.read(server.MAX_BODY_BYTES + 1)
    if not raw or len(raw) > server.MAX_BODY_BYTES:
        raise SystemExit(2)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        (inspect if sys.argv[1] == "inspect" else download)(payload)
    except Exception as exc:
        print(server.safe_diagnostic(exc), file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
