# -*- coding: utf-8 -*-
"""Пост-обробка пошуку: релевантність, безпечна дедуплікація, фільтри.

Чиста логіка без мережі. Не вигадує дані: нічого не додає, лише впорядковує,
прибирає підтверджені дублікати й повертає поле ``also_in`` з іншими джерелами.
"""
from __future__ import annotations

from core import catalog

RELEASE_TYPES = ("album", "single", "ep", "compilation")


def relevance(query: str, name: str) -> float:
    """0..1: точний збіг > початок > усі слова > підрядок. Кирилиця/латиниця зрівнюються."""
    q, n = catalog.fold_text(query), catalog.fold_text(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if n.startswith(q):
        return 0.8
    words = q.split()
    if all(w in n.split() for w in words):
        return 0.65
    if q in n:
        return 0.5
    if all(w in n for w in words):
        return 0.35
    return 0.0


def _label(item: dict) -> str:
    return str(item.get("name") or item.get("title") or "")


def rank(items: list, query: str, sort: str = "relevance") -> list:
    """Стабільне впорядкування за релевантністю; для інших sort порядок провайдера зберігається."""
    items = [i for i in items or [] if isinstance(i, dict)]
    if sort != "relevance":
        return items
    scored = [(relevance(query, _label(i)), idx, i) for idx, i in enumerate(items)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, _, i in scored]


def _is_playable(item: dict) -> bool:
    return not item.get("metadata_only") and str(item.get("source") or "") != "musicbrainz"


def _same_for_display(verdict: dict) -> bool:
    """Для показу в пошуку достатньо артиста + назви + року без конфлікту.
    Це НЕ канонічне злиття: canonical-прив'язка лишається суворішою (catalog.match_albums)."""
    reasons = set(verdict.get("reasons") or [])
    return verdict["decision"] == "same" or {"artist", "title", "year"} <= reasons


def dedupe_albums(items: list) -> list:
    """Об'єднати той самий реліз із різних джерел. Представник — відтворюваний варіант.
    Автозлиття лише коли match_albums == same (рік підтверджує); інакше лишаємо обидва."""
    out: list = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        norm = {"title": item.get("title"), "artist": item.get("artist"), "year": item.get("year"),
                "mbid": item.get("mbid"), "provider": item.get("source"), "id": item.get("id")}
        merged = False
        for idx, kept in enumerate(out):
            knorm = {"title": kept.get("title"), "artist": kept.get("artist"), "year": kept.get("year"),
                     "mbid": kept.get("mbid"), "provider": kept.get("source"), "id": kept.get("id")}
            if kept.get("id") == item.get("id") or _same_for_display(catalog.match_albums(norm, knorm)):
                also = list(kept.get("also_in") or [])
                other = item if _is_playable(kept) else kept
                base = kept if _is_playable(kept) or not _is_playable(item) else item
                src = other.get("source")
                if src and src != base.get("source") and src not in also:
                    also.append(src)
                base = {**base, "also_in": also}
                if not base.get("cover") and other.get("cover"):
                    base["cover"] = other["cover"]
                out[idx] = base
                merged = True
                break
        if not merged:
            out.append(item)
    return out


def dedupe_artists(items: list) -> list:
    """Прибирає лише точні повтори id. Артистів з однаковим ім'ям НЕ зливаємо без доказів
    (MBID або спільний сайт) — це можуть бути різні люди."""
    out: list = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        dup = False
        for kept in out:
            verdict = catalog.match_artists(
                {"name": item.get("name"), "mbid": item.get("mbid"), "provider": item.get("source"),
                 "id": item.get("id"), "website": item.get("website")},
                {"name": kept.get("name"), "mbid": kept.get("mbid"), "provider": kept.get("source"),
                 "id": kept.get("id"), "website": kept.get("website")})
            if verdict["decision"] == "same":
                dup = True
                break
        if not dup:
            out.append(item)
    return out


def apply_filters(items: list, source: str = "", release_type: str = "", availability: str = "") -> list:
    """source — провайдер; release_type — album/single/ep/compilation (лише якщо відомо);
    availability — playable | metadata_only."""
    source, release_type, availability = (source or "").lower(), (release_type or "").lower(), (availability or "").lower()
    res = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if source and str(item.get("source") or "").lower() != source and source not in [s.lower() for s in item.get("also_in") or []]:
            continue
        if release_type and str(item.get("type") or item.get("release_type") or "").lower() != release_type:
            continue
        if availability == "playable" and not _is_playable(item):
            continue
        if availability == "metadata_only" and _is_playable(item):
            continue
        res.append(item)
    return res


def postprocess(kind: str, data, query: str, sort: str = "relevance", source: str = "",
                release_type: str = "", availability: str = ""):
    """Єдина точка для /api/search: kind = albums | artists | all (dict із трьома списками)."""
    if kind == "all" and isinstance(data, dict):
        return {**data,
                "albums": postprocess("albums", data.get("albums"), query, sort, source, release_type, availability),
                "artists": postprocess("artists", data.get("artists"), query, sort, source, "", availability)}
    if kind == "albums":
        items = apply_filters(dedupe_albums(rank(data, query, sort)), source, release_type, availability)
        return items
    if kind == "artists":
        return apply_filters(dedupe_artists(rank(data, query, sort)), source, "", "")
    return data