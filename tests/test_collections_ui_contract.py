import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "webapp" / "v3.js").read_text(encoding="utf-8")


def src():
    a = JS.index("async function renderCollections")
    return JS[a:JS.index("async function renderSavedCollection")]


class CollectionsUiContract(unittest.TestCase):
    def test_uses_server_endpoint_not_localstorage(self):
        self.assertIn('/api/personal-collections', src())
        self.assertNotIn("localStorage", src())

    def test_empty_or_failed_state_renders_nothing_fake(self):
        s = src()
        self.assertIn("!d.enough_data", s)
        self.assertIn('host.innerHTML=""', s)

    def test_text_is_escaped(self):
        s = src()
        for field in ("c.title", "c.reason"):
            self.assertIn("E(" + field, s)

    def test_home_hosts_and_renders_collections(self):
        self.assertIn('id="v3Collections"', JS)
        home = JS[JS.index("async function home()"):JS.index("async function explore()")]
        self.assertIn("renderCollections();", home)

    def test_each_collection_has_own_row_context_and_play_all(self):
        s = src()
        self.assertIn('"coll"+ci', s)
        self.assertIn("data-coll-play", s)
        self.assertIn("setQueue(list,0)", s)

    def test_protected_player_internals_not_touched(self):
        for protected in ("startDeviceSync(", "connectDeviceSocket(", "publishDeviceState(", "function togglePlay("):
            self.assertNotIn(protected, src())


if __name__ == "__main__":
    unittest.main()