import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core import db


class FakeCursor:
    def __init__(self, result):
        self.result = result
        self.sql = ""
        self.params = None
    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
    def fetchone(self):
        return self.result
    def close(self):
        pass


class FakeConnection:
    def __init__(self, result):
        self.autocommit = False
        self.closed = 0
        self.result = result
        self.last_cursor = None
    def cursor(self):
        self.last_cursor = FakeCursor(self.result)
        return self.last_cursor
    def close(self):
        self.closed = 1


class FakePool:
    def __init__(self, connection):
        self.connection = connection
        self.returned = []
    def getconn(self):
        return self.connection
    def putconn(self, connection, close=False):
        self.returned.append((connection, close))


class PostgreSQLPollingLockTests(unittest.TestCase):
    def test_lock_acquired_and_released(self):
        conn = FakeConnection((True,))
        pool = FakePool(conn)
        with patch.object(db, "USE_PG", True), \
             patch.object(db, "_pg_pool", pool), \
             patch.object(db, "_bot_lock_conn", None):
            self.assertTrue(db.acquire_bot_polling_lock())
            self.assertIs(db._bot_lock_conn, conn)
            self.assertTrue(conn.autocommit)
            self.assertTrue(db.release_bot_polling_lock())
            self.assertIsNone(db._bot_lock_conn)
        self.assertFalse(conn.autocommit)
        self.assertEqual(len(pool.returned), 1)
        self.assertIn("pg_advisory_unlock", conn.last_cursor.sql)

    def test_second_instance_is_denied_without_holding_connection(self):
        conn = FakeConnection((False,))
        pool = FakePool(conn)
        with patch.object(db, "USE_PG", True), \
             patch.object(db, "_pg_pool", pool), \
             patch.object(db, "_bot_lock_conn", None):
            self.assertFalse(db.acquire_bot_polling_lock())
            self.assertIsNone(db._bot_lock_conn)
        self.assertFalse(conn.autocommit)
        self.assertEqual(len(pool.returned), 1)

    def test_sqlite_mode_reports_no_distributed_lock(self):
        with patch.object(db, "USE_PG", False):
            self.assertIsNone(db.acquire_bot_polling_lock())


class SQLiteDevicePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "musiclsp-test.db")
        self.database_patch = patch.object(db, "DATABASE_URL", "")
        self.path_patch = patch.object(db, "SQLITE_PATH", self.db_path)
        self.database_patch.start()
        self.path_patch.start()
        db.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.database_patch.stop()
        self.tmp.cleanup()

    def test_saved_queue_round_trip_is_account_scoped_and_bounded(self):
        tracks = [{"id": "a", "title": "A", "artist": "Artist", "extra": "ignored"}]
        self.assertEqual(db.queue_save(987650, tracks), 1)
        saved = db.queue_get(987650)
        self.assertEqual(saved["tracks"][0]["id"], "a")
        self.assertNotIn("extra", saved["tracks"][0])
        self.assertEqual(db.queue_get(987649)["tracks"], [])

    def test_album_artist_collection_add_remove_and_scope(self):
        self.assertTrue(db.collection_add(987648, "album", "alb:1", {"title": "Album"}))
        self.assertTrue(db.collection_add(987648, "artist", "art:1", {"name": "Artist"}))
        self.assertFalse(db.collection_add(987648, "track", "t:1", {"title": "Track"}))
        self.assertEqual(len(db.collection_list(987648)), 2)
        self.assertEqual(db.collection_list(987648, "album")[0]["data"]["title"], "Album")
        self.assertTrue(db.collection_remove(987648, "album", "alb:1"))
        self.assertEqual(len(db.collection_list(987648)), 1)
        self.assertEqual(db.collection_list(987647), [])

    def test_search_history_add_deduplicates_and_keeps_latest_entries(self):
        uid = 987651
        db.search_history_add(uid, "  jazz  ", "tracks")
        db.search_history_add(uid, "JAZZ", "tracks")
        db.search_history_add(uid, "jazz", "albums")
        items = db.search_history_list(uid)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["kind"], "albums")
        self.assertEqual(items[1]["query"], "JAZZ")

    def test_search_history_is_user_scoped_and_can_be_removed_or_cleared(self):
        db.search_history_add(987652, "ambient")
        db.search_history_add(987653, "rock")
        item = db.search_history_list(987652)[0]
        self.assertTrue(db.search_history_remove(987652, item["id"]))
        self.assertEqual(db.search_history_list(987652), [])
        self.assertEqual(len(db.search_history_list(987653)), 1)
        db.search_history_add(987652, "jazz")
        self.assertEqual(db.search_history_clear(987652), 1)
        self.assertEqual(db.search_history_list(987652), [])

    def test_promo_codes_are_normalized_validated_and_unique(self):
        self.assertTrue(db.promo_create(" launch_2026 ", 14, 2))
        self.assertFalse(db.promo_create("LAUNCH_2026", 14, 2))
        with self.assertRaises(ValueError):
            db.promo_create("bad code", 14, 2)
        with self.assertRaises(ValueError):
            db.promo_create("SHORT", 0, 2)

    def test_promo_code_cannot_be_redeemed_more_than_its_uses(self):
        uid = 123458
        db.get_user(uid, "", "Promo QA")
        self.assertTrue(db.promo_create("ONE_USE", 14, 1))
        self.assertEqual(db.promo_redeem(uid, "one_use"), (True, 14))
        self.assertEqual(db.promo_redeem(uid, "ONE_USE"), (False, 0))
        promo = next(p for p in db.promo_list() if p["code"] == "ONE_USE")
        self.assertEqual(int(promo["uses_left"]), 0)
        self.assertTrue(db.is_premium(uid))

    def test_device_state_and_command_round_trip(self):
        uid = 123456
        db.device_register(uid, "phone-1", "Test phone", "iOS")
        state = {"playing": True, "position": 42, "volume": 0.7}
        self.assertTrue(db.device_state_update(uid, "phone-1", json.dumps(state)))

        devices = db.device_list(uid)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_id"], "phone-1")
        self.assertTrue(devices[0]["active"])
        self.assertEqual(devices[0]["state"], state)

        command_id = db.device_command_add(uid, "phone-1", "pause", "{}")
        self.assertIsInstance(command_id, int)
        commands = db.device_commands_get(uid, "phone-1", 0)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["id"], command_id)
        self.assertEqual(commands[0]["action"], "pause")

        # Commands remain pending until the device confirms successful execution.
        self.assertTrue(db.device_command_ack(uid, "phone-1", command_id))
        self.assertEqual(db.device_commands_get(uid, "phone-1", 0), [])
        self.assertTrue(db.device_command_ack(uid, "phone-1", command_id))
        self.assertFalse(db.device_command_ack(999, "phone-1", command_id))

    def test_unacked_command_is_returned_even_when_cursor_has_passed_it(self):
        uid = 123457
        db.device_register(uid, "phone-2", "Test phone", "iOS")
        first = db.device_command_add(uid, "phone-2", "pause", "{}")
        second = db.device_command_add(uid, "phone-2", "volume", '{"volume": 0.5}')
        self.assertTrue(db.device_command_ack(uid, "phone-2", second))
        # A later acknowledged command must not hide an earlier pending one.
        pending = db.device_commands_get(uid, "phone-2", second)
        self.assertEqual([item["id"] for item in pending], [first])

    def test_device_commands_cannot_target_another_users_device(self):
        db.device_register(111, "owner-phone", "Owner", "iOS")
        self.assertIsNone(db.device_command_add(222, "owner-phone", "pause", "{}"))

    def test_playback_claim_rejects_stale_owner_after_handoff(self):
        first = json.dumps({"track": {"id": "a"}, "playing": True})
        second = json.dumps({"track": {"id": "b"}, "playing": True})
        stale = json.dumps({"track": {"id": "stale"}, "playing": True})

        self.assertTrue(db.player_session_claim(111, "phone-a", first, ""))
        self.assertTrue(db.player_session_claim(111, "desktop-b", second, "phone-a"))

        # A delayed heartbeat from the old owner must not steal the session back.
        self.assertFalse(db.player_session_claim(111, "phone-a", stale, "phone-a"))
        session = db.player_session_get(111)
        self.assertEqual(session["owner_device_id"], "desktop-b")
        self.assertEqual(session["state"]["track"]["id"], "b")

    def test_playback_reset_clears_only_the_requested_account(self):
        db.device_register(111, "phone-a", "A", "iOS")
        db.device_register(222, "phone-b", "B", "Windows")
        db.player_session_set(111, "phone-a", json.dumps({"track": {"id": "a"}}))
        db.player_session_set(222, "phone-b", json.dumps({"track": {"id": "b"}}))
        db.device_command_add(111, "phone-a", "pause", "{}")
        db.device_command_add(222, "phone-b", "pause", "{}")

        result = db.playback_reset(111)

        self.assertEqual(result["devices"], 1)
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["commands"], 1)
        self.assertEqual(db.device_list(111), [])
        self.assertIsNone(db.player_session_get(111))
        self.assertEqual(len(db.device_list(222)), 1)
        self.assertIsNotNone(db.player_session_get(222))
        self.assertEqual(len(db.device_commands_get(222, "phone-b", 0)), 1)


if __name__ == "__main__":
    unittest.main()