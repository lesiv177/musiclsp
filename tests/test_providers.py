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


class GlobalSearchCoverageTests(unittest.TestCase):
    def test_global_search_requests_optional_catalogues(self):
        with (
            patch.object(P, "search_tracks_smart", return_value=[]) as tracks,
            patch.object(P, "jamendo_search_albums_smart", return_value=[]),
            patch.object(P, "jamendo_search_artists", return_value=[]),
            patch.object(P, "audius_search_users", return_value=[]),
        ):
            result = P.search_everything("test", limit=8)
        self.assertEqual(result, {"tracks": [], "albums": [], "artists": []})
        requested = tracks.call_args.kwargs["sources"]
        self.assertIn("soundcloud", requested)
        self.assertIn("youtube", requested)


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

if __name__ == "__main__":
    unittest.main()