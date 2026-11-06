import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "webapp" / "index.html"
HTML = HTML_PATH.read_text(encoding="utf-8")
SCRIPTS = re.findall(r"<script\b[^>]*>(.*?)</script\s*>", HTML, flags=re.I | re.S)
JS = SCRIPTS[-1] if SCRIPTS else ""
CSS_LINKS = re.findall(r'href="([^"]+\.css)(?:\?[^"]*)?"', HTML, flags=re.I)


class MiniAppContractTests(unittest.TestCase):
    """Static interaction contract checks for every declared Mini App control."""

    @classmethod
    def setUpClass(cls):
        cls.functions = set(re.findall(
            r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", JS
        ))
        cls.functions.update(re.findall(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
            JS
        ))

    def test_inline_script_exists(self):
        self.assertTrue(JS.strip(), "Main Mini App inline script is missing")

    def test_all_inline_button_handlers_resolve(self):
        handlers = set()
        for attr in re.findall(r"\bonclick\s*=\s*(['\"])(.*?)\1", HTML, re.I | re.S):
            expression = attr[1]
            # Only inspect direct calls; control-flow expressions may contain several.
            handlers.update(re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", expression))
        ignored = {"if", "closeModal", "JSON", "String", "Number", "Math"}
        missing = sorted(name for name in handlers - ignored
                         if name not in self.functions and name not in JS)
        self.assertEqual(missing, [], "Unresolved inline button handlers: " + ", ".join(missing))

    def test_navigation_buttons_have_matching_screens(self):
        targets = set(re.findall(r'data-go="([^"]+)"', HTML))
        screen_ids = set(re.findall(r'<section\b[^>]*\bid="sc([^"]+)"', HTML, re.I))
        self.assertTrue(targets, "No bottom navigation controls found")
        self.assertTrue(targets.issubset(screen_ids),
                        f"Navigation targets without screens: {targets - screen_ids}")
        self.assertIn('onclick="openCreateMenu()"', HTML,
                      "Create action must open the create menu")

    def test_static_element_ids_are_unique(self):
        static_html = re.sub(r"<script[^>]*>.*?</script[^>]*>", "", HTML, flags=re.I | re.S)
        ids = re.findall(r'\bid="([^"]+)"', static_html)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        self.assertEqual(duplicates, [], "Duplicate DOM ids: " + ", ".join(duplicates))

    def test_local_stylesheets_exist(self):
        self.assertTrue(CSS_LINKS, "No local stylesheet references found")
        for href in CSS_LINKS:
            path = ROOT / "webapp" / href.removeprefix("./").split("?")[0]
            self.assertTrue(path.is_file(), f"Missing stylesheet: {href}")

    def test_every_declared_nav_action_is_unique(self):
        actions = re.findall(r'data-go="([^"]+)"', HTML)
        self.assertEqual(len(actions), len(set(actions)), "Duplicate bottom navigation actions")

    def test_search_controls_have_required_data_attributes(self):
        self.assertIn('id="qTabs"', HTML)
        self.assertIn('id="qSortRow"', HTML)
        self.assertIn('id="qBody"', HTML)
        for kind in ("all", "genres", "tracks", "albums", "artists", "public_domain"):
            self.assertIn(f'data-kind="{kind}"', HTML, f"Missing search tab: {kind}")
        for sort in ("relevance", "popularity", "newest"):
            self.assertIn(f'data-sort="{sort}"', HTML, f"Missing search sort: {sort}")

    def test_library_tabs_are_complete(self):
        for view in ("liked", "playlists", "history", "moments"):
            self.assertIn(f'data-view="{view}"', HTML, f"Missing library view: {view}")

    def test_device_sync_controls_are_present(self):
        for element_id in ("barRemote", "devicesList", "barOutput"):
            self.assertIn(f'id="{element_id}"', HTML, f"Missing device-sync element: {element_id}")
        for function in ("startDeviceSync", "connectDeviceSocket", "publishDeviceState",
                         "claimLocalPlayback", "cnExec", "cnStartHere", "transferTo",
                         "sendDeviceCommand", "handoffToDevice", "resumeOnThisDevice"):
            self.assertIn(function, self.functions, f"Missing device-sync function: {function}")

    def test_player_controls_have_implementations(self):
        for function in ("barPrev", "barNext", "barTogglePlay", "toggleShuffle",
                         "toggleRepeat", "toggleLike", "openQueue", "addBookmark",
                         "startRadio", "shareCurrentTrack", "downloadCurrent"):
            self.assertIn(function, self.functions, f"Missing player action: {function}")

    def test_player_transition_regressions_are_guarded(self):
        self.assertIn("let fadeInProgress = false;", JS,
                      "Crossfade needs a lock to prevent duplicate next-track transitions")
        self.assertIn("!fadeTimer && !fadeInProgress", JS,
                      "Progress updates must not trigger overlapping crossfades")
        next_track = JS.split("function nextTrack(auto)", 1)[1].split("\n}", 1)[0]
        self.assertIn("cur.pause();", next_track,
                      "Reaching the end of the queue must stop the audio element")
        self.assertIn("publishDeviceState(true);", next_track,
                      "Queue-end state must be published to connected devices")

    def test_resume_playback_reclaims_shared_session(self):
        toggle = JS.split("function togglePlay()", 1)[1].split("\n}", 1)[0]
        self.assertIn("claimLocalPlayback()", toggle,
                      "Resuming locally must make this device the active Connect device")
        self.assertIn("CN.remote()", toggle,
                      "A remote controller must send commands instead of playing locally")
        pending = JS.split("async function startPending()", 1)[1].split("\n}", 1)[0]
        self.assertIn("claimLocalPlayback()", pending,
                      "Restoring a saved track must claim the Connect session")

    def test_connect_transfer_is_acknowledged_only_after_playback_starts(self):
        start = JS.split("async function cnStartHere(m)", 1)[1].split("\nfunction cnShowTap", 1)[0]
        self.assertIn('type:"ack"', start)
        self.assertLess(start.index("await play("), start.index("ok:true"),
                        "Transfer may be confirmed only after audio really started")

    def test_profile_and_admin_actions_have_implementations(self):
        for function in ("saveSettings", "loadStats", "loadReferral", "openWrapped",
                         "openPromo", "openReportTrack", "submitReport", "redeem",
                         "openAdmin", "admBoot", "admTab", "admBroadcast"):
            self.assertIn(function, self.functions, f"Missing profile/admin action: {function}")


    def test_connect_handoff_does_not_claim_before_server_ack(self):
        play = JS.split("function play(t, startAt)", 1)[1].split("\\n}", 1)[0]
        handoff = JS.split("async function cnStartHere(m)", 1)[1].split("\\nfunction cnShowTap", 1)[0]
        self.assertIn("if(!CN.bypass) claimLocalPlayback()", play,
                      "A transfer target must not claim ownership before the server accepts its ack")
        self.assertIn("CN.bypass=true", handoff)
        self.assertIn('ack({ok:true,state:local})', handoff,
                      "A handoff must be acknowledged only after local playback succeeds")


if __name__ == "__main__":
    unittest.main()