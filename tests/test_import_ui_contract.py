import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "webapp" / "v3.js").read_text(encoding="utf-8")


def preview_src():
    a = JS.index("function showImportPreview")
    return JS[a:JS.index("async function renderRecommendations")]


def handler_src():
    a = JS.index('$("v3ImportFile").onchange=')
    return JS[a:JS.index("\n", a)]


class ImportUiContract(unittest.TestCase):
    def test_file_selection_only_requests_preview_and_creates_nothing(self):
        h = handler_src()
        self.assertIn("preview:true", h)
        self.assertIn("showImportPreview(", h)
        self.assertNotIn("name:", h)  # створення плейлиста лише після підтвердження

    def test_playlist_created_only_on_confirm(self):
        s = preview_src()
        i = s.index("v3ImportConfirm").__index__()
        self.assertIn('post("/api/library/import",{csv,accept:accepted(),name:', s[i:])
        self.assertNotIn("preview:true", s)

    def test_uncertain_requires_explicit_checkbox_and_is_unchecked_by_default(self):
        s = preview_src()
        self.assertIn('data-accept-row', s)
        self.assertIn('type="checkbox"', s)
        self.assertNotIn('checked>', s.split('data-accept-row')[1][:200])

    def test_confirm_disabled_without_matches(self):
        self.assertIn('(found?"":" disabled")', preview_src())

    def test_all_statuses_are_shown_and_uncertain_is_not_imported_silently(self):
        s = preview_src()
        for status in ("matched", "uncertain", "not_found", "invalid"):
            self.assertIn(status, s)
        self.assertIn("не буде імпортовано", s)

    def test_cancel_clears_and_user_text_is_escaped(self):
        s = preview_src()
        self.assertIn('host.innerHTML=""', s)
        for expr in ("E(x.title", "E(x.artist", "E(name)", "E(e.message)"):
            self.assertIn(expr, s)

    def test_no_browser_storage_and_no_protected_player_code(self):
        s = preview_src() + handler_src()
        self.assertNotIn("localStorage", s)
        for protected in ("startDeviceSync(", "connectDeviceSocket(", "publishDeviceState(", "function togglePlay("):
            self.assertNotIn(protected, s)


if __name__ == "__main__":
    unittest.main()
