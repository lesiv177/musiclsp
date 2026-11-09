import unittest

from core import charts as CH
from core import search_rank as SR


def alb(i, title, artist, source, year="2020", **kw):
    return {"id": i, "title": title, "artist": artist, "source": source, "year": year, **kw}


class RelevanceTests(unittest.TestCase):
    def test_exact_prefix_substring_order(self):
        self.assertGreater(SR.relevance("echo", "Echo"), SR.relevance("echo", "Echoes"))
        self.assertGreater(SR.relevance("echo", "Echoes"), SR.relevance("echo", "The Big Echo Show"))
        self.assertEqual(SR.relevance("zzz", "Echo"), 0.0)

    def test_cyrillic_query_finds_latin_name(self):
        self.assertEqual(SR.relevance("бумбокс", "Bumboks"), 1.0)

    def test_rank_is_stable_and_best_first(self):
        items = [{"name": "Big Echo Show", "id": 1}, {"name": "Echo", "id": 2}, {"name": "Echoes", "id": 3}]
        self.assertEqual([i["id"] for i in SR.rank(items, "echo")], [2, 3, 1])
        self.assertEqual([i["id"] for i in SR.rank(items, "echo", "newest")], [1, 2, 3])


class DedupeTests(unittest.TestCase):
    def test_same_release_from_two_sources_is_merged_keeping_playable(self):
        mb = alb("mb:release-group:1", "Album", "Echo", "musicbrainz", metadata_only=True, cover="mb.jpg")
        aud = alb("aud:1", "Album (Deluxe Edition)", "Echo", "audius")
        out = SR.dedupe_albums([mb, aud])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "aud:1")
        self.assertEqual(out[0]["also_in"], ["musicbrainz"])
        self.assertEqual(out[0]["cover"], "mb.jpg")

    def test_different_years_are_not_merged(self):
        out = SR.dedupe_albums([alb("a", "Album", "Echo", "audius", "2010"), alb("b", "Album", "Echo", "jamendo", "2020")])
        self.assertEqual(len(out), 2)

    def test_missing_year_is_not_merged_without_proof(self):
        out = SR.dedupe_albums([alb("a", "Album", "Echo", "audius", ""), alb("b", "Album", "Echo", "jamendo", "")])
        self.assertEqual(len(out), 2)

    def test_same_name_artists_stay_separate(self):
        out = SR.dedupe_artists([{"id": "1", "name": "Echo", "source": "audius"}, {"id": "2", "name": "Echo", "source": "jamendo"}])
        self.assertEqual(len(out), 2)

    def test_artists_with_shared_website_merge(self):
        out = SR.dedupe_artists([
            {"id": "1", "name": "Echo", "source": "audius", "website": "https://echo.band"},
            {"id": "2", "name": "Echo", "source": "soundcloud", "website": "https://www.echo.band/"}])
        self.assertEqual(len(out), 1)


class FilterTests(unittest.TestCase):
    ITEMS = [alb("a", "A", "X", "audius", type="Album"),
             alb("b", "B", "X", "musicbrainz", type="EP", metadata_only=True)]

    def test_filters(self):
        self.assertEqual([i["id"] for i in SR.apply_filters(self.ITEMS, source="audius")], ["a"])
        self.assertEqual([i["id"] for i in SR.apply_filters(self.ITEMS, release_type="ep")], ["b"])
        self.assertEqual([i["id"] for i in SR.apply_filters(self.ITEMS, availability="playable")], ["a"])
        self.assertEqual([i["id"] for i in SR.apply_filters(self.ITEMS, availability="metadata_only")], ["b"])
        self.assertEqual(len(SR.apply_filters(self.ITEMS)), 2)

    def test_postprocess_all_keeps_tracks_untouched(self):
        data = {"tracks": [{"id": "t"}], "albums": self.ITEMS, "artists": []}
        out = SR.postprocess("all", data, "x", "relevance", "", "", "playable")
        self.assertEqual(out["tracks"], [{"id": "t"}])
        self.assertEqual([a["id"] for a in out["albums"]], ["a"])


class ChartTests(unittest.TestCase):
    def test_methodology_never_claims_official_ranking(self):
        for spec in CH.SPECS:
            chart = CH.build_chart(spec, [{"id": "x"}])
            self.assertEqual(chart["kind"], "provider_trending")
            self.assertIn("не офіційний", chart["methodology"])

    def test_period_only_for_jamendo_and_validated(self):
        jam = CH.build_chart(CH.SPECS[2], [{"id": "x"}], "week")
        aud = CH.build_chart(CH.SPECS[0], [{"id": "x"}], "week")
        self.assertEqual(jam["period"], "week")
        self.assertNotIn("period", aud)
        self.assertEqual(CH.normalize_period("year; drop"), "month")

    def test_empty_charts_hidden(self):
        charts = [CH.build_chart(CH.SPECS[0], []), CH.build_chart(CH.SPECS[2], [{"id": "x"}])]
        self.assertEqual([c["id"] for c in CH.visible(charts)], ["popular-pop"])


if __name__ == "__main__":
    unittest.main()