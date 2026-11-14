# -*- coding: utf-8 -*-
"""
HTTP API для Mini App.

Автентифікація: підпис Telegram WebApp initData (HMAC-SHA256).
Підробити user_id ззовні не вийде — саме тому старий варіант із
?user=<id> у query-рядку тут замінено.
"""

import io
import csv
import hmac
import json
import math
import time
import zipfile
import hashlib
import logging
import asyncio
from core.release_radar import is_recent_release
import urllib.parse

import requests
from aiohttp import WSMsgType, web

from core import meta_cache as MC
from core import db
from core import runtime as RT
from core import web_login
from core import meta_sources as MS
from core import radio_browser as RB
from core import supersource as SUP
from core import search_engine as SE
from core import providers as P
from core import smart as SM
from core import charts as CH
from core import recommend as REC
from core import personal_collections as PC
from core import import_match as IM
from core import user_data as UD
from core import radar_feed as RF
from core import favorites as FAV
from core import playlists_ext as PLX
from core import search_rank as SR
from core.catalog_api import annotate_search, enrich_album, enrich_artist
from core.connect import Hub, valid_device_id
from core.auth import verify_init_data
from core.config import (
    ADMIN_IDS, PREMIUM_FEATURES, FREE_LIMITS, PREMIUM_LIMITS,
    APP_VERSION, BOT_USERNAME, REFERRAL_REWARD_DAYS,
    ADMIN_CMD_PASSWORD, BOT_TOKEN,
)

logger = logging.getLogger(__name__)

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Cache-Control": "no-store",
}


def ok(data, status=200):
    return web.json_response(data, status=status, headers=CORS,
                             dumps=lambda o: json.dumps(o, ensure_ascii=False))


def fail(message, status=400):
    return ok({"ok": False, "error": message}, status=status)


# ─── Автентифікація ──────────────────────────────────────────────────────────

# Перевірка підпису живе в core.auth — спільна для бота й панелі.

def auth_user(request):
    raw = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData", "")
    user = verify_init_data(raw)
    if not user:
        return None
    return db.get_user(
        int(user["id"]),
        user.get("username", "") or "",
        user.get("first_name", "") or "",
    )


def require_auth(handler):
    async def wrapper(request):
        u = auth_user(request)
        if not u:
            return fail("Потрібен вхід через Telegram", 401)
        return await handler(request, u)
    return wrapper


async def to_thread(fn, *a, **kw):
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **kw))

async def json_object(request):
    """Читає JSON-об'єкт; некоректне тіло не повинно перетворюватися на 500."""
    try:
        body = await request.json()
    except (ValueError, web.HTTPException, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def bounded_int(value, default, minimum, maximum):
    """Безпечно читає ціле число з межами; None означає некоректний ввід."""
    try:
        parsed = int(default if value in (None, "") else value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(minimum, min(maximum, parsed))


def quality_for(uid):
    return db.limits_for(uid)["quality"]


# ─── Сервісні ────────────────────────────────────────────────────────────────

_login_hits = {}
LOGIN_WINDOW = 300
LOGIN_MAX_ATTEMPTS = 10


def _client_ip(request):
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or getattr(request, "remote", "") or "?"


def _login_allowed(ip, now=None):
    """Не більше LOGIN_MAX_ATTEMPTS спроб входу за LOGIN_WINDOW секунд з однієї адреси."""
    now = time.time() if now is None else now
    hits = [t for t in _login_hits.get(ip, []) if now - t < LOGIN_WINDOW]
    if len(hits) >= LOGIN_MAX_ATTEMPTS:
        _login_hits[ip] = hits
        return False
    hits.append(now)
    _login_hits[ip] = hits
    if len(_login_hits) > 5000:
        for key in [k for k, v in _login_hits.items() if not v or now - v[-1] >= LOGIN_WINDOW]:
            _login_hits.pop(key, None)
    return True


@require_auth
async def h_meta_artist(request, u):
    """Фото, біографія та дискографія артиста з безкоштовних джерел метаданих (без аудіо)."""
    name = (request.query.get("name") or "").strip()[:120]
    if not name:
        return fail("Потрібна назва артиста", 400)
    data = await to_thread(MC.cached, "meta:artist", name.casefold(), lambda: MS.find_artist(name), 21600)
    return ok(data)


@require_auth
async def h_meta_cover(request, u):
    artist = (request.query.get("artist") or "").strip()[:120]
    album = (request.query.get("album") or "").strip()[:160]
    if not artist or not album:
        return fail("Потрібні artist і album", 400)
    data = await to_thread(MC.cached, "meta:cover", f"{artist.casefold()}|{album.casefold()}",
                           lambda: MS.find_cover(artist, album), 86400)
    return ok(data)


@require_auth
async def h_stations(request, u):
    """Інтернет-радіо (лише посилання на офіційні ефіри станцій)."""
    q = request.query
    items = await to_thread(RB.search, (q.get("q") or "")[:80], (q.get("country") or "")[:2],
                            (q.get("tag") or "")[:40], int(q.get("limit") or 30) if str(q.get("limit") or "30").isdigit() else 30)
    return ok({"items": items})


@require_auth
async def h_super_search(request, u):
    """Суперджерело: треки з усіх безпечних джерел + радіо, з позначкою безпеки й атрибуцією."""
    q = request.query
    lim = int(q["limit"]) if str(q.get("limit") or "").isdigit() else 30
    data = await to_thread(SUP.search, (q.get("q") or "").strip(), lim, q.get("kind") or "all", quality_for(int(u["uid"])))
    return ok(data)


@require_auth
async def h_super_sources(request, u):
    return ok(SUP.sources_report())


@require_auth
async def h_meta_status(request, u):
    return ok({"sources": MS.source_health()})


async def h_web_config(request):
    """Що потрібно сторінці для входу з браузера: username бота для Telegram Login Widget."""
    username = RT.get_bot_username()
    return ok({"ok": True, "bot_username": username, "web_login": bool(username and BOT_TOKEN)})


async def h_web_login(request):
    if not _login_allowed(_client_ip(request)):
        return fail("Забагато спроб входу. Спробуйте за кілька хвилин.", 429)
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    user = web_login.verify_widget_login(body, BOT_TOKEN)
    if not user:
        return fail("Не вдалося підтвердити вхід через Telegram", 401)
    token = web_login.issue_session(user, BOT_TOKEN)
    return ok({"ok": True, "token": token, "user": {"id": user["id"], "first_name": user["first_name"],
                                                       "username": user["username"]},
               "expires_in": web_login.SESSION_TTL})


async def h_options(request):
    return web.Response(status=204, headers=CORS)


async def h_health(request):
    registry = P.source_status()
    return ok({
        "ok": True,
        "version": APP_VERSION,
        "sources": {key: value["available"] for key, value in registry.items()},
        "source_registry": registry,
    })


@require_auth
async def h_me(request, u):
    uid = int(u["uid"])
    premium = db.is_premium(uid)
    lim = PREMIUM_LIMITS if premium else FREE_LIMITS
    streak, _ = await to_thread(db.track_streak, uid)
    return ok({
        "ok": True,
        "user": {
            "id": uid,
            "username": u.get("username") or "",
            "name": u.get("first_name") or "",
            "premium": premium,
            "premium_until": str(u.get("premium_until") or ""),
            "lang": u.get("lang") or "uk",
            "theme": u.get("theme") or "midnight",
            "accent": u.get("accent") or "",
            "crossfade": int(u.get("crossfade") or 0),
            "eq": json.loads(u.get("eq_settings") or "null") if u.get("eq_settings") else None,
            "is_admin": uid in ADMIN_IDS,
            "streak": streak,
            "ambient_style": u.get("ambient_style") or "blur",
        },
        "limits": lim,
        "features": [
            {"key": k, "title": t, "description": d} for k, t, d in PREMIUM_FEATURES
        ],
    })


@require_auth
async def h_settings(request, u):
    uid = int(u["uid"])
    try:
        body = await json_object(request)
        if body is None:
            return fail("Некоректний JSON або очікувався JSON-об’єкт")
    except (ValueError, web.HTTPException):
        return fail("Некоректний JSON")
    if not isinstance(body, dict):
        return fail("Очікувався JSON-об'єкт")
    premium = db.is_premium(uid)
    fields = {}
    if "theme" in body:
        theme = str(body["theme"])
        allowed = PREMIUM_LIMITS["themes"] if premium else FREE_LIMITS["themes"]
        if theme not in allowed:
            return fail("Ця тема доступна в Premium")
        fields["theme"] = theme
    if "accent" in body and premium:
        fields["accent"] = str(body["accent"])[:16]
    if "crossfade" in body:
        crossfade = bounded_int(body["crossfade"], 0, 0, 12)
        if crossfade is None:
            return fail("Некоректне значення кросфейду")
        if not premium and crossfade > 0:
            return fail("Кросфейд доступний у Premium")
        fields["crossfade"] = crossfade
    if "eq" in body:
        if not premium:
            return fail("Еквалайзер доступний у Premium")
        eq = body["eq"]
        if (not isinstance(eq, list) or len(eq) != 10
                or any(not isinstance(v, (int, float)) or not -12 <= float(v) <= 12 for v in eq)):
            return fail("Некоректні налаштування еквалайзера")
        fields["eq_settings"] = json.dumps([int(round(float(v))) for v in eq])
    if "ambient_style" in body:
        style = str(body["ambient_style"])
        allowed = PREMIUM_LIMITS["ambient_styles"] if premium else FREE_LIMITS["ambient_styles"]
        if style not in allowed:
            return fail("Цей стиль обкладинки доступний у Premium")
        fields["ambient_style"] = style
    if "lang" in body:
        lang = str(body["lang"])
        if lang not in ("uk", "ru", "en", "pl", "de", "es", "fr", "tr"):
            return fail("Непідтримувана мова")
        fields["lang"] = lang
    db.update_user(uid, **fields)
    return ok({"ok": True, "saved": list(fields.keys())})



# ─── Connect: передача відтворення між пристроями (як Spotify Connect) ────────
# Уся логіка — у core/connect.py; тут лише транспорт (WebSocket) і авторизація.

async def _connect_load(uid):
    saved = await to_thread(db.player_session_get, uid)
    return (saved or {}).get("state") if isinstance(saved, dict) else None


async def _connect_persist(uid, state):
    try:
        if state:
            await to_thread(db.player_session_set, uid, "", json.dumps(state, ensure_ascii=False))
    except Exception as e:
        logger.warning("Connect: не вдалося зберегти сесію: %s", type(e).__name__)


CONNECT = Hub(load=_connect_load, persist=_connect_persist)
_STARTED = time.time()


async def h_connect_ws(request):
    """WebSocket-канал одного пристрою. Авторизація — підписаний initData."""
    user = auth_user(request)
    if not user:
        return fail("Потрібен вхід через Telegram", 401)
    uid = int(user["uid"])
    device_id = str(request.query.get("device_id") or "")
    if not valid_device_id(device_id):
        return fail("Некоректний ідентифікатор пристрою")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024, compress=False)
    if not ws.can_prepare(request).ok:
        return fail("Очікується WebSocket-з’єднання", 400)
    await ws.prepare(request)

    async def send(msg):
        await ws.send_json(msg, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    await CONNECT.connect(uid, device_id, request.query.get("name", ""),
                          request.query.get("platform", ""), send,
                          volume_ok=request.query.get("volume_ok") != "0")
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                if message.data == "ping":
                    await ws.send_str("pong")
                    continue
                try:
                    data = json.loads(message.data)
                except ValueError:
                    continue
                try:
                    await CONNECT.handle(uid, device_id, data)
                except Exception:
                    logger.exception("Connect: помилка обробки повідомлення")
            elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                break
    except (ConnectionError, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        await CONNECT.disconnect(uid, device_id, send)
    return ws


@require_auth
async def h_connect_devices(request, u):
    """Знімок пристроїв (для діагностики та першого малювання списку)."""
    return ok({"ok": True, **CONNECT.devices_for(int(u["uid"]))})


# ─── Каталог ─────────────────────────────────────────────────────────────────

def _search_filters(request):
    """Безпечні необов'язкові фільтри пошуку: source, release_type, availability."""
    source = (request.query.get("source") or "").strip().lower()[:20]
    rtype = (request.query.get("release_type") or "").strip().lower()
    rtype = rtype if rtype in SR.RELEASE_TYPES else ""
    avail = (request.query.get("availability") or "").strip().lower()
    avail = avail if avail in ("playable", "metadata_only") else ""
    return source, rtype, avail


def _engine_tracks(query, limit, qual, sources, sort):
    """Новий рушій пошуку (кеш 5 хв, перевірка живих посилань як у старому шляху)."""
    key = ("se2", query.strip().lower(), limit, qual, tuple(sources or ()), sort)
    hit = SM.cache_get(key)
    if hit is None:
        tracks, extras = SE.search(query, limit, qual, sources, sort)
        SM.cache_put(key, (tracks, extras), 300)
    else:
        tracks, extras = hit
    return SM.filter_available(tracks, quality=qual), extras


async def _engine_all(query, limit, qual, sources, sort):
    (tracks, extras), albums, artists = await asyncio.gather(
        to_thread(_engine_tracks, query, limit, qual, sources, sort),
        to_thread(P.search_albums_smart, query, limit, sort),
        to_thread(P.search_artists_smart, query, limit))
    return {"tracks": tracks, "albums": albums, "artists": artists}, extras


@require_auth
async def h_search(request, u):
    uid = int(u["uid"])
    query = (request.query.get("q") or "").strip()
    if len(query) < 2:
        return fail("Введіть щонайменше 2 символи")
    kind = request.query.get("type", "tracks")
    max_allowed = db.limits_for(uid)["search_results_max"]
    limit = bounded_int(request.query.get("limit"), 30, 1, max(1, int(max_allowed)))
    if limit is None:
        return fail("Некоректний ліміт результатів")

    sort = request.query.get("sort", "relevance")
    if sort not in ("relevance", "popularity", "newest"):
        sort = "relevance"
    sources_raw = (request.query.get("sources") or "").strip()
    sources = [s for s in sources_raw.split(",")
               if s in ("soundcloud", "jamendo", "audius", "archive", "ccmixter",
                        "openverse", "wikimedia", "youtube")] or None

    allowed, used, cap = await to_thread(db.bump_counter, uid, "searches")
    if not allowed:
        return fail(f"Ліміт пошуку на сьогодні вичерпано ({cap}). Premium знімає ліміт.", 429)

    await to_thread(db.search_history_add, uid, query, kind)
    qual = quality_for(uid)
    extras = {}
    if kind == "all":
        if SE.engine_enabled():
            data, extras = await _engine_all(query, min(limit, 20), qual, sources, sort)
        else:
            data = await to_thread(P.search_everything, query, min(limit, 20), qual, sources, sort)
        data = SR.postprocess("all", data, query, sort, *_search_filters(request))
        data = await to_thread(annotate_search, "all", data)
        return ok({"ok": True, "type": kind, "query": query, "sort": sort,
                   "results": data, "searches_left": max(0, cap - used), **extras})
    elif kind == "albums":
        data = await to_thread(P.search_albums_smart, query, limit, sort)
        data = SR.postprocess("albums", data, query, sort, *_search_filters(request))
        data = await to_thread(annotate_search, "albums", data)
    elif kind == "artists":
        data = await to_thread(P.search_artists_smart, query, limit)
        data = SR.postprocess("artists", data, query, sort, *_search_filters(request))
        data = await to_thread(annotate_search, "artists", data)
    elif kind == "public_domain":
        data = await to_thread(P.archive_search, query, limit)
    elif SE.engine_enabled():
        data, extras = await to_thread(_engine_tracks, query, limit, qual, sources, sort)
    else:
        data = await to_thread(SM.search, query, limit, qual, sources, sort)
    return ok({"ok": True, "type": kind, "query": query, "sort": sort,
               "results": data, "searches_left": max(0, cap - used), **extras})


@require_auth
async def h_daily(request, u):
    """'Трек дня' — комунальна щоденна знахідка, однакова для всіх."""
    uid = int(u["uid"])
    offset = 0
    if request.query.get("preview") == "1":
        if not db.limits_for(uid)["daily_preview"]:
            return fail("Погляд наперед доступний у Premium", 403)
        offset = 1
    t = await to_thread(P.daily_track, offset)
    return ok({"ok": True, "track": t, "preview": bool(offset)})


@require_auth
async def h_mood(request, u):
    uid = int(u["uid"])
    mood = request.match_info["mood"]
    allowed = db.limits_for(uid)["moods"]
    if mood not in allowed:
        return fail("Цей настрій доступний у Premium", 403)
    tracks = await to_thread(P.mood_mix, mood, 30)
    label = P.MOOD_MAP.get(mood, {}).get("label", mood)
    return ok({"ok": True, "mood": mood, "label": label, "tracks": tracks})


@require_auth
async def h_moods_list(request, u):
    uid = int(u["uid"])
    allowed = set(db.limits_for(uid)["moods"])
    return ok({"ok": True, "moods": [
        {"key": k, "label": v["label"], "locked": k not in allowed}
        for k, v in P.MOOD_MAP.items()
    ]})


# ─── Вгадай трек (mini-game) ──────────────────────────────────────────────────

@require_auth
async def h_blindtest_pool(request, u):
    pool = await to_thread(P.blindtest_pool, 20)
    return ok({"ok": True, "tracks": pool,
               "rounds_per_day": db.limits_for(int(u["uid"]))["blindtest_per_day"]})


@require_auth
async def h_blindtest_submit(request, u):
    uid = int(u["uid"])
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    streak = max(0, int(body.get("streak", 0)))
    name = u.get("first_name") or u.get("username") or "Гравець"
    best = await to_thread(db.blindtest_submit, uid, name, streak)
    top = await to_thread(db.blindtest_top, 10)
    return ok({"ok": True, "best": best, "top": top})


@require_auth
async def h_blindtest_top(request, u):
    top = await to_thread(db.blindtest_top, 10)
    return ok({"ok": True, "top": top})


# ─── Мій рік у музиці ─────────────────────────────────────────────────────────

@require_auth
async def h_wrapped(request, u):
    uid = int(u["uid"])
    if not db.limits_for(uid)["wrapped"]:
        return fail("Підсумкова картка доступна у Premium", 403)
    stats = await to_thread(db.stats_for, uid, 365)
    label = "Мовчун" if stats["plays"] < 20 else \
            "Дослідник" if len(stats["top_artists"]) >= 8 else \
            "Відданий слухач" if stats["hours"] >= 20 else "Меломан"
    return ok({"ok": True, "stats": stats, "listener_type": label})


async def _safe(coro, default=None):
    """Одна невдала складова не повинна валити всю відповідь — повертаємо
    запасне значення й пишемо в лог, замість 500-ки на весь домашній екран."""
    try:
        return await coro
    except Exception as e:
        logger.warning("Домашній екран: підзапит не вдався (%s)", type(e).__name__)
        return default if default is not None else []


@require_auth
async def h_home(request, u):
    """Стартовий екран: добірки з різних легальних джерел. Добірки (playlists)
    та жанри тепер віддаються окремими легкими ендпоінтами — так один повільний
    чи впалий провайдер не блокує решту головного екрана."""
    uid = int(u["uid"])
    qual = quality_for(uid)
    tag = request.query.get("tag") or ""

    if tag:
        # Жанрова видача використовує лише підтримувані джерела.
        jam, aud = await asyncio.gather(
            _safe(to_thread(P.jamendo_tag_tracks, tag, 24, 0, qual)),
            _safe(to_thread(P.audius_trending, tag, 24)),
        )
        tracks, seen = [], set()
        for track in (jam or []) + (aud or []):
            key = (str(track.get("title", "")).strip().casefold(),
                   str(track.get("artist", "")).strip().casefold())
            if key in seen:
                continue
            seen.add(key)
            tracks.append(track)
        return ok({"ok": True, "sections": [
            {"id": f"tag:{tag}", "title": tag.capitalize(), "tracks": tracks[:60]}
        ]})

    popular, fresh, trending, recent = await asyncio.gather(
        _safe(to_thread(P.jamendo_tag_tracks, "pop", 20, 0, qual, "popularity_month")),
        _safe(to_thread(P.jamendo_tag_tracks, "electronic", 20, 0, qual, "releasedate_desc")),
        _safe(to_thread(P.audius_trending, None, 20)),
        _safe(to_thread(db.history_list, uid, 20)),
    )
    sections = [
        {"id": "recent", "title": "Нещодавнє", "tracks": recent},
        {"id": "popular", "title": "Популярне цього місяця", "tracks": popular},
        {"id": "fresh", "title": "Свіжі релізи", "tracks": fresh},
        {"id": "trending", "title": "У тренді на Audius", "tracks": trending},
    ]
    return ok({
        "ok": True,
        "sections": [s for s in sections if s["tracks"]],
    })


@require_auth
async def h_charts(request, u):
    """Добірки провайдерів із прозорою методикою; порожні чарти не повертаємо."""
    uid = int(u["uid"])
    quality = quality_for(uid)
    period = CH.normalize_period(request.query.get("period"))
    jobs = []
    for _cid, _title, _source, provider, param in CH.SPECS:
        if provider == "audius":
            jobs.append(_safe(to_thread(P.audius_trending, param, 30)))
        else:
            jobs.append(_safe(to_thread(P.jamendo_tag_tracks, param, 30, 0, quality, CH.JAMENDO_ORDER[period])))
    results = await asyncio.gather(*jobs)
    charts = CH.visible([CH.build_chart(spec, tracks, period) for spec, tracks in zip(CH.SPECS, results)])
    return ok({"ok": True, "period": period, "charts": charts})


@require_auth
async def h_discover(request, u):
    """Diverse cross-provider discovery across multiple genres; metadata/catalogue only."""
    queries = ("pop", "rock", "electronic", "hip hop", "jazz")
    artist_jobs = [to_thread(P.search_artists, query, 10) for query in queries]
    album_jobs = [to_thread(P.search_albums, query, 10, "popularity") for query in queries]
    results = await asyncio.gather(*[_safe(job) for job in artist_jobs + album_jobs])
    artists_raw, albums_raw = results[:len(queries)], results[len(queries):]

    def merge(groups, identity, limit=40):
        """Round-robin genre results so one large provider/genre cannot dominate."""
        merged, seen = [], set()
        for offset in range(max((len(group or []) for group in groups), default=0)):
            for group in groups:
                items = group or []
                if offset >= len(items):
                    continue
                item = items[offset]
                if not isinstance(item, dict) or not item.get(identity):
                    continue
                key = str(item[identity]).casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= limit:
                    return merged
        return merged

    return ok({
        "ok": True,
        "artists": merge(artists_raw, "id"),
        "albums": merge(albums_raw, "id"),
        "catalogue": "cross-provider",
        "genres": list(queries),
    })


@require_auth
async def h_collections(request, u):
    """Кураторські добірки (Jamendo + Audius) — окремо від /api/home, щоб
    завантажувались незалежно й не блокували решту головного екрана."""
    playlists = await _safe(to_thread(P.discover_playlists, 10))
    return ok({"ok": True, "playlists": playlists})


@require_auth
async def h_genres(request, u):
    return ok({"ok": True, "genres": [{"tag": t, "label": l} for t, l in P.JAMENDO_TAGS]})


@require_auth
async def h_curated(request, u):
    """Треки кураторського плейлиста (Jamendo або Audius)."""
    uid = int(u["uid"])
    pid = request.match_info["pid"]
    qual = quality_for(uid)
    tracks = await to_thread(P.curated_playlist_tracks, pid, qual)
    return ok({"ok": True, "id": pid, "tracks": tracks})


@require_auth
async def h_track(request, u):
    uid = int(u["uid"])
    t = await to_thread(P.get_track, request.match_info["tid"], quality_for(uid))
    if not t:
        return fail("Трек не знайдено", 404)
    t["in_library"] = await to_thread(db.lib_has, uid, t["id"])
    return ok({"ok": True, "track": t})


@require_auth
async def h_album(request, u):
    uid = int(u["uid"])
    prefix, raw = P.split_id(request.match_info["aid"])
    if prefix == "jam":
        tracks = await to_thread(P.jamendo_album_tracks, raw, quality_for(uid))
    elif prefix == "aud":
        tracks = await to_thread(P.audius_album_tracks, raw)
    elif prefix == "mb":
        from core import musicbrainz as MB
        raw = raw.removeprefix("release-group:")
        album = await to_thread(MB.release_group_by_id, raw)
        if not album:
            return fail("Альбом не знайдено в каталозі", 404)
        return ok({
            "ok": True,
            "tracks": album.get("tracks") or [],
            "metadata_only": True,
            "album": album,
        })
    else:
        return fail("Невідоме джерело альбому", 400)
    if not tracks:
        return fail("Альбом порожній або не знайдений", 404)
    return ok({"ok": True, "tracks": tracks, "album": {
        "id": request.match_info["aid"],
        "title": tracks[0].get("album") or "",
        "artist": tracks[0].get("artist") or "",
        "cover": tracks[0].get("cover") or "",
    }})


@require_auth
async def h_artist(request, u):
    uid = int(u["uid"])
    prefix, raw = P.split_id(request.match_info["aid"])
    qual = quality_for(uid)
    if prefix == "jam":
        artist, tracks = await asyncio.gather(
            to_thread(MC.cached, "jam:artist", raw, lambda: P.jamendo_artist_by_id(raw)),
            to_thread(P.jamendo_artist_tracks, raw, 50, qual),
        )
    elif prefix == "aud":
        artist, tracks = await asyncio.gather(
            to_thread(MC.cached, "aud:artist", raw, lambda: P.audius_user_by_id(raw)),
            to_thread(P.audius_user_tracks, raw, 50),
        )
    elif prefix == "scu":
        artist, tracks = await asyncio.gather(
            to_thread(MC.cached, "scu:artist", raw, lambda: P.soundcloud_user_by_id(raw)),
            to_thread(P.soundcloud_user_tracks, raw, 50),
        )
        if not artist and not tracks:
            return fail("Артиста не знайдено в каталозі", 404)
    elif prefix == "mb":
        raw = raw.removeprefix("artist:")
        from core import musicbrainz as MB
        artist, releases = await asyncio.gather(
            to_thread(MB.artist_by_id, raw),
            to_thread(MB.artist_release_groups, raw, 50),
        )
        if not artist:
            return fail("Артиста не знайдено в каталозі", 404)
        return ok({
            "ok": True,
            "tracks": [],
            "metadata_only": True,
            "releases": releases,
            "artist": {
                "id": request.match_info["aid"],
                "name": artist["name"],
                "cover": next((item.get("cover") for item in releases if item.get("cover")), ""),
                "type": artist.get("type", ""),
                "country": artist.get("country", ""),
                "disambiguation": artist.get("disambiguation", ""),
                "aliases": artist.get("aliases", []),
                "begin_date": artist.get("begin_date", ""),
                "end_date": artist.get("end_date", ""),
                "mbid": artist.get("mbid", ""),
                "source": "musicbrainz",
                "source_url": artist["source_url"],
            },
        })
    else:
        return fail("Невідоме джерело", 400)
    if prefix in {"jam", "aud"}:
        fallback_track = tracks[0] if tracks else {}
        if not artist and not tracks:
            return fail("Артиста не знайдено в каталозі", 404)
        artist = artist or {}
        return ok({"ok": True, "tracks": tracks, "artist": {
            **artist,
            "id": request.match_info["aid"],
            "name": artist.get("name") or fallback_track.get("artist") or "Невідомий артист",
            "cover": artist.get("cover") or fallback_track.get("cover") or "",
            "source": artist.get("source") or ("jamendo" if prefix == "jam" else "audius"),
        }})
    if prefix == "scu":
        fallback_track = tracks[0] if tracks else {}
        artist_name = (artist or {}).get("name") or fallback_track.get("artist") or "Невідомий артист"
        artist_cover = (artist or {}).get("cover") or fallback_track.get("cover") or ""
        return ok({"ok": True, "tracks": tracks, "artist": {
            **(artist or {}),
            "id": request.match_info["aid"], "name": artist_name,
            "cover": artist_cover, "source": "soundcloud",
            "source_url": (artist or {}).get("source_url") or "",
        }})
    if not tracks:
        return fail("У цього артиста поки немає треків", 404)
    return ok({"ok": True, "tracks": tracks, "artist": {
        "id": request.match_info["aid"], "name": tracks[0]["artist"],
        "cover": tracks[0]["cover"],
    }})


_h_artist_base = h_artist
_h_album_base = h_album


async def _enrich_json_response(resp, enrich):
    """Додати поля каталогу до успішної JSON-відповіді; помилки лишаються як є."""
    if getattr(resp, "status", 0) != 200:
        return resp
    try:
        payload = json.loads(resp.body)
        return ok(await to_thread(enrich, payload))
    except Exception:
        logger.warning("catalog enrichment skipped", exc_info=True)
        return resp


async def h_artist(request):
    resp = await _h_artist_base(request)
    prefix, raw = P.split_id(request.match_info["aid"])
    return await _enrich_json_response(resp, lambda p: enrich_artist(prefix, raw, p))


async def h_album(request):
    resp = await _h_album_base(request)
    prefix, raw = P.split_id(request.match_info["aid"])
    return await _enrich_json_response(resp, lambda p: enrich_album(p, prefix, raw))


@require_auth
async def h_recommendations(request, u):
    """Прозорі рекомендації за обраним, історією та підписками; без даних — чесно порожньо."""
    uid = int(u["uid"])
    library, history, following, hidden = await asyncio.gather(
        to_thread(db.lib_list, uid, 200), to_thread(db.history_list, uid, 100),
        to_thread(db.artist_following, uid), to_thread(UD.hidden_keys, uid))
    profile = REC.taste_profile(library, history, following)
    seeds = REC.pick_seeds(profile, 3)
    qual = quality_for(uid)
    lists = await asyncio.gather(*[_safe(to_thread(P.build_radio, s["seed"], 15, qual)) for s in seeds])
    result = REC.build(profile, {s["key"]: (tracks or []) for s, tracks in zip(seeds, lists)},
                       list(library) + list(history), hidden)
    return ok({"ok": True, **result, "basis": [a["artist"] for a in profile["top"][:5]]})


@require_auth
async def h_personal_collections(request, u):
    """Персональні добірки з власних даних користувача; без достатніх даних — чесно порожньо."""
    uid = int(u["uid"])
    library, history, hidden = await asyncio.gather(
        to_thread(db.lib_list, uid, 500), to_thread(db.history_list, uid, 200),
        to_thread(UD.hidden_keys, uid))
    return ok({"ok": True, **PC.build(library, history, hidden)})


@require_auth
async def h_recommendation_hide(request, u):
    body = await json_object(request)
    if body is None or not str(body.get("rec_key") or "").strip():
        return fail("Потрібен rec_key")
    await to_thread(UD.hide_recommendation, int(u["uid"]), str(body["rec_key"]))
    return ok({"ok": True})


@require_auth
async def h_me_export(request, u):
    data = await to_thread(UD.export_user, int(u["uid"]))
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json",
        headers={**CORS, "Content-Disposition": 'attachment; filename="musiclsp-export.json"'})


@require_auth
async def h_me_delete(request, u):
    body = await json_object(request)
    if body is None or body.get("confirm") != "DELETE":
        return fail('Для видалення даних надішліть {"confirm": "DELETE"}')
    deleted = await to_thread(UD.delete_user_data, int(u["uid"]))
    return ok({"ok": True, "deleted": deleted})


@require_auth
async def h_radio(request, u):
    uid = int(u["uid"])
    seed = request.query.get("seed") or ""
    if not seed:
        return fail("Потрібен seed-трек")
    length = db.limits_for(uid)["radio_length"]
    queue = await to_thread(P.build_radio, seed, length, quality_for(uid))
    return ok({"ok": True, "tracks": queue, "length": len(queue)})


async def h_resolve(request):
    """JSON-версія /api/stream: повертає готове посилання, щоб клієнт міг заздалегідь
    підготувати наступні треки й запускати їх без жодного запиту до сервера."""
    u = auth_user(request)
    tid = request.query.get("id") or ""
    if not tid:
        return fail("Немає id треку")
    qual = quality_for(int(u["uid"])) if u else "mp31"
    force = request.query.get("r") == "1"
    url, played = await to_thread(SM.resolve_url, tid, qual,
                                  (request.query.get("t") or "")[:200],
                                  (request.query.get("a") or "")[:200], force)
    if not url:
        return fail("Стрім недоступний", 404)
    return ok({"ok": True, "url": url, "id": played})


async def h_stream(request):
    """
    Редірект на офіційний стрім-URL джерела.
    Аудіо через себе не проксіюємо — так збережено статистику прослуховувань
    артиста в Jamendo/Audius, що є умовою чесного використання.
    """
    u = auth_user(request)
    tid = request.query.get("id") or ""
    if not tid:
        return fail("Немає id треку")
    if tid.startswith("rb:"):
        # інтернет-радіо: редірект на офіційний ефір станції (лише https)
        if not u:
            return fail("Потрібна авторизація", 401)
        station = await to_thread(RB.station_url, tid)
        if not station:
            return fail("Станція недоступна", 404)
        raise web.HTTPFound(location=station, headers=CORS)
    qual = quality_for(int(u["uid"])) if u else "mp31"
    # перевірка посилання на точний вибраний трек; підміни іншим треком немає
    force = request.query.get("r") == "1"
    url, _played = await to_thread(SM.resolve_stream_robust, tid, qual,
                                   (request.query.get("t") or "")[:200],
                                   (request.query.get("a") or "")[:200], force)
    if not url:
        return fail("Стрім недоступний", 404)
    raise web.HTTPFound(location=url, headers=CORS)


# ─── Бібліотека, плейлисти, історія ──────────────────────────────────────────

@require_auth
async def h_library_get(request, u):
    items = await to_thread(db.lib_list, int(u["uid"]))
    items = await to_thread(FAV.collapse, "track", items)
    return ok({"ok": True, "tracks": items})


@require_auth
async def h_library_post(request, u):
    uid = int(u["uid"])
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    action = body.get("action", "add")
    if action == "remove":
        await to_thread(db.lib_remove, uid, body.get("id", ""))
        return ok({"ok": True, "in_library": False})
    track = body.get("track") or {}
    if not track.get("id"):
        return fail("Немає треку")
    if await to_thread(db.lib_has, uid, track["id"]):
        await to_thread(db.lib_remove, uid, track["id"])
        return ok({"ok": True, "in_library": False})
    await to_thread(db.lib_add, uid, track)
    await to_thread(FAV.link_on_save, "track", str(track["id"]), track)
    return ok({"ok": True, "in_library": True})


@require_auth
async def h_queue_get(request, u):
    return ok({"ok": True, **(await to_thread(db.queue_get, int(u["uid"])))})


@require_auth
async def h_queue_post(request, u):
    body = await json_object(request)
    if body is None or not isinstance(body.get("tracks"), list):
        return fail("Очікується список треків")
    count = await to_thread(db.queue_save, int(u["uid"]), body["tracks"], body.get("current_index", 0))
    return ok({"ok": True, "count": count})


@require_auth
async def h_release_radar_get(request, u):
    uid = int(u["uid"])
    period = request.query.get("period", RF.DEFAULT_PERIOD)
    if period not in RF.PERIODS:
        return fail("Невідомий період")
    follows = await to_thread(db.artist_following, uid)
    semaphore = asyncio.Semaphore(5)

    async def albums_for(followed):
        artist = followed.get("artist") or {}
        name = str(followed.get("artist_name") or artist.get("name") or "").strip()
        if not name:
            return None