import os
import unittest
from unittest.mock import Mock, patch

from core.apple_music import (
    AppleMusicError, apple_catalog_configured, apple_catalog_search,
)


class AppleMusicCatalogTests(unittest.TestCase):
    def test_not_configured_without_developer_token(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(apple_catalog_configured())
            with self.assertRaises(AppleMusicError):
                apple_catalog_search("test")

    def test_search_normalises_catalogue_without_exposing_preview_stream(self):
        payload = {
            "results": {
                "songs": {"data": [{
                    "id": "123",
                    "attributes": {
                        "name": "Example Song",
                        "artistName": "Example Artist",
                        "albumName": "Example Album",
                        "url": "https://music.apple.com/pl/album/example/123",
                        "artwork": {"url": "https://img.example/{w}x{h}bb.{f}"},
                        "durationInMillis": 182000,
                        "previews": [{"url": "https://audio.example/preview.m4a"}],
                    },
                }]},
                "albums": {"data": [{
                    "id": "456",
                    "attributes": {"name": "Example Album", "artistName": "Example Artist"},
                }]},
                "artists": {"data": [{
                    "id": "789",
                    "attributes": {"name": "Example Artist"},
                }]},
            }
        }
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": "test-token"}):
            with patch("core.apple_music.requests.get", return_value=response) as get:
                found = apple_catalog_search("Example", "pl", 10)
        self.assertEqual([x["kind"] for x in found], ["song", "album", "artist"])
        self.assertEqual(found[0]["id"], "apple_music:song:123")
        self.assertEqual(found[0]["artwork"], "https://img.example/300x300bb.jpg")
        self.assertNotIn("stream", found[0])
        self.assertNotIn("previews", found[0])
        self.assertNotIn("audio.example", repr(found))
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.music.apple.com/v1/catalog/pl/search")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(kwargs["params"]["types"], "songs,albums,artists")
        self.assertLessEqual(kwargs["params"]["limit"], 25)

    def test_invalid_storefront_is_rejected_before_network_call(self):
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": "test-token"}):
            with patch("core.apple_music.requests.get") as get:
                with self.assertRaises(ValueError):
                    apple_catalog_search("Example", "../", 10)
                get.assert_not_called()

    def test_upstream_errors_are_wrapped(self):
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": "test-token"}):
            with patch("core.apple_music.requests.get", side_effect=__import__(
                    "requests").Timeout("timeout")):
                with self.assertRaises(AppleMusicError):
                    apple_catalog_search("Example")


if __name__ == "__main__":
    unittest.main()
