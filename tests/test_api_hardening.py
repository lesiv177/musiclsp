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

    def test_health_reports_jamendo_disabled_without_commercial_approval(self):
        with (
            patch.object(api.P, "jamendo_enabled", return_value=False),
            patch.object(api.P, "audius_host", return_value="https://audius.example"),
            patch.object(api, "optional_music_source_status", return_value={}),
        ):
            response = api.h_health(SimpleNamespace())
        payload = json.loads(response.text)
        self.assertFalse(payload["sources"]["jamendo"])
        self.assertTrue(payload["sources"]["audius"])

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


class AppleSearchRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _search(self, apple_side_effect=None, apple_results=None):
        request = SimpleNamespace(
            query={"q": "example artist", "type": "all", "limit": "10", "sort": "relevance", "sources": ""},
            headers={},
        )
        base_results = {"tracks": [{"id": "jam:1", "title": "Open track"}], "albums": [], "artists": []}
        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        with patch.object(api, "auth_user", return_value={"uid": 7}), \
             patch.object(api.db, "limits_for", return_value={"search_results_max": 30}), \
             patch.object(api.db, "bump_counter", return_value=(True, 2, 20)), \
             patch.object(api, "quality_for", return_value="high"), \
             patch.object(api.P, "search_everything", return_value=base_results), \
             patch.object(api, "apple_catalog_configured", return_value=True), \
             patch.object(api, "apple_catalog_search", side_effect=apple_side_effect, return_value=apple_results or []), \
             patch.object(api, "to_thread", side_effect=inline_to_thread):
            response = await api.h_search(request)
        return json.loads(response.text)

    async def test_global_search_merges_apple_catalogue_without_changing_playable_results(self):
        apple = [{"id": "apple_music:song:123", "kind": "song", "title": "Apple Song",
                  "external_url": "https://music.apple.com/pl/album/example/123"}]
        payload = await self._search(apple_results=apple)
        self.assertEqual(payload["results"]["tracks"][0]["id"], "jam:1")
        self.assertEqual(payload["apple_results"], apple)

    async def test_apple_catalogue_failure_does_not_break_global_search(self):
        payload = await self._search(apple_side_effect=api.AppleMusicError("upstream unavailable"))
        self.assertEqual(payload["results"]["tracks"][0]["id"], "jam:1")
        self.assertEqual(payload["apple_results"], [])


if __name__ == "__main__":
    unittest.main()


class SearchRouteTests(unittest.TestCase):
    def test_search_handler_requires_auth(self):
        """Регресія: h_search лишився без @require_auth, і пошук падав з TypeError."""
        import inspect
        params = list(inspect.signature(api.h_search).parameters)
        self.assertEqual(params, ["request"], "h_search має бути обгорнутий require_auth")