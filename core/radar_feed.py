# -*- coding: utf-8 -*-
"""Release Radar: чиста логіка стрічки нових релізів (без мережі).

Правила з плану: артист — лише точний збіг (MBID або нормалізоване ім'я, без підрядків);
дедуплікація одного релізу з різних джерел; майбутні релізи не показуємо без підтвердження;
фільтр за періодом; позначка «переглянуто».
"""
from __future__ import annotations

from datetime import date, timedelta

from core import catalog
from core.db import conn_cursor, now_str, q, rows
from core.release_radar_matching import release_matches_followed_artist

PERIODS = {"week": 7, "month": 30, "quarter": 90}
DEFAULT_PERIOD = "quarter"
RELEASE_TYPES = {"album", "single", "ep", "compilation"}


def parse_date(raw):
    raw = str(raw or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def release_key(release: dict) -> str:
    """Стабільний ключ для дедуплікації між джерелами: артист + назва (без видань) + рік."""
    d = parse_date(release.get("release_date"))
    return "|".join((catalog.artist_key(release.get("artist") or release.get("artist_name")),
                     catalog.title_key(release.get("title")), str(d.year) if d else ""))


def normalize_release_type(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in RELEASE_TYPES else ""


def build_feed(follows: list, albums_by_artist: dict, period: str = DEFAULT_PERIOD,
               seen: set | None = None, today: date | None = None, limit: int = 60) -> list:
    """follows: [{artist_name, artist:{...}}]; albums_by_artist: {name_casefold: [album,...]}."""
    today = today or date.today()
    days = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
    start = today - timedelta(days=days)
    seen = seen or set()
    merged: dict = {}
    for followed in follows or []:
        artist = followed.get("artist") or {}
        name = str(followed.get("artist_name") or artist.get("name") or "").strip()
        if not name:
            continue
        identity = {**artist, "name": artist.get("name") or name}
        for album in albums_by_artist.get(name.casefold(), []):
            if not isinstance(album, dict) or not release_matches_followed_artist(album, identity):
                continue
            released = parse_date(album.get("release_date"))
            # Лише підтверджена дата в межах періоду; майбутні/без дати не показуємо.
            if not released or not (start <= released <= today):
                continue
            key = release_key(album)
            if not key.strip("|"):
                continue
            source = str(album.get("source") or album.get("provider") or "").strip()
            entry = merged.get(key)
            if entry is None:
                entry = {**album, "followed_artist": name, "release_key": key,
                         "release_type": normalize_release_type(album.get("release_type") or album.get("type")),
                         "sources": []}
                merged[key] = entry
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
            if not entry.get("cover") and album.get("cover"):
                entry["cover"] = album["cover"]
    items = list(merged.values())
    for item in items:
        item["seen"] = item["release_key"] in seen
    items.sort(key=lambda r: (parse_date(r.get("release_date")) or date.min, r["title"] if r.get("title") else ""),
               reverse=True)
    return items[:max(1, int(limit))]


# ── Позначка «переглянуто» (окрема таблиця radar_seen, див. user_data) ─────

def seen_keys(uid: int) -> set:
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT release_key FROM radar_seen WHERE uid=%s"), (int(uid),))
        return {r["release_key"] for r in rows(cur)}


def mark_seen(uid: int, keys) -> int:
    keys = list(dict.fromkeys(str(k)[:300] for k in keys or [] if k))[:200]
    for key in keys:
        with conn_cursor() as (_c, cur):
            cur.execute(q("DELETE FROM radar_seen WHERE uid=%s AND release_key=%s"), (int(uid), key))
            cur.execute(q("INSERT INTO radar_seen (uid, release_key, seen_at) VALUES (%s,%s,%s)"),
                        (int(uid), key, now_str()))
    return len(keys)