import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3_JS = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")
V3_CSS = (ROOT / "webapp" / "v3.css").read_text(encoding="utf-8")
LEGACY_CSS = (ROOT / "webapp" / "legacy.css").read_text(encoding="utf-8")


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

    def test_v3_page_copy_uses_shared_translation_catalog(self):
        for key in ("hero_title_a", "hero_title_b", "hero_desc", "hero_cta",
                    "onboard_body", "home_collections", "h_mood",
                    "search_genres", "lib_create_playlist", "pr_stats"):
            self.assertIn('tr("'+key+'"', V3_JS)

    def test_theme_tokens_are_bridged_to_v3(self):
        for token in ("--bg", "--panel", "--line", "--ink", "--muted", "--accent"):
            self.assertIn("var("+token, V3_CSS)
        self.assertIn("--v3-bg", V3_CSS)
        self.assertIn("--v3-accent", V3_CSS)

    def test_legacy_defaults_do_not_lock_account_theme_palette(self):
        self.assertIn("[data-theme]{", LEGACY_CSS)
        theme_defaults = LEGACY_CSS.split("[data-theme]{", 1)[1].split("}", 1)[0]
        self.assertNotIn("!important", theme_defaults)

    def test_home_reloads_after_v3_shell_rebuild(self):
        self.assertIn('if(page==="home"&&!$("v3-home")?.querySelector(".v3-hero"))home();', V3_JS)
        self.assertIn('else{nav("home");}', V3_JS)

    def test_locale_rebuild_preserves_legacy_connect_controls(self):
        self.assertIn('const preservedLegacy=["topProfile","planChip","deviceChip"]', V3_JS)
        self.assertIn("preservedLegacy.forEach(node=>node.remove())", V3_JS)
        self.assertIn('preservedLegacy.forEach((n,i)=>{const slot=$(["v3AvatarSlot","v3PlanSlot","v3DeviceSlot"][i]);if(n&&slot)slot.appendChild(n);})', V3_JS)

    def test_playlist_track_removal_control_is_rendered_and_does_not_play(self):
        self.assertIn('data-remove-playlist-track=', V3_JS)
        self.assertIn('if(e.target.closest("[data-remove-playlist-track]"))return;', V3_JS)
        self.assertIn('data-remove-playlist-track]', V3_JS)

    def test_connect_and_legacy_player_are_not_embedded_in_v3(self):
        for protected in ("startDeviceSync(", "connectDeviceSocket(",
                          "publishDeviceState(", "claimLocalPlayback(",
                          "function togglePlay(", "const A = new Audio()"):
            self.assertNotIn(protected, V3_JS)


if __name__ == "__main__":
    unittest.main()

class CatalogUiContractTests(unittest.TestCase):
    def test_availability_and_related_sources_are_rendered(self):
        self.assertIn("d.availability", V3_JS)
        self.assertIn("d.catalog", V3_JS)
        self.assertIn("Доступно для відтворення", V3_JS)
        self.assertIn("Також у:", V3_JS)

    def test_no_play_button_without_playable_audio(self):
        self.assertIn("noPlay", V3_JS)
        self.assertIn("if(!metadataAlbum&&!noPlay)", V3_JS)

    def test_player_and_connect_untouched_by_catalog_ui(self):
        for forbidden in ("connect/ws", "Hub(", "playerWidget"):
            self.assertNotIn(forbidden, V3_JS)


class RecommendationsAndPrivacyUiTests(unittest.TestCase):
    def test_recommendation_and_privacy_endpoints_are_used(self):
        for needle in ("/api/recommendations", "/api/recommendations/hide", "/api/me/export", "/api/me/delete"):
            self.assertIn(needle, V3_JS)

    def test_delete_requires_confirmation(self):
        self.assertIn("window.confirm", V3_JS)
        self.assertIn('confirm:"DELETE"', V3_JS)

    def test_routes_registered_in_api(self):
        api = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")
        for route in ('"/api/recommendations"', '"/api/recommendations/hide"', '"/api/me/export"', '"/api/me/delete"'):
            self.assertIn(route, api)