import os
import tempfile
import unittest
from unittest.mock import patch

from core import db, favorites as FAV


class FavoritesTests(unittest.TestCase):
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

    def test_same_track_from_two_providers_collapses_but_nothing_is_lost(self):
        a = {"id": "aud:1", "title": "Song", "artist": "Echo", "duration": 200}
        b = {"id": "jam:9", "title": "Song", "artist": "Echo", "duration": 201}
        FAV.link_on_save("track", a["id"], a)
        FAV.link_on_save("track", b["id"], b)
        out = FAV.collapse("track", [b, a])  # b — новіший
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "jam:9")
        self.assertEqual(out[0]["also_saved"], ["aud:1"])
        self.assertTrue(out[0]["catalog_id"].startswith("cat:track:"))

    def test_ambiguous_tracks_are_not_collapsed(self):
        a = {"id": "aud:1", "title": "Song", "artist": "Echo"}
        b = {"id": "jam:9", "title": "Song", "artist": "Echo"}
        FAV.link_on_save("track", a["id"], a)
        FAV.link_on_save("track", b["id"], b)  # без тривалості -> review
        out = FAV.collapse("track", [b, a])
        self.assertEqual([i["id"] for i in out], ["jam:9", "aud:1"])

    def test_albums_collection_items_use_item_id(self):
        d1 = {"title": "Night", "artist": "Echo", "year": 2020, "track_count": 9}
        FAV.link_on_save("album", "aud:7", d1)
        FAV.link_on_save("album", "jam:5", d1)
        items = [{"item_id": "jam:5", "data": d1}, {"item_id": "aud:7", "data": d1}, {"item_id": "aud:99", "data": {}}]
        out = FAV.collapse("album", items, lambda i: i.get("item_id"))
        self.assertEqual([i["item_id"] for i in out], ["jam:5", "aud:99"])
        self.assertEqual(out[0]["also_saved"], ["aud:7"])

    def test_artist_link_and_unknown_items_untouched(self):
        self.assertTrue(FAV.link_on_save("artist", "aud:1", {"name": "Echo"}).startswith("cat:artist:"))
        out = FAV.collapse("artist", [{"item_id": "zzz:1"}, "bad", {"item_id": "aud:404"}], lambda i: i.get("item_id"))
        self.assertEqual(len(out), 2)

    def test_link_failure_never_blocks_saving(self):
        with patch("core.catalog_store.link_track", side_effect=RuntimeError("db")):
            self.assertEqual(FAV.link_on_save("track", "aud:1", {"title": "S", "artist": "E"}), "")
        self.assertEqual(FAV.link_on_save("track", "bad-id", {"title": "S", "artist": "E"}), "")
        self.assertEqual(FAV.link_on_save("album", "aud:1", {"title": "S"}), "")  # немає артиста

    def test_collapse_failure_returns_items_unchanged(self):
        items = [{"id": "aud:1"}]
        with patch("core.catalog_store.resolve_many", side_effect=RuntimeError("db")):
            self.assertEqual(FAV.collapse("track", items), items)

    def test_does_not_mutate_input(self):
        a = {"id": "aud:1", "title": "Song", "artist": "Echo", "duration": 200}
        FAV.link_on_save("track", a["id"], a)
        FAV.collapse("track", [a])
        self.assertNotIn("catalog_id", a)


if __name__ == "__main__":
    unittest.main()