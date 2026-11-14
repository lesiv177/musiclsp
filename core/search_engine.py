# -*- coding: utf-8 -*-
"""
Новий рушій основного пошуку MusicLSP (v2).

Чим відрізняється від старого шляху «один запит → список»:
  1. Розбір запиту: «Артист — Назва», «назва by артист», кирилиця/латиниця, зайві символи.
  2. Кілька варіантів запиту паралельно (оригінал, нормалізований, «артист назва», розбитий).
  3. Злиття результатів усіх джерел методом RRF (reciprocal rank fusion) + текстова релевантність
     + довіра до джерела + повнота картки (обкладинка, тривалість). Дублікати зливаються, лишається найкращий.
  4. Фільтр безпеки релізу (core.supersource): без підтвердженої дозволеної ліцензії файл не показується.
  5. Якщо відтворюваного нічого немає — «дискавері» з метаданих (Deezer/iTunes): артисти й альбоми
     БЕЗ відтворення, з готовим запитом для пошуку доступних треків.
  6. Підказка «Можливо, ви шукали…» і діагностика по джерелах (скільки результатів, скільки відкинуто).

Відтворення й Connect не змінюються: рушій повертає ті самі структури треків.
Вимкнути й повернутися до старого пошуку: SEARCH_ENGINE=legacy.
"""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait

from core import catalog, search_rank, supersource

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="search-engine")
RRF_K = 60
# довіра до джерела: чим вища, тим вище за інших за однакової релевантності
TRUST = {"jamendo": 1.0, "audius": 0.9, "archive": 0.8, "openverse": 0.75, "ccmixter": 0.75,
         "wikimedia": 0.7, "soundcloud": 0.85, "youtube": 0.6, "curated": 0.9}
_SPLIT = re.compile(r"\s+[-–—:]\s+|\s+by\s+", re.I)


def engine_enabled() -> bool:
    return os.getenv("SEARCH_ENGINE", "v2").strip().lower() != "legacy"


def analyze(query):
    raw = " ".join(str(query or "").split())[:120]
    norm = " ".join(re.sub(r"[^\w\s]", " ", raw, flags=re.UNICODE).split())
    artist = title = ""
    parts = [p.strip() for p in _SPLIT.split(raw, maxsplit=1) if p.strip()]
    if len(parts) == 2:
        artist, title = parts
        if re.search(r"\s+by\s+", raw, re.I) and " - " not in raw:
            artist, title = parts[1], parts[0]
    variants, seen = [], set()
    for v in (raw, norm, f"{artist} {title}".strip() if artist else "", title if artist else ""):
        key = v.casefold()
        if v and len(v) >= 2 and key not in seen:
            seen.add(key)
            variants.append(v)
    return {"raw": raw, "norm": norm, "artist": artist, "title": title, "variants": variants[:4]}


def _name_key(t):
    return (catalog.fold_text(t.get("title")), catalog.artist_key(t.get("artist")))


def _relevance(qi, t):
    q = qi["norm"] or qi["raw"]
    hay = f"{t.get('artist', '')} {t.get('title', '')}"
    base = max(search_rank.relevance(q, hay), search_rank.relevance(q, t.get("title", "")),
               0.9 * search_rank.relevance(q, t.get("artist", "")))
    if qi["artist"] and qi["title"]:
        a = search_rank.relevance(qi["artist"], t.get("artist", ""))
        b = search_rank.relevance(qi["title"], t.get("title", ""))
        base = max(base, 0.45 * a + 0.55 * b)
    return base


def fuse(qi, groups, sort="relevance"):
    """groups: [(variant_weight, [tracks])]. Повертає впорядкований список без дублікатів."""
    pool = {}
    for weight, tracks in groups:
        for rank, t in enumerate(tracks or []):
            if not isinstance(t, dict) or not str(t.get("title") or "").strip():
                continue
            key = _name_key(t)
            entry = pool.setdefault(key, {"track": t, "rrf": 0.0})
            entry["rrf"] += weight / (RRF_K + rank)
            if len(str(t.get("cover") or "")) > len(str(entry["track"].get("cover") or "")) and entry["track"].get("source") == t.get("source"):
                entry["track"] = t
    if not pool:
        return []
    top = max(e["rrf"] for e in pool.values()) or 1.0
    scored = []
    for i, e in enumerate(pool.values()):
        t = e["track"]
        completeness = (0.5 if t.get("cover") else 0) + (0.5 if t.get("duration") else 0)
        score = (0.55 * _relevance(qi, t) + 0.25 * (e["rrf"] / top)
                 + 0.12 * TRUST.get(str(t.get("source")), 0.5) + 0.08 * completeness)
        scored.append((score, -i, t))
    if sort == "relevance":
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [t for _, _, t in scored]


def _run_variants(qi, limit, fetch):
    futures = [(1.0 if i == 0 else 0.7, _POOL.submit(fetch, v, limit)) for i, v in enumerate(qi["variants"])]
    done, _ = wait([f for _, f in futures], timeout=9)
    groups, errors = [], 0
    for weight, f in futures:
        if f not in done:
            errors += 1
            continue
        try:
            groups.append((weight, f.result() or []))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("search variant failed: %s", type(exc).__name__)
    return groups, errors


def search_tracks(query, limit=30, quality="mp32", sources=None, sort="relevance", fetch=None):
    """Повертає (tracks, diagnostics)."""
    started = time.monotonic()
    qi = analyze(query)
    if not qi["variants"]:
        return [], {"variants": 0, "dropped": 0, "ms": 0}
    if fetch is None:
        from core import providers
        fetch = lambda q, n: providers.search_tracks(q, limit=n, quality=quality, sources=sources, sort=sort)  # noqa: E731
    groups, errors = _run_variants(qi, limit, fetch)
    fused = fuse(qi, groups, sort)
    kept = supersource.annotate(fused)
    by_source = {}
    for t in kept:
        by_source[t.get("source", "?")] = by_source.get(t.get("source", "?"), 0) + 1
    return kept[:limit], {"variants": len(qi["variants"]), "failed_variants": errors,
                          "candidates": len(fused), "dropped": len(fused) - len(kept),
                          "by_source": by_source, "ms": int((time.monotonic() - started) * 1000)}


def did_you_mean(query, tracks, pool_names):
    """Проста підказка: найближча назва артиста з результатів, якщо запит виглядає як її одруківка."""
    import difflib
    q = catalog.fold_text(query)
    if len(q) < 4 or not pool_names:
        return ""
    best = difflib.get_close_matches(q, [catalog.fold_text(n) for n in pool_names], n=1, cutoff=0.78)
    if not best or best[0] == q:
        return ""
    for n in pool_names:
        if catalog.fold_text(n) == best[0]:
            return n
    return ""


def discovery(query, limit=8, fetch=None):
    """Метадані-пропозиції (без відтворення), коли доступних треків немає."""
    from core import meta_sources
    qi = analyze(query)
    name = qi["artist"] or qi["raw"]
    try:
        data = meta_sources.find_artist(name, fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("discovery failed: %s", type(exc).__name__)
        return {}
    if not data.get("albums") and not data.get("image"):
        return {}
    return {"artist": {"name": data["name"], "image": data["image"], "bio": data["bio"][:300]},
            "albums": [{**a, "metadata_only": True, "search_query": f"{data['name']} {a['title']}"}
                       for a in data["albums"][:limit]],
            "note": "Це інформація про релізи. Відтворення доступне лише для треків з легальних джерел каталогу."}


def search(query, limit=30, quality="mp32", sources=None, sort="relevance", fetch=None, discovery_fn=None):
    tracks, diag = search_tracks(query, limit, quality, sources, sort, fetch)
    extras = {"engine": "v2", "diagnostics": diag}
    if not tracks:
        found = (discovery_fn or discovery)(query)
        if found:
            extras["discovery"] = found
    else:
        hint = did_you_mean(query, tracks, [t.get("artist", "") for t in tracks[:20]])
        if hint:
            extras["did_you_mean"] = hint
    return tracks, extras