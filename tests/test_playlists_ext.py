import os
import tempfile
import unittest
from unittest.mock import patch

from core import db, playlists_ext as PX


class PlaylistExtTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patches = [patch.object(db, "SQLITE_PATH", self.path), patch.object(db, "USE_PG", False)]
        for p in self.patches:
            p.start()
        db.init_db()
        self.pl, _ = db.pl_create(1, "Mix", "d")
        for i in range(4):
            db.pl_add_track(1, self.pl["id"], {"id": f"aud:{i}", "title": f"T{i}", "artist": "A", "cover": ""})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.unlink(self.path)

    def order(self, pid=None):
        return [t["id"] for t in db.pl_get(pid or self.pl["id"])["tracks"]]

    def test_rename_description_cover(self):
        ok, _ = PX.update(1, self.pl["id"], name="  New   name ", description="x", cover="https://e.com/c.jpg")
        got = db.pl_get(self.pl["id"])
        self.assertTrue(ok)
        self.assertEqual((got["name"], got["description"], got["cover"]), ("New name", "x", "https://e.com/c.jpg"))

    def test_invalid_values_rejected_and_nothing_changed(self):
        for kw in ({"name": ""}, {"name": 5}, {"cover": "javascript:alert(1)"}, {"cover": "data:text/html,x"},
                   {"is_public": "yes"}, {}):
            ok, err = PX.update(1, self.pl["id"], **kw)
            self.assertFalse(ok, kw)
        self.assertEqual(db.pl_get(self.pl["id"])["name"], "Mix")

    def test_cannot_edit_foreign_playlist(self):
        ok, err = PX.update(2, self.pl["id"], name="Hacked")
        self.assertFalse(ok)
        self.assertEqual(db.pl_get(self.pl["id"])["name"], "Mix")
        self.assertFalse(PX.reorder(2, self.pl["id"], ["aud:0"])[0])

    def test_empty_cover_removes_it(self):
        PX.update(1, self.pl["id"], cover="https://e.com/c.jpg")
        PX.update(1, self.pl["id"], cover="")
        self.assertEqual(db.pl_get(self.pl["id"])["cover"], "")

    def test_reorder_full_partial_and_garbage(self):
        self.assertTrue(PX.reorder(1, self.pl["id"], ["aud:3", "aud:2", "aud:1", "aud:0"])[0])
        self.assertEqual(self.order(), ["aud:3", "aud:2", "aud:1", "aud:0"])
        PX.reorder(1, self.pl["id"], ["aud:0", "ghost", "aud:0"])  # часткова, дубль, невідомий
        self.assertEqual(self.order(), ["aud:0", "aud:3", "aud:2", "aud:1"])
        self.assertFalse(PX.reorder(1, self.pl["id"], "nope")[0])
        self.assertEqual(len(self.order()), 4)

    def test_public_flag_and_view_hides_owner(self):
        PX.update(1, self.pl["id"], is_public=False)
        self.assertIsNone(PX.public_view(db.pl_get(self.pl["id"])))
        PX.update(1, self.pl["id"], is_public=True)
        view = PX.public_view(db.pl_get(self.pl["id"]))
        self.assertNotIn("uid", view)
        self.assertEqual(len(view["tracks"]), 4)

    def test_existing_playlists_stay_public_by_default(self):
        self.assertIsNotNone(PX.public_view(db.pl_get(self.pl["id"])))

    def test_copy_shared_works_for_other_user_and_respects_privacy(self):
        code = db.pl_get(self.pl["id"])["share_code"]
        new, err = PX.copy_shared(2, code)
        self.assertEqual(new["uid"], 2)
        self.assertEqual(self.order(new["id"]), ["aud:0", "aud:1", "aud:2", "aud:3"])
        self.assertEqual(int(db.pl_get(new["id"])["is_public"]), 0)
        PX.update(1, self.pl["id"], is_public=False)
        self.assertIsNone(PX.copy_shared(3, code)[0])
        self.assertIsNone(PX.copy_shared(3, "nonexistent")[0])


if __name__ == "__main__":
    unittest.main()