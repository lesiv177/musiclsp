#!/usr/bin/env python3
"""Безпечна (лише GET) перевірка Production MusicLSP після деплою.

  MUSICLSP_BASE_URL=https://your-app.up.railway.app python scripts/prod_smoke.py
  MUSICLSP_INIT_DATA='<Telegram initData>' ...   # необов'язково: авторизовані перевірки

Нічого не змінює і не видаляє. Код виходу 0 — усе гаразд, 1 — є провали.
"""
import json
import os
import re
import sys
import time

import requests

TIMEOUT = 20
PROTECTED = ["/api/me", "/api/library", "/api/playlists", "/api/personal-collections",
             "/api/recommendations", "/api/release-radar", "/api/me/export", "/api/history"]
ADMIN = ["/api/admin/overview", "/api/admin/users", "/api/admin/system"]
AUTH_GETS = ["/api/me", "/api/home", "/api/search?q=daft", "/api/charts", "/api/recommendations",
             "/api/personal-collections", "/api/collections", "/api/release-radar", "/api/playlists",
             "/api/library", "/api/history", "/api/queue", "/api/stats"]
SECRET_RE = re.compile(r"(?i)(client_id|client_secret|api_key|access_token|bot_token)=[^&\s\"']{6,}|\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


def timed_get(session, url, headers=None):
    t0 = time.monotonic()
    resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=False)
    return resp, time.monotonic() - t0


def run(base, init_data="", session=None):
    base = base.rstrip("/")
    session = session or requests.Session()
    rep = Report()

    resp, dt = timed_get(session, base + "/")
    rep.add("GET / (WebApp)", resp.status_code == 200 and "<html" in resp.text.lower(), f"{resp.status_code}, {dt:.2f}s")
    if resp.status_code == 200:
        assets = sorted(set(re.findall(r'(?:src|href)="([^"#?]+\.(?:js|css))"', resp.text)))
        for a in [x for x in assets if not x.startswith(("http:", "https:", "//"))][:20]:
            r, _ = timed_get(session, f"{base}/{a.lstrip('/')}")
            rep.add(f"asset {a}", r.status_code == 200, str(r.status_code))

    resp, dt = timed_get(session, base + "/api/health")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    rep.add("GET /api/health", resp.status_code == 200 and data.get("ok") is True, f"{resp.status_code}, {dt:.2f}s")
    rep.add("health: version present", bool(data.get("version")), str(data.get("version")))
    sources = data.get("sources") or {}
    rep.add("health: sources reported", isinstance(sources, dict) and bool(sources),
            ", ".join(f"{k}={'up' if v else 'DOWN'}" for k, v in sources.items()))

    for path in PROTECTED:
        r, _ = timed_get(session, base + path)
        rep.add(f"no auth -> {path}", r.status_code == 401, str(r.status_code))
    for path in ADMIN:
        r, _ = timed_get(session, base + path)
        rep.add(f"no auth -> {path}", r.status_code in (401, 403), str(r.status_code))

    if init_data:
        headers = {"X-Telegram-Init-Data": init_data}
        for path in AUTH_GETS:
            try:
                r, dt = timed_get(session, base + path, headers)
                body = r.json()
                good = r.status_code == 200 and body.get("ok") is not False
                leak = bool(SECRET_RE.search(r.text))
                rep.add(f"auth GET {path}", good and not leak,
                        f"{r.status_code}, {dt:.2f}s" + (", УВАГА: схоже на секрет у відповіді" if leak else ""))
            except (requests.RequestException, ValueError) as exc:
                rep.add(f"auth GET {path}", False, type(exc).__name__)
    else:
        print("(MUSICLSP_INIT_DATA не задано — авторизовані перевірки пропущено)")
    return rep


def main():
    base = os.environ.get("MUSICLSP_BASE_URL", "").strip()
    if not base.startswith(("http://", "https://")):
        print("Задайте MUSICLSP_BASE_URL, напр. https://your-app.up.railway.app")
        return 2
    rep = run(base, os.environ.get("MUSICLSP_INIT_DATA", "").strip())
    print(f"\nПідсумок: {len(rep.rows) - len(rep.failed)} OK, {len(rep.failed)} FAIL")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
