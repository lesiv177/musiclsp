import os
import tempfile
import unittest
from unittest.mock import patch

from core import db, recommend as REC, user_data as UD


def trk(i, title, artist):
    return {"id": i, "title": title, "artist": artist}


class RecommendTests(unittest.TestCase):
    def test_not_enough_data_returns_nothing(self):
        profile = REC.taste_profile([trk("a:1", "T", "Echo")])
        out = REC.build(profile, {}, [], set())
        self.assertFalse(out["enough_data"])
        self.assertEqual(out["tracks"], [])

    def test_weights_and_seeds(self):
        profile = REC.taste_profile(
            library=[trk("a:1", "One", "Echo"), trk("a:2", "Two", "Echo")],
            history=[trk("a:3", "Three", "Other")],
            following=[{"artist_name": "Followed"}])
        self.assertEqual([a["artist"] for a in profile["top"]][:2], ["Echo", "Followed"])
        self.assertEqual([a["artist"] for a in REC.pick_seeds(profile)], ["Echo", "Other"])  # Followed без seed

    def test_known_hidden_and_per_artist_cap(self):
        profile = REC.taste_profile([trk("a:1", "One", "Echo")] * 3)
        cands = {"echo": [trk("n:1", "One", "Echo"),               # вже відомий
                          trk("n:2", "A", "New"), trk("n:3", "B", "New"),
                          trk("n:4", "C", "New"), trk("n:5", "D", "New"),
                          trk("n:6", "Hidden", "Zed")]}
        hidden = {REC.track_key(trk("n:6", "Hidden", "Zed"))}
        out = REC.build(profile, cands, [trk("a:1", "One", "Echo")], hidden, per_artist=3)
        ids = [t["id"] for t in out["tracks"]]
        self.assertEqual(ids, ["n:2", "n:3", "n:4"])
        self.assertTrue(all(t["reason"] == "Схоже на Echo" and t["new_artist"] for t in out["tracks"]))

    def test_seed_artists_are_interleaved(self):
        profile = REC.taste_profile([trk("a:1", "x", "A")] * 2 + [trk("b:1", "y", "B")] * 2)
        cands = {"a": [trk("1", "t1", "P"), trk("2", "t2", "Q")], "b": [trk("3", "t3", "R"), trk("4", "t4", "S")]}
        out = REC.build(profile, cands, [], set(), limit=4)
        self.assertEqual([t["id"] for t in out["tracks"]], ["1", "3", "2", "4"])


class UserDataTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patches = [patch.object(db, "SQLITE_PATH", self.path), patch.object(db, "USE_PG", False)]
        for p in self.patches:
            p.start()
        for stmt in db.SCHEMA_SQLITE:
            with db.conn_cursor() as (_c, cur):
                cur.execute(stmt)
        UD.ensure_schema()
        for uid in (1, 2):
            db.get_user(uid, f"user{uid}", f"Name{uid}")
            db.lib_add(uid, {"id": f"aud:{uid}", "title": "T", "artist": "A"})
            db.history_add(uid, {"id": f"aud:{uid}", "title": "T", "artist": "A"}, 30)
            playlist, _err = db.pl_create(uid, f"PL{uid}")
            db.pl_add_track(uid, playlist["id"], {"id": f"aud:{uid}", "title": "T", "artist": "A"})
            UD.hide_recommendation(uid, "k|t")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.unlink(self.path)

    def test_hidden_keys_per_user(self):
        self.assertEqual(UD.hidden_keys(1), {"k|t"})
        UD.hide_recommendation(1, "k|t")  # повтор не дублює
        self.assertEqual(len(UD.hidden_keys(1)), 1)

    def test_export_contains_only_own_data(self):
        data = UD.export_user(1)
        self.assertEqual(data["account"]["username"], "user1")
        self.assertEqual([r["tid"] for r in data["library"]], ["aud:1"])
        self.assertEqual(data["playlists"][0]["name"], "PL1")
        self.assertEqual(len(data["playlists"][0]["tracks"]), 1)
        self.assertNotIn("aud:2", str(data))

    def test_delete_removes_only_own_data(self):
        deleted = UD.delete_user_data(1)
        self.assertEqual(deleted["library"], 1)
        self.assertEqual(UD.export_user(1)["library"], [])
        self.assertEqual(UD.export_user(1)["playlists"], [])
        self.assertEqual(UD.export_user(1)["account"]["username"], "")
        self.assertEqual(len(UD.export_user(2)["library"]), 1)
        self.assertEqual(UD.export_user(2)["account"]["username"], "user2")
        self.assertEqual(UD.hidden_keys(2), {"k|t"})


if __name__ == "__main__":
    unittest.main()