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

    def test_connect_routes_are_registered(self):
        app = api.build_app()
        paths = {route.resource.canonical for route in app.router.routes()}
        self.assertTrue({
            "/api/connect/ws", "/api/connect/devices",
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


class SearchRouteTests(unittest.TestCase):
    def test_search_handler_requires_auth(self):
        """Регресія: h_search лишився без @require_auth, і пошук падав з TypeError."""
        import inspect
        params = list(inspect.signature(api.h_search).parameters)
        self.assertEqual(params, ["request"], "h_search має бути обгорнутий require_auth")