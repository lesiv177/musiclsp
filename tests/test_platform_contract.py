import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
PLATFORM_JS = (ROOT / "webapp" / "platform.js").read_text(encoding="utf-8")
V3_JS = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")
API = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")


class PlatformContractTests(unittest.TestCase):
    def test_updated_platform_assets_have_cache_busting_versions(self):
        self.assertIn('./v3.js?v=9', HTML)
        self.assertIn('./platform.js?v=3', HTML)
        self.assertIn('./platform.css?v=3', HTML)

    def test_discovery_covers_multiple_genres_and_balances_results(self):
        discover = API.split('async def h_discover', 1)[1].split('@require_auth', 1)[0]
        for genre in ('"pop"', '"rock"', '"electronic"', '"hip hop"', '"jazz"'):
            self.assertIn(genre, discover)
        self.assertIn('Round-robin genre results', discover)
        self.assertIn('merge(artists_raw, "id")', discover)
        self.assertIn('merge(albums_raw, "id")', discover)

    def test_chart_catalogue_has_four_provider_sections(self):
        charts = API.split('async def h_charts', 1)[1].split('@require_auth', 1)[0]
        for chart_id in ('audius-trending', 'electronic-trending', 'popular-pop', 'popular-rock'):
            self.assertIn(chart_id, charts)
        self.assertIn('P.audius_trending', charts)
        self.assertIn('P.jamendo_tag_tracks', charts)

    def test_chart_playback_uses_the_existing_queue_bridge(self):
        self.assertIn('window.MLSPv3?.playTracks', PLATFORM_JS)
        self.assertIn('playTracks:(tracks,index=0)=>', V3_JS)
        self.assertIn('setQueue(tracks,', V3_JS)

    def test_metadata_discography_links_to_canonical_release_record(self):
        self.assertIn('release.source_url', V3_JS)
        self.assertIn('target="_blank" rel="noopener noreferrer"', V3_JS)


if __name__ == '__main__':
    unittest.main()
