# -*- coding: utf-8 -*-
"""
Захист квоти YouTube Data API: кеш + денний бюджет.

search.list коштує 100 одиниць, безкоштовна квота — 10 000 на добу (скидається опівночі за тихоокеанським часом).
Тому: 1) кожен запит кешуємо (знайдене 24 год, порожнє 6 год); 2) ведемо денний бюджет
(YOUTUBE_DAILY_BUDGET, за замовчуванням 9000 — із запасом); 3) якщо Google відповів, що квоту вичерпано,
блокуємо виклики до скидання. Лічильник у пам'яті процесу: після перезапуску він починається з нуля,
тому реальну межу тримає й відповідь Google (крок 3).
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

SEARCH_COST = 100
HIT_TTL = 24 * 3600
MISS_TTL = 6 * 3600
MAX_CACHE = 2000
_PT = ZoneInfo("America/Los_Angeles")
_LOCK = threading.Lock()
_CACHE = {}
_STATE = {"day": "", "used": 0, "blocked_day": ""}


def _day() -> str:
    return datetime.now(_PT).strftime("%Y-%m-%d")


def budget() -> int:
    try:
        return max(0, int(os.getenv("YOUTUBE_DAILY_BUDGET", "9000")))
    except ValueError:
        return 9000


def _roll():
    today = _day()
    if _STATE["day"] != today:
        _STATE.update(day=today, used=0)


def norm_key(query) -> str:
    return re.sub(r"[^\w]+", " ", str(query or "").casefold()).strip()


def cache_get(query):
    key = norm_key(query)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        _CACHE.pop(key, None)
    return None


def cache_put(query, value):
    key = norm_key(query)
    if not key:
        return
    with _LOCK:
        if len(_CACHE) >= MAX_CACHE:
            now = time.time()
            for k in [k for k, v in _CACHE.items() if v[0] <= now] or list(_CACHE)[:MAX_CACHE // 4]:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time() + (HIT_TTL if value else MISS_TTL), list(value))


def allow(cost=SEARCH_COST) -> bool:
    with _LOCK:
        _roll()
        if _STATE["blocked_day"] == _STATE["day"]:
            return False
        return _STATE["used"] + cost <= budget()


def spend(cost=SEARCH_COST):
    with _LOCK:
        _roll()
        _STATE["used"] += cost


def block_today():
    with _LOCK:
        _roll()
        _STATE["blocked_day"] = _STATE["day"]


def status() -> dict:
    with _LOCK:
        _roll()
        return {"day": _STATE["day"], "used": _STATE["used"], "budget": budget(),
                "blocked": _STATE["blocked_day"] == _STATE["day"], "cached_queries": len(_CACHE)}


def reset_for_tests():
    with _LOCK:
        _CACHE.clear()
        _STATE.update(day="", used=0, blocked_day="")