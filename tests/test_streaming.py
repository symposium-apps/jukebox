from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from jukebox import streaming


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
class StreamingTest(unittest.TestCase):
    def test_mp4_becomes_private_hls_vod(self):
        with tempfile.TemporaryDirectory(prefix="jukebox-streaming-") as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
                ],
                check=True,
            )
            streaming.configure(root / "state")
            track_id = "a" * 40
            first = streaming.prepare(track_id, source)
            self.assertIn(first["state"], {"preparing", "ready"})
            deadline = time.time() + 15
            result = first
            while time.time() < deadline:
                result = streaming.status(track_id, source)
                if result["ready"]:
                    break
                time.sleep(0.05)
            self.assertTrue(result["ready"], result)
            manifest, content_type = streaming.artifact(track_id, "stream.m3u8")
            self.assertEqual(content_type, "application/vnd.apple.mpegurl")
            segment = sorted(manifest.parent.glob("segment-*.ts"))[0]
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(segment)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertTrue(probe.startswith("h264,"), probe)
            text = streaming.manifest_with_ticket(manifest, "private-ticket", "generation-1").decode()
            self.assertIn("#EXT-X-ENDLIST", text)
            self.assertIn("segment-00000.ts?ticket=private-ticket&generation=generation-1", text)
            with self.assertRaises(KeyError):
                streaming.artifact(track_id, "../source.mp4")


if __name__ == "__main__":
    unittest.main()
