import unittest
from unittest.mock import patch

from core import providers as P


def run_jobs_inline(jobs, timeout=4):
    """Run mocked provider jobs in their declared order, without network I/O."""
    results = []
    for job in jobs:
        results.extend(job() or [])
    return results


class SoundCloudPriorityTests(unittest.TestCase):
    def _empty_provider_patches(self):
        return [
            patch.object(P, "audius_search_tracks", return_value=[]),
            patch.object(P, "archive_search", return_value=[]),
            patch.object(P, "ccmixter_search", return_value=[]),
            patch.object(P, "openverse_search", return_value=[]),
            patch.object(P, "wikimedia_search", return_value=[]),
        ]

    def test_soundcloud_results_come_first_in_default_search(self):
        soundcloud = {"id": "sc:1", "title": "Track SC", "artist": "Artist"}
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist"}
        patches = [
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search", return_value=[soundcloud]),
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
            *self._empty_provider_patches(),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            results = P.search_tracks("Artist", limit=10)
        self.assertEqual([track["id"] for track in results], ["sc:1", "jam:2"])

    def test_soundcloud_wins_duplicate_track(self):
        soundcloud = {"id": "sc:1", "title": "Same Track", "artist": "Same Artist"}
        jamendo = {"id": "jam:2", "title": "Same Track", "artist": "Same Artist"}
        patches = [
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search", return_value=[soundcloud]),
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
            *self._empty_provider_patches(),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            results = P.search_tracks("Same Track", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "sc:1")

    def test_explicit_provider_filter_is_respected(self):
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist"}
        with (
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "soundcloud_search") as soundcloud_search,
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
        ):
            results = P.search_tracks("Artist", limit=10, sources=["jamendo"])
        soundcloud_search.assert_not_called()
        self.assertEqual([track["id"] for track in results], ["jam:2"])


if __name__ == "__main__":
    unittest.main()
