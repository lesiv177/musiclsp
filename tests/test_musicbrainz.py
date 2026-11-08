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

    def test_artist_by_id_validates_id_and_returns_metadata(self):
        artist_id = "12345678-abcd-4abc-8def-1234567890ab"
        payload = {"id": artist_id, "name": "Lil Peep"}
        with patch.object(MB, "_get_json", return_value=payload) as get:
            result = MB.artist_by_id("mb:artist:" + artist_id)
        self.assertEqual(result["name"], "Lil Peep")
        self.assertEqual(get.call_args.args[0], f"artist/{artist_id}")
        with patch.object(MB, "_get_json") as get:
            self.assertIsNone(MB.artist_by_id("not-a-mbid"))
            get.assert_not_called()

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

    def test_search_release_groups_returns_metadata_only_album_entries(self):
        payload = {"release-groups": [{
            "id": "12345678-abcd-4abc-8def-1234567890ab",
            "title": "Album One",
            "primary-type": "Album",
            "first-release-date": "2026-04-01",
            "artist-credit": [{"artist": {"name": "Artist One"}}],
        }]}
        with patch.object(MB, "_get_json", return_value=payload) as get:
            result = MB.search_release_groups("Album One", 100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "mb:release-group:12345678-abcd-4abc-8def-1234567890ab")
        self.assertEqual(result[0]["artist"], "Artist One")
        self.assertTrue(result[0]["metadata_only"])
        self.assertEqual(get.call_args.args[1]["limit"], 25)

    def test_release_group_details_returns_non_playable_track_metadata(self):
        release_group_id = "12345678-abcd-4abc-8def-1234567890ab"
        release_id = "87654321-abcd-4abc-8def-1234567890ab"
        group = {
            "id": release_group_id, "title": "Album One",
            "first-release-date": "2026-04-01", "primary-type": "Album",
            "artist-credit": [{"artist": {"name": "Artist One"}}],
            "releases": [{"id": release_id, "date": "2026-04-01"}],
        }
        release = {"media": [{"tracks": [{
            "number": "1", "title": "Track One",
            "recording": {"id": "11111111-abcd-4abc-8def-1234567890ab", "length": 183000},
        }]}]}
        with (
            patch.object(MB, "_get_json", side_effect=[group, release]) as get,
            patch.object(MB, "release_group_cover_url", return_value="https://coverartarchive.org/test-front-500.jpg"),
        ):
            result = MB.release_group_by_id("mb:release-group:" + release_group_id)
        self.assertEqual(result["title"], "Album One")
        self.assertEqual(result["tracks"][0]["title"], "Track One")
        self.assertEqual(result["tracks"][0]["duration"], 183)
        self.assertTrue(result["tracks"][0]["metadata_only"])
        self.assertEqual(get.call_count, 2)

    def test_cover_art_archive_prefers_front_thumbnail(self):
        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {"images": [
            {"front": False, "image": "https://coverartarchive.org/back.jpg"},
            {"front": True, "image": "https://coverartarchive.org/front.jpg",
             "thumbnails": {"500": "https://coverartarchive.org/front-500.jpg"}},
        ]}
        with patch.object(MB.requests, "get", return_value=response) as get:
            url = MB.release_group_cover_url("12345678-abcd-4abc-8def-1234567890ab")
        self.assertEqual(url, "https://coverartarchive.org/front-500.jpg")
        self.assertEqual(get.call_args.kwargs["timeout"], 6)

    def test_cover_art_archive_missing_art_is_non_fatal(self):
        response = unittest.mock.Mock()
        response.status_code = 404
        with patch.object(MB.requests, "get", return_value=response):
            self.assertEqual(MB.release_group_cover_url("12345678-abcd-4abc-8def-1234567890ab"), "")

    def test_cover_art_archive_rejects_invalid_mbid_without_network(self):
        with patch.object(MB.requests, "get") as get:
            self.assertEqual(MB.release_group_cover_url("bad-id"), "")
            get.assert_not_called()

    def test_release_group_details_rejects_invalid_mbid(self):
        with patch.object(MB, "_get_json") as get:
            self.assertIsNone(MB.release_group_by_id("not-an-mbid"))
            get.assert_not_called()

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
