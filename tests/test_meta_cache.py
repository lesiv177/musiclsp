import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core import meta_cache as MC

API = (Path(__file__).resolve().parents[1] / "services" / "bot" / "api.py").read_text(encoding="utf-8")


class MetaCacheTests(unittest.TestCase):
    def setUp(self):
        MC.clear()

    def test_second_call_hits_cache(self):
        calls = []
        loader = lambda: calls.append(1) or {"name": "A"}
        self.assertEqual(MC.cached("ns", "1", loader), {"name": "A"})
        self.assertEqual(MC.cached("ns", "1", loader), {"name": "A"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(MC.stats()["hits"], 1)

    def test_namespaces_and_keys_are_separate(self):
        MC.cached("a", "1", lambda: "x")
        self.assertEqual(MC.cached("b", "1", lambda: "y"), "y")
        self.assertEqual(MC.cached("a", "2", lambda: "z"), "z")

    def test_ttl_expiry(self):
        calls = []
        now = [1000.0]
        with patch.object(MC, "_now", lambda: now[0]):
            MC.cached("ns", "k", lambda: calls.append(1) or {"v": 1}, ttl=10)
            now[0] += 5
            MC.cached("ns", "k", lambda: calls.append(1) or {"v": 1}, ttl=10)
            self.assertEqual(len(calls), 1)
            now[0] += 6
            MC.cached("ns", "k", lambda: calls.append(1) or {"v": 1}, ttl=10)
            self.assertEqual(len(calls), 2)

    def test_empty_result_uses_short_negative_ttl(self):
        calls = []
        now = [0.0]
        with patch.object(MC, "_now", lambda: now[0]):
            MC.cached("ns", "k", lambda: calls.append(1), ttl=900, negative_ttl=30)
            now[0] += 10
            MC.cached("ns", "k", lambda: calls.append(1), ttl=900, negative_ttl=30)
            self.assertEqual(len(calls), 1)
            now[0] += 25
            MC.cached("ns", "k", lambda: calls.append(1), ttl=900, negative_ttl=30)
            self.assertEqual(len(calls), 2)

    def test_exceptions_are_not_cached(self):
        def boom():
            raise RuntimeError("provider down")
        with self.assertRaises(RuntimeError):
            MC.cached("ns", "k", boom)
        self.assertEqual(MC.cached("ns", "k", lambda: {"ok": True}), {"ok": True})

    def test_callers_cannot_mutate_cached_value(self):
        first = MC.cached("ns", "k", lambda: {"tags": ["a"]})
        first["tags"].append("hacked")
        second = MC.cached("ns", "k", lambda: {"tags": ["zzz"]})
        self.assertEqual(second, {"tags": ["a"]})
        second["tags"].append("again")
        self.assertEqual(MC.cached("ns", "k", lambda: None), {"tags": ["a"]})

    def test_single_flight_for_concurrent_requests(self):
        calls, gate = [], threading.Event()

        def slow():
            calls.append(1)
            gate.wait(2)
            return {"v": 1}

        results = []
        threads = [threading.Thread(target=lambda: results.append(MC.cached("ns", "k", slow))) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.15)
        gate.set()
        for t in threads:
            t.join(3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 8)

    def test_size_is_bounded_and_lru(self):
        with patch.object(MC, "MAX_ENTRIES", 3):
            for i in range(3):
                MC.cached("ns", i, lambda i=i: {"i": i})
            MC.cached("ns", 0, lambda: {"i": "fresh"})  # touch 0 -> most recent
            MC.cached("ns", 3, lambda: {"i": 3})        # evicts 1
            self.assertEqual(MC.stats()["size"], 3)
            calls = []
            MC.cached("ns", 1, lambda: calls.append(1) or {"i": 1})
            self.assertEqual(len(calls), 1)

    def test_invalidate(self):
        MC.cached("ns", "k", lambda: {"v": 1})
        MC.invalidate("ns", "k")
        self.assertEqual(MC.cached("ns", "k", lambda: {"v": 2}), {"v": 2})


class ApiWiringContract(unittest.TestCase):
    def test_profiles_are_cached(self):
        for ns in ("jam:artist", "aud:artist", "scu:artist"):
            self.assertIn(f'MC.cached, "{ns}"', API)

    def test_tracks_and_streams_are_never_cached(self):
        # URL потоків/треки залежать від якості, ліцензій і спливають — не кешуємо
        banned = ("tracks", "stream", "get_track", "resolve")
        for line in API.splitlines():
            if "MC.cached" in line:
                self.assertFalse(any(word in line for word in banned), line.strip())


if __name__ == "__main__":
    unittest.main()