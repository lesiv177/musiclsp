import unittest
from unittest.mock import patch

from core import providers as P


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload or {}, headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        P._host_cooldown.clear()

    def tearDown(self):
        P._host_cooldown.clear()

    def test_429_starts_cooldown_and_next_calls_do_not_hit_the_host(self):
        session = FakeSession(FakeResponse(429, headers={"Retry-After": "40"}), FakeResponse(200, {"data": []}))
        with patch.object(P, "_http_session", return_value=session):
            self.assertIsNone(P._get("https://node.test/v1/tracks/search", {"query": "a"}))
            self.assertIsNone(P._get("https://node.test/v1/tracks/trending"))
        self.assertEqual(len(session.calls), 1)  # другий виклик пропущено

    def test_other_hosts_are_not_affected(self):
        session = FakeSession(FakeResponse(429), FakeResponse(200, {"ok": 1}))
        with patch.object(P, "_http_session", return_value=session):
            P._get("https://a.test/x")
            self.assertEqual(P._get("https://b.test/x"), {"ok": 1})

    def test_cooldown_expires(self):
        session = FakeSession(FakeResponse(429), FakeResponse(200, {"ok": 1}))
        with patch.object(P, "_http_session", return_value=session):
            P._get("https://a.test/x")
            P._host_cooldown["a.test"] = P.time.monotonic() - 1
            self.assertEqual(P._get("https://a.test/x"), {"ok": 1})

    def test_retry_after_is_clamped_and_garbage_uses_default(self):
        self.assertEqual(P._retry_after_seconds(FakeResponse(429, headers={"Retry-After": "99999"})), P.RATE_LIMIT_MAX_COOLDOWN)
        self.assertEqual(P._retry_after_seconds(FakeResponse(429, headers={"Retry-After": "0"})), 5.0)
        self.assertEqual(P._retry_after_seconds(FakeResponse(429, headers={"Retry-After": "soon"})), P.RATE_LIMIT_DEFAULT_COOLDOWN)
        self.assertEqual(P._retry_after_seconds(FakeResponse(429)), P.RATE_LIMIT_DEFAULT_COOLDOWN)

    def test_400_is_logged_without_parameter_values_and_does_not_cool_down(self):
        session = FakeSession(FakeResponse(400))
        with patch.object(P, "_http_session", return_value=session), self.assertLogs(P.logger, "WARNING") as cm:
            self.assertIsNone(P._get("https://a.test/v1/x", {"query": "секретний запит", "limit": 5}))
        text = "\n".join(cm.output)
        self.assertIn("limit,query", text)
        self.assertNotIn("секретний", text)
        self.assertEqual(P._cooldown_left("a.test"), 0)


class AudiusInputGuardTests(unittest.TestCase):
    def test_empty_query_does_not_call_audius(self):
        with patch.object(P, "audius_host", side_effect=AssertionError("не має викликатись")):
            self.assertEqual(P.audius_search_tracks("   "), [])
            self.assertEqual(P.audius_search_albums(""), [])
            self.assertEqual(P.audius_search_tracks(None), [])

    def test_query_and_limit_are_bounded(self):
        seen = {}
        def fake_get(url, params=None, timeout=None):
            seen.update(params or {})
            return {"data": []}
        with patch.object(P, "audius_host", return_value="https://node.test"), patch.object(P, "_get", fake_get):
            P.audius_search_tracks("x" * 500, 9999)
        self.assertEqual(len(seen["query"]), P.AUDIUS_MAX_QUERY)
        self.assertEqual(seen["limit"], 100)


if __name__ == "__main__":
    unittest.main()
