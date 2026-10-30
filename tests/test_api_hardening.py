import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.bot import api


class APIHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_bounded_int_accepts_and_clamps_values(self):
        self.assertEqual(api.bounded_int("12", 30, 1, 20), 12)
        self.assertEqual(api.bounded_int("999", 30, 1, 20), 20)
        self.assertEqual(api.bounded_int("-5", 30, 1, 20), 1)

    def test_bounded_int_rejects_invalid_values(self):
        self.assertIsNone(api.bounded_int("not-a-number", 30, 1, 20))
        self.assertIsNone(api.bounded_int("2.5", 30, 1, 20))

    async def test_error_middleware_hides_internal_exception_details(self):
        request = SimpleNamespace(path="/private/test")

        async def broken_handler(_request):
            raise RuntimeError("secret-database-password")

        response = await api.error_middleware(request, broken_handler)
        self.assertEqual(response.status, 500)
        payload = json.loads(response.text)
        self.assertEqual(payload["error"], "Внутрішня помилка сервера. Спробуйте ще раз.")
        self.assertNotIn("secret-database-password", response.text)

    async def test_json_object_rejects_non_object_payloads(self):
        request = SimpleNamespace(json=AsyncMock(return_value=["not", "an", "object"]))
        self.assertIsNone(await api.json_object(request))

    async def test_json_object_handles_malformed_payload(self):
        request = SimpleNamespace(json=AsyncMock(side_effect=ValueError("bad json")))
        self.assertIsNone(await api.json_object(request))

    def test_device_id_validation(self):
        self.assertTrue(api.valid_device_id("ios-device_123"))
        self.assertFalse(api.valid_device_id(""))
        self.assertFalse(api.valid_device_id("bad device"))
        self.assertFalse(api.valid_device_id("x" * 81))

    def test_safe_device_track_drops_non_finite_numbers(self):
        track = api.safe_device_track({
            "id": "sc:123", "title": "Song", "duration": float("nan"),
            "cover": "https://example.test/cover", "unexpected": "discard",
        })
        self.assertEqual(track["id"], "sc:123")
        self.assertEqual(track["title"], "Song")
        self.assertNotIn("duration", track)
        self.assertNotIn("unexpected", track)

    async def test_device_state_rejects_infinite_position(self):
        request = SimpleNamespace(
            headers={}, query={},
            json=AsyncMock(return_value={
                "device_id": "phone-1",
                "state": {"position": float("inf"), "volume": 0.5},
            }),
        )
        with patch.object(api, "auth_user", return_value={"uid": 42}), \
             patch.object(api, "to_thread", new=AsyncMock()) as run_thread:
            response = await api.h_device_state(request)
        self.assertEqual(response.status, 400)
        self.assertFalse(run_thread.called)

    async def test_device_state_clamps_values_and_sanitizes_track(self):
        request = SimpleNamespace(
            headers={}, query={},
            json=AsyncMock(return_value={
                "device_id": "phone-1",
                "state": {
                    "playing": "false", "position": 99999, "volume": -4,
                    "track": {"id": "sc:1", "title": "Song", "duration": float("nan")},
                    "queue": [{"id": "sc:1", "title": "Song"}, {"title": "invalid"}],
                    "index": 500,
                },
            }),
        )
        with patch.object(api, "auth_user", return_value={"uid": 42}), \
             patch.object(api, "to_thread", new=AsyncMock(return_value=True)) as run_thread:
            response = await api.h_device_state(request)
        self.assertEqual(response.status, 200)
        encoded = run_thread.await_args_list[0].args[3]
        saved = json.loads(encoded)
        self.assertFalse(saved["playing"])
        self.assertEqual(saved["position"], 86400)
        self.assertEqual(saved["volume"], 0)
        self.assertEqual(saved["queue"], [{"id": "sc:1", "title": "Song"}])
        self.assertEqual(saved["index"], 0)
        self.assertNotIn("duration", saved["track"])

    async def test_device_command_is_pushed_to_connected_sessions(self):
        request = SimpleNamespace(
            headers={}, query={},
            json=AsyncMock(return_value={"device_id": "pc-1", "action": "next", "payload": {}}),
        )
        with patch.object(api, "auth_user", return_value={"uid": 42}), \
             patch.object(api, "to_thread", new=AsyncMock(side_effect=[
                 [{"device_id": "pc-1", "active": True, "state": {}}], 77
             ])), \
             patch.object(api, "broadcast_device_event", new=AsyncMock()) as broadcast:
            response = await api.h_device_command(request)
        self.assertEqual(response.status, 200)
        broadcast.assert_awaited_once_with(42, {
            "type": "command", "device_id": "pc-1",
            "command": {"id": 77, "action": "next", "payload": {}},
        })

    async def test_device_command_rejects_offline_target(self):
        request = SimpleNamespace(
            headers={}, query={},
            json=AsyncMock(return_value={"device_id": "pc-1", "action": "next", "payload": {}}),
        )
        with patch.object(api, "auth_user", return_value={"uid": 42}), \
             patch.object(api, "to_thread", new=AsyncMock(return_value=[
                 {"device_id": "pc-1", "active": False, "state": {}}
             ])) as run_thread:
            response = await api.h_device_command(request)
        self.assertEqual(response.status, 404)
        self.assertEqual(run_thread.await_count, 1)

    def test_device_routes_are_registered(self):
        app = api.build_app()
        paths = {route.resource.canonical for route in app.router.routes()}
        self.assertTrue({
            "/api/devices", "/api/devices/register", "/api/devices/state",
            "/api/devices/command", "/api/devices/commands", "/api/devices/ws",
        }.issubset(paths))

    def test_bounded_offline_download(self):
        class Response:
            headers = {"Content-Length": "5"}
            def __init__(self):
                self.closed = False
            def raise_for_status(self):
                return None
            def iter_content(self, chunk_size):
                return [b"hello"]
            def close(self):
                self.closed = True

        response = Response()
        with patch.object(api.requests, "get", return_value=response):
            self.assertIsNone(api._download_bounded("https://audio.test/file", 4))
        self.assertTrue(response.closed)

        response = Response()
        with patch.object(api.requests, "get", return_value=response):
            self.assertEqual(api._download_bounded("https://audio.test/file", 5), b"hello")
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
