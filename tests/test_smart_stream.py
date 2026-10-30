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
