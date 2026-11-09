import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "v3.css").read_text(encoding="utf-8")


def playlist_src():
    a = JS.index("async function playlist(id)")
    m = re.search(r"\nasync function |\nfunction ", JS[a + 10:])
    return JS[a:a + 10 + m.start()] if m else JS[a:]


class PlaylistUiContract(unittest.TestCase):
    def test_edit_uses_server_update_with_all_fields(self):
        src = playlist_src()
        self.assertIn('action:"update"', src)
        for field in ("name", "description", "is_public"):
            self.assertIn(field, src)
        self.assertIn("v3EditBox", src)

    def test_reorder_goes_to_server_and_not_localstorage(self):
        src = playlist_src()
        self.assertIn('action:"reorder"', src)
        self.assertIn("order:ids", src)
        self.assertNotIn("localStorage", src)

    def test_move_buttons_do_not_trigger_playback(self):
        self.assertIn("[data-move-playlist-track]", JS[JS.index("function bindRows"):JS.index("function nav(")])

    def test_user_text_is_escaped(self):
        src = playlist_src()
        self.assertIn("E(p.description", src)
        self.assertIn("E(p.name", src)

    def test_edge_buttons_not_rendered(self):
        src = playlist_src()
        self.assertIn("i>0?", src)
        self.assertIn("i<list.length-1?", src)

    def test_css_present_and_form_hidden_by_default(self):
        for sel in (".v3-pl-edit", ".v3-pl-move", ".v3-pl-desc"):
            self.assertIn(sel, CSS)
        self.assertIn("hidden", playlist_src())

    def test_player_and_connect_untouched(self):
        self.assertNotIn("connect", playlist_src().lower().replace("setqueue", ""))


if __name__ == "__main__":
    unittest.main()