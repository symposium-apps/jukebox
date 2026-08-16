import unittest

from companion.youtube_urls import canonicalize_source_url as companion_canonicalize
from jukebox.youtube_urls import canonicalize_source_url


class YouTubeUrlTest(unittest.TestCase):
    def test_supported_share_and_embed_shapes_are_canonicalized(self):
        video = "YJzxYFyZvFM"
        playlist = "PL1234567890abcdef"
        clip = "Ugkx1234567890abcdef"
        cases = {
            f"https://www.youtube.com/watch?v={video}&si=tracking&feature=youtu.be": f"https://www.youtube.com/watch?v={video}",
            f"https://youtube.com/watch?app=desktop&v={video}&pp=tracking": f"https://www.youtube.com/watch?v={video}",
            f"https://m.youtube.com/watch?si=tracking&v={video}&feature=youtu.be": f"https://www.youtube.com/watch?v={video}",
            f"https://music.youtube.com/watch?v={video}": f"https://www.youtube.com/watch?v={video}",
            f"https://youtu.be/{video}?si=tracking&t=42": f"https://www.youtube.com/watch?v={video}",
            f"https://www.youtube.com/shorts/{video}?si=tracking": f"https://www.youtube.com/watch?v={video}",
            f"https://www.youtube.com/live/{video}?si=tracking": f"https://www.youtube.com/watch?v={video}",
            f"https://www.youtube.com/embed/{video}?rel=0": f"https://www.youtube.com/watch?v={video}",
            f"https://www.youtube-nocookie.com/embed/{video}?rel=0": f"https://www.youtube.com/watch?v={video}",
            f"https://youtube.com/v/{video}": f"https://www.youtube.com/watch?v={video}",
            f"https://music.youtube.com/watch?v={video}&list={playlist}&si=tracking": f"https://www.youtube.com/watch?v={video}&list={playlist}",
            f"https://www.youtube.com/playlist?list={playlist}&si=tracking": f"https://www.youtube.com/playlist?list={playlist}",
            f"https://music.youtube.com/playlist?list={playlist}": f"https://www.youtube.com/playlist?list={playlist}",
            f"https://www.youtube.com/podcast?list={playlist}": f"https://www.youtube.com/playlist?list={playlist}",
            f"https://www.youtube.com/watch?list={playlist}": f"https://www.youtube.com/playlist?list={playlist}",
            f"https://www.youtube.com/clip/{clip}?si=tracking": f"https://www.youtube.com/clip/{clip}",
            f"https://www.youtube.com/attribution_link?u=%2Fwatch%3Fv%3D{video}%26feature%3Dshare": f"https://www.youtube.com/watch?v={video}",
            f"https://www.youtube.com/watch?v={video}&list=RD{video}&start_radio=1": f"https://www.youtube.com/watch?v={video}",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(canonicalize_source_url(source), expected)
                self.assertEqual(companion_canonicalize(source), expected)

    def test_non_media_and_unsafe_shapes_are_rejected(self):
        video = "YJzxYFyZvFM"
        rejected = (
            "",
            f"http://www.youtube.com/watch?v={video}",
            f"https://youtube.com.evil.example/watch?v={video}",
            f"https://user@youtube.com/watch?v={video}",
            f"https://www.youtube.com:444/watch?v={video}",
            "https://www.youtube.com/",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/shorts/",
            "https://www.youtube.com/live/",
            "https://www.youtube.com/embed/not!valid",
            f"https://www.youtube-nocookie.com/watch?v={video}",
            "https://www.youtube.com/@channel",
            "https://www.youtube.com/channel/UC1234567890",
            "https://www.youtube.com/results?search_query=test",
            f"https://www.youtube.com/redirect?q=https://example.com/watch?v={video}",
            "https://www.youtube.com/attribution_link?u=https://example.com/",
        )
        for source in rejected:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    canonicalize_source_url(source)
                with self.assertRaises(ValueError):
                    companion_canonicalize(source)


if __name__ == "__main__":
    unittest.main()
