import unittest
from contextlib import ExitStack
from unittest.mock import patch

from core import providers as P


def run_jobs_inline(jobs, timeout=4):
    """Run mocked provider jobs in their declared order, without network I/O."""
    results = []
    for job in jobs:
        results.extend(job() or [])
    return results


class AudiusAlbumCatalogueTests(unittest.TestCase):
    def test_album_search_returns_only_album_collections(self):
        payload = {"data": [
            {"id": "album-1", "is_album": True, "playlist_name": "Example Album",
             "user": {"name": "Example Artist"}, "artwork": {"480x480": "https://img.test/a.jpg"},
             "release_date": "2026-10-15T12:00:00Z",
             "permalink": "/artist/example-album"},
            {"id": "playlist-1", "is_album": False, "playlist_name": "Mix"},
        ]}
        with patch.object(P, "audius_host", return_value="https://audius.test"), \
             patch.object(P, "_get", return_value=payload) as get:
            albums = P.audius_search_albums("Example", 10)
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["id"], "aud:album-1")
        self.assertEqual(albums[0]["title"], "Example Album")
        self.assertEqual(albums[0]["artist"], "Example Artist")
        self.assertEqual(albums[0]["release_date"], "2026-10-15")
        self.assertEqual(albums[0]["source_url"], "https://audius.co/artist/example-album")
        self.assertEqual(get.call_args.args[0], "https://audius.test/v1/playlists/search")

    def test_album_tracks_include_collection_metadata_and_filter_noncommercial_tracks(self):
        metadata = {"data": {
            "playlist_name": "Example Album",
            "user": {"name": "Example Artist"},
            "artwork": {"480x480": "https://img.test/album.jpg"},
        }}
        tracks = {"data": [
            {"id": "track-1", "title": "Allowed", "license": "cc-by",
             "user": {"name": "Original Artist"}, "duration": 120},
            {"id": "track-2", "title": "Noncommercial", "license": "cc-by-nc-sa",
             "user": {"name": "Original Artist"}, "duration": 120},
        ]}
        with patch.object(P, "audius_host", return_value="https://audius.test"), \
             patch.object(P, "_get", side_effect=[metadata, tracks]):
            result = P.audius_album_tracks("album-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "aud:track-1")
        self.assertEqual(result[0]["album"], "Example Album")
        self.assertEqual(result[0]["album_id"], "aud:album-1")
        self.assertEqual(result[0]["artist"], "Example Artist")
        self.assertEqual(result[0]["cover"], "https://img.test/album.jpg")

    def test_audius_stream_resolver_rechecks_track_license(self):
        with patch.object(P, "audius_track", return_value=None), \
             patch.object(P, "audius_stream_url_verified") as stream:
            self.assertEqual(P.resolve_stream("aud:noncommercial-track"), "")
            stream.assert_not_called()

    def test_explicit_noncommercial_audius_track_is_rejected(self):
        self.assertIsNone(P._aud_track({"id": "x", "license": "CC BY-NC-ND"}))
        self.assertIsNone(P._aud_track({"id": "x", "license": "Attribution-NonCommercial"}))
        self.assertIsNotNone(P._aud_track({"id": "x", "license": "CC BY-SA"}))

    def test_audius_track_respects_creator_stream_and_visibility_restrictions(self):
        base = {"id": "track-1", "title": "Track", "license": "CC BY"}
        for restriction in (
            {"is_available": False},
            {"is_streamable": False},
            {"is_delete": True},
            {"is_unlisted": True},
            {"is_stream_gated": True},
            {"is_private": True},
        ):
            with self.subTest(restriction=restriction):
                self.assertIsNone(P._aud_track({**base, **restriction}))
        self.assertIsNotNone(P._aud_track(base))

    def test_audius_album_respects_private_and_unlisted_flags(self):
        base = {"id": "album-1", "is_album": True, "playlist_name": "Album"}
        self.assertIsNotNone(P._aud_album(base))
        self.assertIsNone(P._aud_album({**base, "is_private": True}))
        self.assertIsNone(P._aud_album({**base, "is_unlisted": True}))


class AudiusPlaylistVisibilityTests(unittest.TestCase):
    def test_trending_playlists_exclude_private_unlisted_and_invalid_items(self):
        payload = {"data": [
            {"id": "public", "playlist_name": "Public"},
            {"id": "private", "playlist_name": "Private", "is_private": True},
            {"id": "unlisted", "playlist_name": "Unlisted", "is_unlisted": True},
            {"id": "", "playlist_name": "No ID"},
            {"id": "no-title", "playlist_name": ""},
        ]}
        with (
            patch.object(P, "audius_host", return_value="https://audius.test"),
            patch.object(P, "_get", return_value=payload),
        ):
            results = P.audius_trending_playlists(limit=10)
        self.assertEqual([item["id"] for item in results], ["aud:public"])


class JamendoPlaylistFallbackTests(unittest.TestCase):
    def test_synthetic_jamendo_collections_hidden_when_source_not_approved(self):
        with patch.object(P, "jamendo_enabled", return_value=False):
            self.assertEqual(P._synthetic_collections(10), [])


class SoundCloudPriorityTests(unittest.TestCase):
    def _empty_provider_patches(self):
        return [
            patch.object(P, "audius_search_tracks", return_value=[]),
            patch.object(P, "archive_search", return_value=[]),
            patch.object(P, "ccmixter_search", return_value=[]),
            patch.object(P, "openverse_search", return_value=[]),
            patch.object(P, "wikimedia_search", return_value=[]),
        ]

    def _search(self, sc_on, query="Artist"):
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist", "source": "jamendo"}
        sc = {"id": "sc:9", "title": "Track SC", "artist": "Artist", "source": "soundcloud"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(P, "_run_parallel", side_effect=run_jobs_inline))
            stack.enter_context(patch.object(P, "soundcloud_enabled", return_value=sc_on))
            stack.enter_context(patch.object(P, "jamendo_enabled", return_value=True))
            ss = stack.enter_context(patch.object(P, "soundcloud_search", return_value=[sc]))
            stack.enter_context(patch.object(P, "jamendo_search_tracks", return_value=[jamendo]))
            for provider in ("audius_search_tracks", "archive_search", "ccmixter_search",
                             "openverse_search", "wikimedia_search"):
                stack.enter_context(patch.object(P, provider, return_value=[]))
            results = P.search_tracks(query, limit=10)
        return ss, [t["id"] for t in results]

    def test_soundcloud_is_primary_source_when_enabled(self):
        ss, ids = self._search(True)
        ss.assert_called_once()
        self.assertEqual(ids, ["sc:9", "jam:2"])

    def test_soundcloud_skipped_without_credentials(self):
        ss, ids = self._search(False)
        ss.assert_not_called()
        self.assertEqual(ids, ["jam:2"])

    def test_youtube_is_catalog_only_and_last(self):
        yt = {"id": "yt:abc", "title": "T", "artist": "A", "source": "youtube"}
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist", "source": "jamendo"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(P, "_run_parallel", side_effect=run_jobs_inline))
            stack.enter_context(patch.object(P, "soundcloud_enabled", return_value=False))
            stack.enter_context(patch.object(P, "youtube_enabled", return_value=True))
            stack.enter_context(patch.object(P, "jamendo_enabled", return_value=True))
            stack.enter_context(patch.object(P, "youtube_search", return_value=[yt]))
            stack.enter_context(patch.object(P, "jamendo_search_tracks", return_value=[jamendo]))
            for provider in ("audius_search_tracks", "archive_search", "ccmixter_search",
                             "openverse_search", "wikimedia_search"):
                stack.enter_context(patch.object(P, provider, return_value=[]))
            ids = [t["id"] for t in P.search_tracks("x", limit=10)]
        self.assertEqual(ids, ["jam:2", "yt:abc"])

    def test_explicit_provider_filter_is_respected(self):
        jamendo = {"id": "jam:2", "title": "Track Jam", "artist": "Artist"}
        with (
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "soundcloud_enabled", return_value=True),
            patch.object(P, "jamendo_enabled", return_value=True),
            patch.object(P, "soundcloud_search") as soundcloud_search,
            patch.object(P, "jamendo_search_tracks", return_value=[jamendo]),
        ):
            results = P.search_tracks("Artist", limit=10, sources=["jamendo"])
        soundcloud_search.assert_not_called()
        self.assertEqual([track["id"] for track in results], ["jam:2"])

    def test_parallel_runner_preserves_source_order_and_ignores_failure(self):
        def broken():
            raise RuntimeError("provider unavailable")

        results = P._run_parallel([
            lambda: [{"id": "first"}],
            broken,
            lambda: [{"id": "third"}],
        ], timeout=2)
        self.assertEqual([item["id"] for item in results], ["first", "third"])

    def test_parallel_runner_uses_bounded_shared_pool(self):
        self.assertEqual(P._PROVIDER_EXECUTOR._max_workers, 12)

    def test_safe_log_url_never_includes_query_secrets(self):
        safe = P._safe_log_url("https://api.example.test/search?client_secret=do-not-log")
        self.assertEqual(safe, "api.example.test/search")
        self.assertNotIn("do-not-log", safe)

    def test_get_returns_none_for_provider_http_errors(self):
        class Response:
            status_code = 503
            def json(self):
                raise AssertionError("JSON must not be parsed for HTTP errors")

        with patch.object(P, "_http_session") as session:
            session.return_value.get.return_value = Response()
            self.assertIsNone(P._get("https://provider.test/search"))

    def test_get_returns_parsed_json_for_success(self):
        class Response:
            status_code = 200
            def json(self):
                return {"results": [{"id": 1}]}

        with patch.object(P, "_http_session") as session:
            session.return_value.get.return_value = Response()
            self.assertEqual(P._get("https://provider.test/search"), {"results": [{"id": 1}]})

    def test_duration_parser_handles_invalid_values(self):
        self.assertEqual(P.fmt_duration(125), "2:05")
        self.assertEqual(P.fmt_duration("not-a-duration"), "")

    def test_url_playable_uses_thread_local_session(self):
        class Response:
            status_code = 200
            def close(self):
                pass
        with patch.object(P, "_http_session") as session:
            session.return_value.head.return_value = Response()
            self.assertTrue(P._url_playable("https://audio.example.test/track.mp3"))
            session.return_value.head.assert_called_once()


class CommercialProviderApprovalTests(unittest.TestCase):
    def test_jamendo_requires_credentials_and_commercial_approval(self):
        with (
            patch.object(P, "JAMENDO_CLIENT_ID", "client"),
            patch.dict(P._os.environ, {"JAMENDO_COMMERCIAL_APPROVED": "false"}),
        ):
            self.assertFalse(P.jamendo_enabled())
            with patch.object(P, "_get") as get:
                self.assertEqual(P.jamendo_search_tracks("music"), [])
                get.assert_not_called()
        with (
            patch.object(P, "JAMENDO_CLIENT_ID", "client"),
            patch.dict(P._os.environ, {"JAMENDO_COMMERCIAL_APPROVED": "true"}),
        ):
            self.assertTrue(P.jamendo_enabled())

    def test_soundcloud_requires_explicit_aggregation_approval(self):
        with (
            patch.object(P, "SOUNDCLOUD_CLIENT_ID", "client"),
            patch.object(P, "SOUNDCLOUD_CLIENT_SECRET", "secret"),
            patch.dict(P._os.environ, {"SOUNDCLOUD_AGGREGATION_APPROVED": "false"}),
        ):
            self.assertFalse(P.soundcloud_enabled())
        with (
            patch.object(P, "SOUNDCLOUD_CLIENT_ID", "client"),
            patch.object(P, "SOUNDCLOUD_CLIENT_SECRET", "secret"),
            patch.dict(P._os.environ, {"SOUNDCLOUD_AGGREGATION_APPROVED": "true"}),
        ):
            self.assertTrue(P.soundcloud_enabled())

    def test_soundcloud_approval_gate_also_blocks_direct_stream_resolution(self):
        with (
            patch.object(P, "soundcloud_enabled", return_value=False),
            patch.object(P, "_sc_headers") as headers,
        ):
            self.assertEqual(P.soundcloud_stream_url("123"), "")
            self.assertIsNone(P.soundcloud_track("123"))
            headers.assert_not_called()

    def test_jamendo_tracks_require_explicit_commercially_usable_license(self):
        base = {"id": "7", "name": "Track", "audio": "https://audio.example/7.mp3"}
        self.assertIsNone(P._jam_track({**base, "license_ccurl": ""}))
        self.assertIsNone(P._jam_track({**base, "license_ccurl":
            "http://creativecommons.org/licenses/by-nc-sa/3.0/"}))
        allowed = P._jam_track({**base, "license_ccurl":
            "http://creativecommons.org/licenses/by/4.0/"})
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed["license_short"], "CC BY")


class GlobalSearchCoverageTests(unittest.TestCase):
    def test_global_search_requests_optional_catalogues(self):
        with (
            patch.object(P, "search_tracks_smart", return_value=[]) as tracks,
            patch.object(P, "search_albums_smart", return_value=[]),
            patch.object(P, "search_artists_smart", return_value=[]),
        ):
            result = P.search_everything("test", limit=8)
        self.assertEqual(result, {"tracks": [], "albums": [], "artists": []})
        requested = tracks.call_args.kwargs["sources"]
        self.assertIn("soundcloud", requested)
        self.assertIn("youtube", requested)

    def test_global_search_forwards_quality_sources_and_sort(self):
        with (
            patch.object(P, "search_tracks_smart", return_value=[]) as tracks,
            patch.object(P, "search_albums_smart", return_value=[]) as albums,
            patch.object(P, "search_artists_smart", return_value=[]),
        ):
            P.search_everything("test", limit=8, quality="mp3", sources=["audius"], sort="newest")
        self.assertEqual(tracks.call_args.kwargs["quality"], "mp3")
        self.assertEqual(tracks.call_args.kwargs["sources"], ["audius"])
        self.assertEqual(tracks.call_args.kwargs["sort"], "newest")
        self.assertEqual(albums.call_args.kwargs["sort"], "newest")

    def test_artist_search_aggregates_both_catalogues_and_deduplicates_ids(self):
        jam = {"id": "jam:1", "name": "Artist", "source": "jamendo"}
        aud = {"id": "aud:2", "name": "Artist", "source": "audius"}
        duplicate = {"id": "jam:1", "name": "Artist duplicate", "source": "jamendo"}
        with (
            patch.object(P, "_run_parallel", side_effect=run_jobs_inline),
            patch.object(P, "jamendo_search_artists", return_value=[jam, duplicate]),
            patch.object(P, "audius_search_users", return_value=[aud]),
        ):
            results = P.search_artists("Artist", limit=10)
        self.assertEqual([item["id"] for item in results], ["jam:1", "aud:2"])

    def test_audius_artist_tracks_drop_noncommercial_or_invalid_records(self):
        payload = [
            {"id": "1", "title": "Allowed", "license": "CC-BY", "user": {"name": "A"}},
            {"id": "2", "title": "Blocked", "license": "CC-BY-NC", "user": {"name": "A"}},
        ]
        with (
            patch.object(P, "audius_host", return_value="https://discovery.example"),
            patch.object(P, "_get", return_value={"data": payload}),
        ):
            results = P.audius_user_tracks("artist-id", limit=10)
        self.assertEqual([item["id"] for item in results], ["aud:1"])


class InternetArchiveRightsTests(unittest.TestCase):
    def test_only_explicit_commercial_cc_and_public_domain_licenses_pass(self):
        self.assertFalse(P._archive_commercially_usable(""))
        self.assertFalse(P._archive_commercially_usable("https://creativecommons.org/licenses/by-nc/4.0/"))
        self.assertTrue(P._archive_commercially_usable("https://creativecommons.org/publicdomain/mark/1.0/"))
        self.assertTrue(P._archive_commercially_usable("https://creativecommons.org/licenses/by/4.0/"))
        self.assertTrue(P._archive_commercially_usable("https://creativecommons.org/licenses/by-sa/4.0/"))
        self.assertTrue(P._archive_commercially_usable("https://creativecommons.org/licenses/by-nd/4.0/"))

    def test_missing_archive_license_is_not_labelled_public_domain(self):
        self.assertEqual(P._archive_license(""), ("", "Ліцензія не вказана"))

    def test_archive_search_excludes_missing_and_noncommercial_licenses(self):
        docs = [
            {"identifier": "no-license", "title": "Unknown"},
            {"identifier": "nc", "title": "Noncommercial",
             "licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/"},
            {"identifier": "cc-by", "title": "Commercial CC",
             "licenseurl": "https://creativecommons.org/licenses/by/4.0/"},
        ]
        with (
            patch.object(P, "ARCHIVE_ENABLED", True),
            patch.object(P, "_get", return_value={"response": {"docs": docs}}),
        ):
            results = P.archive_search("music", limit=10)
        self.assertEqual([item["id"] for item in results], ["arc:cc-by"])
        self.assertTrue(results[0]["downloadable"])


    def test_archive_stream_resolution_is_disabled_with_the_source(self):
        with patch.object(P, "ARCHIVE_ENABLED", False), patch.object(P, "_get") as get:
            self.assertEqual(P.archive_stream_url("licensed-item"), "")
            get.assert_not_called()

    def test_archive_stream_requires_explicit_commercial_license(self):
        files = [{"name": "recording.mp3"}]
        unknown = {"metadata": {}, "files": files}
        licensed = {"metadata": {"licenseurl": "https://creativecommons.org/licenses/by/4.0/"},
                    "files": files}
        with patch.object(P, "_get", return_value=unknown):
            self.assertEqual(P.archive_stream_url("unknown"), "")
        with patch.object(P, "_get", return_value=licensed):
            self.assertEqual(P.archive_stream_url("licensed"),
                             "https://archive.org/download/licensed/recording.mp3")

class OpenCatalogueRightsTests(unittest.TestCase):
    def test_openverse_requires_commercially_usable_explicit_license(self):
        base = {"id": "a", "url": "https://audio.example/track.mp3", "title": "Track"}
        self.assertIsNone(P._ov_track({**base, "license": "by-nc",
                                      "license_url": "https://creativecommons.org/licenses/by-nc/4.0/"}))
        self.assertIsNone(P._ov_track({**base, "license": "", "license_url": ""}))
        allowed = P._ov_track({**base, "license": "by",
                               "license_url": "https://creativecommons.org/licenses/by/4.0/"})
        self.assertIsNotNone(allowed)
        self.assertTrue(allowed["downloadable"])

    def test_wikimedia_requires_explicit_commercially_usable_license(self):
        def page(license_url):
            return {"title": "File:Track.ogg", "pageid": 9, "imageinfo": [{
                "mime": "audio/ogg", "url": "https://upload.wikimedia.org/track.ogg",
                "extmetadata": {"LicenseUrl": {"value": license_url}},
            }]}
        self.assertIsNone(P._wm_track(page("")))
        self.assertIsNone(P._wm_track(page("https://creativecommons.org/licenses/by-nc/4.0/")))
        self.assertIsNotNone(P._wm_track(page("https://creativecommons.org/licenses/by/4.0/")))

    def test_ccmixter_json_requires_commercially_usable_license(self):
        base = {"id": "cc1", "title": "Track", "file_url": "https://ccmixter.example/track.mp3"}
        self.assertIsNone(P._ccm_from_json({**base, "license_url": ""}))
        self.assertIsNone(P._ccm_from_json({**base,
            "license_url": "https://creativecommons.org/licenses/by-nc/4.0/"}))
        self.assertIsNotNone(P._ccm_from_json({**base,
            "license_url": "https://creativecommons.org/licenses/by/4.0/"}))

    def test_ccmixter_xml_ignores_entries_without_allowed_license(self):
        xml = '<rss><channel><item><title>Track</title><enclosure url="https://ccmixter.example/a.mp3"/><license href="https://creativecommons.org/licenses/by-nc/4.0/"/></item></channel></rss>'
        self.assertEqual(P._ccm_from_xml(xml, 10), [])
        allowed_xml = '<rss><channel><item><title>Track</title><enclosure url="https://ccmixter.example/a.mp3"/><license href="https://creativecommons.org/licenses/by/4.0/"/></item></channel></rss>'
        self.assertEqual(len(P._ccm_from_xml(allowed_xml, 10)), 1)



class CcMixterApiContractTests(unittest.TestCase):
    def test_json_search_uses_documented_search_parameter_over_https(self):
        entry = {
            "upload_id": "cc-1", "upload_name": "Track", "user_name": "Artist",
            "file_url": "https://ccmixter.example/track.mp3",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }
        with patch.object(P, "_get", return_value=[entry]) as get:
            results = P.ccmixter_search("ambient", limit=5)
        self.assertEqual([item["id"] for item in results], ["ccm:cc-1"])
        self.assertEqual(get.call_args.args[0], "https://ccmixter.org/api/query")
        self.assertEqual(get.call_args.args[1]["search"], "ambient")
        self.assertEqual(get.call_args.args[1]["search_type"], "any")
        self.assertEqual(get.call_args.args[1]["f"], "json")
        self.assertNotIn("query", get.call_args.args[1])

    def test_xml_fallback_requests_rss_with_documented_search_parameter(self):
        class Response:
            status_code = 200
            text = ('<rss><channel><item><title>Track</title>'
                    '<enclosure url="https://ccmixter.example/a.mp3"/>'
                    '<license href="https://creativecommons.org/licenses/by/4.0/"/>'
                    '</item></channel></rss>')
        with (
            patch.object(P, "_get", return_value=None),
            patch.object(P, "_http_session") as session,
        ):
            session.return_value.get.return_value = Response()
            results = P.ccmixter_search("ambient", limit=5)
        self.assertEqual(len(results), 1)
        call = session.return_value.get.call_args
        self.assertEqual(call.args[0], "https://ccmixter.org/api/query")
        self.assertEqual(call.kwargs["params"]["search"], "ambient")
        self.assertEqual(call.kwargs["params"]["search_type"], "any")
        self.assertEqual(call.kwargs["params"]["f"], "rss")



class PersistedOpenCataloguePlaybackTests(unittest.TestCase):
    def test_ccmixter_track_reloads_by_numeric_upload_id(self):
        entry = {
            "upload_id": "12345", "upload_name": "Saved Track", "user_name": "Artist",
            "file_url": "https://ccmixter.example/saved.mp3",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }
        with (
            patch.object(P, "_ccm_cache_get", return_value=None),
            patch.object(P, "_get", return_value=[entry]) as get,
            patch.object(P, "_ccm_cache_put") as cache_put,
        ):
            track = P.ccmixter_track("12345")
        self.assertEqual(track["id"], "ccm:12345")
        self.assertEqual(track["stream"], entry["file_url"])
        self.assertEqual(get.call_args.args[1], {"ids": "12345", "f": "json"})
        cache_put.assert_called_once_with(track)

    def test_ccmixter_rejects_non_numeric_ids_without_network(self):
        with patch.object(P, "_get") as get:
            self.assertIsNone(P.ccmixter_track("not-an-id"))
        get.assert_not_called()

    def test_openverse_track_reloads_detail_and_rechecks_license(self):
        uid = "12345678-1234-4abc-8def-1234567890ab"
        item = {
            "id": uid, "url": "https://audio.example/saved.mp3",
            "title": "Saved Track", "license": "by",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }
        with (
            patch.object(P, "_ov_cache_get", return_value=None),
            patch.object(P, "_get", return_value=item) as get,
            patch.object(P, "_ov_cache_put") as cache_put,
        ):
            track = P.openverse_track(uid)
        self.assertEqual(track["id"], f"ov:{uid}")
        self.assertEqual(track["stream"], item["url"])
        self.assertEqual(get.call_args.args[0], f"{P.OPENVERSE_API}{uid}/")
        cache_put.assert_called_once_with(track)

    def test_openverse_rejects_noncommercial_license_on_reload(self):
        uid = "12345678-1234-4abc-8def-1234567890ab"
        item = {
            "id": uid, "url": "https://audio.example/saved.mp3",
            "license": "by-nc",
            "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
        }
        with (
            patch.object(P, "_ov_cache_get", return_value=None),
            patch.object(P, "_get", return_value=item),
        ):
            self.assertIsNone(P.openverse_track(uid))

    def test_wikimedia_track_reloads_by_page_id_and_rechecks_license(self):
        page = {"title": "File:Saved.ogg", "pageid": 42, "imageinfo": [{
            "mime": "audio/ogg", "url": "https://upload.wikimedia.org/saved.ogg",
            "extmetadata": {"LicenseUrl": {
                "value": "https://creativecommons.org/licenses/by/4.0/"
            }},
        }]}
        with (
            patch.object(P, "_wm_cache_get", return_value=None),
            patch.object(P, "_get", return_value={"query": {"pages": {"42": page}}}) as get,
            patch.object(P, "_wm_cache_put") as cache_put,
        ):
            track = P.wikimedia_track("42")
        self.assertEqual(track["id"], "wm:42")
        self.assertEqual(track["stream"], page["imageinfo"][0]["url"])
        self.assertEqual(get.call_args.args[1]["pageids"], "42")
        cache_put.assert_called_once_with(track)

    def test_wikimedia_rejects_noncommercial_license_on_reload(self):
        page = {"title": "File:Saved.ogg", "pageid": 42, "imageinfo": [{
            "mime": "audio/ogg", "url": "https://upload.wikimedia.org/saved.ogg",
            "extmetadata": {"LicenseUrl": {
                "value": "https://creativecommons.org/licenses/by-nc/4.0/"
            }},
        }]}
        with (
            patch.object(P, "_wm_cache_get", return_value=None),
            patch.object(P, "_get", return_value={"query": {"pages": {"42": page}}}),
        ):
            self.assertIsNone(P.wikimedia_track("42"))


class ArchiveCuratedCatalogueTests(unittest.TestCase):
    def test_archive_collection_tracks_exclude_missing_and_noncommercial_licenses(self):
        docs = [
            {"identifier": "unknown", "title": "Unknown"},
            {"identifier": "nc", "title": "Noncommercial",
             "licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/"},
            {"identifier": "allowed", "title": "Allowed",
             "licenseurl": "https://creativecommons.org/licenses/by/4.0/"},
        ]
        with patch.object(P, "ARCHIVE_ENABLED", True), \
             patch.object(P, "_get", return_value={"response": {"docs": docs}}):
            tracks = P.archive_collection_tracks("netlabels", limit=10)
        self.assertEqual([track["id"] for track in tracks], ["arc:allowed"])
        self.assertEqual(tracks[0]["license_short"], "CC BY")

    def test_archive_curated_tiles_are_hidden_when_archive_is_disabled(self):
        with patch.object(P, "ARCHIVE_ENABLED", False), \
             patch.object(P, "archive_collection_tracks") as collection:
            self.assertEqual(P._archive_curated_tiles(), [])
        collection.assert_not_called()

if __name__ == "__main__":
    unittest.main()