import unittest
from unittest.mock import patch
from core import smart


class StreamValidationTests(unittest.TestCase):
    def test_check_url_uses_provider_thread_local_session(self):
        class Response:
            status_code = 200
            def close(self):
                pass
        with patch.object(smart.P, "_http_session") as session:
            session.return_value.head.return_value = Response()
            self.assertTrue(smart.check_url("https://audio.example.test/unique-stream-check"))
            session.return_value.head.assert_called_once()

    def test_check_url_falls_back_to_range_get_when_head_is_blocked(self):
        class Response:
            def __init__(self, status): self.status_code=status
            def close(self): pass
        with patch.object(smart.P, "_http_session") as session:
            session.return_value.head.return_value = Response(405)
            session.return_value.get.return_value = Response(206)
            self.assertTrue(smart.check_url("https://audio.example.test/unique-range-check"))
            session.return_value.get.assert_called_once()

    def test_preflight_does_not_mark_track_dead_on_network_timeout(self):
        track_id = "aud:transient-timeout-regression"
        url = "https://audio.example.test/transient-timeout-regression"
        with (
            patch.object(smart.P, "resolve_stream", return_value=url),
            patch.object(smart.P, "_http_session") as session,
        ):
            session.return_value.head.side_effect = TimeoutError("temporary timeout")
            self.assertFalse(smart._preflight_track({"id": track_id}))
        self.assertFalse(smart.is_dead(track_id))

    def test_preflight_does_not_mark_track_dead_when_resolver_returns_no_url(self):
        track_id = "aud:temporary-resolver-failure"
        with patch.object(smart.P, "resolve_stream", return_value=""):
            self.assertFalse(smart._preflight_track({"id": track_id}))
        self.assertFalse(smart.is_dead(track_id))

    def test_check_url_treats_provider_rate_limit_and_server_error_as_transient(self):
        class Response:
            def __init__(self, status): self.status_code = status
            def close(self): pass
        for status in (408, 429, 500, 502, 503):
            url = f"https://audio.example.test/transient-status-{status}"
            with self.subTest(status=status), patch.object(smart.P, "_http_session") as session:
                session.return_value.head.return_value = Response(status)
                self.assertIsNone(smart.check_url(url))

    def test_transient_stream_check_does_not_mark_track_dead(self):
        track_id = "aud:transient-resolve-regression"
        url = "https://audio.example.test/transient-resolve-regression"
        with patch.object(smart.P, "resolve_stream", return_value=url), \
             patch.object(smart, "check_url", return_value=None):
            self.assertEqual(smart._resolve_checked(track_id, "mp32"), "")
        self.assertFalse(smart.is_dead(track_id))

    def test_resolve_stream_robust_does_not_mark_unknown_track_dead(self):
        track_id = "aud:unknown-resolve-regression"
        with patch.object(smart, "_resolve_checked", return_value=""):
            self.assertEqual(smart.resolve_stream_robust(track_id), ("", ""))
        self.assertFalse(smart.is_dead(track_id))

    def test_check_url_still_rejects_permanently_missing_track(self):
        class Response:
            status_code = 404
        url = "https://audio.example.test/permanent-404-regression"
        with patch.object(smart.P, "_http_session") as session:
            session.return_value.head.return_value = Response()
            self.assertFalse(smart.check_url(url))

    def test_unverified_soundcloud_stream_is_rejected(self):
        url = "https://cf-media.sndcdn.com/signed-stream-regression-test"
        with patch.object(smart.P, "resolve_stream", return_value=url), \
             patch.object(smart, "check_url", return_value=False):
            self.assertEqual(smart._resolve_checked("sc:regression-test-unique", "mp32"), "")


    def test_force_refresh_bypasses_cached_stream_url(self):
        track_id = "sc:force-refresh-regression-unique"
        quality = "mp32"
        fresh_url = "https://cf-media.sndcdn.com/fresh-force-refresh-regression"
        smart.cache_put(("stream", track_id, quality), "https://old.example.test/stale", 300)
        with patch.object(smart.P, "resolve_stream", return_value=fresh_url), \
             patch.object(smart, "check_url", return_value=True):
            result = smart.resolve_stream_robust(
                track_id, quality, "Regression title", "Regression artist", force=True
            )
        self.assertEqual(result, (fresh_url, track_id))


if __name__ == "__main__":
    unittest.main()