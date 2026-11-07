import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")
API_TREE = ast.parse(API_SOURCE)


class ReleaseRadarContractTests(unittest.TestCase):
    def test_release_radar_uses_bounded_concurrent_provider_lookups(self):
        function = next(
            node for node in API_TREE.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "h_release_radar_get"
        )
        source = ast.get_source_segment(API_SOURCE, function)
        self.assertIn("asyncio.Semaphore(5)", source)
        self.assertIn("asyncio.gather(", source)
        self.assertIn("await to_thread(P.search_albums_smart", source)

    def test_release_radar_isolates_provider_failures_per_artist(self):
        function = next(
            node for node in API_TREE.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "h_release_radar_get"
        )
        source = ast.get_source_segment(API_SOURCE, function)
        self.assertIn("except Exception as exc:", source)
        self.assertIn("return []", source)
        self.assertIn('logger.warning("Release Radar lookup failed', source)


if __name__ == "__main__":
    unittest.main()
