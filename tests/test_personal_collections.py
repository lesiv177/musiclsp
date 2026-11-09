import unittest
from pathlib import Path

from core import personal_collections as PC

API = (Path(__file__).resolve().parents[1] / "services" / "bot" / "api.py").read_text(encoding="utf-8")


def T(i, artist="Echo", title=None):
    return {"id": f"aud:{artist}{i}", "title": title or f"Song {i}", "artist": artist}


class PersonalCollectionsTests(unittest.TestCase):
    def test_no_data_is_honestly_empty(self):
        out = PC.build([], [])
        self.assertFalse(out["enough_data"])
        self.assertEqual(out["collections"], [])
        self.assertTrue(out["reason"])

    def test_too_few_tracks_per_artist_gives_nothing(self):
        out = PC.build([T(1), T(2), T(1, "Other")], [])
        self.assertFalse(out["enough_data"])

    def test_artist_mix_requires_three_distinct_tracks(self):
        out = PC.build([T(1), T(2)], [T(3)])
        self.assertTrue(out["enough_data"])
        c = out["collections"][0]
        self.assertEqual((c["kind"], c["title"]), ("artist_mix", "Більше від Echo"))
        self.assertEqual(len(c["tracks"]), 3)

    def test_duplicates_across_providers_count_once(self):
        a = {"id": "aud:1", "title": "Song", "artist": "Echo"}
        b = {"id": "jam:9", "title": "Song", "artist": "echo"}
        out = PC.build([a, b, T(2), ], [])
        self.assertFalse(out["enough_data"])  # лише 2 різні треки

    def test_hidden_tracks_are_excluded(self):
        from core.recommend import track_key
        lib = [T(1), T(2), T(3)]
        out = PC.build(lib, [], hidden={track_key(lib[0])})
        self.assertFalse(out["enough_data"])

    def test_forgotten_favorites(self):
        lib = [T(i, f"A{i}") for i in range(6)]
        hist = [lib[0]]
        out = PC.build(lib, hist)
        c = [x for x in out["collections"] if x["kind"] == "forgotten_favorites"][0]
        self.assertEqual(len(c["tracks"]), 5)
        self.assertNotIn(lib[0]["id"], [t["id"] for t in c["tracks"]])

    def test_limits(self):
        lib = [T(i, f"Artist{a}") for a in range(10) for i in range(5)]
        out = PC.build(lib, [])
        self.assertLessEqual(len(out["collections"]), PC.MAX_COLLECTIONS)
        for c in out["collections"]:
            self.assertLessEqual(len(c["tracks"]), PC.MAX_TRACKS)

    def test_invalid_items_ignored(self):
        out = PC.build([None, {}, {"id": "x"}, "str"], [{"title": "no id"}])
        self.assertFalse(out["enough_data"])

    def test_route_is_auth_protected_and_registered(self):
        self.assertIn('r.add_get("/api/personal-collections", h_personal_collections)', API)
        i = API.index("async def h_personal_collections")
        self.assertIn("@require_auth", API[i - 40:i])


if __name__ == "__main__":
    unittest.main()