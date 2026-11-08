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


    def test_recent_artist_releases_builds_date_scoped_query(self):
        payload = {"release-groups": [
            {"id": "new-id", "title": "New Single", "first-release-date": "2026-10-12",
             "primary-type": "Single"},
            {"id": "old-id", "title": "Old Single", "first-release-date": "2025-01-01"},
            {"id": "", "title": "Invalid", "first-release-date": "2026-11-01"},
        ]}
        with patch.object(MB, "_get_json", return_value=payload) as get:
            result = MB.recent_artist_releases("mb:artist:12345678-abcd-4abc-8def-1234567890ab", "2026-01-01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "New Single")
        self.assertIn("date:[2026-01-01 TO *]", get.call_args.args[1]["query"])

    def test_recent_artist_releases_rejects_invalid_inputs(self):
        with patch.object(MB, "_get_json") as get:
            self.assertEqual(MB.recent_artist_releases("not-a-valid-id", "2026-01-01"), [])
            self.assertEqual(MB.recent_artist_releases("12345678-abcd", "not-a-date"), [])
            get.assert_not_called()


    def test_resolve_artist_uses_exact_matching_and_returns_unique_candidate(self):
        candidate = {
            "mbid": "12345678-abcd-4abc-8def-1234567890ab",
            "name": "OG Buda", "aliases": ["OG Buda"],
        }
        with patch.object(MB, "search_artists", return_value=[candidate]) as search:
            result = MB.resolve_artist("og-buda")
        self.assertEqual(result["mbid"], candidate["mbid"])
        search.assert_called_once_with("og-buda", limit=25)

    def test_resolve_artist_returns_none_for_ambiguous_results(self):
        candidates = [
            {"mbid": "12345678-abcd-4abc-8def-1234567890ab", "name": "Artist"},
            {"mbid": "87654321-abcd-4abc-8def-1234567890ab", "name": "Artist"},
        ]
        with patch.object(MB, "search_artists", return_value=candidates):
            self.assertIsNone(MB.resolve_artist("Artist"))


    def test_get_json_caches_successful_response(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"artists": []}
        response.raise_for_status.return_value = None
        with patch.object(MB, "_throttle"), patch.object(MB.requests, "get", return_value=response) as get:
            first = MB._get_json("artist", {"query": "test"})
            second = MB._get_json("artist", {"query": "test"})
        self.assertEqual(first, second)
        get.assert_called_once()

    def test_get_json_does_not_cache_http_failures(self):
        response = unittest.mock.Mock()
        response.raise_for_status.side_effect = RuntimeError("upstream failure")
        with patch.object(MB, "_throttle"), patch.object(MB.requests, "get", return_value=response) as get:
            with self.assertRaises(RuntimeError):
                MB._get_json("artist", {"query": "failure"})
            with self.assertRaises(RuntimeError):
                MB._get_json("artist", {"query": "failure"})
        self.assertEqual(get.call_count, 2)

    def test_get_json_rejects_non_object_json(self):
        response = unittest.mock.Mock()
        response.json.return_value = ["unexpected"]
        response.raise_for_status.return_value = None
        with patch.object(MB, "_throttle"), patch.object(MB.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                MB._get_json("artist", {"query": "bad-shape"})

if __name__ == "__main__":
    unittest.main()
