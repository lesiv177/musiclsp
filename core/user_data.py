# -*- coding: utf-8 -*-
"""Дані користувача: приховані рекомендації, експорт і видалення власних даних."""
from __future__ import annotations

import json
import logging

from core.db import conn_cursor, now_str, q, rows

logger = logging.getLogger(__name__)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS rec_hidden (
        uid BIGINT NOT NULL, rec_key TEXT NOT NULL, hidden_at TEXT, PRIMARY KEY (uid, rec_key))""",
    """CREATE TABLE IF NOT EXISTS radar_seen (
        uid BIGINT NOT NULL, release_key TEXT NOT NULL, seen_at TEXT, PRIMARY KEY (uid, release_key))""",
]

# Таблиці з колонкою uid, які містять особисті дані (порядок не важливий).
PERSONAL_TABLES = [
    "library", "history", "search_history", "collection_library", "followed_artists",
    "release_radar_notifications", "radar_seen", "saved_queues", "eq_presets", "rec_hidden",
    "playback_devices", "account_playback", "playback_commands", "blindtest_scores",
]
# Профіль зберігається як акаунт (тариф, мова); його поля очищаємо окремо.
EXPORT_TABLES = [
    "library", "history", "search_history", "collection_library", "followed_artists",
    "saved_queues", "eq_presets", "rec_hidden",
]
USER_COLUMNS = ("uid", "username", "first_name", "lang", "premium", "premium_until",
                "theme", "accent", "quality", "created_at")


def ensure_schema():
    for stmt in SCHEMA:
        try:
            with conn_cursor() as (_c, cur):
                cur.execute(stmt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("user_data schema: %s", exc)


def hide_recommendation(uid: int, rec_key: str) -> bool:
    rec_key = str(rec_key or "")[:300]
    if not rec_key:
        return False
    with conn_cursor() as (_c, cur):
        cur.execute(q("DELETE FROM rec_hidden WHERE uid=%s AND rec_key=%s"), (int(uid), rec_key))
        cur.execute(q("INSERT INTO rec_hidden (uid, rec_key, hidden_at) VALUES (%s,%s,%s)"),
                    (int(uid), rec_key, now_str()))
    return True


def hidden_keys(uid: int) -> set:
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT rec_key FROM rec_hidden WHERE uid=%s"), (int(uid),))
        return {r["rec_key"] for r in rows(cur)}


def export_user(uid: int) -> dict:
    """Повний експорт особистих даних лише цього користувача (ізоляція за uid)."""
    uid = int(uid)
    data = {"exported_at": now_str(), "uid": uid}
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT * FROM users WHERE uid=%s"), (uid,))
        row = cur.fetchone()
        data["account"] = {k: dict(row).get(k) for k in USER_COLUMNS} if row else {}
        for table in EXPORT_TABLES:
            cur.execute(q(f"SELECT * FROM {table} WHERE uid=%s"), (uid,))  # table — з білого списку
            data[table] = rows(cur)
        cur.execute(q("SELECT * FROM playlists WHERE uid=%s"), (uid,))
        playlists = rows(cur)
        for p in playlists:
            cur.execute(q("SELECT * FROM playlist_tracks WHERE pid=%s ORDER BY position, id"), (p["id"],))
            p["tracks"] = rows(cur)
        data["playlists"] = playlists
    return json.loads(json.dumps(data, default=str))


def delete_user_data(uid: int) -> dict:
    """Видаляє особисті дані. Рядок акаунта лишається (тариф/мова), але ім'я й username очищаються."""
    uid = int(uid)
    deleted = {}
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT id FROM playlists WHERE uid=%s"), (uid,))
        pids = [r["id"] for r in rows(cur)]
        for pid in pids:
            cur.execute(q("DELETE FROM playlist_tracks WHERE pid=%s"), (pid,))
        cur.execute(q("DELETE FROM playlists WHERE uid=%s"), (uid,))
        deleted["playlists"] = len(pids)
        for table in PERSONAL_TABLES + ["track_reports"]:
            cur.execute(q(f"DELETE FROM {table} WHERE uid=%s"), (uid,))
            deleted[table] = cur.rowcount
        cur.execute(q("UPDATE users SET username=%s, first_name=%s WHERE uid=%s"), ("", "", uid))
    return deleted