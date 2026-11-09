import io
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

import requests

from core import db, log_safety, playlists_ext as PX, providers, user_data
from core.safe_url import public_http_url


class SafeUrlTests(unittest.TestCase):
    def test_accepts_public_http_and_https(self):
        for url in ("https://example.com/a?b=1", "http://artist.example.org", "https://sub.site.co.uk/x#y"):
            self.assertEqual(public_http_url(url), url)

    def test_rejects_dangerous_schemes(self):
        for url in ("javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd",
                    "ftp://example.com", "//example.com", "example.com", "vbscript:x", ""):
            self.assertEqual(public_http_url(url), "", url)

    def test_rejects_credentials_and_local_hosts(self):
        for url in ("https://user:pass@example.com", "https://user@example.com",
                    "http://localhost/x", "http://127.0.0.1/x", "http://10.0.0.5/x",
                    "http://192.168.1.1", "http://169.254.169.254/latest", "http://[::1]/x",
                    "http://printer.local/x", "http://intranet/x", "http://svc.internal/x"):
            self.assertEqual(public_http_url(url), "", url)

    def test_rejects_control_chars_whitespace_bad_port_and_long(self):
        for url in ("https://exa mple.com", "https://example.com/a\nb", "https://example.com/\x00",
                    "https://example.com:99999999/x", "https://example.com/" + "a" * 3000):
            self.assertEqual(public_http_url(url), "", repr(url)[:40])

    def test_non_strings(self):
        for value in (None, 123, [], {}):
            self.assertEqual(public_http_url(value), "")


class ProfileUrlTests(unittest.TestCase):
    def test_jamendo_profile_sanitizes_urls(self):
        data = {"results": [{"id": "7", "name": "A", "image": "javascript:x",
                             "website": "http://127.0.0.1/admin", "shareurl": "https://u:p@jamendo.com/a"}]}
        with patch.object(providers, "jamendo_enabled", return_value=True), \
                patch.object(providers, "_get", return_value=data):
            out = providers.jamendo_artist_by_id("7")
        self.assertEqual((out["website"], out["source_url"], out["cover"]), ("", "", ""))

    def test_audius_profile_keeps_good_and_drops_bad(self):
        data = {"data": {"id": "1", "handle": "h", "name": "N", "website": "https://ok.example.com",
                         "profile_picture": {"480x480": "http://localhost/x.png"}}}
        with patch.object(providers, "audius_host", return_value="https://host.example.com"), \
                patch.object(providers, "_get", return_value=data):
            out = providers.audius_user_by_id("1")
        self.assertEqual(out["website"], "https://ok.example.com")
        self.assertEqual(out["cover"], "")

    def test_playlist_cover_uses_shared_validator(self):
        self.assertIsNone(PX.clean_cover("http://169.254.169.254/x"))
        self.assertIsNone(PX.clean_cover("https://u:p@example.com/c.jpg"))
        self.assertEqual(PX.clean_cover(""), "")
        self.assertEqual(PX.clean_cover("https://example.com/c.jpg"), "https://example.com/c.jpg")


class LogRedactionTests(unittest.TestCase):
    def test_redact_patterns(self):
        cases = [
            "GET https://api.example.com/x?client_id=SECRET123&limit=5",
            "Authorization: Bearer abcdef1234567890",
            "token 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw9 leaked",
            "postgres://user:hunter2@db.host:5432/app",
        ]
        for text in cases:
            out = log_safety.redact(text)
            for secret in ("SECRET123", "abcdef1234567890", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw9", "hunter2"):
                self.assertNotIn(secret, out)
        self.assertIn("limit=5", log_safety.redact(cases[0]))

    def test_installed_factory_redacts_real_log_output(self):
        log_safety.install()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("musiclsp.test.redact")
        logger.addHandler(handler)
        logger.propagate = False
        try:
            try:
                raise requests.ConnectionError("https://api.jamendo.com/v3.0/x?client_id=TOPSECRET&format=json")
            except Exception:
                logger.warning("fail: %s", "key=TOPSECRET2")
                logger.exception("boom")
        finally:
            logger.removeHandler(handler)
        text = stream.getvalue()
        self.assertNotIn("TOPSECRET", text)
        self.assertIn("[REDACTED]", text)

    def test_get_logs_do_not_contain_query_secrets(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        providers.logger.addHandler(handler)
        try:
            with patch.object(providers, "_http_session") as sess:
                sess.return_value.get.side_effect = requests.ConnectionError("boom ?client_id=LEAK")
                providers._get("https://api.example.com/v1/x", {"client_id": "LEAK"})
        finally:
            providers.logger.removeHandler(handler)
        self.assertNotIn("LEAK", stream.getvalue())


class UserIsolationTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patches = [patch.object(db, "SQLITE_PATH", self.path), patch.object(db, "USE_PG", False)]
        for p in self.patches:
            p.start()
        db.init_db()
        self.p1, _ = db.pl_create(1, "Mine", "")
        self.p2, _ = db.pl_create(2, "Theirs", "")
        db.pl_add_track(1, self.p1["id"], {"id": "aud:1", "title": "T", "artist": "A", "cover": ""})
        db.pl_add_track(2, self.p2["id"], {"id": "aud:2", "title": "T2", "artist": "B", "cover": ""})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.unlink(self.path)

    def test_cannot_modify_or_delete_foreign_playlist(self):
        pid = self.p2["id"]
        self.assertFalse(db.pl_add_track(1, pid, {"id": "aud:9", "title": "x", "artist": "", "cover": ""})[0])
        self.assertFalse(db.pl_remove_track(1, pid, "aud:2"))
        self.assertFalse(db.pl_delete(1, pid))
        self.assertFalse(PX.update(1, pid, name="hacked")[0])
        self.assertFalse(PX.reorder(1, pid, ["aud:2"])[0])
        got = db.pl_get(pid)
        self.assertEqual((got["name"], len(got["tracks"])), ("Theirs", 1))

    def test_listing_only_own_playlists(self):
        self.assertEqual([p["id"] for p in db.pl_list(1)], [self.p1["id"]])
        self.assertEqual([p["id"] for p in db.pl_list(2)], [self.p2["id"]])

    def test_private_playlist_hidden_and_public_view_hides_owner(self):
        PX.update(2, self.p2["id"], is_public=False)
        self.assertIsNone(PX.public_view(db.pl_get(self.p2["id"])))
        PX.update(2, self.p2["id"], is_public=True)
        view = PX.public_view(db.pl_get(self.p2["id"]))
        self.assertNotIn("uid", view)

    def test_export_and_delete_touch_only_that_user(self):
        user_data.ensure_schema()
        export = user_data.export_user(1)
        self.assertNotIn("Theirs", str(export))
        user_data.delete_user_data(1)
        self.assertIsNotNone(db.pl_get(self.p2["id"]))
        self.assertEqual(len(db.pl_get(self.p2["id"])["tracks"]), 1)


if __name__ == "__main__":
    unittest.main()