import unittest
from unittest.mock import patch

from core import musicbrainz as MB


class MusicBrainzMetadataTests(unittest.TestCase):
    def setUp(self):
        MB._cache.clear()

    def test_artist_search_normalizes_artist_and_limits(self):
        payload = {"artists": [
            {"id": "artist-id", "name": "OG Buda", "country": "RU",
             "life-span": {"begin": "1994"}},
            {"id": "", "name": "Invalid"},
        ]}
        with patch.object(MB, "_get_json", return_value=payload) as get:
            result = MB.search_artists("OG Buda", 100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "OG Buda")
        self.assertEqual(result[0]["id"], "mb:artist:artist-id")
        self.assertEqual(get.call_args.args[1]["limit"], 25)

    def test_empty_artist_query_does_not_call_api(self):
        with patch.object(MB, "_get_json") as get:
            self.assertEqual(MB.search_artists("  "), [])
            get.assert_not_called()

    def test_release_groups_include_dates_and_metadata_only_ids(self):
        payload = {"release-groups": [
            {"id": "release-id", "title": "Album", "primary-type": "Album",
             "first-release-date": "2026-05-10"},
            {"id": "", "title": "Invalid"},
        ]}
        with patch.object(MB, "_get_json", return_value=payload):
            result = MB.artist_release_groups("mb:artist:artist-id", 200)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["year"], "2026")
        self.assertEqual(result[0]["id"], "mb:release-group:release-id")

    def test_recording_search_formats_artist_credit(self):
        payload = {"recordings": [{
            "id": "recording-id", "title": "Track", "length": 182000,
            "artist-credit": [{"artist": {"name": "Artist One"}},
                              {"artist": {"name": "Artist Two"}}],
        }]}
        with patch.object(MB, "_get_json", return_value=payload):
            result = MB.search_recordings("Track")
        self.assertEqual(result[0]["artist"], "Artist One, Artist Two")
        self.assertEqual(result[0]["length_ms"], 182000)

    def test_empty_artist_mbid_does_not_call_api(self):
        with patch.object(MB, "_get_json") as get:
            self.assertEqual(MB.artist_release_groups(""), [])
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
