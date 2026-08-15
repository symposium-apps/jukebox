from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from jukebox import link_import


class InspectYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, url, download=False):
        self.url = url
        return {
            "id": "playlist-1",
            "title": "Design Test Playlist",
            "uploader": "Test Curator",
            "thumbnail": "https://i.ytimg.com/vi/playlist/default.jpg",
            "entries": [
                {
                    "id": "video000001",
                    "title": "First Track",
                    "artist": "First Artist",
                    "album": "First Album",
                    "duration": 183,
                    "thumbnail": "https://i.ytimg.com/vi/video000001/default.jpg",
                    "webpage_url": "https://music.youtube.com/watch?v=video000001",
                },
                {
                    "id": "video000002",
                    "title": "[Private video]",
                    "availability": "private",
                },
            ],
        }


class DownloadYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, url, download=False):
        work = Path(self.options["paths"]["home"])
        for hook in self.options["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            hook({"status": "finished", "filename": str(work / "source.webm")})
        for hook in self.options["postprocessor_hooks"]:
            hook({"status": "started", "postprocessor": "FFmpegExtractAudio"})
            hook({"status": "started", "postprocessor": "FFmpegMetadata"})
            hook({"status": "started", "postprocessor": "EmbedThumbnail"})
        (work / "source.mp3").write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00")
        Image.new("RGB", (20, 20), "#7954d8").save(work / "source.jpg")
        return {"id": "video000001", "title": "First Track", "artist": "First Artist", "album": "First Album"}


class BlockedYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, url, download=False):
        self.options["logger"].error("Sign in to confirm you're not a bot")
        return None


class LinkImportTest(unittest.TestCase):
    def setUp(self):
        with link_import.IMPORT_LOCK:
            link_import.INSPECTIONS.clear()
            link_import.JOBS.clear()

    def test_source_validation_is_https_and_host_exact(self):
        self.assertEqual(
            link_import.validate_source_url("https://music.youtube.com/playlist?list=abc"),
            "https://music.youtube.com/playlist?list=abc",
        )
        self.assertEqual(
            link_import.validate_source_url("https://www.youtube.com/watch?v=video000001&list=RDvideo000001&start_radio=1"),
            "https://www.youtube.com/watch?v=video000001",
        )
        for value in (
            "http://music.youtube.com/watch?v=abc",
            "https://youtube.com.evil.example/watch?v=abc",
            "https://user@youtube.com/watch?v=abc",
            "https://youtube.com:444/watch?v=abc",
            "https://youtube.com/@channel",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                link_import.validate_source_url(value)

    def test_network_block_has_actionable_error_without_automatic_browser_cookies(self):
        with self.assertRaisesRegex(ValueError, "blocking requests from this server"):
            link_import.inspect_source("https://www.youtube.com/watch?v=video000001", ydl_class=BlockedYDL)

    def test_explicit_private_cookie_and_proxy_files_are_opt_in(self):
        with tempfile.TemporaryDirectory(prefix="jukebox-youtube-config-") as temporary:
            root = Path(temporary)
            config = root / "Jukebox API"
            config.mkdir()
            cookie_file = config / "youtube-cookies.txt"
            proxy_file = config / "youtube-proxy.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            proxy_file.write_text("socks5h://proxy.example:1080\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"SYM_APP_USER_DATA_DIR": str(root)}):
                options = link_import._private_ytdlp_options()
            self.assertEqual(options["cookiefile"], str(cookie_file.resolve()))
            self.assertEqual(options["proxy"], "socks5h://proxy.example:1080")
            self.assertEqual(cookie_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(proxy_file.stat().st_mode & 0o777, 0o600)

    def test_inspection_returns_selection_data_without_download_urls(self):
        result = link_import.inspect_source(
            "https://music.youtube.com/playlist?list=fixture",
            ydl_class=InspectYDL,
        )
        self.assertEqual(result["source_type"], "playlist")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["items"][0]["duration_text"], "3:03")
        self.assertNotIn("url", result["items"][0])
        self.assertTrue(result["items"][1]["unavailable"])
        self.assertEqual(result["formats"][1]["status"], "coming_next")

    def test_job_downloads_mp3_artwork_rescans_and_creates_playlist(self):
        inspected = link_import.inspect_source(
            "https://music.youtube.com/playlist?list=fixture",
            ydl_class=InspectYDL,
        )
        with tempfile.TemporaryDirectory(prefix="jukebox-import-test-") as temporary:
            root = Path(temporary)
            library = root / "UserData" / "Music"
            state = root / "State"
            playlist_calls = []

            def scan_callback(force=False):
                return [
                    {"id": f"track-{index}", "relative_path": path.relative_to(library).as_posix()}
                    for index, path in enumerate(sorted(library.rglob("*.mp3")), 1)
                ]

            def playlist_callback(destination, track_ids):
                playlist_calls.append((destination, track_ids))

            job = link_import.create_job(
                {
                    "inspection_id": inspected["inspection_id"],
                    "item_ids": ["video000001"],
                    "format": "mp3",
                    "quality": "best",
                    "artwork": True,
                    "destination": {"type": "playlist_new", "name": "Design Test Playlist"},
                },
                library_dir=library,
                state_dir=state,
                quota_bytes=100 * 1024 * 1024,
                scan_callback=scan_callback,
                playlist_callback=playlist_callback,
                ydl_class=DownloadYDL,
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                job = link_import.get_job(job["id"])
                if job["status"] in link_import.TERMINAL_JOB_STATES:
                    break
                time.sleep(0.02)
            self.assertEqual(job["status"], "complete", job)
            self.assertEqual(job["completed"], 1)
            mp3s = list(library.rglob("*.mp3"))
            self.assertEqual(len(mp3s), 1)
            self.assertIn("First Artist - First Album", mp3s[0].as_posix())
            self.assertTrue((mp3s[0].parent / "cover.jpg").is_file())
            self.assertTrue((state / "youtube-import-index.json").is_file())
            self.assertEqual(playlist_calls[0][0]["name"], "Design Test Playlist")
            self.assertEqual(playlist_calls[0][1], ["track-1"])
            self.assertFalse((state / "youtube-import-work" / job["id"]).exists())

    def test_job_rejects_mp4_until_video_library_support_exists(self):
        inspected = link_import.inspect_source(
            "https://music.youtube.com/playlist?list=fixture",
            ydl_class=InspectYDL,
        )
        with tempfile.TemporaryDirectory(prefix="jukebox-import-test-") as temporary, self.assertRaisesRegex(ValueError, "coming next"):
            link_import.create_job(
                {"inspection_id": inspected["inspection_id"], "item_ids": ["video000001"], "format": "mp4"},
                library_dir=Path(temporary) / "Music",
                state_dir=Path(temporary) / "State",
                quota_bytes=1000,
                scan_callback=lambda **_: [],
                playlist_callback=lambda *_: None,
                ydl_class=DownloadYDL,
            )


if __name__ == "__main__":
    unittest.main()
