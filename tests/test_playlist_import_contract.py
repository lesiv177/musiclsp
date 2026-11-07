import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")
API_TREE = ast.parse(API_SOURCE)


class PlaylistImportContractTests(unittest.TestCase):
    def test_import_searches_are_bounded_and_concurrent(self):
        handler = next(
            node for node in API_TREE.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "h_import_playlist"
        )
        source = ast.get_source_segment(API_SOURCE, handler)
        self.assertIn("asyncio.Semaphore(5)", source)
        self.assertIn("asyncio.gather(", source)
        self.assertIn("async with semaphore:", source)

    def test_import_does_not_create_empty_playlist_when_no_tracks_match(self):
        handler = next(
            node for node in API_TREE.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "h_import_playlist"
        )
        source = ast.get_source_segment(API_SOURCE, handler)
        self.assertLess(source.index("if not candidates:"), source.index("db.pl_create"))
        self.assertIn("плейлист не створено", source)

    def test_import_deduplicates_resolved_track_ids(self):
        handler = next(
            node for node in API_TREE.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "h_import_playlist"
        )
        source = ast.get_source_segment(API_SOURCE, handler)
        self.assertIn("seen_ids = set()", source)
        self.assertIn("track_id in seen_ids", source)


if __name__ == "__main__":
    unittest.main()
