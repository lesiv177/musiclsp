import unittest
from unittest.mock import patch

from core import providers as P


def run_jobs_inline(jobs, timeout=4):
    """Run mocked provider jobs in their declared order, without network I/O."""
    results = []
    for job in jobs:
        results.extend(job() or [])
    return results


class SoundCloudPriorityTests(unittest.TestCase):
    def _empty_provider_patches(self):
        return [
            patch.object(P, "audius_search_tracks", return_value=[]),
            patch.object(P, "archive_search", return_value=[]),
            patch.object(P, "ccmixter_search", return_value=[]),
            patch.object(P, "openverse_search", return_value=[]),
            patch.object(P, "wikimedia_search", return_value=[]),
        ]

    def test_soundcloud_is_excluded_from_default_search(self):
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist"}
        patches = [
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search") as soundcloud_search,
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
            *self._empty_provider_patches(),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            results = P.search_tracks("Artist", limit=10)
        soundcloud_search.assert_not_called()
        self.assertEqual([track["id"] for track in results], ["jam:2"])

    def test_soundcloud_does_not_override_playable_default_sources(self):
        jamendo = {"id": "jam:2", "title": "Same Track", "artist": "Same Artist"}
        patches = [
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search") as soundcloud_search,
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
            *self._empty_provider_patches(),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            results = P.search_tracks("Same Track", limit=10)
        soundcloud_search.assert_not_called()
        self.assertEqual([track["id"] for track in results], ["jam:2"])

    def test_explicit_provider_filter_is_respected(self):
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist"}
        with (
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search") as soundcloud_search,
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
        ):
            results = P.search_tracks("Artist", limit=10, sources=["jamendo"])
        soundcloud_search.assert_not_called()
        self.assertEqual([track["id"] for track in results], ["jam:2"])

    def test_parallel_runner_preserves_source_order_and_ignores_failure(self):
        def broken():
            raise RuntimeError("provider unavailable")

        results = P._run_parallel([
            lambda: [{"id": "first"}],
            broken,
            lambda: [{"id": "third"}],
        ], timeout=2)
        self.assertEqual([item["id"] for item in results], ["first", "third"])

    def test_parallel_runner_uses_bounded_shared_pool(self):
        self.assertEqual(P._PROVIDER_EXECUTOR._max_workers, 12)

    def test_safe_log_url_never_includes_query_secrets(self):
        safe = P._safe_log_url("https://api.example.test/search?client_secret=do-not-log")
        self.assertEqual(safe, "api.example.test/search")
        self.assertNotIn("do-not-log", safe)

    def test_get_returns_none_for_provider_http_errors(self):
        class Response:
            status_code = 503
            def json(self):
                raise AssertionError("JSON must not be parsed for HTTP errors")

        with patch.object(P, "_http_session") as session:
            session.return_value.get.return_value = Response()
            self.assertIsNone(P._get("https://provider.test/search"))

    def test_get_returns_parsed_json_for_success(self):
        class Response:
            status_code = 200
            def json(self):
                return {"results": [{"id": 1}]}

        with patch.object(P, "_http_session") as session:
            session.return_value.get.return_value = Response()
            self.assertEqual(P._get("https://provider.test/search"), {"results": [{"id": 1}]})

    def test_duration_parser_handles_invalid_values(self):
        self.assertEqual(P.fmt_duration(125), "2:05")
        self.assertEqual(P.fmt_duration("not-a-duration"), "")

    def test_url_playable_uses_thread_local_session(self):
        class Response:
            status_code = 200
            def close(self):
                pass
        with patch.object(P, "_http_session") as session:
            session.return_value.head.return_value = Response()
            self.assertTrue(P._url_playable("https://audio.example.test/track.mp3"))
            session.return_value.head.assert_called_once()


if __name__ == "__main__":
    unittest.main()
