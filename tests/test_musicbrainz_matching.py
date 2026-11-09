import unittest

from core.musicbrainz_matching import (
    normalize_artist_name,
    rank_artist_candidates,
    resolve_exact_artist,
)


MBID_ONE = "12345678-abcd-4abc-8def-1234567890ab"
MBID_TWO = "87654321-abcd-4abc-8def-1234567890ab"


class MusicBrainzMatchingTests(unittest.TestCase):
    def test_normalization_ignores_case_and_punctuation(self):
        self.assertEqual(normalize_artist_name("  OG-Buda! "), "og buda")

    def test_unique_exact_name_match_is_resolved(self):
        candidates = [
            {"mbid": MBID_ONE, "name": "OG Buda"},
            {"mbid": MBID_TWO, "name": "Other Artist"},
        ]
        match = resolve_exact_artist("og-buda", candidates)
        self.assertEqual(match["mbid"], MBID_ONE)
        self.assertEqual(match["match_type"], "name")
        self.assertFalse(match["ambiguous"])

    def test_alias_match_is_supported(self):
        candidates = [{"mbid": MBID_ONE, "name": "Stage Name",
                       "aliases": [{"name": "Former Name"}]}]
        match = resolve_exact_artist("Former Name", candidates)
        self.assertEqual(match["match_type"], "alias")
        self.assertEqual(match["match_score"], 0.98)

    def test_duplicate_exact_names_are_not_auto_resolved(self):
        candidates = [
            {"mbid": MBID_ONE, "name": "Artist"},
            {"mbid": MBID_TWO, "name": "Artist"},
        ]
        ranked = rank_artist_candidates("Artist", candidates)
        self.assertEqual(len(ranked), 2)
        self.assertTrue(all(item["ambiguous"] for item in ranked))
        self.assertIsNone(resolve_exact_artist("Artist", candidates))

    def test_partial_names_and_invalid_mbids_are_not_matched(self):
        candidates = [
            {"mbid": MBID_ONE, "name": "The Artist"},
            {"mbid": "not-an-id", "name": "Artist"},
        ]
        self.assertEqual(rank_artist_candidates("Artist", candidates), [])


if __name__ == "__main__":
    unittest.main()