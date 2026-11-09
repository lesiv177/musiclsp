import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "v3.css").read_text(encoding="utf-8")


class RadarUiContract(unittest.TestCase):
    def radar_src(self):
        a = JS.index("async function renderReleaseRadar")
        return JS[a:JS.index("async function renderRecommendations")]

    def test_period_param_and_all_periods_present(self):
        src = self.radar_src()
        self.assertIn('/api/release-radar?period="', src)
        for key in ("week", "month", "quarter"):
            self.assertIn(f'["{key}"', JS)

    def test_seen_is_posted_to_server_not_localstorage(self):
        src = self.radar_src()
        self.assertIn("/api/release-radar/seen", src)
        self.assertNotIn("localStorage", src)

    def test_artist_names_are_escaped(self):
        src = self.radar_src()
        self.assertIn("E(x.release_date)" if "E(x.release_date)" in src else "E(String(x.release_date)", src)
        self.assertIn("E(x.artist_name", src)

    def test_no_stray_closing_div_without_artists(self):
        src = self.radar_src()
        self.assertNotIn('.join(""):"")+\'</div>\'', src)

    def test_css_classes_defined(self):
        for cls in (".v3-new-badge", ".v3-release-date", ".v3-count", ".v3-pill.active"):
            self.assertIn(cls, CSS)

    def test_empty_state_is_honest_about_dates(self):
        self.assertIn("підтвердженою датою", self.radar_src())


if __name__ == "__main__":
    unittest.main()