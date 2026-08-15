from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from jukebox import import_companion


class ImportCompanionTest(unittest.TestCase):
    def environment(self, url: str = "http://100.114.116.88:47321", token: str = "t" * 32):
        return mock.patch.dict(
            os.environ,
            {
                "JUKEBOX_IMPORT_COMPANION_URL": url,
                "JUKEBOX_IMPORT_COMPANION_TOKEN": token,
            },
            clear=False,
        )

    def test_configuration_requires_a_private_literal_address_and_token(self):
        with self.environment():
            self.assertTrue(import_companion.configured())
        with self.environment("https://public.example/download"):
            self.assertFalse(import_companion.configured())
            with self.assertRaisesRegex(import_companion.CompanionUnavailable, "address is invalid"):
                import_companion.inspect_source("https://www.youtube.com/watch?v=fixture")
        with self.environment(token=""):
            self.assertFalse(import_companion.configured())

    def test_download_extracts_only_the_fixed_bundle_files(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("media.mp4", b"\x00\x00\x00\x18ftypmp42fixture")
            archive.writestr("thumbnail.jpg", b"jpeg-fixture")
            archive.writestr("info.json", json.dumps({"id": "fixture", "title": "Fixture Video"}))
        responses = [
            {"id": "job-1"},
            {"id": "job-1", "status": "complete", "stage": "Ready", "progress": 100},
            None,
        ]

        def fake_result(_job_id: str, destination: Path, _progress):
            destination.write_bytes(payload.getvalue())
            return destination

        with tempfile.TemporaryDirectory(prefix="jukebox-companion-test-") as temporary, self.environment(), mock.patch.object(
            import_companion, "_request", side_effect=responses
        ), mock.patch.object(import_companion, "_download_result", side_effect=fake_result):
            destination = Path(temporary) / "result"
            media, metadata = import_companion.download_source(
                "https://www.youtube.com/watch?v=fixture",
                output_format="mp4",
                quality="720",
                artwork=True,
                destination=destination,
                progress=lambda *_: None,
                cancelled=lambda: False,
            )
            self.assertEqual(media.name, "media.mp4")
            self.assertEqual(media.read_bytes(), b"\x00\x00\x00\x18ftypmp42fixture")
            self.assertEqual(metadata["title"], "Fixture Video")
            self.assertTrue((destination / "thumbnail.jpg").is_file())
            self.assertFalse((Path(temporary) / "outside.txt").exists())

            unsafe = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("media.mp4", b"fixture")
                archive.writestr("info.json", "{}")
                archive.writestr("../outside.txt", b"must-not-extract")
            with self.assertRaisesRegex(import_companion.CompanionDownloadFailed, "unsafe"):
                import_companion._unpack_bundle(unsafe, destination, "mp4")

    def test_cancel_stops_before_result_download(self):
        calls: list[str] = []

        def fake_json(path: str, *, method: str = "GET", **_kwargs):
            calls.append(f"{method} {path}")
            if method == "POST":
                return {"id": "job-cancel"}
            return None

        with tempfile.TemporaryDirectory(prefix="jukebox-companion-cancel-") as temporary, self.environment(), mock.patch.object(
            import_companion, "_request", side_effect=fake_json
        ), mock.patch.object(import_companion, "_download_result") as result:
            with self.assertRaises(import_companion.CompanionCancelled):
                import_companion.download_source(
                    "https://www.youtube.com/watch?v=fixture",
                    output_format="mp3",
                    quality="192",
                    artwork=False,
                    destination=Path(temporary),
                    progress=lambda *_: None,
                    cancelled=lambda: True,
                )
            result.assert_not_called()
            self.assertIn("DELETE /v1/jobs/job-cancel", calls)


if __name__ == "__main__":
    unittest.main()
