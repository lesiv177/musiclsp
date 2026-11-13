import os, unittest
from core import meta_sources as M


def fake(routes):
    calls = []
    def fetch(url, params=None):
        calls.append(url)
        for k, v in routes.items():
            if url.startswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        return None
    fetch.calls = calls
    return fetch


class MetaSourcesTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("META_SOURCES_OFF", None)

    def test_cover_chain_falls_through_to_itunes(self):
        f = fake({M.DEEZER: {"data": []},
                  M.ITUNES: {"results": [{"artistName": "Daft Punk", "collectionName": "Discovery",
                                          "artworkUrl100": "https://x/a/100x100bb.jpg"}]}})
        r = M.find_cover("Daft Punk", "Discovery", f)
        self.assertEqual(r["source"], "itunes")
        self.assertTrue(r["url"].endswith("/600x600bb.jpg"))

    def test_cover_rejects_wrong_artist(self):
        f = fake({M.DEEZER: {"data": [{"artist": {"name": "Other"}, "title": "Discovery", "cover_xl": "https://x/c.jpg"}]}})
        self.assertEqual(M.find_cover("Daft Punk", "Discovery", f)["url"], "")

    def test_non_https_urls_dropped(self):
        f = fake({M.DEEZER: {"data": [{"artist": {"name": "A"}, "title": "B", "cover_xl": "http://x/c.jpg"}]}})
        self.assertEqual(M.find_cover("A", "B", f)["url"], "")

    def test_failing_source_is_skipped(self):
        f = fake({M.DEEZER: RuntimeError("boom"),
                  M.AUDIODB: {"album": [{"strAlbum": "B", "strAlbumThumb": "https://x/t.jpg"}]}})
        r = M.find_cover("A", "B", f)
        self.assertEqual((r["source"], r["url"]), ("audiodb", "https://x/t.jpg"))

    def test_source_can_be_disabled(self):
        os.environ["META_SOURCES_OFF"] = "deezer"
        f = fake({M.DEEZER: {"data": [{"artist": {"name": "A"}, "title": "B", "cover_xl": "https://x/c.jpg"}]}})
        self.assertEqual(M.find_cover("A", "B", f)["url"], "")
        self.assertNotIn(M.DEEZER + "/search/album", f.calls)
        self.assertEqual(M.source_health()["deezer"], "off")

    def test_artist_bundle(self):
        f = fake({
            M.DEEZER + "/search/artist": {"data": [{"id": 7, "name": "Muse", "picture_xl": "https://x/p.jpg"}]},
            M.DEEZER + "/artist/7/albums": {"data": [
                {"title": "Drones", "release_date": "2015-06-05", "record_type": "album", "cover_big": "https://x/d.jpg"},
                {"title": "drones", "release_date": "2015-06-05"},
                {"title": "Absolution", "release_date": "2003-09-15", "record_type": "album"}]},
            M.WIKI: {"type": "standard", "title": "Muse", "extract": "Muse are a band.",
                     "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Muse"}}},
        })
        r = M.find_artist("Muse", f)
        self.assertEqual(r["image"], "https://x/p.jpg")
        self.assertEqual(r["bio_source"], "wikipedia")
        self.assertEqual([a["title"] for a in r["albums"]], ["Drones", "Absolution"])

    def test_empty_inputs_and_no_results(self):
        self.assertEqual(M.find_cover("", "x", fake({}))["url"], "")
        r = M.find_artist("Nobody", fake({}))
        self.assertEqual((r["image"], r["bio"], r["albums"]), ("", "", []))


if __name__ == "__main__":
    unittest.main()
