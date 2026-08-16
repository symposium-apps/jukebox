import json
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class LogicalMediaTest(unittest.TestCase):
    def test_duplicate_files_become_one_item_with_one_choice_per_format(self):
        module = Path(__file__).parents[1] / "jukebox" / "static" / "logical-media.js"
        script = r"""
const logical = require(process.argv[1]);
const tracks = [
  {id: 'old-audio', source: 'youtube:Bpaf6Dm9iRc', name: "Tate McRae - It's ok I'm ok (Official Video)", artist: '', album: 'Singles', extension: '.mp3', size: 5563729},
  {id: 'paired-audio', source: 'youtube:Bpaf6Dm9iRc', name: "Tate McRae - It's ok I'm ok (Official Video)", artist: 'Tate McRae', album: 'Singles', extension: '.mp3', size: 3960925},
  {id: 'video', source: 'youtube:Bpaf6Dm9iRc', name: "Tate McRae - It's ok I'm ok (Official Video)", artist: 'Tate McRae', album: 'Singles', extension: '.mp4', size: 37480009}
];
const grouped = logical.groupTracks(tracks, [tracks[2]], track => track.source);
process.stdout.write(JSON.stringify(grouped));
"""
        output = subprocess.run(
            [shutil.which("node") or "node", "-e", script, str(module)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        grouped = json.loads(output)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["id"], "old-audio")
        self.assertEqual(grouped[0]["artist"], "Tate McRae")
        self.assertEqual(grouped[0]["variant_ids"], ["old-audio", "paired-audio", "video"])
        self.assertEqual(
            grouped[0]["format_variants"],
            [
                {"format": "mp3", "track_id": "old-audio", "size": 5563729},
                {"format": "mp4", "track_id": "video", "size": 37480009},
            ],
        )


if __name__ == "__main__":
    unittest.main()
