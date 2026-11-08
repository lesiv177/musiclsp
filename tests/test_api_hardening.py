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

    async def test_soundcloud_artist_profile_is_returned_without_tracks(self):
        request = SimpleNamespace(match_info={"aid": "scu:77"})
        profile = {
            "id": "scu:77", "name": "Example Artist",
            "cover": "https://img.test/avatar.jpg",
            "source": "soundcloud", "source_url": "https://soundcloud.com/example-artist",
        }

        async def fake_to_thread(fn, *args, **kwargs):
            if fn is api.P.soundcloud_user_tracks:
                return []
            if fn is api.P.soundcloud_user_by_id:
                return profile
            raise AssertionError(f"Unexpected provider function: {fn}")

        with patch.object(api, "auth_user", return_value={"uid": 123}), \
             patch.object(api, "quality_for", return_value="mp32"), \
             patch.object(api, "to_thread", side_effect=fake_to_thread):
            response = await api.h_artist(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["artist"]["name"], "Example Artist")
        self.assertEqual(payload["tracks"], [])

    async def test_soundcloud_artist_uses_track_metadata_when_profile_lookup_fails(self):
        request = SimpleNamespace(match_info={"aid": "scu:77"})
        tracks = [{"id": "sc:1", "artist": "Fallback Artist", "cover": "https://img.test/track.jpg"}]

        async def fake_to_thread(fn, *args, **kwargs):
            if fn is api.P.soundcloud_user_tracks:
                return tracks
            if fn is api.P.soundcloud_user_by_id:
                return None
            raise AssertionError(f"Unexpected provider function: {fn}")

        with patch.object(api, "auth_user", return_value={"uid": 123}), \
             patch.object(api, "quality_for", return_value="mp32"), \
             patch.object(api, "to_thread", side_effect=fake_to_thread):
            response = await api.h_artist(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["artist"]["name"], "Fallback Artist")
        self.assertEqual(payload["artist"]["cover"], "https://img.test/track.jpg")
        self.assertEqual(payload["tracks"], tracks)

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

    async def test_health_reports_jamendo_disabled_without_commercial_approval(self):
        with (
            patch.object(api.P, "jamendo_enabled", return_value=False),
            patch.object(api.P, "audius_host", return_value="https://audius.example"),
            patch.object(api.P, "source_status", return_value={"jamendo": {"available": False}, "audius": {"available": True}}),
        ):
            response = await api.h_health(SimpleNamespace())
        payload = json.loads(response.text)
        self.assertFalse(payload["sources"]["jamendo"])
        self.assertTrue(payload["sources"]["audius"])

    async def test_discover_merges_and_deduplicates_artist_and_album_catalogues(self):
        request = SimpleNamespace()
        async def fake_to_thread(fn, *args, **kwargs):
            query = args[0]
            if fn is api.P.search_artists:
                return [
                    {"id": "aud:1", "name": "Artist One"},
                    {"id": "mb:artist:2", "name": "Artist Two"},
                ] if query == "pop" else [
                    {"id": "aud:1", "name": "Artist One"},
                    {"id": "jam:3", "name": "Artist Three"},
                ]
            if fn is api.P.search_albums:
                return [
                    {"id": "jam:10", "title": "Album One"},
                    {"id": "aud:11", "title": "Album Two"},
                ] if query == "pop" else [
                    {"id": "jam:10", "title": "Album One"},
                    {"id": "aud:12", "title": "Album Three"},
                ]
            raise AssertionError(f"Unexpected provider function: {fn}")

        with (
            patch.object(api, "auth_user", return_value={"uid": 123}),
            patch.object(api, "to_thread", side_effect=fake_to_thread),
        ):
            response = await api.h_discover(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual([x["id"] for x in payload["artists"]],
                         ["aud:1", "mb:artist:2", "jam:3"])
        self.assertEqual([x["id"] for x in payload["albums"]],
                         ["jam:10", "aud:11", "aud:12"])


    async def test_musicbrainz_artist_detail_strips_artist_id_prefix(self):
        request = SimpleNamespace(match_info={"aid": "mb:artist:11111111-1111-4111-8111-111111111111"})
        artist = {
            "id": "mb:artist:11111111-1111-4111-8111-111111111111",
            "name": "Metadata Artist",
            "source_url": "https://musicbrainz.org/artist/11111111-1111-4111-8111-111111111111",
        }
        async def fake_to_thread(fn, *args, **kwargs):
            if fn.__name__ == "artist_by_id":
                self.assertEqual(args[0], "11111111-1111-4111-8111-111111111111")
                return artist
            if fn.__name__ == "artist_release_groups":
                self.assertEqual(args[0], "11111111-1111-4111-8111-111111111111")
                return [{"id": "mb:release-group:1", "title": "First Album"}]
            raise AssertionError(f"Unexpected function: {fn}")
        with (
            patch.object(api, "auth_user", return_value={"uid": 123}),
            patch.object(api, "quality_for", return_value="mp32"),
            patch.object(api, "to_thread", side_effect=fake_to_thread),
        ):
            response = await api.h_artist(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["metadata_only"])
        self.assertEqual(payload["artist"]["name"], "Metadata Artist")
        self.assertEqual(payload["releases"][0]["title"], "First Album")


    async def test_charts_endpoint_returns_provider_chart_sections(self):
        request = SimpleNamespace()
        async def fake_to_thread(fn, *args, **kwargs):
            if fn is api.P.audius_trending:
                return [{"id": "aud:1", "title": "Trending Track"}]
            if fn is api.P.jamendo_tag_tracks:
                self.assertIn(args[0], ("pop", "rock"))
                suffix = "1" if args[0] == "pop" else "2"
                return [{"id": "jam:" + suffix, "title": "Popular " + args[0].title() + " Track"}]
            raise AssertionError(f"Unexpected provider function: {fn}")
        with (
            patch.object(api, "auth_user", return_value={"uid": 123}),
            patch.object(api, "quality_for", return_value="mp32"),
            patch.object(api, "to_thread", side_effect=fake_to_thread),
        ):
            response = await api.h_charts(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual([x["id"] for x in payload["charts"]],
                         ["global-trending", "electronic-trending", "popular-pop", "popular-rock"])
        self.assertEqual(payload["charts"][0]["tracks"][0]["id"], "aud:1")
        self.assertEqual(payload["charts"][2]["tracks"][0]["id"], "jam:1")
        self.assertEqual(payload["charts"][3]["tracks"][0]["id"], "jam:2")


    def test_connect_routes_are_registered(self):
        app = api.build_app()
        paths = {route.resource.canonical for route in app.router.routes()}
        self.assertTrue({
            "/api/connect/ws", "/api/connect/devices",
            "/api/queue", "/api/collection", "/api/search-history",
            "/api/release-radar", "/api/library/import",
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


class MusicCatalogueRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_album_search_uses_merged_provider_catalogue(self):
        request = SimpleNamespace(
            query={"q": "Example Album", "type": "albums", "limit": "10", "sort": "relevance", "sources": ""},
            headers={},
        )
        albums = [{"id": "aud:album-1", "title": "Example Album", "source": "audius"}]
        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        with (
            patch.object(api, "auth_user", return_value={"uid": 7}),
            patch.object(api.db, "limits_for", return_value={"search_results_max": 30}),
            patch.object(api.db, "bump_counter", return_value=(True, 2, 20)),
            patch.object(api.db, "search_history_add", return_value=True),
            patch.object(api, "quality_for", return_value="high"),
            patch.object(api, "to_thread", side_effect=inline_to_thread),
            patch.object(api.P, "search_albums_smart", return_value=albums) as search,
        ):
            response = await api.h_search(request)
        payload = json.loads(response.text)
        self.assertEqual(payload["results"], albums)
        search.assert_called_once_with("Example Album", 10, "relevance")

    async def test_audius_album_route_returns_album_tracks(self):
        request = SimpleNamespace(match_info={"aid": "aud:album-1"}, headers={})
        tracks = [{"id": "aud:track-1", "title": "Track", "album": "Album"}]
        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        with (
            patch.object(api, "auth_user", return_value={"uid": 7}),
            patch.object(api, "quality_for", return_value="high"),
            patch.object(api, "to_thread", side_effect=inline_to_thread),
            patch.object(api.P, "audius_album_tracks", return_value=tracks) as album_tracks,
        ):
            response = await api.h_album(request)
        payload = json.loads(response.text)
        self.assertEqual(payload["tracks"], tracks)
        self.assertEqual(payload["album"]["id"], "aud:album-1")
        album_tracks.assert_called_once_with("album-1")


class SearchRouteTests(unittest.TestCase):
    def test_search_handler_requires_auth(self):
        """Регресія: h_search лишився без @require_auth, і пошук падав з TypeError."""
        import inspect
        params = list(inspect.signature(api.h_search).parameters)
        self.assertEqual(params, ["request"], "h_search має бути обгорнутий require_auth")