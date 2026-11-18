# -*- coding: utf-8 -*-
"""
Крок 2: «метадані → відтворюваний трек». Для пари (артист, назва) шукаємо трек у всіх легальних джерелах
каталогу й вибираємо найкращий збіг. Слабкі збіги відкидаємо: краще чесне «не знайдено», ніж чужий трек.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait

from core import catalog, search_rank, supersource

logger = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="track-resolver")
MIN_TITLE = 0.65     # усі слова назви є у назві кандидата
MIN_ARTIST = 0.35
MAX_BATCH = 30
TRUST = {"jamendo": 1.0, "audius": 0.9, "archive": 0.8, "openverse": 0.75, "ccmixter": 0.75,
         "wikimedia": 0.7, "soundcloud": 0.85, "youtube": 0.6, "curated": 0.9}


def _clean_title(title):
    """Прибираємо дописки на кшталт (feat. X), [Remastered], - Single для порівняння."""
    import re
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", str(title or ""))
    t = re.sub(r"\s+-\s+(single|remaster(ed)?.*|radio edit|live.*)$", "", t, flags=re.I)
    return " ".join(t.split())


def pick(artist, title, candidates):
    want_title = _clean_title(title) or str(title or "")
    best, best_score = None, 0.0
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        cand_title = _clean_title(c.get("title"))
        t = max(search_rank.relevance(want_title, cand_title), search_rank.relevance(cand_title, want_title) * 0.9)
        if t < MIN_TITLE:
            continue
        a = search_rank.relevance(artist, c.get("artist", "")) if artist else 1.0
        if artist and a < MIN_ARTIST:
            # артист міг бути вписаний у назву («Артист - Назва» на SoundCloud/YouTube)
            if search_rank.relevance(artist, c.get("title", "")) < 0.35:
                continue
            a = 0.4
        score = 0.55 * t + 0.3 * a + 0.15 * TRUST.get(str(c.get("source")), 0.5)
        if score > best_score:
            best, best_score = c, score
    return best


def _default_search(quality):
    def run(query, limit):
        from core import providers
        return providers.search_tracks(query, limit=limit, quality=quality)
    return run


def resolve_one(artist, title, quality="mp32", search_fn=None):
    artist, title = str(artist or "").strip()[:120], str(title or "").strip()[:160]
    if not title:
        return None
    fn = search_fn or _default_search(quality)
    for query in dict.fromkeys([f"{artist} {title}".strip(), title]):
        try:
            found = fn(query, 10) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve search failed: %s", type(exc).__name__)
            continue
        safe = supersource.annotate([t for t in found if isinstance(t, dict)])
        best = pick(artist, title, safe)
        if best:
            return best
    return None


def resolve_many(items, quality="mp32", search_fn=None, timeout=14):
    """items: [{"artist","title"}]. Повертає список тієї ж довжини: трек або None."""
    items = [i for i in (items or [])[:MAX_BATCH]]
    futures = [_POOL.submit(resolve_one, (i or {}).get("artist"), (i or {}).get("title"), quality, search_fn)
               for i in items]
    wait(futures, timeout=timeout)
    out = []
    for f in futures:
        try:
            out.append(f.result(timeout=0) if f.done() else None)
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


# ── цілий альбом одним махом (економить квоту YouTube: ~101 одиниця замість 100 на кожен трек) ──
MAX_YT_FALLBACK = 3      # скільки нез'ясованих треків альбому дозволено добирати окремим пошуком


def _pick_album_playlist(artist, album, playlists):
    best, best_score = None, 0.0
    for p in playlists or []:
        title, channel = str(p.get("title") or ""), str(p.get("channel") or "")
        t = max(search_rank.relevance(album, title), 0.9 * search_rank.relevance(_clean_title(album), _clean_title(title)))
        a = max(search_rank.relevance(artist, channel), search_rank.relevance(artist, title)) if artist else 1.0
        if t < 0.75 or a < 0.5:
            continue
        score = 0.6 * t + 0.3 * a + (0.1 if "topic" in channel.casefold() else 0)
        if score > best_score:
            best, best_score = p, score
    return best


def _default_playlist_fn(artist, album):
    from core import providers
    pl = _pick_album_playlist(artist, album, providers.youtube_album_playlists(f"{artist} {album} album".strip()))
    return providers.youtube_playlist_tracks(pl["playlist_id"]) if pl else []


def align_album(titles, candidates):
    """Зіставляє треклист (назви) із треками плейлиста. Повертає список тієї ж довжини: трек або None."""
    used, out = set(), []
    for want in titles:
        w = _clean_title(want) or str(want or "")
        best, best_i, best_score = None, -1, 0.0
        for i, c in enumerate(candidates or []):
            if i in used:
                continue
            ct = _clean_title(c.get("title"))
            sc = max(search_rank.relevance(w, ct), 0.9 * search_rank.relevance(ct, w))
            if sc > best_score:
                best, best_i, best_score = c, i, sc
        if best is not None and best_score >= 0.7:
            used.add(best_i)
            out.append(best)
        else:
            out.append(None)
    return out


def resolve_album(artist, album, titles, quality="mp32", playlist_fn=None, search_fn=None):
    """Повертає {"results": [трек|None...], "via": "playlist"|"search", "found": N}."""
    titles = [str(t or "")[:160] for t in (titles or [])[:60]]
    playlist_fn = playlist_fn or _default_playlist_fn
    cands = []
    try:
        cands = playlist_fn(str(artist or "")[:120], str(album or "")[:160]) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("album playlist failed: %s", type(exc).__name__)
    results = align_album(titles, cands) if cands else [None] * len(titles)
    via = "playlist" if any(results) else "search"
    missing = [i for i, r in enumerate(results) if r is None]
    # решту добираємо звичайним пошуком по всіх джерелах (кількість обмежена, щоб не палити квоту)
    limited = missing[:MAX_YT_FALLBACK] if via == "playlist" else missing[:12]
    if limited:
        found = resolve_many([{"artist": artist, "title": titles[i]} for i in limited], quality, search_fn)
        for i, t in zip(limited, found):
            results[i] = t
    return {"results": results, "via": via, "found": sum(1 for r in results if r)}