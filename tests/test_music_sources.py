import os
import unittest
from unittest.mock import patch

from core.music_sources import OPTIONAL_SOURCES, available_sources, source_status


class OptionalMusicSourceRegistryTests(unittest.TestCase):
    def test_optional_sources_are_disabled_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(available_sources(), ())

    def test_apple_music_requires_token_and_implemented_adapter(self):
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": "test-token"}, clear=True):
            status = source_status()["apple_music"]
            self.assertTrue(status["configured"])
            self.assertTrue(status["catalogue_implemented"])
            self.assertFalse(status["implemented"])
            self.assertFalse(status["available"])
            self.assertEqual(available_sources(), ())

    def test_commercial_sources_require_adapter_and_explicit_approval(self):
        env = {
            "TIDAL_CLIENT_ID": "id",
            "TIDAL_CLIENT_SECRET": "secret",
            "SEVENDIGITAL_API_KEY": "key",
            "SEVENDIGITAL_PARTNER_ID": "partner",
            "AMAZON_MUSIC_CLIENT_ID": "id",
            "AMAZON_MUSIC_CLIENT_SECRET": "secret",
            "EPIDEMIC_SOUND_API_TOKEN": "token",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(available_sources(), ())
            for key in ("TIDAL", "SEVEN_DIGITAL", "AMAZON_MUSIC", "EPIDEMIC_SOUND"):
                os.environ[f"{key}_APPROVED"] = "true"
            status = source_status()
            for key in ("tidal", "seven_digital", "amazon_music", "epidemic_sound"):
                self.assertTrue(status[key]["configured"])
                self.assertFalse(status[key]["implemented"])
                self.assertFalse(status[key]["available"])
            self.assertEqual(available_sources(), ())

    def test_status_never_exposes_credentials(self):
        with patch.dict(os.environ, {"APPLE_MUSIC_DEVELOPER_TOKEN": "do-not-leak"}, clear=True):
            status = source_status()
        self.assertNotIn("do-not-leak", repr(status))
        self.assertTrue(status["apple_music"]["configured"])
        self.assertFalse(status["apple_music"]["available"])

    def test_registry_has_unique_keys(self):
        keys = [source.key for source in OPTIONAL_SOURCES]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
