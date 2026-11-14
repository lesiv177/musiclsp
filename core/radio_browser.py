# -*- coding: utf-8 -*-
"""
Інтернет-радіо через відкриту базу Radio Browser (radio-browser.info).

Це лише довідник станцій: ми віддаємо назву, країну, жанр-теги, логотип і ПОСИЛАННЯ на офіційний ефір
станції. Сервер MusicLSP потік не проксіює й не записує: клієнт відтворює його напряму.
Беремо тільки https-потоки; станції, позначені як непрацюючі, відкидаємо.
Вимкнути: RADIO_ENABLED=0.
"""
from __future__ import annotations

import logging
import os
import urllib.parse

logger = logging.getLogger(__name__)

SERVERS = ("https://de1.api.radio-browser.info", "https://at1.api.radio-browser.info",
           "https://nl1.api.radio-browser.info")


def enabled() -> bool:
    return os.getenv("RADIO_ENABLED", "1").strip() != "0"


def _default_fetch(url, params=None):
    from core import providers
    return providers._get(url, params=params, timeout=8)


def _https(url):
    url = str(url or "").strip()
    return url if url.startswith("https://") else ""


def normalize(st):
    if not isinstance(st, dict):
        return None
    stream = _https(st.get("url_resolved") or st.get("url"))
    sid = str(st.get("stationuuid") or "").strip()
    name = str(st.get("name") or "").strip()
    if not stream or not sid or not name or int(st.get("lastcheckok") or 0) != 1:
        return None
    tags = [t.strip() for t in str(st.get("tags") or "").split(",") if t.strip()][:5]
    return {"id": f"rb:{sid}", "title": name[:120], "artist": str(st.get("country") or "Радіо")[:60],
            "album": "", "cover": _https(st.get("favicon")), "duration": 0, "duration_str": "LIVE",
            "source": "radio", "source_label": "Radio Browser", "live": True, "stream": stream,
            "source_url": _https(st.get("homepage")), "tags": tags, "country_code": str(st.get("countrycode") or "")[:2],
            "bitrate": int(st.get("bitrate") or 0), "license": "", "license_short": "Ефір станції",
            "downloadable": False, "download_url": ""}


def _query(path, params, fetch):
    fetch = fetch or _default_fetch
    for base in SERVERS:
        data = fetch(base + path, params)
        if isinstance(data, list):
            return data
    return []


def search(query="", country="", tag="", limit=30, fetch=None):
    if not enabled():
        return []
    limit = max(1, min(int(limit), 50))
    params = {"limit": limit * 2, "hidebroken": "true", "order": "clickcount", "reverse": "true"}
    if query:
        params["name"] = str(query)[:80]
    if country:
        params["countrycode"] = str(country)[:2].upper()
    if tag:
        params["tag"] = str(tag)[:40]
    out, seen = [], set()
    try:
        for st in _query("/json/stations/search", params, fetch):
            item = normalize(st)
            if item and item["id"] not in seen:
                seen.add(item["id"])
                out.append(item)
            if len(out) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("radio search failed: %s", type(exc).__name__)
    return out


_UUID = __import__("re").compile(r"^[0-9a-fA-F-]{8,40}$")


def station_url(uuid, fetch=None):
    """Актуальне https-посилання на ефір станції за її UUID (для редіректу з /api/stream)."""
    uuid = str(uuid or "").removeprefix("rb:").strip()
    if not enabled() or not _UUID.match(uuid):
        return ""
    try:
        for st in _query(f"/json/stations/byuuid/{uuid}", {}, fetch):
            item = normalize(st)
            if item:
                return item["stream"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("radio resolve failed: %s", type(exc).__name__)
    return ""