# -*- coding: utf-8 -*-
"""
HTTP API для Mini App.

Автентифікація: підпис Telegram WebApp initData (HMAC-SHA256).
Підробити user_id ззовні не вийде — саме тому старий варіант із
?user=<id> у query-рядку тут замінено.
"""

import io
import hmac
import json
import math
import time
import zipfile
import hashlib
import logging
import asyncio
import urllib.parse

import requests
from aiohttp import WSMsgType, web

from core import db
from core import runtime as RT
from core import providers as P
from core import smart as SM
from core.auth import verify_init_data
from core.config import (
    ADMIN_IDS, PREMIUM_FEATURES, FREE_LIMITS, PREMIUM_LIMITS,
    APP_VERSION, JAMENDO_CLIENT_ID, BOT_USERNAME, REFERRAL_REWARD_DAYS,
    ADMIN_CMD_PASSWORD,
)

logger = logging.getLogger(__name__)

_DEVICE_SOCKETS = {}


async def broadcast_device_event(uid, event):
    """Broadcast one event to all live sessions belonging to a Telegram user."""
    uid = int(uid)
    sockets = tuple(_DEVICE_SOCKETS.get(uid, ()))
    stale = []
    for socket in sockets:
        if socket.closed:
            stale.append(socket)
            continue
        try:
            await socket.send_json(event)
        except Exception:
            stale.append(socket)
    bucket = _DEVICE_SOCKETS.get(uid)
    if bucket is not None:
        for socket in stale:
            bucket.discard(socket)
        if not bucket:
            _DEVICE_SOCKETS.pop(uid, None)

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
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

async def h_options(request):
    return web.Response(status=204, headers=CORS)


async def h_health(request):
    return ok({
        "ok": True,
        "version": APP_VERSION,
        "sources": {
            "jamendo": bool(JAMENDO_CLIENT_ID),
            "audius": bool(P.audius_host()),
            "archive": True,
            "ccmixter": True,
            "openverse": True,
            "wikimedia": True,
            "soundcloud": P.soundcloud_enabled(),
        },
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



def valid_device_id(value):
    value = str(value or "")
    return bool(value and len(value) <= 80 and all(
        ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value
    ))


def safe_device_track(track):
    if not isinstance(track, dict) or not track.get("id"):
        return None
    fields = ("id", "title", "artist", "cover", "duration", "source",
              "source_label", "license_short", "source_url")
    safe = {}
    for key in fields:
        value = track.get(key)
        if isinstance(value, str):
            safe[key] = value[:2000 if key in ("cover", "source_url") else 240]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                if math.isfinite(float(value)):
                    safe[key] = value
            except (OverflowError, ValueError):
                continue
    safe["id"] = str(safe["id"])[:160]
    return safe if safe["id"] else None


# ─── Пристрої та передавання відтворення між сесіями ──────────────────────────
@require_auth
async def h_devices_get(request, u):
    devices = await to_thread(db.device_list, int(u["uid"]))
    return ok({"ok": True, "devices": devices})


@require_auth
async def h_device_register(request, u):
    try:
        body = await json_object(request)
        if body is None:
            return fail("Некоректний JSON або очікувався JSON-об’єкт")
    except (ValueError, web.HTTPException):
        return fail("Некоректний JSON")
    if not isinstance(body, dict):
        return fail("Очікувався JSON-об’єкт")
    device_id = str(body.get("device_id") or "")
    if not valid_device_id(device_id):
        return fail("Некоректний ідентифікатор пристрою")
    await to_thread(db.device_register, int(u["uid"]), device_id,
                    str(body.get("name") or "MusicLSP device"),
                    str(body.get("platform") or "web"))
    return ok({"ok": True, "device_id": device_id})


@require_auth
async def h_device_state(request, u):
    try:
        body = await json_object(request)
        if body is None:
            return fail("Некоректний JSON або очікувався JSON-об’єкт")
    except (ValueError, web.HTTPException):
        return fail("Некоректний JSON")
    if not isinstance(body, dict):
        return fail("Очікувався JSON-об’єкт")
    device_id = str(body.get("device_id") or "")
    state = body.get("state") or {}
    if not valid_device_id(device_id) or not isinstance(state, dict):
        return fail("Некоректний стан пристрою")
    try:
        position_raw = float(state.get("position") or 0)
        volume_raw = float(state.get("volume") or 0)
        if not math.isfinite(position_raw) or not math.isfinite(volume_raw):
            raise ValueError("non-finite playback state")
        position = int(max(0, min(86400, position_raw)))
        volume = max(0.0, min(1.0, volume_raw))
    except (TypeError, ValueError, OverflowError):
        return fail("Некоректний стан відтворення")
    safe = {"playing": state.get("playing") is True, "position": position, "volume": volume}
    track = safe_device_track(state.get("track"))
    if track:
        safe["track"] = track
    queue = state.get("queue")
    if isinstance(queue, list):
        safe["queue"] = [item for raw in queue[:30] if (item := safe_device_track(raw))]
        index = bounded_int(state.get("index"), 0, 0, max(0, len(safe["queue"]) - 1))
        safe["index"] = index if safe["queue"] else -1
    updated = await to_thread(db.device_state_update, int(u["uid"]), device_id,
                              json.dumps(safe, ensure_ascii=False))
    if not updated:
        return fail("Пристрій не зареєстрований", 404)
    await broadcast_device_event(int(u["uid"]), {"type": "state", "device_id": device_id, "state": safe})
    return ok({"ok": True})


@require_auth
async def h_device_command(request, u):
    try:
        body = await json_object(request)
        if body is None:
            return fail("Некоректний JSON або очікувався JSON-об’єкт")
    except (ValueError, web.HTTPException):
        return fail("Некоректний JSON")
    if not isinstance(body, dict):
        return fail("Очікувався JSON-об’єкт")
    device_id = str(body.get("device_id") or "")
    action = str(body.get("action") or "")
    payload = body.get("payload") or {}
    if action not in {"handoff", "play", "pause", "next", "previous", "seek", "volume"}:
        return fail("Невідома команда")
    if not valid_device_id(device_id) or not isinstance(payload, dict):
        return fail("Некоректна команда")
    uid = int(u["uid"])
    devices = await to_thread(db.device_list, uid)
    target = next((item for item in devices if item.get("device_id") == device_id), None)
    if not target or not target.get("active"):
        return fail("Пристрій зараз не в мережі", 404)
    if action == "play" and not payload:
        state = target.get("state") or {}
        queue = state.get("queue") if isinstance(state.get("queue"), list) else []
        if not queue and state.get("track"):
            queue = [state["track"]]
        if not queue:
            return fail("На пристрої немає треку для відновлення", 409)
        payload = {"queue": queue[:30], "index": state.get("index", 0), "position": state.get("position", 0)}
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > 30000:
        return fail("Завелика команда")
    command_id = await to_thread(db.device_command_add, uid, device_id, action, encoded)
    if command_id is None:
        return fail("Пристрій не знайдено або вже не в мережі", 404)
    command = {"id": command_id, "action": action, "payload": payload}
    await broadcast_device_event(uid, {"type": "command", "device_id": device_id, "command": command})
    return ok({"ok": True, "command_id": command_id})


@require_auth
async def h_device_commands(request, u):
    device_id = str(request.query.get("device_id") or "")
    after = bounded_int(request.query.get("after"), 0, 0, 2147483647)
    if not valid_device_id(device_id) or after is None:
        return fail("Некоректний запит")
    commands = await to_thread(db.device_commands_get, int(u["uid"]), device_id, after)
    return ok({"ok": True, "commands": commands})


async def h_devices_ws(request):
    """Authenticated low-latency event channel for this user's devices."""
    user = auth_user(request)
    if not user:
        return fail("Потрібен вхід через Telegram", 401)
    uid = int(user["uid"])
    device_id = str(request.query.get("device_id") or "")
    after = bounded_int(request.query.get("after"), 0, 0, 2147483647)
    if not valid_device_id(device_id) or after is None:
        return fail("Некоректний запит")
    devices = await to_thread(db.device_list, uid)
    if not any(item.get("device_id") == device_id for item in devices):
        return fail("Пристрій не зареєстрований", 404)
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4096, compress=False)
    if not ws.can_prepare(request).ok:
        return fail("Очікується WebSocket-з’єднання", 400)
    await ws.prepare(request)
    bucket = _DEVICE_SOCKETS.setdefault(uid, set())
    bucket.add(ws)
    try:
        await ws.send_json({"type": "ready", "device_id": device_id})
        for item in devices:
            await ws.send_json({"type": "state", "device_id": item.get("device_id"), "state": item.get("state") or {}})
        pending = await to_thread(db.device_commands_get, uid, device_id, after)
        for command in pending:
            await ws.send_json({"type": "command", "device_id": device_id, "command": command})
        async for message in ws:
            if message.type == WSMsgType.TEXT and message.data == "ping":
                await ws.send_str("pong")
            elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                break
    except (ConnectionError, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        bucket.discard(ws)
        if not bucket:
            _DEVICE_SOCKETS.pop(uid, None)
    return ws


# ─── Каталог ─────────────────────────────────────────────────────────────────

@require_auth
async def youtube_search_tracks(query, limit=12):
    """Пошук YouTube через офіційний Data API; повертаємо лише публічні
    відео, для яких API підтвердив можливість вбудованого відтворення."""
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return []
    try:
        search = requests.get("https://www.googleapis.com/youtube/v3/search",
            params={"part":"snippet","type":"video","videoEmbeddable":"true",
                    "safeSearch":"strict","maxResults":min(25,max(1,int(limit))),
                    "q":query,"key":key}, timeout=8)
        search.raise_for_status()
        items = search.json().get("items", [])
        ids = [x.get("id",{}).get("videoId") for x in items]
        ids = [x for x in ids if x]
        if not ids:
            return []
        check = requests.get("https://www.googleapis.com/youtube/v3/videos",
            params={"part":"status,contentDetails,snippet","id":",".join(ids),"key":key},
            timeout=8)
        check.raise_for_status()
        verified = {x["id"]:x for x in check.json().get("items", [])
                    if x.get("status",{}).get("embeddable") is True
                    and x.get("status",{}).get("privacyStatus") == "public"}
        out = []
        for item in items:
            vid = item.get("id",{}).get("videoId")
            v = verified.get(vid)
            if not v:
                continue
            snippet = v.get("snippet", {})
            thumbs = snippet.get("thumbnails", {})
            cover = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url","")
            out.append({"id":"yt:"+vid,"title":snippet.get("title","")[:240],
                        "artist":snippet.get("channelTitle","")[:180],
                        "cover":cover,"duration":0,"duration_str":"",
                        "source":"youtube","source_label":"YouTube"})
        return out
    except Exception as exc:
        logger.warning("YouTube search failed: %s", type(exc).__name__)
        return []


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
               if s in ("jamendo", "audius", "archive", "ccmixter", "openverse", "wikimedia")] or None

    allowed, used, cap = await to_thread(db.bump_counter, uid, "searches")
    if not allowed:
        return fail(f"Ліміт пошуку на сьогодні вичерпано ({cap}). Premium знімає ліміт.", 429)

    qual = quality_for(uid)
    if kind == "all":
        data = await to_thread(P.search_everything, query, min(limit, 20))
        return ok({"ok": True, "type": kind, "query": query, "sort": sort,
                   "results": data, "searches_left": max(0, cap - used)})
    elif kind == "albums":
        data = await to_thread(P.jamendo_search_albums_smart, query, limit, sort)
    elif kind == "artists":
        jam = await to_thread(P.jamendo_search_artists, query, limit)
        aud = await to_thread(P.audius_search_users, query, max(5, limit // 3))
        data = jam + aud
    elif kind == "public_domain":
        data = await to_thread(P.archive_search, query, limit)
    else:
        data = await to_thread(SM.search, query, limit, qual, sources, sort)
        youtube = await to_thread(youtube_search_tracks, query, min(12, limit))
        # YouTube є першим джерелом каталогу; інші ліцензовані джерела лишаються нижче.
        if youtube:
            data = youtube + [x for x in data if x.get("id") not in {y["id"] for y in youtube}]
            data = data[:limit]
    return ok({"ok": True, "type": kind, "query": query, "sort": sort,
               "results": data, "searches_left": max(0, cap - used)})


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
        logger.warning("Домашній екран: підзапит не вдався (%s)", e)
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
    if prefix != "jam":
        return fail("Альбоми доступні для Jamendo", 400)
    tracks = await to_thread(P.jamendo_album_tracks, raw, quality_for(uid))
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
        tracks = await to_thread(P.jamendo_artist_tracks, raw, 50, qual)
    elif prefix == "aud":
        tracks = await to_thread(P.audius_user_tracks, raw, 50)
    else:
        return fail("Невідоме джерело", 400)
    if not tracks:
        return fail("У цього артиста поки немає треків", 404)
    return ok({"ok": True, "tracks": tracks, "artist": {
        "id": request.match_info["aid"], "name": tracks[0]["artist"],
        "cover": tracks[0]["cover"],
    }})


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
    qual = quality_for(int(u["uid"])) if u else "mp31"
    # перевірка посилання + запасне джерело, якщо основне не відповідає
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
    return ok({"ok": True, "in_library": True})


@require_auth
async def h_playlists_get(request, u):
    return ok({"ok": True, "playlists": await to_thread(db.pl_list, int(u["uid"]))})


@require_auth
async def h_playlist_get(request, u):
    p = await to_thread(db.pl_get, int(request.match_info["pid"]))
    if not p:
        return fail("Плейлист не знайдено", 404)
    if int(p["uid"]) != int(u["uid"]):
        return fail("Немає доступу", 403)
    return ok({"ok": True, "playlist": p})


@require_auth
async def h_playlists_post(request, u):
    uid = int(u["uid"])
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    action = body.get("action", "")

    if action == "create":
        p, err = await to_thread(db.pl_create, uid, body.get("name", "Новий плейлист"),
                                 body.get("description", ""))
        return ok({"ok": True, "playlist": p}) if p else fail(err)

    if action == "delete":
        await to_thread(db.pl_delete, uid, int(body["pid"]))
        return ok({"ok": True})

    if action == "add_track":
        done, err = await to_thread(db.pl_add_track, uid, int(body["pid"]), body["track"])
        return ok({"ok": True}) if done else fail(err)

    if action == "remove_track":
        await to_thread(db.pl_remove_track, uid, int(body["pid"]), body["id"])
        return ok({"ok": True})

    if action == "copy":
        p, err = await to_thread(db.pl_copy, uid, int(body["pid"]), body.get("name"))
        return ok({"ok": True, "playlist": p}) if p else fail(err)

    return fail("Невідома дія")


@require_auth
async def h_shared(request, u):
    p = await to_thread(db.pl_get_by_code, request.match_info["code"])
    if not p:
        return fail("Плейлист не знайдено", 404)
    return ok({"ok": True, "playlist": p})


@require_auth
async def h_history_post(request, u):
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    track = body.get("track") or {}
    if track.get("id"):
        await to_thread(db.history_add, int(u["uid"]), track, int(body.get("seconds") or 0))
    return ok({"ok": True})


@require_auth
async def h_history_get(request, u):
    return ok({"ok": True, "tracks": await to_thread(db.history_list, int(u["uid"]), 100)})


@require_auth
async def h_report_post(request, u):
    """Скарга на трек (невірна ліцензія/автор, мертве посилання тощо)."""
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    track = body.get("track") or {}
    if not track.get("id"):
        return fail("Не вказано трек")
    reason = (body.get("reason") or "").strip()
    await to_thread(db.report_track, int(u["uid"]), track, reason)
    return ok({"ok": True})


@require_auth
async def h_stats(request, u):
    uid = int(u["uid"])
    days = min(int(request.query.get("days", 30)), db.limits_for(uid)["history_days"])
    return ok({"ok": True, "stats": await to_thread(db.stats_for, uid, days)})


@require_auth
async def h_promo(request, u):
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    done, days = await to_thread(db.promo_redeem, int(u["uid"]), body.get("code", ""))
    if not done:
        return fail("Код недійсний або вичерпаний")
    return ok({"ok": True, "days": days})


@require_auth
async def h_download(request, u):
    """Готує посилання на завантаження, якщо ліцензія треку це дозволяє."""
    uid = int(u["uid"])
    if not db.is_premium(uid):
        return fail("Завантаження доступне у Premium", 403)
    allowed, used, cap = await to_thread(db.bump_counter, uid, "downloads")
    if not allowed:
        return fail(f"Ліміт завантажень на сьогодні: {cap}", 429)
    url, track = await to_thread(P.resolve_download, request.query.get("id", ""))
    if not url:
        return fail("Ліцензія цього треку не дозволяє завантаження", 403)
    return ok({"ok": True, "url": url, "track": track,
               "attribution": _attribution(track)})


def _attribution(t):
    if not t:
        return ""
    return (f"{t.get('artist','')} — {t.get('title','')} · {t.get('license_short','')} · "
            f"{t.get('source_url','')}")


# ─── Реферальна система ──────────────────────────────────────────────────────

@require_auth
async def h_referral_get(request, u):
    uid = int(u["uid"])
    stats = await to_thread(db.referral_stats, uid)
    username = RT.get_bot_username()
    link = f"https://t.me/{username}?start=ref_{stats['code']}" if username else ""
    return ok({"ok": True, **stats, "link": link})


@require_auth
async def h_referral_post(request, u):
    uid = int(u["uid"])
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    done, who = await to_thread(db.apply_referral, uid, body.get("code", ""))
    if not done:
        msg = "Ви вже використали реферальний код" if who == "already" else "Код недійсний"
        return fail(msg)
    return ok({"ok": True, "reward_days": REFERRAL_REWARD_DAYS, "referrer": who})


# ─── Пресети еквалайзера ─────────────────────────────────────────────────────

@require_auth
async def h_eq_get(request, u):
    uid = int(u["uid"])
    presets = await to_thread(db.eq_preset_list, uid)
    return ok({"ok": True, "presets": presets, "limit": db.limits_for(uid)["eq_presets"]})


@require_auth
async def h_eq_post(request, u):
    uid = int(u["uid"])
    if not db.is_premium(uid):
        return fail("Пресети еквалайзера доступні у Premium", 403)
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    action = body.get("action")
    if action == "save":
        limit = db.limits_for(uid)["eq_presets"]
        p, err = await to_thread(
            db.eq_preset_save, uid, body.get("name", ""), body.get("settings"), limit)
        if not p:
            return fail(err or "Не вдалося зберегти")
        return ok({"ok": True, "preset": p})
    if action == "delete":
        await to_thread(db.eq_preset_delete, uid, int(body.get("id", 0)))
        return ok({"ok": True})
    return fail("Невідома дія")


# ─── Офлайн-пакет (Premium) ──────────────────────────────────────────────────

def _safe_filename(name):
    return "".join(c for c in (name or "track") if c not in '\\/:*?"<>|')[:80] or "track"


def _download_bounded(url, max_bytes):
    """Завантажує аудіо потоково з жорстким лімітом пам'яті."""
    if max_bytes <= 0:
        return None
    response = None
    try:
        response = requests.get(url, timeout=(5, 25), stream=True)
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            return None
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks) if total else None
    except (requests.RequestException, ValueError, TypeError):
        return None
    finally:
        if response is not None:
            response.close()


def _build_offline_zip(pl, max_tracks=15, max_total_mb=60):
    buf = io.BytesIO()
    added, total_mb = 0, 0.0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = [
            "MusicLSP — офлайн-пакет", pl.get("name", ""), "",
            "Кожен трек нижче поширюється під ліцензією, що дозволяє завантаження "
            "(Creative Commons або дозвіл артиста). Вказуйте автора при поширенні.", "",
        ]
        for t in pl.get("tracks", [])[:max_tracks]:
            try:
                url, full = P.resolve_download(t["id"])
            except Exception:
                url, full = "", None
            if not url:
                continue
            remaining_bytes = int(max_total_mb * 1024 * 1024 - total_mb * 1024 * 1024)
            data = _download_bounded(url, remaining_bytes)
            if not data:
                continue
            size_mb = len(data) / (1024 * 1024)
            info = full or t
            fname = _safe_filename(f"{info.get('artist','')} - {info.get('title','')}") + ".mp3"
            zf.writestr(fname, data)
            readme.append(
                f"- {info.get('artist','')} — {info.get('title','')} · "
                f"{info.get('license_short','')} · {info.get('source_url','')}"
            )
            total_mb += size_mb
            added += 1
        zf.writestr("README.txt", "\n".join(readme))
    return buf.getvalue() if added else None


@require_auth
async def h_offline_pack(request, u):
    uid = int(u["uid"])
    if not db.limits_for(uid)["offline_pack"]:
        return fail("Офлайн-пакет доступний у Premium", 403)
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return fail("Невірний плейлист", 404)
    pl = await to_thread(db.pl_get, pid)
    if not pl or int(pl["uid"]) != uid:
        return fail("Плейлист не знайдено", 404)
    data = await to_thread(_build_offline_zip, pl)
    if not data:
        return fail("У плейлисті немає треків із дозволом на завантаження")
    return web.Response(
        body=data, status=200,
        headers={**CORS, "Content-Type": "application/zip",
                 "Content-Disposition": f'attachment; filename="{_safe_filename(pl.get("name"))}.zip"'},
    )



# ─── Веб-адмінка (лише для ADMIN_IDS + пароль, як у боті) ───────────────────
# Підпис initData доводить, що це саме цей Telegram-користувач; пароль — друга
# перепона на випадок, якщо чийсь акаунт/телефон потрапить не в ті руки.
# Розблокування живе в пам'яті процесу 12 годин (після перезапуску — знову пароль).

_ADMIN_UNLOCKED = {}          # uid -> час закінчення
_ADMIN_FAILS = {}             # uid -> (кількість, час останньої спроби)
ADMIN_UNLOCK_TTL = 12 * 3600


def _admin_ok(uid):
    exp = _ADMIN_UNLOCKED.get(uid, 0)
    return uid in ADMIN_IDS and exp > time.time()


def require_admin(handler):
    async def wrapper(request):
        u = auth_user(request)
        if not u:
            return fail("Потрібен вхід через Telegram", 401)
        uid = int(u["uid"])
        if uid not in ADMIN_IDS:
            return fail("Немає доступу", 403)
        if not _admin_ok(uid):
            return fail("Потрібен пароль адмінки", 401)
        return await handler(request, u)
    return wrapper


@require_auth
async def h_admin_login(request, u):
    uid = int(u["uid"])
    if uid not in ADMIN_IDS:
        return fail("Немає доступу", 403)
    n, last = _ADMIN_FAILS.get(uid, (0, 0))
    if n >= 5 and time.time() - last < 300:
        return fail("Забагато спроб. Зачекайте 5 хвилин.", 429)
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    pw = str(body.get("password", ""))
    if hmac.compare_digest(pw.encode(), ADMIN_CMD_PASSWORD.encode()):
        _ADMIN_UNLOCKED[uid] = time.time() + ADMIN_UNLOCK_TTL
        _ADMIN_FAILS.pop(uid, None)
        await to_thread(db.audit, uid, "web_admin_login", "", "")
        return ok({"ok": True})
    _ADMIN_FAILS[uid] = (n + 1 if time.time() - last < 300 else 1, time.time())
    logger.warning("Невдала спроба входу в веб-адмінку, uid=%s", uid)
    return fail("Невірний пароль", 403)


@require_auth
async def h_admin_status(request, u):
    uid = int(u["uid"])
    return ok({"ok": True, "is_admin": uid in ADMIN_IDS, "unlocked": _admin_ok(uid)})


def _user_view(u):
    return {
        "uid": int(u["uid"]), "name": u.get("first_name") or "",
        "username": u.get("username") or "", "premium": bool(u.get("premium")),
        "premium_until": str(u.get("premium_until") or "")[:10],
        "created_at": str(u.get("created_at") or "")[:10],
        "trial_used": bool(u.get("trial_used")),
        "lang": u.get("lang") or "",
    }


@require_admin
async def h_admin_overview(request, u):
    stats = await to_thread(db.global_stats)
    signups = await to_thread(db.signups_by_day, 14)
    stats["open_reports"] = await to_thread(db.open_reports_count)
    extra = await to_thread(db.admin_extra_stats)
    return ok({"ok": True, "stats": stats, "extra": extra, "signups": signups})


@require_admin
async def h_admin_users(request, u):
    offset = max(0, int(request.query.get("offset", 0) or 0))
    search = (request.query.get("q") or "").strip()[:60]
    items, total = await to_thread(db.users_page, offset, 20, search)
    return ok({"ok": True, "total": total, "users": [_user_view(x) for x in items]})


@require_admin
async def h_admin_user_action(request, u):
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    try:
        target = int(body.get("uid"))
    except (TypeError, ValueError):
        return fail("Невірний ID")
    action = body.get("action", "")
    if not await to_thread(db.user_exists, target):
        return fail("Користувача не знайдено", 404)
    if action == "grant":
        days = max(1, min(int(body.get("days") or 7), 3650))
        await to_thread(db.extend_premium, target, days)
    elif action == "revoke":
        await to_thread(db.set_premium, target, False)
    else:
        return fail("Невідома дія")
    await to_thread(db.audit, int(u["uid"]), "web_admin_" + action, str(target),
                    str(body.get("days") or ""))
    fresh = await to_thread(db.get_user, target)
    return ok({"ok": True, "user": _user_view(fresh)})


@require_admin
async def h_admin_reports(request, u):
    offset = max(0, int(request.query.get("offset", 0) or 0))
    items, total = await to_thread(db.reports_page, offset, 20, "open")
    return ok({"ok": True, "total": total, "reports": [
        {"id": int(r["id"]), "tid": r.get("tid") or "", "title": r.get("title") or "",
         "artist": r.get("artist") or "", "source": r.get("source") or "",
         "reason": r.get("reason") or "", "uid": int(r.get("uid") or 0),
         "created_at": str(r.get("created_at") or "")[:16]} for r in items]})


@require_admin
async def h_admin_report_resolve(request, u):
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    await to_thread(db.report_resolve, int(body.get("id") or 0))
    await to_thread(db.audit, int(u["uid"]), "web_admin_report_resolve", str(body.get("id")), "")
    return ok({"ok": True})


@require_admin
async def h_admin_promos(request, u):
    if request.method == "POST":
        body = await json_object(request)
        if body is None:
            return fail("Некоректний JSON або очікувався JSON-об’єкт")
        action = body.get("action")
        if action == "create":
            import random, string
            code = (body.get("code") or "").strip().upper() or "".join(
                random.choices(string.ascii_uppercase + string.digits, k=8))
            days = max(1, min(int(body.get("days") or 30), 3650))
            uses = max(1, min(int(body.get("uses") or 1), 100000))
            try:
                await to_thread(db.promo_create, code, days, uses)
            except Exception:
                return fail("Такий код уже існує")
            await to_thread(db.audit, int(u["uid"]), "web_admin_promo_create", code, f"{days}d x{uses}")
        elif action == "delete":
            await to_thread(db.promo_delete, str(body.get("code") or ""))
        else:
            return fail("Невідома дія")
    promos = await to_thread(db.promo_list)
    return ok({"ok": True, "promos": [
        {"code": p["code"], "days": int(p["days"]), "uses_left": int(p["uses_left"])} for p in promos]})


@require_admin
async def h_admin_user_info(request, u):
    try:
        target = int(request.query.get("uid", ""))
    except ValueError:
        return fail("Невірний ID")
    d = await to_thread(db.user_detail, target)
    if not d:
        return fail("Користувача не знайдено", 404)
    v = _user_view(d)
    v.update(plays=d["plays"], playlists=d["playlists"], library=d["library"],
             last_play=d["last_play"], top_artists=d["top_artists"])
    return ok({"ok": True, "user": v})


@require_admin
async def h_admin_dm(request, u):
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    text = (body.get("text") or "").strip()
    try:
        target = int(body.get("uid"))
    except (TypeError, ValueError):
        return fail("Невірний ID")
    if not text or len(text) > 3500:
        return fail("Текст порожній або задовгий")
    bot = RT.get_bot()
    if bot is None:
        return fail("Бот ще не готовий")
    from telegram.constants import ParseMode
    try:
        await bot.send_message(target, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        return fail("Не вдалося надіслати: " + str(e)[:120])
    await to_thread(db.audit, int(u["uid"]), "web_admin_dm", str(target), text[:100])
    return ok({"ok": True})


@require_admin
async def h_admin_audit(request, u):
    items = await to_thread(db.audit_list, 40)
    return ok({"ok": True, "items": [
        {"actor": int(r.get("actor") or 0), "action": r.get("action") or "",
         "target": r.get("target") or "", "details": r.get("details") or "",
         "created_at": str(r.get("created_at") or "")[:16]} for r in items]})


_BROADCAST_STATE = {"running": False, "text": "", "total": 0, "sent": 0, "blocked": 0, "failed": 0, "done": True}


async def _broadcast_job(text, segment="all"):
    from telegram.constants import ParseMode
    from telegram.error import RetryAfter, Forbidden, TelegramError
    bot = RT.get_bot()
    st = _BROADCAST_STATE
    try:
        uids = await to_thread(db.user_ids_by_segment, segment)
        st.update(total=len(uids), sent=0, blocked=0, failed=0, done=False)
        for uid in uids:
            for attempt in range(3):
                try:
                    await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
                    st["sent"] += 1
                    break
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 0.5)
                except Forbidden:
                    st["blocked"] += 1
                    break
                except TelegramError:
                    if attempt == 2:
                        st["failed"] += 1
                    else:
                        await asyncio.sleep(1)
            await asyncio.sleep(0.05)
    finally:
        st["done"] = True
        st["running"] = False


@require_admin
async def h_admin_broadcast(request, u):
    st = _BROADCAST_STATE
    if request.method == "GET":
        return ok({"ok": True, **{k: st[k] for k in ("running", "total", "sent", "blocked", "failed", "done")}})
    body = await json_object(request)
    if body is None:
        return fail("Некоректний JSON або очікувався JSON-об’єкт")
    text = (body.get("text") or "").strip()
    if not text:
        return fail("Порожній текст")
    if len(text) > 3500:
        return fail("Занадто довгий текст")
    if st["running"]:
        return fail("Розсилка вже триває")
    if RT.get_bot() is None:
        return fail("Бот ще не готовий")
    segment = body.get("segment") if body.get("segment") in ("all", "premium", "free") else "all"
    st.update(running=True, text=text, done=False, sent=0, blocked=0, failed=0, total=0)
    await to_thread(db.audit, int(u["uid"]), "web_admin_broadcast", segment, text[:100])
    asyncio.get_running_loop().create_task(_broadcast_job(text, segment))
    return ok({"ok": True})


@web.middleware
async def error_middleware(request, handler):
    """Будь-яка помилка або 404 повертається як JSON із CORS-заголовками.
    Без цього Safari показує безглузде «Load failed» замість причини."""
    try:
        return await handler(request)
    except web.HTTPException as e:
        if e.status < 400:      # редіректи (302 на аудіо) — це не помилки, віддаємо як є
            raise
        if e.status == 404:
            return fail("Не знайдено. Перезапустіть сервіс bot на Railway — можливо, він старої версії.", 404)
        return fail(e.reason or "Помилка", e.status)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Повна помилка лишається в серверному журналі; клієнту не віддаємо
        # токени, SQL, шляхи файлів або інші внутрішні деталі.
        logger.exception("Необроблена помилка API %s", request.path)
        return fail("Внутрішня помилка сервера. Спробуйте ще раз.", 500)


def build_app():
    app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[error_middleware])
    r = app.router
    r.add_route("OPTIONS", "/{tail:.*}", h_options)
    r.add_get("/", h_health)
    r.add_get("/api/health", h_health)
    r.add_get("/api/me", h_me)
    r.add_get("/api/resolve", h_resolve)
    r.add_post("/api/settings", h_settings)
    r.add_get("/api/devices", h_devices_get)
    r.add_post("/api/devices/register", h_device_register)
    r.add_post("/api/devices/state", h_device_state)
    r.add_post("/api/devices/command", h_device_command)
    r.add_get("/api/devices/commands", h_device_commands)
    r.add_get("/api/devices/ws", h_devices_ws)
    r.add_get("/api/search", h_search)
    r.add_get("/api/home", h_home)
    r.add_get("/api/collections", h_collections)
    r.add_get("/api/genres", h_genres)
    r.add_get("/api/daily", h_daily)
    r.add_get("/api/curated/{pid}", h_curated)
    r.add_get("/api/referral", h_referral_get)
    r.add_post("/api/referral", h_referral_post)
    r.add_get("/api/eq_presets", h_eq_get)
    r.add_post("/api/eq_presets", h_eq_post)
    r.add_get("/api/moods", h_moods_list)
    r.add_get("/api/mood/{mood}", h_mood)
    r.add_get("/api/blindtest", h_blindtest_pool)
    r.add_post("/api/blindtest", h_blindtest_submit)
    r.add_get("/api/blindtest/top", h_blindtest_top)
    r.add_get("/api/wrapped", h_wrapped)
    r.add_get("/api/playlist/{pid}/offline", h_offline_pack)
    r.add_get("/api/track/{tid}", h_track)
    r.add_get("/api/album/{aid}", h_album)
    r.add_get("/api/artist/{aid}", h_artist)
    r.add_get("/api/radio", h_radio)
    r.add_get("/api/stream", h_stream)
    r.add_get("/api/library", h_library_get)
    r.add_post("/api/library", h_library_post)
    r.add_get("/api/playlists", h_playlists_get)
    r.add_post("/api/playlists", h_playlists_post)
    r.add_get("/api/playlist/{pid}", h_playlist_get)
    r.add_get("/api/shared/{code}", h_shared)
    r.add_get("/api/history", h_history_get)
    r.add_post("/api/history", h_history_post)
    r.add_post("/api/report", h_report_post)
    r.add_get("/api/stats", h_stats)
    r.add_post("/api/promo", h_promo)
    r.add_get("/api/download", h_download)
    r.add_post("/api/admin/login", h_admin_login)
    r.add_get("/api/admin/status", h_admin_status)
    r.add_get("/api/admin/overview", h_admin_overview)
    r.add_get("/api/admin/users", h_admin_users)
    r.add_post("/api/admin/user", h_admin_user_action)
    r.add_get("/api/admin/user_info", h_admin_user_info)
    r.add_post("/api/admin/dm", h_admin_dm)
    r.add_get("/api/admin/audit", h_admin_audit)
    r.add_get("/api/admin/reports", h_admin_reports)
    r.add_post("/api/admin/report_resolve", h_admin_report_resolve)
    r.add_route("*", "/api/admin/promos", h_admin_promos)
    r.add_route("*", "/api/admin/broadcast", h_admin_broadcast)
    return app
