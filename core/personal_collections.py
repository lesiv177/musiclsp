# -*- coding: utf-8 -*-
"""Персональні добірки з власних даних користувача (обране + історія). Чиста логіка, без мережі.

Нічого не вигадується: добірка з'являється лише якщо є достатньо реальних треків.
 - «Більше від {артист}»: артисти, у яких ≥ MIN_ARTIST_TRACKS різних треків в обраному/історії;
 - «Забуті улюблені»: обрані треки, яких немає серед останніх прослуховувань (≥ MIN_FORGOTTEN).
"""
from __future__ import annotations

from collections import OrderedDict

from core import catalog
from core.recommend import track_key

MIN_ARTIST_TRACKS = 3
MIN_FORGOTTEN = 5
MAX_COLLECTIONS = 4
MAX_TRACKS = 30
RECENT_HISTORY_WINDOW = 50


def _valid(t) -> bool:
    return isinstance(t, dict) and bool(t.get("id")) and bool(t.get("title"))


def _dedup(tracks) -> list:
    seen, out = set(), []
    for t in tracks:
        if not _valid(t):
            continue
        key = track_key(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def build(library=None, history=None, hidden=None) -> dict:
    hidden = set(hidden or ())
    library = [t for t in _dedup(library or []) if track_key(t) not in hidden]
    history = [t for t in _dedup(history or []) if track_key(t) not in hidden]
    collections = []

    # Більше від артиста: обране має пріоритет, далі історія; різні треки, без дублікатів.
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for t in library + history:
        ak = catalog.artist_key(t.get("artist"))
        if not ak:
            continue
        g = groups.setdefault(ak, {"name": str(t.get("artist")), "tracks": [], "keys": set()})
        k = track_key(t)
        if k not in g["keys"]:
            g["keys"].add(k)
            g["tracks"].append(t)
    ranked = sorted(groups.values(), key=lambda g: len(g["tracks"]), reverse=True)  # стабільне сортування
    for g in ranked:
        if len(g["tracks"]) < MIN_ARTIST_TRACKS or len(collections) >= MAX_COLLECTIONS - 1:
            continue
        collections.append({
            "id": "artist:" + catalog.artist_key(g["name"]),
            "kind": "artist_mix",
            "title": f"Більше від {g['name']}",
            "reason": f"У твоїй колекції {len(g['tracks'])} треків цього артиста",
            "tracks": g["tracks"][:MAX_TRACKS],
        })

    recent = {track_key(t) for t in history[:RECENT_HISTORY_WINDOW]}
    forgotten = [t for t in library if track_key(t) not in recent]
    if len(forgotten) >= MIN_FORGOTTEN and len(collections) < MAX_COLLECTIONS:
        collections.append({
            "id": "forgotten",
            "kind": "forgotten_favorites",
            "title": "Забуті улюблені",
            "reason": "Треки з обраного, які ти давно не слухав",
            "tracks": forgotten[:MAX_TRACKS],
        })

    if not collections:
        return {"enough_data": False, "collections": [],
                "reason": "Поки замало даних: збережи кілька треків одного артиста або більше слухай."}
    return {"enough_data": True, "collections": collections, "reason": ""}