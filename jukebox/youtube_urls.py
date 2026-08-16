from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{6,20}")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{6,100}")
CLIP_ID = re.compile(r"[A-Za-z0-9_-]{10,100}")


def _error() -> ValueError:
    return ValueError("This is not a supported YouTube or YouTube Music link")


def _canonical(path: str, query: dict[str, list[str]]) -> str:
    return urlunparse(("https", "www.youtube.com", path, "", urlencode({key: values[0] for key, values in query.items() if values and values[0]}), ""))


def _watch(video_id: str, playlist_values: list[str]) -> str:
    playlist_id = str((playlist_values or [""])[0]).strip()
    if not playlist_id or playlist_id.startswith("RD"):
        return _canonical("/watch", {"v": [video_id]})
    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise _error()
    return _canonical("/watch", {"v": [video_id], "list": [playlist_id]})


def canonicalize_source_url(value: object, *, _depth: int = 0) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or _depth > 2:
        raise _error()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise _error() from exc
    if parsed.scheme != "https" or host not in YOUTUBE_HOSTS or parsed.username or parsed.password or port not in {None, 443}:
        raise _error()

    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query, keep_blank_values=False)

    if path == "/attribution_link" and host not in {"youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        target = str((query.get("u") or [""])[0]).strip()
        if not target.startswith("/") or target.startswith("//"):
            raise _error()
        return canonicalize_source_url(f"https://www.youtube.com{target}", _depth=_depth + 1)

    if host == "youtu.be":
        video_id = path.strip("/")
        if not VIDEO_ID.fullmatch(video_id):
            raise _error()
        return _watch(video_id, query.get("list", []))

    if host in {"youtube-nocookie.com", "www.youtube-nocookie.com"} and not path.startswith("/embed/"):
        raise _error()

    for prefix in ("/shorts/", "/live/", "/embed/", "/v/"):
        if path.startswith(prefix):
            video_id = path.removeprefix(prefix)
            if not VIDEO_ID.fullmatch(video_id):
                raise _error()
            return _watch(video_id, query.get("list", []))

    if path == "/watch":
        video_id = str((query.get("v") or [""])[0]).strip()
        playlist_id = str((query.get("list") or [""])[0]).strip()
        if video_id:
            if not VIDEO_ID.fullmatch(video_id):
                raise _error()
            return _watch(video_id, [playlist_id])
        if playlist_id and PLAYLIST_ID.fullmatch(playlist_id):
            return _canonical("/playlist", {"list": [playlist_id]})
        raise _error()

    if path in {"/playlist", "/podcast"}:
        playlist_id = str((query.get("list") or [""])[0]).strip()
        if not PLAYLIST_ID.fullmatch(playlist_id):
            raise _error()
        return _canonical("/playlist", {"list": [playlist_id]})

    if path.startswith("/clip/"):
        clip_id = path.removeprefix("/clip/")
        if not CLIP_ID.fullmatch(clip_id):
            raise _error()
        return urlunparse(("https", "www.youtube.com", f"/clip/{clip_id}", "", "", ""))

    raise _error()
