import unittest
from pathlib import Path


class MobileAppContractTests(unittest.TestCase):
    def test_discovery_and_create_are_available_without_extra_navigation_tabs(self):
        html = Path("webapp/index.html").read_text(encoding="utf-8")
        self.assertIn('id="scExplore"', html)
        self.assertIn("async function loadExplore()", html)
        self.assertIn('onclick="openCreateMenu()"', html)
        self.assertIn('grid-template-columns:repeat(4,minmax(0,1fr))', html)
        self.assertNotIn('data-go="Explore"', html)

    def test_playback_always_uses_server_stream_resolver(self):
        html = Path("webapp/index.html").read_text(encoding="utf-8")
        start = html.index("function streamUrl(t){")
        end = html.index("\n}\n", start) + 3
        block = html[start:end]
        self.assertIn('/api/stream?id=', block)
        self.assertNotIn('return t.stream', block)

    def test_genre_endpoint_uses_supported_playable_sources(self):
        api = Path("services/bot/api.py").read_text(encoding="utf-8")
        start = api.index("async def h_home")
        end = api.index("\n\n@require_auth", start)
        block = api[start:end]
        self.assertNotIn("P.soundcloud_search", block)
        self.assertIn("P.jamendo_tag_tracks", block)
        self.assertIn("P.audius_trending", block)


if __name__ == "__main__":
    unittest.main()
