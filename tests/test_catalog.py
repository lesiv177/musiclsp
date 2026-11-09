import os
import tempfile
import unittest
from unittest.mock import patch

from core import catalog, db

MB1 = "12345678-abcd-4abc-8def-1234567890ab"
MB2 = "87654321-abcd-4abc-8def-1234567890ab"


class NormalizationTests(unittest.TestCase):
    def test_case_diacritics_punctuation(self):
        self.assertEqual(catalog.fold_text("  Béyoncé!! "), "beyonce")
        self.assertEqual(catalog.fold_text("OG-Buda"), "og buda")

    def test_cyrillic_and_latin_meet(self):
        self.assertEqual(catalog.artist_key("Океан Ельзи"), catalog.artist_key("Okean Elzy"))
        self.assertEqual(catalog.artist_key("Бумбокс"), "bumboks")

    def test_leading_the_is_ignored(self):
        self.assertEqual(catalog.artist_key("The Prodigy"), catalog.artist_key("Prodigy"))

    def test_title_key_drops_edition_and_feat(self):
        self.assertEqual(catalog.title_key("Album (Deluxe Edition)"), "album")
        self.assertEqual(catalog.title_key("Song (feat. Someone)"), "song")
        self.assertNotEqual(catalog.title_key("Song 2"), catalog.title_key("Song"))

    def test_valid_mbid_strips_prefix(self):
        self.assertEqual(catalog.valid_mbid("mb:artist:" + MB1.upper()), MB1)
        self.assertEqual(catalog.valid_mbid("nonsense"), "")


class ArtistMatchTests(unittest.TestCase):
    def test_same_mbid_is_same(self):
        r = catalog.match_artists({"name": "A", "mbid": MB1}, {"name": "B", "mbid": MB1})
        self.assertEqual(r["decision"], "same")

    def test_conflicting_mbid_is_different(self):
        r = catalog.match_artists({"name": "A", "mbid": MB1}, {"name": "A", "mbid": MB2})
        self.assertEqual(r["decision"], "different")

    def test_name_only_never_auto_merges(self):
        r = catalog.match_artists({"name": "Echo", "country": "UA"}, {"name": "echo", "country": "UA"})
        self.assertEqual(r["decision"], "review")

    def test_same_name_different_country_is_not_merged(self):
        r = catalog.match_artists({"name": "Echo", "country": "UA"}, {"name": "Echo", "country": "US"})
        self.assertNotEqual(r["decision"], "same")

    def test_cyrillic_name_matches_latin_with_proof(self):
        a = {"name": "Бумбокс", "website": "https://bumboks.example/"}
        b = {"name": "Bumboks", "website": "https://www.bumboks.example"}
        self.assertEqual(catalog.match_artists(a, b)["decision"], "same")

    def test_shared_website_confirms(self):
        a = {"name": "Echo", "website": "https://www.echo.band/", "country": "UA"}
        b = {"name": "ECHO", "website": "http://echo.band", "country": "UA"}
        self.assertEqual(catalog.match_artists(a, b)["decision"], "same")

    def test_unsafe_urls_are_ignored(self):
        a = {"name": "Echo", "website": "javascript:alert(1)"}
        b = {"name": "Echo", "website": "javascript:alert(1)"}
        self.assertEqual(catalog.match_artists(a, b)["decision"], "review")

    def test_shared_tracks_confirm(self):
        a = {"name": "Echo", "tracks": ["One", "Two"]}
        b = {"name": "Echo", "tracks": ["two (feat. X)", "ONE"]}
        self.assertEqual(catalog.match_artists(a, b)["decision"], "same")

    def test_different_names_are_different(self):
        self.assertEqual(catalog.match_artists({"name": "A"}, {"name": "B"})["decision"], "different")


class AlbumMatchTests(unittest.TestCase):
    def test_deluxe_edition_same_year_matches(self):
        a = {"title": "Album", "artist": "Echo", "year": 2020, "track_count": 10}
        b = {"title": "Album (Deluxe Edition)", "artist": "Echo", "year": "2020", "track_count": 10}
        self.assertEqual(catalog.match_albums(a, b)["decision"], "same")

    def test_year_conflict_not_same(self):
        a = {"title": "Album", "artist": "Echo", "year": 2010}
        b = {"title": "Album", "artist": "Echo", "year": 2020}
        self.assertNotEqual(catalog.match_albums(a, b)["decision"], "same")

    def test_missing_year_needs_review(self):
        a = {"title": "Album", "artist": "Echo"}
        b = {"title": "Album", "artist": "Echo"}
        self.assertEqual(catalog.match_albums(a, b)["decision"], "review")

    def test_upc_matches(self):
        r = catalog.match_albums({"title": "X", "artist": "A", "upc": "0123"}, {"title": "Y", "artist": "B", "upc": "0123"})
        self.assertEqual(r["decision"], "same")

    def test_other_artist_is_different(self):
        r = catalog.match_albums({"title": "Album", "artist": "A"}, {"title": "Album", "artist": "B"})
        self.assertEqual(r["decision"], "different")


class AvailabilityTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(catalog.album_availability([{"playable": True}, {"playable": True}])["state"], "all")
        part = catalog.album_availability([{"playable": True}, {}])
        self.assertEqual((part["state"], part["can_play"]), ("partial", True))
        none = catalog.album_availability([{"title": "x"}])
        self.assertEqual((none["state"], none["can_play"]), ("metadata_only", False))
        self.assertFalse(catalog.album_availability([])["can_play"])


class ProvenanceTests(unittest.TestCase):
    def test_priority_and_provenance(self):
        merged = catalog.merge_artist_profiles([
            {"provider": "audius", "name": "Echo", "bio": "Audius bio", "image": "a.jpg", "source_url": "https://audius.co/echo"},
            {"provider": "musicbrainz", "name": "Echo", "country": "UA", "bio": "", "source_url": "https://musicbrainz.org/artist/1"},
        ])
        self.assertEqual(merged["fields"]["country"], "UA")
        self.assertEqual(merged["fields"]["bio"], "Audius bio")  # порожнє не перезаписує
        self.assertEqual(merged["provenance"]["name"]["source"], "musicbrainz")
        self.assertEqual(merged["provenance"]["image"]["url"], "https://audius.co/echo")


class StoreTests(unittest.TestCase):
    def setUp(self):
        from core import catalog_store
        self.store = catalog_store
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patches = [patch.object(db, "SQLITE_PATH", self.path), patch.object(db, "USE_PG", False)]
        for p in self.patches:
            p.start()
        self.store.ensure_schema()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.unlink(self.path)

    def test_new_artist_is_created_and_idempotent(self):
        rec = {"provider": "audius", "id": "1", "name": "Echo", "country": "UA"}
        first = self.store.link_artist(rec)
        self.assertEqual(first["status"], "created")
        again = self.store.link_artist(rec)
        self.assertEqual(again["canonical_id"], first["canonical_id"])

    def test_same_name_without_proof_goes_to_review_not_merged(self):
        a = self.store.link_artist({"provider": "audius", "id": "1", "name": "Echo", "country": "UA"})
        b = self.store.link_artist({"provider": "soundcloud", "id": "9", "name": "Echo", "country": "UA"})
        self.assertEqual(b["status"], "review")
        self.assertNotEqual(a["canonical_id"], b["canonical_id"])
        self.assertIsNone(self.store.resolve("soundcloud", "artist", "9"))
        self.assertEqual(len(self.store.review_queue()), 1)

    def test_corroborated_artist_links_to_existing(self):
        a = self.store.link_artist({"provider": "audius", "id": "1", "name": "Echo", "website": "https://echo.band"})
        b = self.store.link_artist({"provider": "soundcloud", "id": "9", "name": "ECHO", "website": "https://www.echo.band/"})
        self.assertEqual(b["status"], "linked")
        self.assertEqual(b["canonical_id"], a["canonical_id"])
        self.assertEqual(self.store.resolve("soundcloud", "artist", "9"), a["canonical_id"])

    def test_manual_confirm_resolves_review(self):
        a = self.store.link_artist({"provider": "audius", "id": "1", "name": "Echo"})
        b = self.store.link_artist({"provider": "soundcloud", "id": "9", "name": "Echo"})
        self.assertEqual(b["status"], "review")
        self.store.confirm_merge("soundcloud", "artist", "9", a["canonical_id"])
        self.assertEqual(self.store.resolve("soundcloud", "artist", "9"), a["canonical_id"])
        self.assertEqual(self.store.review_queue(), [])


if __name__ == "__main__":
    unittest.main()


class ReleaseLinkingTests(StoreTests):
    """Альбоми й треки: ті самі гарантії безпеки, що й для артистів."""

    def test_album_same_across_providers_with_year(self):
        a = self.store.link_album({"provider": "audius", "id": "a1", "title": "Night", "artist": "Echo",
                                   "year": 2020, "track_count": 9})
        b = self.store.link_album({"provider": "jamendo", "id": "j9", "title": "Night",
                                   "artist": "ECHO", "year": 2020, "track_count": 9})
        self.assertEqual(b["status"], "linked")
        self.assertEqual(a["canonical_id"], b["canonical_id"])

    def test_album_deluxe_edition_is_reviewed_not_auto_merged(self):
        a = self.store.link_album({"provider": "audius", "id": "a1", "title": "Night", "artist": "Echo",
                                   "year": 2020, "track_count": 9})
        b = self.store.link_album({"provider": "jamendo", "id": "j9", "title": "Night (Deluxe Edition)",
                                   "artist": "Echo", "year": 2020, "track_count": 14})
        self.assertEqual(b["status"], "review")
        self.assertNotEqual(a["canonical_id"], b["canonical_id"])

    def test_album_without_confirmation_goes_to_review_not_merged(self):
        a = self.store.link_album({"provider": "audius", "id": "a1", "title": "Night", "artist": "Echo"})
        b = self.store.link_album({"provider": "jamendo", "id": "j9", "title": "Night", "artist": "Echo"})
        self.assertEqual(b["status"], "review")
        self.assertNotEqual(a["canonical_id"], b["canonical_id"])

    def test_album_year_conflict_stays_separate(self):
        a = self.store.link_album({"provider": "audius", "id": "a1", "title": "Night", "artist": "Echo", "year": 2010})
        b = self.store.link_album({"provider": "jamendo", "id": "j9", "title": "Night", "artist": "Echo", "year": 2022})
        self.assertNotEqual(a["canonical_id"], b["canonical_id"])

    def test_track_merge_requires_duration_or_isrc(self):
        a = self.store.link_track({"provider": "audius", "id": "t1", "title": "Song", "artist": "Echo", "duration": 200})
        b = self.store.link_track({"provider": "jamendo", "id": "t2", "title": "Song", "artist": "Echo", "duration": 201})
        self.assertEqual(a["canonical_id"], b["canonical_id"])
        c = self.store.link_track({"provider": "soundcloud", "id": "t3", "title": "Song", "artist": "Echo"})
        self.assertEqual(c["status"], "review")
        self.assertNotEqual(c["canonical_id"], a["canonical_id"])

    def test_track_isrc_and_idempotency(self):
        rec = {"provider": "audius", "id": "t1", "title": "Song", "artist": "Echo", "isrc": "USRC17607839"}
        first = self.store.link_track(rec)
        other = self.store.link_track({"provider": "jamendo", "id": "t7", "title": "Song (Remastered)",
                                       "artist": "Echo", "isrc": "us-rc1-76-07839"})
        self.assertEqual(first["canonical_id"], other["canonical_id"])
        self.assertEqual(self.store.link_track(rec)["status"], "linked")

    def test_different_artists_never_merge(self):
        a = self.store.link_track({"provider": "audius", "id": "t1", "title": "Song", "artist": "Echo", "duration": 200})
        b = self.store.link_track({"provider": "jamendo", "id": "t2", "title": "Song", "artist": "Other", "duration": 200})
        self.assertNotEqual(a["canonical_id"], b["canonical_id"])

    def test_requires_artist_and_title(self):
        with self.assertRaises(ValueError):
            self.store.link_track({"provider": "audius", "id": "t1", "title": "", "artist": "Echo"})