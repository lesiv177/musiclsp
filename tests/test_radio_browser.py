import os, unittest
from core import radio_browser as R

ST = {"stationuuid": "u1", "name": "Jazz FM", "url_resolved": "https://s/jazz", "favicon": "https://s/f.png",
      "country": "Poland", "countrycode": "pl", "tags": "jazz,smooth", "lastcheckok": 1, "bitrate": 128}


class RadioTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("RADIO_ENABLED", None)

    def test_normalize_ok(self):
        n = R.normalize(ST)
        self.assertEqual((n["id"], n["live"], n["stream"]), ("rb:u1", True, "https://s/jazz"))

    def test_rejects_http_broken_and_bad(self):
        self.assertIsNone(R.normalize({**ST, "url_resolved": "http://s/x", "url": "http://s/x"}))
        self.assertIsNone(R.normalize({**ST, "lastcheckok": 0}))
        self.assertIsNone(R.normalize({**ST, "name": ""}))
        self.assertIsNone(R.normalize("x"))

    def test_search_fallback_server_and_dedupe(self):
        calls = []
        def fetch(url, params=None):
            calls.append(url)
            return None if "de1" in url else [ST, ST, {**ST, "stationuuid": "u2", "lastcheckok": 0}]
        r = R.search("jazz", fetch=fetch)
        self.assertEqual([x["id"] for x in r], ["rb:u1"])
        self.assertEqual(len(calls), 2)

    def test_disabled(self):
        os.environ["RADIO_ENABLED"] = "0"
        self.assertEqual(R.search("x", fetch=lambda *a, **k: [ST]), [])

    def test_exception_is_safe(self):
        def boom(*a, **k): raise RuntimeError("x")
        self.assertEqual(R.search("x", fetch=boom), [])

    def test_station_url(self):
        U = "abcdef12-0000-1111-2222-333333333333"
        f = lambda url, params=None: [ST] if ("byuuid/" + U) in url else None
        self.assertEqual(R.station_url("rb:" + U, f), "https://s/jazz")
        self.assertEqual(R.station_url("rb:../etc", f), "")
        self.assertEqual(R.station_url("", f), "")


if __name__ == "__main__":
    unittest.main()