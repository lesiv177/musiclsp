import unittest

from core.release_radar_matching import release_matches_followed_artist


class ReleaseRadarArtistMatchingTests(unittest.TestCase):
    def test_matching_mbid_takes_precedence_over_different_display_names(self):
        self.assertTrue(release_matches_followed_artist(
            {"artist_mbid": "abc-123", "artist": "Stage Name"},
            {"mbid": "abc-123", "name": "Former Name"},
        ))

    def test_different_mbids_do_not_match_even_when_names_are_equal(self):
        self.assertFalse(release_matches_followed_artist(
            {"artist_mbid": "abc-123", "artist": "Artist"},
            {"mbid": "xyz-789", "name": "Artist"},
        ))

    def test_name_fallback_requires_exact_normalized_match(self):
        self.assertTrue(release_matches_followed_artist(
            {"artist": "OG-Buda"},
            {"name": "og buda"},
        ))
        self.assertFalse(release_matches_followed_artist(
            {"artist": "OG Buda feat. Guest"},
            {"name": "OG Buda"},
        ))

    def test_missing_identity_does_not_match(self):
        self.assertFalse(release_matches_followed_artist({}, {"name": "Artist"}))


if __name__ == "__main__":
    unittest.main()