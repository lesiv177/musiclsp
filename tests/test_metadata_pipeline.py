import unittest
from core import metadata_search as MS, track_resolver as TR, search_engine as E

MBID = "11111111-2222-3333-4444-555555555555"
CC = "https://creativecommons.org/licenses/by/4.0/"


class FakeMB:
    def __init__(self):
        self.calls = []

    def search_artists(self, q, limit=10):
        self.calls.append(("artists", q))
        return [{"id": f"mb:artist:{MBID}", "mbid": MBID, "name": "OG Buda", "aliases": [], "source_url": "https://mb/a"}] if q.lower() == "og buda" else []

    def artist_release_groups(self, mbid, limit=25):
        return [{"id": "mb:release-group:a", "mbid": "a", "title": "Miu Miu", "first_release_date": "2024-01-29", "source_url": "https://mb/rg/a"},
                {"id": "mb:release-group:b", "mbid": "b", "title": "На чиле", "first_release_date": "2021-07-27", "source_url": "https://mb/rg/b"},
                {"id": "mb:release-group:c", "mbid": "c", "title": "miu miu", "first_release_date": "2024-02-01"}]

    def search_recordings(self, q, limit=10):
        self.calls.append(("rec", q))
        return [{"title": "Miu Miu", "artist": "OG Buda", "mbid": "r1"}, {"title": "miu miu", "artist": "OG Buda"},
                {"title": "Мало", "artist": "OG Buda"}]


def trk(id_, title, artist, source="jamendo", **kw):
    d = {"id": id_, "title": title, "artist": artist, "source": source, "source_label": source, "stream": "",
         "license": CC, "license_short": "CC BY", "cover": "https://c/x.jpg", "duration": 100}
    d.update(kw)
    return d


class MetaSearchTest(unittest.TestCase):
    def setUp(self):
        MS._CACHE.clear()

    def test_artist_lookup_albums_sorted_deduped(self):
        r = MS.lookup("OG Buda", mb=FakeMB())
        self.assertEqual(r["artist"]["name"], "OG Buda")
        self.assertEqual([a["title"] for a in r["albums"]], ["Miu Miu", "На чиле"])
        self.assertEqual(len(r["recordings"]), 2)

    def test_not_artist_gives_recordings_only(self):
        r = MS.lookup("Miu Miu", mb=FakeMB())
        self.assertIsNone(r["artist"])
        self.assertTrue(r["recordings"])

    def test_failure_is_safe(self):
        class Boom:
            def search_artists(self, *a, **k): raise RuntimeError("x")
        self.assertEqual(MS.lookup("x y", mb=Boom())["albums"], [])

    def test_cached(self):
        mb = FakeMB()
        MS.lookup("OG Buda", mb=mb)
        n = len(mb.calls)
        MS.lookup("OG Buda", mb=mb)
        self.assertEqual(len(mb.calls), n)


class ResolverTest(unittest.TestCase):
    def test_pick_best_and_rejects_wrong(self):
        cands = [trk("a:1", "Other", "Someone"), trk("j:1", "Miu Miu (Remastered)", "OG Buda"), trk("a:2", "Miu Miu", "Random Guy")]
        self.assertEqual(TR.pick("OG Buda", "Miu Miu", cands)["id"], "j:1")
        self.assertIsNone(TR.pick("OG Buda", "Miu Miu", [trk("a:1", "Other", "OG Buda")]))
        self.assertIsNone(TR.pick("OG Buda", "Miu Miu", [trk("a:2", "Miu Miu", "Random Guy")]))

    def test_artist_in_title_accepted(self):
        self.assertIsNotNone(TR.pick("OG Buda", "Miu Miu", [trk("s:1", "OG Buda - Miu Miu", "uploader", source="soundcloud")]))

    def test_resolve_many_and_unsafe_dropped(self):
        NC = "https://creativecommons.org/licenses/by-nc/4.0/"
        fn = lambda q, n: [trk("arc:1", "Miu Miu", "OG Buda", source="archive", license=NC)] if "Zed" in q else [trk("j:1", "Miu Miu", "OG Buda")]
        out = TR.resolve_many([{"artist": "OG Buda", "title": "Miu Miu"}, {"artist": "Zed", "title": "Miu Miu"}, {"title": ""}], search_fn=fn)
        self.assertEqual(out[0]["id"], "j:1")
        self.assertIsNone(out[1])
        self.assertIsNone(out[2])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        MS._CACHE.clear()

    def test_artist_query_pulls_canonical_tracks_and_albums(self):
        queries = []
        def fetch(q, n):
            queries.append(q)
            if "Miu Miu" in q:
                return [trk("j:1", "Miu Miu", "OG Buda")]
            return [trk("j:9", "Random", "Other")]
        mb = FakeMB()
        tracks, extras = E.search("OG Buda", fetch=fetch, discovery_fn=lambda q: {}, meta_fn=lambda q, a, t: MS.lookup(q, a, t, mb=mb))
        self.assertIn("OG Buda Miu Miu", queries)
        self.assertEqual(tracks[0]["id"], "j:1")
        self.assertEqual(extras["metadata"]["artist"]["name"], "OG Buda")
        self.assertEqual(len(extras["metadata"]["albums"]), 2)
        self.assertTrue(extras["diagnostics"]["musicbrainz"])


if __name__ == "__main__":
    unittest.main()


class AlbumResolveTest(unittest.TestCase):
    PL = [{"id": "yt:1", "title": "Miu Miu", "artist": "OG Buda", "source": "youtube"},
          {"id": "yt:2", "title": "Лазерный (Official Audio)", "artist": "OG Buda", "source": "youtube"},
          {"id": "yt:3", "title": "Intro", "artist": "OG Buda", "source": "youtube"}]

    def test_align_by_title(self):
        out = TR.align_album(["Лазерный", "Miu Miu", "Nonexistent Song"], self.PL)
        self.assertEqual([o["id"] if o else None for o in out], ["yt:2", "yt:1", None])

    def test_resolve_album_via_playlist_with_search_fallback(self):
        calls = []
        def sfn(q, n):
            calls.append(q)
            return [trk("j:9", "Nonexistent Song", "OG Buda")]
        r = TR.resolve_album("OG Buda", "Miu Miu", ["Miu Miu", "Nonexistent Song"], playlist_fn=lambda a, b: self.PL, search_fn=sfn)
        self.assertEqual(r["via"], "playlist")
        self.assertEqual([x["id"] for x in r["results"]], ["yt:1", "j:9"])
        self.assertEqual(r["found"], 2)
        self.assertEqual(len(calls), 1)      # шукали лише відсутній трек

    def test_no_playlist_falls_back_to_search(self):
        r = TR.resolve_album("OG Buda", "X", ["Miu Miu"], playlist_fn=lambda a, b: [],
                             search_fn=lambda q, n: [trk("j:1", "Miu Miu", "OG Buda")])
        self.assertEqual((r["via"], r["found"]), ("search", 1))

    def test_playlist_pick_prefers_topic_channel(self):
        pls = [{"playlist_id": "A" * 12, "title": "Miu Miu full album reaction", "channel": "Some Fan"},
               {"playlist_id": "B" * 12, "title": "Miu Miu", "channel": "OG Buda - Topic"}]
        self.assertEqual(TR._pick_album_playlist("OG Buda", "Miu Miu", pls)["playlist_id"], "B" * 12)
        self.assertIsNone(TR._pick_album_playlist("OG Buda", "Miu Miu", [{"playlist_id": "C" * 12, "title": "Other", "channel": "x"}]))

    def test_provider_playlist_helpers_use_quota_cache(self):
        from unittest import mock
        from core import providers as P, youtube_guard as YG
        YG.reset_for_tests()
        class R:
            status_code = 200
            def __init__(s, d): s.d = d
            def json(s): return s.d
        class S:
            n = 0
            def get(s, url, **k):
                s.n += 1
                if "playlistItems" in url:
                    return R({"items": [{"snippet": {"title": "Miu Miu", "channelTitle": "OG Buda - Topic", "resourceId": {"videoId": "vid1"}}}]})
                return R({"items": [{"id": {"playlistId": "PLabcdefghij"}, "snippet": {"title": "Miu Miu", "channelTitle": "OG Buda - Topic"}}]})
        s = S()
        with mock.patch.object(P, "YOUTUBE_API_KEY", "k"), mock.patch.object(P, "_http_session", lambda: s):
            pls = P.youtube_album_playlists("OG Buda Miu Miu album")
            tr1 = P.youtube_playlist_tracks(pls[0]["playlist_id"])
            P.youtube_album_playlists("OG Buda Miu Miu album"); P.youtube_playlist_tracks(pls[0]["playlist_id"])
        self.assertEqual(s.n, 2)
        self.assertEqual(tr1[0]["id"], "yt:vid1")
        self.assertEqual(YG.status()["used"], 101)
        self.assertEqual(P.youtube_playlist_tracks("../bad"), [])