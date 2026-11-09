import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


def fn(name):
    for node in ast.parse(API).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(API, node)
    raise AssertionError(name)


class PlaylistApiContract(unittest.TestCase):
    def test_no_unchecked_int_pid_or_body_indexing(self):
        src = fn("h_playlists_post")
        self.assertNotIn('int(body["pid"])', src)
        self.assertNotIn('body["track"]', src)
        self.assertIn("_pid(body)", src)

    def test_actions_present(self):
        src = fn("h_playlists_post")
        for action in ("update", "reorder", "copy_shared", "create", "delete", "add_track", "remove_track"):
            self.assertIn(f'"{action}"', src)

    def test_new_playlists_private_and_shared_hides_private(self):
        self.assertIn("is_public=False", fn("h_playlists_post"))
        self.assertIn("PLX.public_view", fn("h_shared"))
        self.assertNotIn('"playlist": p}', fn("h_shared"))

    def test_frontend_copy_shared_uses_code_and_safe_attribute(self):
        self.assertIn('action:"copy_shared", code', HTML)
        self.assertIn('data-code="${esc(String(code))}"', HTML)
        self.assertNotIn("copyShared(${p.id})", HTML)


if __name__ == "__main__":
    unittest.main()