import asyncio
import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import auth, log_safety, web_login as WL

TOKEN = "123456789:AAtestTOKENtestTOKENtestTOKENtest12"


def widget(uid=42, now=None, token=TOKEN, **extra):
    now = int(now or time.time())
    data = {"id": uid, "first_name": "Іван", "username": "ivan", "auth_date": now, **extra}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(token.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


class WidgetVerifyTests(unittest.TestCase):
    def test_valid_widget_login(self):
        user = WL.verify_widget_login(widget(), TOKEN)
        self.assertEqual(user, {"id": 42, "username": "ivan", "first_name": "Іван"})

    def test_tampered_or_wrong_bot_is_rejected(self):
        data = widget()
        data["id"] = 43
        self.assertIsNone(WL.verify_widget_login(data, TOKEN))
        self.assertIsNone(WL.verify_widget_login(widget(token="1:otherOTHERotherOTHERotherOTHER"), TOKEN))
        no_hash = widget(); no_hash.pop("hash")
        self.assertIsNone(WL.verify_widget_login(no_hash, TOKEN))

    def test_stale_and_future_logins_are_rejected(self):
        now = time.time()
        self.assertIsNone(WL.verify_widget_login(widget(now=now - WL.LOGIN_MAX_AGE - 5), TOKEN, now=now))
        self.assertIsNone(WL.verify_widget_login(widget(now=now + 3600), TOKEN, now=now))

    def test_garbage_input_is_rejected(self):
        for bad in (None, [], "x", {}, {"hash": "a"}, {"id": True, "auth_date": 1, "hash": "a"}):
            self.assertIsNone(WL.verify_widget_login(bad, TOKEN))
        self.assertIsNone(WL.verify_widget_login(widget(), ""))


class SessionTests(unittest.TestCase):
    def test_roundtrip(self):
        token = WL.issue_session({"id": 7, "username": "u", "first_name": "N"}, TOKEN)
        self.assertTrue(token.startswith(WL.WEB_PREFIX))
        self.assertEqual(WL.verify_session(token, TOKEN), {"id": 7, "username": "u", "first_name": "N"})

    def test_expired_wrong_key_and_tampered(self):
        now = time.time()
        token = WL.issue_session({"id": 7}, TOKEN, now=now, ttl=10)
        self.assertIsNone(WL.verify_session(token, TOKEN, now=now + 11))
        self.assertIsNone(WL.verify_session(token, "999:otherOTHERotherOTHERotherOTHER", now=now))
        body, sig = token[len(WL.WEB_PREFIX):].split(".")
        forged = WL.issue_session({"id": 8}, "999:otherOTHERotherOTHERotherOTHER")
        forged_body = forged[len(WL.WEB_PREFIX):].split(".")[0]
        self.assertIsNone(WL.verify_session(f"{WL.WEB_PREFIX}{forged_body}.{sig}", TOKEN))

    def test_never_raises_on_garbage(self):
        for bad in ("", None, "mlspweb.", "mlspweb.a.b", "mlspweb." + "x" * 5000, 5, "mlspweb.!!!.???"):
            self.assertIsNone(WL.verify_session(bad, TOKEN))

    def test_telegram_init_data_path_accepts_web_session(self):
        token = WL.issue_session({"id": 11, "username": "w", "first_name": "W"}, TOKEN)
        user = auth.verify_init_data(token, token=TOKEN)
        self.assertEqual(user["id"], 11)
        self.assertIsNone(auth.verify_init_data("mlspweb.bad.bad", token=TOKEN))


class LogRedactionTests(unittest.TestCase):
    def test_session_and_bearer_are_redacted(self):
        token = WL.issue_session({"id": 5}, TOKEN)
        line = f"GET /api/stream?id=1&initData={token} Authorization: Bearer abc.def-123"
        out = log_safety.redact(line)
        self.assertNotIn(token, out)
        self.assertNotIn("abc.def-123", out)
        self.assertNotIn(token[len(WL.WEB_PREFIX):][:20], out)


class EndpointTests(unittest.TestCase):
    def _api(self):
        from services.bot import api
        return api

    def _req(self, body, ip="1.2.3.4"):
        async def _json():
            return body
        return SimpleNamespace(headers={"X-Forwarded-For": ip}, query={}, remote=ip, json=_json,
                               can_read_body=True, content_length=10, text=None)

    def test_login_endpoint_issues_session_for_valid_widget_data(self):
        api = self._api()
        api._login_hits.clear()
        async def fake_json(request):
            return request._body
        req = self._req(None)
        req._body = widget()
        with patch.object(api, "BOT_TOKEN", TOKEN), patch.object(api, "json_object", fake_json):
            resp = asyncio.run(api.h_web_login(req))
        data = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertEqual(WL.verify_session(data["token"], TOKEN)["id"], 42)

    def test_login_endpoint_rejects_bad_signature_and_rate_limits(self):
        api = self._api()
        api._login_hits.clear()
        async def fake_json(request):
            return request._body
        bad = widget(); bad["hash"] = "0" * 64
        statuses = []
        with patch.object(api, "BOT_TOKEN", TOKEN), patch.object(api, "json_object", fake_json):
            for _ in range(api.LOGIN_MAX_ATTEMPTS + 2):
                req = self._req(None, ip="9.9.9.9"); req._body = bad
                statuses.append(asyncio.run(api.h_web_login(req)).status)
        self.assertEqual(statuses[:api.LOGIN_MAX_ATTEMPTS], [401] * api.LOGIN_MAX_ATTEMPTS)
        self.assertEqual(statuses[api.LOGIN_MAX_ATTEMPTS:], [429, 429])

    def test_web_config_reports_bot_username(self):
        api = self._api()
        with patch.object(api.RT, "get_bot_username", return_value="musiclsp_bot"), patch.object(api, "BOT_TOKEN", TOKEN):
            data = json.loads(asyncio.run(api.h_web_config(self._req(None))).text)
        self.assertEqual(data, {"ok": True, "bot_username": "musiclsp_bot", "web_login": True})

    def test_routes_registered_and_cors_allows_authorization(self):
        api = self._api()
        paths = {(r.method, getattr(r.resource, "canonical", "")) for r in api.build_app().router.routes()}
        self.assertIn(("POST", "/api/auth/web-login"), paths)
        self.assertIn(("GET", "/api/web-config"), paths)
        self.assertIn("Authorization", api.CORS["Access-Control-Allow-Headers"])


if __name__ == "__main__":
    unittest.main()