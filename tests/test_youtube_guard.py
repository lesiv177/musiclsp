import os, unittest
from unittest import mock
from core import providers as P, youtube_guard as YG


class Resp:
    def __init__(self, code=200, data=None):
        self.status_code = code
        self._d = data or {"items": [{"id": {"videoId": "abc"}, "snippet": {"title": "OG Buda - Miu Miu", "channelTitle": "OG Buda - Topic", "thumbnails": {}}}]}

    def json(self):
        return self._d


class Sess:
    def __init__(self, resp):
        self.resp, self.calls = resp, 0

    def get(self, *a, **k):
        self.calls += 1
        return self.resp


class GuardTest(unittest.TestCase):
    def setUp(self):
        YG.reset_for_tests()
        os.environ.pop("YOUTUBE_DAILY_BUDGET", None)

    def test_cache_hits_do_not_spend_quota(self):
        s = Sess(Resp())
        with mock.patch.object(P, "YOUTUBE_API_KEY", "k"), mock.patch.object(P, "_http_session", lambda: s):
            a = P.youtube_search("OG Buda Miu Miu")
            b = P.youtube_search("og buda  miu-miu")
        self.assertEqual((len(a), s.calls), (1, 1))
        self.assertEqual(a[0]["artist"], "OG Buda")
        self.assertEqual(YG.status()["used"], 100)
        self.assertEqual(len(b), 1)

    def test_budget_blocks_calls(self):
        os.environ["YOUTUBE_DAILY_BUDGET"] = "100"
        s = Sess(Resp())
        with mock.patch.object(P, "YOUTUBE_API_KEY", "k"), mock.patch.object(P, "_http_session", lambda: s):
            P.youtube_search("one")
            self.assertEqual(P.youtube_search("two"), [])
        self.assertEqual(s.calls, 1)

    def test_403_blocks_until_tomorrow(self):
        s = Sess(Resp(403))
        with mock.patch.object(P, "YOUTUBE_API_KEY", "k"), mock.patch.object(P, "_http_session", lambda: s):
            self.assertEqual(P.youtube_search("x1"), [])
            self.assertEqual(P.youtube_search("x2"), [])
        self.assertEqual(s.calls, 1)
        self.assertTrue(YG.status()["blocked"])

    def test_no_key_no_calls(self):
        s = Sess(Resp())
        with mock.patch.object(P, "YOUTUBE_API_KEY", ""), mock.patch.object(P, "_http_session", lambda: s):
            self.assertEqual(P.youtube_search("x"), [])
        self.assertEqual(s.calls, 0)

    def test_empty_results_cached_shorter(self):
        s = Sess(Resp(200, {"items": []}))
        with mock.patch.object(P, "YOUTUBE_API_KEY", "k"), mock.patch.object(P, "_http_session", lambda: s):
            P.youtube_search("nothing here")
            P.youtube_search("nothing here")
        self.assertEqual(s.calls, 1)


class FallbackTest(unittest.TestCase):
    def setUp(self):
        YG.reset_for_tests()

    def _run(self, found, mode="fallback"):
        yt = []
        track = {"id": "j:1", "title": "A", "artist": "B", "source": "jamendo"}
        def fake_grouped(jobs, timeout=6):
            return [[dict(track, id=f"j:{i}", title=f"T{i}") for i in range(found)]] if jobs else []
        with mock.patch.dict(os.environ, {"YOUTUBE_MODE": mode}), \
             mock.patch.object(P, "youtube_enabled", lambda: True), \
             mock.patch.object(P, "jamendo_enabled", lambda: True), \
             mock.patch.object(P, "soundcloud_enabled", lambda: False), \
             mock.patch.object(P, "_run_parallel_grouped", fake_grouped), \
             mock.patch.object(P, "youtube_search", lambda q, limit=8: yt.append(q) or [{"id": "yt:1", "title": "Yt", "artist": "Z", "source": "youtube"}]):
            out = P.search_tracks("query", limit=20)
        return out, yt

    def test_fallback_called_only_when_few_results(self):
        out, yt = self._run(1)
        self.assertEqual(len(yt), 1)
        self.assertTrue(any(t["source"] == "youtube" for t in out))

    def test_not_called_when_enough_results(self):
        out, yt = self._run(8)
        self.assertEqual(yt, [])


if __name__ == "__main__":
    unittest.main()