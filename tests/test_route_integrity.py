"""Перевірки маршрутів без aiohttp: дублікати, перекриття обробників, фронтенд ↔ бекенд."""
import collections
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "services" / "bot" / "api.py").read_text(encoding="utf-8")
V3 = (ROOT / "webapp" / "v3.js").read_text(encoding="utf-8")

ROUTES = re.findall(r'r\.add_(get|post|delete|put)\("([^"]+)", (\w+)\)', API)
# Свідомі обгортки: _h_artist_base = h_artist; потім h_artist перевизначається.
WRAPPED = {"h_artist", "h_album"}


class RouteIntegrity(unittest.TestCase):
    def test_no_duplicate_method_path(self):
        counts = collections.Counter((m, p) for m, p, _ in ROUTES)
        self.assertEqual([k for k, c in counts.items() if c > 1], [])

    def test_no_handler_is_silently_overridden(self):
        defs = collections.Counter(re.findall(r"^(?:async )?def (\w+)\(", API, re.M))
        self.assertEqual({k for k, c in defs.items() if c > 1} - WRAPPED, set())

    def test_every_route_handler_exists(self):
        names = set(re.findall(r"^(?:async )?def (\w+)\(", API, re.M))
        self.assertEqual([h for _, _, h in ROUTES if h not in names], [])

    def test_v3_frontend_calls_only_registered_routes(self):
        registered = {p for _, p, _ in ROUTES}
        patterns = [re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$") for p in registered]
        called = set(re.findall(r'(?:get|post)\(\s*"(/api/[^"?/][^"?]*[^"?/])"', V3))
        prefixes = set(re.findall(r'"(/api/[a-z-]+/)"\s*\+', V3))  # динамічні: "/api/playlist/"+id
        missing = [c for c in called if not any(rx.match(c) for rx in patterns)]
        missing += [c for c in prefixes if not any(r.startswith(c) for r in registered)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()