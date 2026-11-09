import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("prod_smoke", Path(__file__).resolve().parents[1] / "scripts" / "prod_smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class FakeResp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else (__import__("json").dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, timeout=None, allow_redirects=False):
        path = "/" + url.split("//", 1)[1].split("/", 1)[1] if url.count("/") > 2 else "/"
        self.calls.append((path, bool(headers)))
        return self.routes(path, bool(headers))


def healthy(path, authed):
    if path == "/":
        return FakeResp(200, text='<html><script src="v3.js"></script></html>')
    if path == "/v3.js":
        return FakeResp(200, text="//")
    if path == "/api/health":
        return FakeResp(200, {"ok": True, "version": "x", "sources": {"audius": True}})
    if path.startswith("/api/admin") or not authed:
        return FakeResp(401, {"ok": False})
    return FakeResp(200, {"ok": True})


class ProdSmokeScript(unittest.TestCase):
    def test_all_green_on_healthy_server(self):
        rep = SMOKE.run("https://x.test", "init", FakeSession(healthy))
        self.assertEqual(rep.failed, [])

    def test_only_get_requests_are_made(self):
        s = FakeSession(healthy)
        SMOKE.run("https://x.test", "init", s)
        self.assertTrue(s.calls)
        self.assertNotIn("post", Path(SMOKE.__file__).read_text(encoding="utf-8").lower().replace("posted", ""))

    def test_detects_open_protected_endpoint(self):
        def leaky(path, authed):
            return FakeResp(200, {"ok": True}) if path == "/api/library" else healthy(path, authed)
        rep = SMOKE.run("https://x.test", "", FakeSession(leaky))
        self.assertIn("no auth -> /api/library", [r[0] for r in rep.failed])

    def test_detects_secret_in_response(self):
        def secret(path, authed):
            if authed and path == "/api/home":
                return FakeResp(200, {"ok": True}, text='{"ok": true, "u": "https://a.b/x?client_id=ABCDEF123456"}')
            return healthy(path, authed)
        rep = SMOKE.run("https://x.test", "init", FakeSession(secret))
        self.assertIn("auth GET /api/home", [r[0] for r in rep.failed])

    def test_requires_base_url(self):
        import os
        old = os.environ.pop("MUSICLSP_BASE_URL", None)
        try:
            self.assertEqual(SMOKE.main(), 2)
        finally:
            if old:
                os.environ["MUSICLSP_BASE_URL"] = old


if __name__ == "__main__":
    unittest.main()