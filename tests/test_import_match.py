import unittest

from core import import_match as IM


def c(i, title, artist, source="audius"):
    return {"id": i, "title": title, "artist": artist, "source": source}


class ImportMatchTests(unittest.TestCase):
    def test_exact_title_and_artist_is_matched(self):
        self.assertEqual(IM.classify("Song", "Echo", c("1", "Song", "Echo"))[0], IM.MATCHED)

    def test_feat_and_edition_do_not_block_match(self):
        self.assertEqual(IM.classify("Song", "Echo", c("1", "Song (feat. X)", "Echo"))[0], IM.MATCHED)

    def test_same_title_other_artist_is_uncertain_not_imported(self):
        self.assertEqual(IM.classify("Song", "Echo", c("1", "Song", "Someone Else"))[0], IM.UNCERTAIN)
        self.assertIsNone(IM.best_match("Song", "Echo", [c("1", "Song", "Someone Else")]))

    def test_partial_title_is_uncertain(self):
        self.assertEqual(IM.classify("Song", "Echo", c("1", "Song Of The Sea", "Echo"))[0], IM.UNCERTAIN)

    def test_unrelated_is_not_found(self):
        self.assertEqual(IM.classify("Song", "Echo", c("1", "Totally Different", "Echo"))[0], IM.NOT_FOUND)

    def test_artist_optional(self):
        self.assertEqual(IM.classify("Song", "", c("1", "Song", "Anyone"))[0], IM.MATCHED)

    def test_cyrillic_artist_matches_latin(self):
        self.assertEqual(IM.classify("Song", "Бумбокс", c("1", "Song", "Bumboks"))[0], IM.MATCHED)

    def test_best_match_prefers_artist_match_over_first_result(self):
        cands = [c("1", "Song", ""), c("2", "Song", "Echo")]
        self.assertEqual(IM.best_match("Song", "Echo", cands)["id"], "2")

    def test_report_and_summary(self):
        ok = IM.report_row(0, "Song", "Echo", [c("1", "Song", "Echo")])
        unsure = IM.report_row(1, "Song", "Echo", [c("2", "Song", "Other")])
        none = IM.report_row(2, "Zzz", "Echo", [c("3", "Song", "Echo")])
        self.assertEqual((ok["status"], ok["match"]["id"]), (IM.MATCHED, "1"))
        self.assertEqual((unsure["status"], unsure["match"], unsure["alternatives"][0]["id"]), (IM.UNCERTAIN, None, "2"))
        self.assertEqual(none["status"], IM.NOT_FOUND)
        self.assertEqual(IM.summarize([ok, unsure, none]), {"matched": 1, "uncertain": 1, "not_found": 1, "invalid": 0, "total": 3})


if __name__ == "__main__":
    unittest.main()


class ManualConfirmTests(unittest.TestCase):
    CANDS = [
        {"id": "a1", "title": "Midnight City Remix", "artist": "M83", "source": "x"},
        {"id": "b2", "title": "Totally Different", "artist": "Nobody", "source": "x"},
    ]

    def test_confirmed_uncertain_candidate_is_picked(self):
        from core import import_match as IM
        self.assertEqual(IM.best_match("Midnight City", "M83", self.CANDS), None)
        self.assertEqual(IM.pick_confirmed("Midnight City", "M83", self.CANDS, "a1")["id"], "a1")

    def test_client_cannot_inject_unknown_or_unrelated_id(self):
        from core import import_match as IM
        self.assertIsNone(IM.pick_confirmed("Midnight City", "M83", self.CANDS, "zzz"))
        self.assertIsNone(IM.pick_confirmed("Midnight City", "M83", self.CANDS, "b2"))
        self.assertIsNone(IM.pick_confirmed("Midnight City", "M83", self.CANDS, ""))

    def test_parse_accept_ignores_garbage(self):
        from core import import_match as IM
        self.assertEqual(IM.parse_accept({"1": "a1", "x": "b", "-2": "c", "3": ["l"], "4": ""}), {1: "a1"})
        self.assertEqual(IM.parse_accept(None), {})
        self.assertEqual(IM.parse_accept([1, 2]), {})
