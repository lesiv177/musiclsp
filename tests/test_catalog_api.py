import os
import tempfile
import unittest
from unittest.mock import patch

from core import catalog_api, db


class CatalogApiTests(unittest.TestCase):
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

    def test_artist_gets_catalog_field_and_keeps_original_fields(self):
        payload = {"ok": True, "tracks": [{"title": "One"}], "artist": {"name": "Echo", "id": "aud:1", "cover": "c.jpg"}}
        out = catalog_api.enrich_artist("aud", "1", payload)
        self.assertEqual(out["artist"], payload["artist"])
        self.assertTrue(out["catalog"]["canonical_id"].startswith("cat:artist:"))
        self.assertFalse(out["catalog"]["needs_review"])

    def test_same_artist_on_two_providers_links_with_proof(self):
        a = {"ok": True, "tracks": [{"title": "One"}, {"title": "Two"}], "artist": {"name": "Echo"}}
        b = {"ok": True, "tracks": [{"title": "two"}, {"title": "one"}], "artist": {"name": "Echo"}}
        first = catalog_api.enrich_artist("aud", "1", a)
        second = catalog_api.enrich_artist("scu", "9", b)
        self.assertEqual(first["catalog"]["canonical_id"], second["catalog"]["canonical_id"])
        self.assertEqual(second["catalog"]["related"], [{"provider": "audius", "kind": "artist", "external_id": "1"}])

    def test_same_name_without_proof_is_flagged_not_merged(self):
        catalog_api.enrich_artist("aud", "1", {"tracks": [], "artist": {"name": "Echo"}})
        out = catalog_api.enrich_artist("scu", "9", {"tracks": [], "artist": {"name": "Echo"}})
        self.assertTrue(out["catalog"]["needs_review"])
        self.assertEqual(out["catalog"]["related"], [])

    def test_unknown_prefix_or_missing_artist_is_untouched(self):
        payload = {"ok": True, "tracks": []}
        self.assertEqual(catalog_api.enrich_artist("zzz", "1", payload), payload)
        self.assertEqual(catalog_api.enrich_artist("aud", "1", payload), payload)

    def test_store_failure_never_breaks_page(self):
        payload = {"tracks": [], "artist": {"name": "Echo"}}
        with patch("core.catalog_store.link_artist", side_effect=RuntimeError("db down")):
            self.assertEqual(catalog_api.enrich_artist("aud", "1", payload), payload)

    def test_metadata_only_album_has_no_play(self):
        out = catalog_api.enrich_album({"metadata_only": True, "tracks": [{"title": "x"}], "album": {}})
        self.assertEqual(out["availability"], {"state": "metadata_only", "playable": 0, "total": 1, "can_play": False})

    def test_provider_album_availability(self):
        out = catalog_api.enrich_album({"tracks": [{"id": "aud:1"}, {"title": "no id"}]})
        self.assertEqual((out["availability"]["state"], out["availability"]["can_play"]), ("partial", True))
        full = catalog_api.enrich_album({"tracks": [{"id": "aud:1"}]})
        self.assertEqual(full["availability"]["state"], "all")

    def test_album_gets_catalog_and_track_ids_additively(self):
        payload = {"ok": True, "album": {"id": "aud:7", "title": "Night", "artist": "Echo"},
                   "tracks": [{"id": "aud:1", "title": "Song", "artist": "Echo", "duration": 200}]}
        out = catalog_api.enrich_album(payload, "aud", "7")
        self.assertTrue(out["catalog"]["canonical_id"].startswith("cat:album:"))
        self.assertTrue(out["tracks"][0]["catalog_id"].startswith("cat:track:"))
        self.assertEqual(out["tracks"][0]["id"], "aud:1")
        self.assertEqual(out["album"], payload["album"])
        self.assertEqual(out["availability"]["state"], "all")

    def test_metadata_only_album_links_but_tracks_untouched_and_no_play(self):
        payload = {"metadata_only": True, "album": {"title": "Night", "artist": "Echo", "year": 2020},
                   "tracks": [{"title": "Song"}]}
        out = catalog_api.enrich_album(payload, "mb", "release-group:abc")
        self.assertIn("catalog", out)
        self.assertNotIn("catalog_id", out["tracks"][0])
        self.assertFalse(out["availability"]["can_play"])

    def test_album_failure_keeps_page_and_availability(self):
        payload = {"album": {"title": "Night", "artist": "Echo"}, "tracks": [{"id": "aud:1", "title": "S"}]}
        with patch("core.catalog_store.link_album", side_effect=RuntimeError("db down")):
            out = catalog_api.enrich_album(payload, "aud", "7")
        self.assertNotIn("catalog", out)
        self.assertEqual(out["tracks"], payload["tracks"])
        self.assertEqual(out["availability"]["state"], "all")

    def test_album_without_prefix_is_backward_compatible(self):
        out = catalog_api.enrich_album({"tracks": [{"id": "aud:1"}]})
        self.assertNotIn("catalog", out)

    def test_search_annotates_only_known_items_and_never_writes(self):
        catalog_api.enrich_artist("aud", "1", {"tracks": [], "artist": {"name": "Echo"}})
        items = [{"id": "aud:1", "name": "Echo"}, {"id": "aud:2", "name": "Other"}, {"id": "zzz:3"}, "bad"]
        out = catalog_api.annotate_search("artists", items)
        self.assertTrue(out[0]["catalog_id"].startswith("cat:artist:"))
        self.assertNotIn("catalog_id", out[1])
        self.assertEqual(out[2:], items[2:])
        self.assertEqual(self.store.resolve("audius", "artist", "2"), None)  # нічого не створено

    def test_search_annotates_albums_and_all_shape(self):
        catalog_api.enrich_album({"album": {"title": "Night", "artist": "Echo"},
                                  "tracks": [{"id": "aud:1", "title": "S", "artist": "Echo"}]}, "aud", "7")
        data = {"albums": [{"id": "aud:7", "title": "Night"}], "artists": [], "tracks": [{"id": "aud:1"}]}
        out = catalog_api.annotate_search("all", data)
        self.assertIn("catalog_id", out["albums"][0])
        self.assertEqual(out["tracks"], data["tracks"])
        self.assertEqual(catalog_api.annotate_search("public_domain", [1]), [1])

    def test_search_annotation_failure_returns_original(self):
        items = [{"id": "aud:1"}]
        with patch("core.catalog_store.resolve_many", side_effect=RuntimeError("db")):
            self.assertEqual(catalog_api.annotate_list("artist", items), items)

    def test_metadata_only_mb_album_is_annotated_by_stripped_id(self):
        catalog_api.enrich_album({"metadata_only": True, "album": {"title": "Night", "artist": "Echo", "year": 2020},
                                  "tracks": []}, "mb", "release-group:abc")
        out = catalog_api.annotate_list("album", [{"id": "mb:release-group:abc"}])
        self.assertIn("catalog_id", out[0])


if __name__ == "__main__":
    unittest.main()