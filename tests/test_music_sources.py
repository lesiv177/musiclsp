import os
import unittest
from unittest.mock import patch

from core.music_sources import MUSIC_SOURCES, available_sources, source_status


class MusicSourceRegistryTests(unittest.TestCase):
    def test_registry_contains_only_implemented_integrations(self):
        keys = [source.key for source in MUSIC_SOURCES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), {
            "audius", "archive", "ccmixter", "openverse", "wikimedia",
            "jamendo", "soundcloud", "youtube",
        })
        self.assertTrue(all(source.implemented for source in MUSIC_SOURCES))

    def test_commercial_providers_require_credentials_and_explicit_approval(self):
        with patch.dict(os.environ, {}, clear=True):
            status = source_status()
        self.assertFalse(status["jamendo"]["available"])
        self.assertFalse(status["soundcloud"]["available"])
        self.assertFalse(status["youtube"]["available"])
        self.assertTrue(status["audius"]["available"])
        self.assertTrue(status["archive"]["available"])
        self.assertTrue(status["ccmixter"]["available"])
        self.assertTrue(status["openverse"]["available"])
        self.assertTrue(status["wikimedia"]["available"])

    def test_jamendo_requires_client_id_and_approval(self):
        env = {"JAMENDO_CLIENT_ID": "test-id", "JAMENDO_COMMERCIAL_APPROVED": "true"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(source_status()["jamendo"]["available"])
            self.assertIn("jamendo", available_sources())

    def test_soundcloud_requires_credentials_and_approval(self):
        env = {
            "SOUNDCLOUD_CLIENT_ID": "test-id",
            "SOUNDCLOUD_CLIENT_SECRET": "test-secret",
            "SOUNDCLOUD_AGGREGATION_APPROVED": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(source_status()["soundcloud"]["available"])
            self.assertIn("soundcloud", available_sources())

    def test_soundcloud_registry_exposes_artist_catalogue(self):
        soundcloud = next(source for source in MUSIC_SOURCES if source.key == "soundcloud")
        self.assertIn("tracks", soundcloud.catalogue_types)
        self.assertIn("artists", soundcloud.catalogue_types)

    def test_archive_can_be_disabled(self):
        with patch.dict(os.environ, {"ARCHIVE_ENABLED": "0"}, clear=True):
            self.assertFalse(source_status()["archive"]["available"])

    def test_youtube_is_catalogue_only_and_needs_api_key(self):
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test-key"}, clear=True):
            status = source_status()["youtube"]
        self.assertTrue(status["available"])
        self.assertEqual(status["playback_mode"], "official_embed")
        self.assertEqual(status["catalogue_types"], ["tracks"])

    def test_status_never_exposes_credentials(self):
        with patch.dict(os.environ, {
            "JAMENDO_CLIENT_ID": "private-id",
            "SOUNDCLOUD_CLIENT_SECRET": "private-secret",
        }, clear=True):
            status = source_status()
        self.assertNotIn("private-id", repr(status))
        self.assertNotIn("private-secret", repr(status))


if __name__ == "__main__":
    unittest.main()