# -*- coding: utf-8 -*-
"""
Надійність і розумний пошук поверх core.providers.

• TTL-кеш пошуку та посилань на стрім — популярні треки не смикають джерела щоразу;
• перевірка посилання перед віддачею клієнту (мертве не потрапляє в плеєр);
• список «мертвих» треків — вони зникають із видачі;
• запасне джерело: якщо трек не грає, шукаємо той самий в іншому легальному джерелі;
• розумний пошук «артист — назва» з ранжуванням.

Усе працює лише з легальними джерелами з core.providers.
"""

import re
import copy
import time
import logging
import difflib
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait

from core import providers as P

logger = logging.getLogger(__name__)

# ─── TTL-кеш ─────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_CACHE = {}          # key -> (expires, value)
_CACHE_MAX = 1500


def cache_get(key):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > time.time():
            return copy.deepcopy(hit[1])
        _CACHE.pop(key, None)
    return None


def cache_put(key, value, ttl):
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            now = time.time()
            for k in [k for k, v in _CACHE.items() if v[0] <= now]:
                _CACHE.pop(k, None)
            if len(_CACHE) >= _CACHE_MAX:          # усе ще повно — викидаємо найстаріші
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:_CACHE_MAX // 5]:
                    _CACHE.pop(k, None)
        _CACHE[key] = (time.time() + ttl, copy.deepcopy(value))


# ─── «Мертві» треки ──────────────────────────────────────────────────────────

_DEAD = {}           # track_id -> до якого часу вважаємо мертвим
DEAD_TTL = 30 * 60


def mark_dead(track_id):
    if track_id:
        with _LOCK:
            _DEAD[track_id] = time.time() + DEAD_TTL


def is_dead(track_id):
    with _LOCK:
        exp = _DEAD.get(track_id, 0)
        if exp and exp < time.time():
            _DEAD.pop(track_id, None)
            return False
        return exp > 0


# ─── Перевірка посилань ──────────────────────────────────────────────────────

def check_url(url, timeout=4):
    """True — грає або невідомо; False — точно мертве (404/410/5xx, відмова).
    Таймаут не карає трек: повільне джерело ≠ мертве."""
    if not url:
        return False
    key = ("url", url)
    hit = cache_get(key)
    if hit is not None:
        return hit
    ok = True
    try:
        r = P._http_session().head(url, timeout=timeout, allow_redirects=True)
        if r.status_code in (403, 405, 501):          # HEAD не підтримується — пробуємо шматок
            r = P._http_session().get(url, timeout=timeout, headers={"Range": "bytes=0-1"},
                               stream=True, allow_redirects=True)
            r.close()
        ok = r.status_code < 400
    except Exception as e:
        name = type(e).__name__
        ok = "Timeout" in name                        # таймаут — вважаємо живим
    cache_put(key, ok, 300 if ok else 120)
    return ok


def _preflight_track(track, quality="mp32"):
    """Повертає True лише якщо отримано URL і він пройшов мережеву перевірку."""
    tid = str(track.get("id") or "")
    if not tid or is_dead(tid):
        return False
    url = str(track.get("stream") or "")
    if not url:
        try:
            url = P.resolve_stream(tid, quality) or ""
        except Exception:
            return False
    if not url or not check_url(url, timeout=5):
        mark_dead(tid)
        return False
    track["stream"] = url
    cache_put(("stream", tid, quality), url, STREAM_TTL)
    return True


def filter_available(tracks, budget=12, workers=10, quality="mp32"):
    """Показуємо лише треки з URL, який пройшов preflight-перевірку.
    Неперевірені через таймаут або повільне джерело треки не показуємо."""
    if not tracks:
        return []
    candidates = [t for t in tracks if t.get("id") and not is_dead(t["id"])]
    if not candidates:
        return []
    verdict = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as ex:
        futs = {ex.submit(_preflight_track, t, quality): t for t in candidates}
        done, pending = wait(list(futs), timeout=budget)
        for future in done:
            track = futs[future]
            try:
                verdict[track["id"]] = bool(future.result())
            except Exception:
                verdict[track["id"]] = False
        for future in pending:
            future.cancel()
    return [t for t in candidates if verdict.get(t["id"], False)]


# ─── Розумний пошук ──────────────────────────────────────────────────────────

def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)                       # (feat. …), [Remix]
    s = re.sub(r"\b(feat|ft|official|audio|video|lyrics)\b\.?", " ", s)
    return re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).strip()


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return max(jac, difflib.SequenceMatcher(None, a, b).ratio())


_SPLIT = re.compile(r"\s+[-–—:]\s+|\s+by\s+", re.I)


def parse_query(query):
    """'Imagine Dragons - Believer' → ('imagine dragons','believer'); інакше (None, None)."""
    parts = [p.strip() for p in _SPLIT.split(query or "", maxsplit=1) if p.strip()]
    if len(parts) == 2:
        if re.search(r"\s+by\s+", query, re.I) and " - " not in query:
            return parts[1], parts[0]                             # «title by artist»
        return parts[0], parts[1]
    return None, None


def score(query, t):
    q = _norm(query)
    qa, qt = parse_query(query)
    title, artist = t.get("title", ""), t.get("artist", "")
    if qa and qt:
        s = 0.55 * _sim(qt, title) + 0.45 * _sim(qa, artist)
        s2 = 0.55 * _sim(qa, title) + 0.45 * _sim(qt, artist)    # переплутали порядок
        base = max(s, s2)
    else:
        base = max(_sim(q, title), _sim(q, artist) * 0.9, _sim(q, f"{artist} {title}"))
        words = q.split()
        hay = _norm(f"{artist} {title}")
        if words and all(w in hay for w in words):
            base = max(base, 0.85)
    return base


def rank(query, tracks):
    """Стабільне сортування за збігом із запитом; джерело порядку — оригінальна видача."""
    scored = [(score(query, t), -i, t) for i, t in enumerate(tracks)]
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [t for _, _, t in scored]


def search(query, limit=30, quality="mp32", sources=None, sort="relevance", check=True):
    """Кешований розумний пошук: «артист — назва», ранжування, прибирання мертвих."""
    key = ("search", query.strip().lower(), limit, quality, tuple(sources or ()), sort)
    res = cache_get(key)
    if res is None:
        qa, qt = parse_query(query)
        pool = list(P.search_tracks_smart(query, limit, quality, sources, sort))
        if qa and qt:                                             # додатково шукаємо як «артист назва»
            seen = {t["id"] for t in pool}
            for t in P.search_tracks(f"{qa} {qt}", limit, quality, sources, sort):
                if t["id"] not in seen:
                    pool.append(t)
        if sort == "relevance" and pool:
            pool = rank(query, pool)
        res = pool[:limit]
        cache_put(key, res, 300)
        # прямі посилання зі видачі одразу кладемо в кеш стрімів — перший ▶ без звернень до API
        for t in res:
            if (t.get("stream") or "").startswith("https://"):
                cache_put(("stream", t["id"], quality), t["stream"], STREAM_TTL)
    return filter_available(res, quality=quality) if check else res


# ─── Стрім: перевірка + запасне джерело ──────────────────────────────────────

STREAM_TTL = 600


def _resolve_checked(track_id, quality, force=False):
    """Прямий URL, який пройшов перевірку, або ''. force оновлює застарілий URL."""
    key = ("stream", track_id, quality)
    if force:
        with _LOCK:
            _CACHE.pop(key, None)
    else:
        hit = cache_get(key)
        if hit:
            return hit
    if is_dead(track_id) and not force:
        return ""
    url = P.resolve_stream(track_id, quality)
    if url and check_url(url):
        cache_put(key, url, STREAM_TTL)
        return url
    return ""


def _alternative(track_id, title, artist, quality):
    """Той самий трек в іншому джерелі: (url, id) або ('', '')."""
    if not title:
        t = P.get_track(track_id, quality) or {}
        title, artist = t.get("title", ""), t.get("artist", "")
    if not title:
        return "", ""
    prefix = P.split_id(track_id)[0]
    query = f"{artist} {title}".strip()
    cands = []
    for t in P.search_tracks(query, 10, quality):
        if t["id"] == track_id or is_dead(t["id"]):
            continue
        ts = _sim(title, t.get("title", ""))
        as_ = _sim(artist, t.get("artist", "")) if artist else 1.0
        if ts >= 0.8 and as_ >= 0.6:
            cands.append((ts + as_ + (0.05 if P.split_id(t["id"])[0] != prefix else 0), t))
    cands.sort(key=lambda x: -x[0])
    for _, t in cands[:3]:
        url = t.get("stream") or P.resolve_stream(t["id"], quality)
        candidate_prefix = P.split_id(t["id"])[0]
        if url and (candidate_prefix in ("aud", "sc") or check_url(url)):
            logger.info("Запасне джерело: %s → %s", track_id, t["id"])
            return url, t["id"]
        mark_dead(t["id"])
    return "", ""


def resolve_stream_robust(track_id, quality="mp32", title="", artist="", force=False):
    """Повертає лише перевірений URL саме вибраного треку; без повторів і fallback."""
    url = _resolve_checked(track_id, quality, force=force)
    if url:
        return url, track_id
    if not force:
        mark_dead(track_id)
    return "", ""


def resolve_url(track_id, quality="mp32", title="", artist="", force=False):
    """Для JSON-ендпоінта /api/resolve (передзавантаження наступних треків)."""
    return resolve_stream_robust(track_id, quality, title, artist, force=force)
