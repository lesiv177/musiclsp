import os, unittest
from core import search_engine as E

CC = "https://creativecommons.org/licenses/by/4.0/"
NC = "https://creativecommons.org/licenses/by-nc/4.0/"


def t(id_, title, artist, source="jamendo", **kw):
    d = {"id": id_, "title": title, "artist": artist, "source": source, "source_label": source,
         "stream": "", "license": CC, "license_short": "CC BY", "cover": "https://c/x.jpg", "duration": 100}
    d.update(kw)
    return d


class EngineTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SEARCH_ENGINE", None)

    def test_analyze(self):
        qi = E.analyze("Imagine Dragons - Believer")
        self.assertEqual((qi["artist"], qi["title"]), ("Imagine Dragons", "Believer"))
        self.assertIn("Imagine Dragons Believer", qi["variants"])
        qi = E.analyze("Believer by Imagine Dragons")
        self.assertEqual((qi["artist"], qi["title"]), ("Imagine Dragons", "Believer"))
        self.assertEqual(E.analyze("  ")["variants"], [])

    def test_fusion_dedupes_and_ranks_exact_first(self):
        def fetch(q, n):
            return [t("a:1", "Other Song", "Zed"), t("jam:1", "Believer", "Imagine Dragons"),
                    t("aud:9", "believer", "imagine dragons", source="audius")]
        tracks, diag = E.search_tracks("Believer", 10, fetch=fetch)
        self.assertEqual(tracks[0]["title"].lower(), "believer")
        self.assertEqual(sum(1 for x in tracks if x["title"].lower() == "believer"), 1)
        self.assertEqual(tracks[0]["safety"]["level"], "green")

    def test_unsafe_dropped(self):
        fetch = lambda q, n: [t("arc:1", "Song", "A", source="archive", license=NC), t("arc:2", "Song Two", "A", source="archive")]
        tracks, diag = E.search_tracks("Song", 10, fetch=fetch)
        self.assertEqual([x["id"] for x in tracks], ["arc:2"])
        self.assertEqual(diag["dropped"], 1)

    def test_variant_failure_isolated(self):
        def fetch(q, n):
            if q == "Zed - Song":
                raise RuntimeError("x")
            return [t("jam:1", "Song", "Zed")]
        tracks, diag = E.search_tracks("Zed - Song", 10, fetch=fetch)
        self.assertEqual(len(tracks), 1)
        self.assertGreaterEqual(diag["failed_variants"], 1)

    def test_discovery_when_empty_and_none_when_found(self):
        d = {"artist": {"name": "X"}, "albums": []}
        tr, ex = E.search("Nobody", fetch=lambda q, n: [], discovery_fn=lambda q: d)
        self.assertEqual((tr, ex["discovery"]), ([], d))
        tr, ex = E.search("Song", fetch=lambda q, n: [t("jam:1", "Song", "A")], discovery_fn=lambda q: d)
        self.assertNotIn("discovery", ex)

    def test_discovery_metadata_only_flag(self):
        fake = lambda url, params=None: ({"data": [{"id": 1, "name": "Muse", "picture_xl": "https://x/p.jpg"}]} if url.endswith("/search/artist")
                  else {"data": [{"title": "Drones", "release_date": "2015-06-05"}]} if "/albums" in url else None)
        r = E.discovery("Muse", fetch=fake)
        self.assertTrue(r["albums"][0]["metadata_only"])
        self.assertEqual(r["albums"][0]["search_query"], "Muse Drones")

    def test_did_you_mean(self):
        self.assertEqual(E.did_you_mean("imagin dragons", [], ["Imagine Dragons"]), "Imagine Dragons")
        self.assertEqual(E.did_you_mean("imagine dragons", [], ["Imagine Dragons"]), "")

    def test_flag(self):
        self.assertTrue(E.engine_enabled())
        os.environ["SEARCH_ENGINE"] = "legacy"
        self.assertFalse(E.engine_enabled())


if __name__ == "__main__":
    unittest.main()


class ApiWiringTest(unittest.TestCase):
    def test_engine_tracks_cached_and_filtered(self):
        from unittest import mock
        from services.bot import api
        calls = []
        def fake_search(q, l, qu, s, so):
            calls.append(q)
            return [{"id": "x"}], {"engine": "v2"}
        with mock.patch.object(api.SE, "search", fake_search), \
             mock.patch.object(api.SM, "filter_available", lambda t, quality=None: t):
            a = api._engine_tracks("wiring-test-q", 5, "mp32", None, "relevance")
            b = api._engine_tracks("wiring-test-q", 5, "mp32", None, "relevance")
        self.assertEqual(a, b)
        self.assertEqual(len(calls), 1)