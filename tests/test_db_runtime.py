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

    def test_device_commands_cannot_target_another_users_device(self):
        db.device_register(111, "owner-phone", "Owner", "iOS")
        self.assertIsNone(db.device_command_add(222, "owner-phone", "pause", "{}"))

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
