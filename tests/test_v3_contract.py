import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3_JS = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")
V3_CSS = (ROOT / "webapp" / "v3.css").read_text(encoding="utf-8")


class V3ContractTests(unittest.TestCase):
    def test_unified_search_renders_all_result_types(self):
        self.assertIn('st.kind==="all"', V3_JS)
        for key in ("results.tracks", "results.artists", "results.albums"):
            self.assertIn(key, V3_JS)
        self.assertIn('section(tr("search_tracks"', V3_JS)
        self.assertIn('collectionRows(artists.slice(0,8),"artists")', V3_JS)
        self.assertIn('collectionRows(albums.slice(0,8),"albums")', V3_JS)

    def test_artist_and_album_rows_keep_their_kind(self):
        self.assertIn('data-collection-kind=', V3_JS)
        self.assertIn('b.dataset.collectionKind', V3_JS)
        self.assertIn('kind==="artists"?"artist":"album"', V3_JS)

    def test_search_ignores_stale_success_and_error_responses(self):
        self.assertIn("if(seq!==st.seq)return;", V3_JS)
        self.assertIn("if(seq==st.seq)", V3_JS)

    def test_v3_uses_existing_translation_keys(self):
        for key in ("nav_home", "nav_search", "nav_lib", "nav_me",
                    "search_all", "search_tracks", "search_artists",
                    "search_albums", "lib_liked", "lib_playlists",
                    "lib_history", "lib_moments"):
            self.assertIn('tr("'+key+'"', V3_JS)

    def test_v3_refreshes_when_account_language_changes(self):
        self.assertIn("function refreshLocale()", V3_JS)
        self.assertIn("new MutationObserver", V3_JS)
        self.assertIn('attributeFilter:["lang"]', V3_JS)

    def test_theme_tokens_are_bridged_to_v3(self):
        for token in ("--bg", "--panel", "--line", "--ink", "--muted", "--accent"):
            self.assertIn("var("+token, V3_CSS)
        self.assertIn("--v3-bg", V3_CSS)
        self.assertIn("--v3-accent", V3_CSS)

    def test_connect_and_legacy_player_are_not_embedded_in_v3(self):
        for protected in ("startDeviceSync(", "connectDeviceSocket(",
                          "publishDeviceState(", "claimLocalPlayback(",
                          "function togglePlay(", "const A = new Audio()"):
            self.assertNotIn(protected, V3_JS)


if __name__ == "__main__":
    unittest.main()
