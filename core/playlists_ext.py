# -*- coding: utf-8 -*-
"""Розширення плейлистів: редагування, перестановка, приватність, безпечний публічний вигляд.

Працює поверх наявних таблиць playlists/playlist_tracks; єдина зміна схеми — колонка
``is_public`` (існуючі плейлисти лишаються доступними за посиланням, нові — приватні).
"""
from __future__ import annotations

import logging
from core.safe_url import public_http_url

from core.db import conn_cursor, one, q, rows

logger = logging.getLogger(__name__)

NAME_MAX, DESC_MAX, COVER_MAX = 60, 200, 500
_UNSET = object()


def ensure_schema():
    try:
        with conn_cursor() as (_c, cur):
            cur.execute("ALTER TABLE playlists ADD COLUMN is_public INTEGER DEFAULT 1")
    except Exception:  # noqa: BLE001 — колонка вже існує
        pass


def clean_name(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:NAME_MAX]
    return value or None


def clean_description(value):
    return " ".join(value.split())[:DESC_MAX] if isinstance(value, str) else None


def clean_cover(value):
    """Лише http(s)-URL; порожній рядок — прибрати обкладинку; javascript:/data: відхиляються."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return ""
    if len(value) > COVER_MAX:
        return None
    return public_http_url(value, COVER_MAX) or None


def _owned(cur, uid, pid):
    cur.execute(q("SELECT id FROM playlists WHERE id=%s AND uid=%s"), (int(pid), int(uid)))
    return one(cur) is not None


def update(uid, pid, name=_UNSET, description=_UNSET, cover=_UNSET, is_public=_UNSET):
    """Оновити лише передані поля. Повертає (True, "") або (False, причина)."""
    sets, vals = [], []
    if name is not _UNSET:
        n = clean_name(name)
        if n is None:
            return False, "Некоректна назва плейлиста"
        sets.append("name=%s"); vals.append(n)
    if description is not _UNSET:
        d = clean_description(description)
        if d is None:
            return False, "Некоректний опис"
        sets.append("description=%s"); vals.append(d)
    if cover is not _UNSET:
        c = clean_cover(cover)
        if c is None:
            return False, "Обкладинка має бути http(s)-посиланням"
        sets.append("cover=%s"); vals.append(c)
    if is_public is not _UNSET:
        if not isinstance(is_public, bool):
            return False, "is_public має бути true/false"
        sets.append("is_public=%s"); vals.append(1 if is_public else 0)
    if not sets:
        return False, "Немає змін"
    with conn_cursor() as (_c, cur):
        if not _owned(cur, uid, pid):
            return False, "Плейлист не знайдено"
        cur.execute(q("UPDATE playlists SET " + ", ".join(sets) + " WHERE id=%s AND uid=%s"),
                    (*vals, int(pid), int(uid)))
    return True, ""


def reorder(uid, pid, ordered_tids):
    """Нова послідовність треків. Невідомі ID ігноруються, непередані — лишаються в кінці в
    попередньому порядку; жоден трек не губиться і не дублюється."""
    if not isinstance(ordered_tids, list) or not all(isinstance(t, str) for t in ordered_tids):
        return False, "Очікується список ID треків"
    with conn_cursor() as (_c, cur):
        if not _owned(cur, uid, pid):
            return False, "Плейлист не знайдено"
        cur.execute(q("SELECT tid FROM playlist_tracks WHERE pid=%s ORDER BY position, id"), (int(pid),))
        current = [r["tid"] for r in rows(cur)]
        known, seen, final = set(current), set(), []
        for tid in ordered_tids + current:
            if tid in known and tid not in seen:
                seen.add(tid)
                final.append(tid)
        for pos, tid in enumerate(final):
            cur.execute(q("UPDATE playlist_tracks SET position=%s WHERE pid=%s AND tid=%s"),
                        (pos, int(pid), tid))
    return True, ""


def public_view(playlist):
    """Вигляд для чужих очей: без uid власника. None, якщо плейлист приватний."""
    if not isinstance(playlist, dict):
        return None
    if int(playlist.get("is_public") if playlist.get("is_public") is not None else 1) != 1:
        return None
    hidden = {"uid", "is_public"}  # id потрібен фронтенду; uid власника не розкриваємо
    return {k: v for k, v in playlist.items() if k not in hidden}


def copy_shared(uid, code):
    """Зберегти собі чужий ПУБЛІЧНИЙ плейлист за share-кодом. Копія — приватна, власник — uid."""
    from core import db
    src = db.pl_get_by_code(str(code or "")[:40])
    view = public_view(src) if src else None
    if view is None:
        return None, "Плейлист не знайдено"
    new, err = db.pl_create(int(uid), view.get("name") or "Плейлист", view.get("description") or "")
    if not new:
        return None, err
    update(uid, new["id"], is_public=False)
    for t in src.get("tracks") or []:
        db.pl_add_track(int(uid), new["id"], t)
    return db.pl_get(new["id"]), ""