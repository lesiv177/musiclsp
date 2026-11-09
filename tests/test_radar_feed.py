import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from core import db, radar_feed as RF

TODAY = date(2026, 11, 9)


def follow(name, mbid=""):
    return {"artist_name": name, "artist": {"name": name, **({"artist_mbid": mbid} if mbid else {})}}


class FeedTests(unittest.TestCase):
    def feed(self, follows, albums, **kw):
        return RF.build_feed(follows, albums, today=TODAY, **kw)

    def test_substring_artist_is_rejected(self):
        albums = {"echo": [{"title": "X", "artist": "Echo Chamber Band", "release_date": "2026-11-01"}]}
        self.assertEqual(self.feed([follow("Echo")], albums), [])

    def test_exact_name_other_case_matches(self):
        albums = {"echo": [{"title": "X", "artist": "ECHO", "release_date": "2026-11-01"}]}
        self.assertEqual(len(self.feed([follow("Echo")], albums)), 1)

    def test_same_release_from_two_sources_is_merged(self):
        albums = {"echo": [
            {"title": "Night", "artist": "Echo", "release_date": "2026-10-20", "source": "audius"},
            {"title": "Night (Deluxe Edition)", "artist": "echo", "release_date": "2026-10-21", "source": "jamendo",
             "cover": "c.jpg"}]}
        out = self.feed([follow("Echo")], albums)
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]["sources"]), ["audius", "jamendo"])
        self.assertEqual(out[0]["cover"], "c.jpg")

    def test_future_missing_and_invalid_dates_are_hidden(self):
        albums = {"echo": [
            {"title": "A", "artist": "Echo", "release_date": "2027-01-01"},
            {"title": "B", "artist": "Echo", "release_date": ""},
            {"title": "C", "artist": "Echo", "release_date": "2026"},
            {"title": "D", "artist": "Echo", "release_date": "garbage!!"}]}
        self.assertEqual(self.feed([follow("Echo")], albums), [])

    def test_period_filter(self):
        albums = {"echo": [{"title": "Old", "artist": "Echo", "release_date": "2026-09-01"},
                           {"title": "New", "artist": "Echo", "release_date": "2026-11-05"}]}
        self.assertEqual([r["title"] for r in self.feed([follow("Echo")], albums, period="week")], ["New"])
        self.assertEqual(len(self.feed([follow("Echo")], albums, period="quarter")), 2)

    def test_mbid_conflict_blocks_same_name(self):
        mb1, mb2 = "12345678-abcd-4abc-8def-1234567890ab", "87654321-abcd-4abc-8def-1234567890ab"
        albums = {"echo": [{"title": "X", "artist": "Echo", "artist_mbid": mb2, "release_date": "2026-11-01"}]}
        self.assertEqual(self.feed([follow("Echo", mb1)], albums), [])

    def test_sorted_newest_first_and_seen_flag(self):
        albums = {"echo": [{"title": "A", "artist": "Echo", "release_date": "2026-10-01"},
                           {"title": "B", "artist": "Echo", "release_date": "2026-11-01"}]}
        out = self.feed([follow("Echo")], albums, seen={RF.release_key(albums["echo"][0])})
        self.assertEqual([r["title"] for r in out], ["B", "A"])
        self.assertEqual([r["seen"] for r in out], [False, True])

    def test_release_type_normalized(self):
        albums = {"echo": [{"title": "A", "artist": "Echo", "release_date": "2026-11-01", "type": "EP"},
                           {"title": "B", "artist": "Echo", "release_date": "2026-11-02", "type": "weird"}]}
        types = {r["title"]: r["release_type"] for r in self.feed([follow("Echo")], albums)}
        self.assertEqual(types, {"A": "ep", "B": ""})


class SeenStoreTests(unittest.TestCase):
    def setUp(self):
        from core import user_data
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patches = [patch.object(db, "SQLITE_PATH", self.path), patch.object(db, "USE_PG", False)]
        for p in self.patches:
            p.start()
        user_data.ensure_schema()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.unlink(self.path)

    def test_mark_seen_is_idempotent_and_isolated_per_user(self):
        RF.mark_seen(1, ["k1", "k1", "k2"])
        RF.mark_seen(1, ["k1"])
        self.assertEqual(RF.seen_keys(1), {"k1", "k2"})
        self.assertEqual(RF.seen_keys(2), set())


if __name__ == "__main__":
    unittest.main()