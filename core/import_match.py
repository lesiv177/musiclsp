# -*- coding: utf-8 -*-
"""Перевірене зіставлення рядків імпорту з каталогом (замість сліпого «перший результат»)."""
from __future__ import annotations

from core import catalog

MATCHED, UNCERTAIN, NOT_FOUND, INVALID = "matched", "uncertain", "not_found", "invalid"


def _artist_ok(artist: str, cand_artist: str):
    """None — артист не вказаний; True/False — збіг із кандидатом."""
    a = catalog.artist_key(artist)
    if not a:
        return None
    c = catalog.artist_key(cand_artist)
    return bool(c) and (a == c or a in c or c in a)


def classify(title: str, artist: str, cand: dict) -> tuple:
    """(status, score). matched — назва збігається (без feat/edition) і артист не суперечить."""
    tk, ck = catalog.title_key(title), catalog.title_key(cand.get("title"))
    if not tk or not ck:
        return NOT_FOUND, 0.0
    artist_ok = _artist_ok(artist, cand.get("artist"))
    if tk == ck:
        if artist_ok is False:
            return UNCERTAIN, 0.5
        return MATCHED, 1.0 if artist_ok else 0.9
    if min(len(tk), len(ck)) >= 4 and (tk in ck or ck in tk):
        return UNCERTAIN, 0.6 if artist_ok is not False else 0.4
    return NOT_FOUND, 0.0


def best_match(title: str, artist: str, candidates: list):
    """Найкращий ВПЕВНЕНИЙ збіг або None. Невпевнені збіги не імпортуються мовчки."""
    best = None
    for cand in candidates or []:
        if not isinstance(cand, dict) or not cand.get("id"):
            continue
        status, score = classify(title, artist, cand)
        if status == MATCHED and (best is None or score > best[0]):
            best = (score, cand)
    return best[1] if best else None


def pick_confirmed(title: str, artist: str, candidates: list, chosen_id):
    """Кандидат, якого користувач підтвердив вручну. Береться ЛИШЕ з результатів пошуку для цього
    рядка (id від клієнта не довіряємо), і лише якщо він не «not_found»."""
    chosen = str(chosen_id or "")
    if not chosen:
        return None
    for cand in candidates or []:
        if isinstance(cand, dict) and cand.get("id") and str(cand["id"]) == chosen:
            status, _ = classify(title, artist, cand)
            return cand if status != NOT_FOUND else None
    return None


def parse_accept(raw) -> dict:
    """{індекс рядка: id треку} з тіла запиту; некоректне ігнорується."""
    out = {}
    if isinstance(raw, dict):
        for k, v in list(raw.items())[:100]:
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if idx >= 0 and isinstance(v, (str, int)) and str(v):
                out[idx] = str(v)[:200]
    return out


def _brief(c: dict) -> dict:
    return {"id": c.get("id"), "title": c.get("title"), "artist": c.get("artist"), "source": c.get("source")}


def report_row(index: int, title: str, artist: str, candidates: list) -> dict:
    row = {"index": index, "title": title, "artist": artist, "status": NOT_FOUND, "match": None, "alternatives": []}
    scored = []
    for cand in candidates or []:
        if isinstance(cand, dict) and cand.get("id"):
            status, score = classify(title, artist, cand)
            if status != NOT_FOUND:
                scored.append((status == MATCHED, score, cand))
    scored.sort(key=lambda x: (-int(x[0]), -x[1]))
    if scored and scored[0][0]:
        row["status"], row["match"] = MATCHED, _brief(scored[0][2])
        rest = scored[1:]
    elif scored:
        row["status"] = UNCERTAIN
        rest = scored
    else:
        rest = []
    row["alternatives"] = [_brief(c) for _, _, c in rest[:3]]
    return row


def summarize(report: list) -> dict:
    out = {MATCHED: 0, UNCERTAIN: 0, NOT_FOUND: 0, INVALID: 0, "total": len(report)}
    for r in report:
        out[r["status"]] += 1
    return out
