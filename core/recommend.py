# -*- coding: utf-8 -*-
"""Прозорі рекомендації за правилами (без ML). Чиста логіка.

Сигнали: обрані треки (вага 2), прослуховування (1), підписки на артистів (3).
Якщо сигналів мало — повертаємо ``enough_data=False`` і нічого не вигадуємо.
Кожна рекомендація має зрозуміле ``reason``.
"""
from __future__ import annotations

from collections import Counter

from core import catalog

MIN_SIGNAL = 3
W_LIBRARY, W_HISTORY, W_FOLLOW = 2, 1, 3


def track_key(t: dict) -> str:
    return f"{catalog.artist_key(t.get('artist'))}|{catalog.title_key(t.get('title'))}"


def taste_profile(library=None, history=None, following=None) -> dict:
    """Зважений профіль улюблених артистів + посідні треки-«насіння» (seed)."""
    weights: Counter = Counter()
    names: dict = {}
    seeds: dict = {}

    def add(name, w, track=None):
        key = catalog.artist_key(name)
        if not key:
            return
        weights[key] += w
        names.setdefault(key, str(name))
        if track and track.get("id"):
            seeds.setdefault(key, track["id"])

    for t in library or []:
        add(t.get("artist"), W_LIBRARY, t)
    for t in history or []:
        add(t.get("artist"), W_HISTORY, t)
    for f in following or []:
        add(f.get("artist_name") or (f.get("artist") or {}).get("name"), W_FOLLOW)
    top = [{"key": k, "artist": names[k], "weight": w, "seed": seeds.get(k, "")}
           for k, w in weights.most_common()]
    return {"total": sum(weights.values()), "top": top}


def pick_seeds(profile: dict, limit: int = 3) -> list:
    """Артисти з відомим seed-треком, за спаданням ваги."""
    return [a for a in profile["top"] if a["seed"]][:limit]


def build(profile: dict, candidates_by_seed: dict, known: list, hidden: set, limit: int = 30,
          per_artist: int = 3) -> dict:
    """candidates_by_seed: {seed_artist_key: [track,...]}. known — вже відомі користувачу треки."""
    if profile["total"] < MIN_SIGNAL:
        return {"enough_data": False, "tracks": [], "reason": "Замало даних: додай треки в обране або послухай музику."}
    seen = {track_key(t) for t in known or []} | set(hidden or ())
    liked_artists = {a["key"] for a in profile["top"]}
    out, per = [], Counter()
    by_key = {a["key"]: a["artist"] for a in profile["top"]}
    # Чергуємо seed-артистів, щоб один не домінував.
    lists = [(k, list(v)) for k, v in candidates_by_seed.items()]
    idx = 0
    while len(out) < limit and any(v for _, v in lists):
        k, v = lists[idx % len(lists)]
        idx += 1
        while v:
            t = v.pop(0)
            if not isinstance(t, dict) or not t.get("id") or not t.get("title"):
                continue
            tk = track_key(t)
            ak = catalog.artist_key(t.get("artist"))
            if tk in seen or per[ak] >= per_artist:
                continue
            seen.add(tk)
            per[ak] += 1
            out.append({**t, "reason": f"Схоже на {by_key.get(k, '')}".strip(),
                        "rec_key": tk, "new_artist": ak not in liked_artists})
            break
    return {"enough_data": True, "tracks": out, "reason": ""}