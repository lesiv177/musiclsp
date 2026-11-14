import unittest
from core import supersource as SS

CC = "https://creativecommons.org/licenses/by/4.0/"
NC = "https://creativecommons.org/licenses/by-nc/4.0/"


def tr(source, **kw):
    base = {"id": f"{source}:1", "title": "T", "artist": "A", "source": source, "source_label": source,
            "stream": "https://s/x.mp3", "license": CC, "license_short": "CC BY"}
    base.update(kw)
    return base


class SuperSourceTest(unittest.TestCase):
    def test_archive_requires_commercial_license(self):
        self.assertEqual(SS.assess(tr("archive"))[0], "green")
        self.assertEqual(SS.assess(tr("archive", license=NC))[0], "red")
        self.assertEqual(SS.assess(tr("archive", license=""))[0], "red")

    def test_non_https_dropped_empty_stream_ok(self):
        self.assertEqual(SS.assess(tr("openverse", stream="http://s/x"))[0], "red")
        self.assertEqual(SS.assess(tr("audius", stream=""))[0], "amber")  # резолвиться через /api/stream
        self.assertEqual(SS.assess(tr("audius", stream="http://x"))[0], "red")

    def test_audius_amber_jamendo_license_gate(self):
        self.assertEqual(SS.assess(tr("audius", license=""))[0], "amber")
        self.assertEqual(SS.assess(tr("jamendo"))[0], "green")
        self.assertEqual(SS.assess(tr("jamendo", license=NC))[0], "red")

    def test_youtube_embed_only_amber_and_unknown_source_red(self):
        self.assertEqual(SS.assess(tr("youtube", stream=""))[0], "amber")
        self.assertEqual(SS.assess(tr("napster"))[0], "red")

    def test_radio(self):
        self.assertEqual(SS.assess(tr("radio"))[0], "green")
        self.assertEqual(SS.assess(tr("radio", stream="http://x"))[0], "red")

    def test_search_filters_and_credits(self):
        r = SS.search("x", search_fn=lambda q, n: [tr("archive"), tr("archive", license=NC, id="a:2"), "junk"],
                      radio_fn=lambda q, n: [tr("radio", id="rb:1")])
        self.assertEqual(len(r["tracks"]), 1)
        self.assertEqual(r["dropped"], 1)
        self.assertIn("CC BY", r["tracks"][0]["credit"])
        self.assertEqual(r["tracks"][0]["safety"]["level"], "green")
        self.assertEqual(len(r["radio"]), 1)

    def test_errors_do_not_break(self):
        def boom(*a): raise RuntimeError("x")
        r = SS.search("x", search_fn=boom, radio_fn=boom)
        self.assertEqual((r["tracks"], r["radio"]), ([], []))

    def test_empty_query_and_kind(self):
        self.assertEqual(SS.search("  ")["tracks"], [])
        r = SS.search("x", kind="radio", search_fn=lambda *a: 1 / 0, radio_fn=lambda q, n: [])
        self.assertEqual(r["tracks"], [])

    def test_report_has_no_secrets(self):
        rep = SS.sources_report()
        keys = {r["key"] for r in rep["sources"]}
        self.assertTrue({"jamendo", "audius", "archive", "radio"} <= keys)
        self.assertNotIn("CLIENT", str(rep))


if __name__ == "__main__":
    unittest.main()