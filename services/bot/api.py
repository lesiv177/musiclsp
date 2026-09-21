# -*- coding: utf-8 -*-
"""
HTTP API для Mini App.

Автентифікація: підпис Telegram WebApp initData (HMAC-SHA256).
Підробити user_id ззовні не вийде — саме тому старий варіант із
?user=<id> у query-рядку тут замінено.
"""

import hmac
import json
import time
import hashlib
import logging
import asyncio
import urllib.parse

from aiohttp import web

from core import db
from core import providers as P
from core.auth import verify_init_data
from core.config import (
    ADMIN_IDS, PREMIUM_FEATURES, FREE_LIMITS, PREMIUM_LIMITS,
    APP_VERSION, JAMENDO_CLIENT_ID,
)

logger = logging.getLogger(__name__)

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
        },
    })


@require_auth
async def h_me(request, u):
    uid = int(u["uid"])
    premium = db.is_premium(uid)
    lim = PREMIUM_LIMITS if premium else FREE_LIMITS
    return ok({
        "ok": True,
        "user": {
            "id": uid,
            "username": u.get("username") or "",
            "name": u.get("first_name") or "",
            "premium": premium,
            "premium_until": str(u.get("premium_until") or ""),
            "theme": u.get("theme") or "midnight",
            "accent": u.get("accent") or "",
            "crossfade": int(u.get("crossfade") or 0),
            "eq": json.loads(u.get("eq_settings") or "null") if u.get("eq_settings") else None,
            "is_admin": uid in ADMIN_IDS,
        },
        "limits": lim,
        "features": [
            {"key": k, "title": t, "description": d} for k, t, d in PREMIUM_FEATURES
        ],
    })


@require_auth
async def h_settings(request, u):
    uid = int(u["uid"])
    body = await request.json()
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
        if not premium and int(body["crossfade"] or 0) > 0:
            return fail("Кросфейд доступний у Premium")
        fields["crossfade"] = max(0, min(12, int(body["crossfade"] or 0)))
    if "eq" in body:
        if not premium:
            return fail("Еквалайзер доступний у Premium")
        fields["eq_settings"] = json.dumps(body["eq"])[:2000]
    db.update_user(uid, **fields)
    return ok({"ok": True, "saved": list(fields.keys())})


# ─── Каталог ─────────────────────────────────────────────────────────────────

@require_auth
async def h_search(request, u):
    uid = int(u["uid"])
    query = (request.query.get("q") or "").strip()
    if len(query) < 2:
        return fail("Введіть щонайменше 2 символи")
    kind = request.query.get("type", "tracks")
    limit = max(1, min(50, int(request.query.get("limit", 30))))

    sort = request.query.get("sort", "relevance")
    if sort not in ("relevance", "popularity", "newest"):
        sort = "relevance"
    sources_raw = (request.query.get("sources") or "").strip()
    sources = [s for s in sources_raw.split(",") if s in ("jamendo", "audius", "archive")] or None

    allowed, used, cap = await to_thread(db.bump_counter, uid, "searches")
    if not allowed:
        return fail(f"Ліміт пошуку на сьогодні вичерпано ({cap}). Premium знімає ліміт.", 429)

    qual = quality_for(uid)
    if kind == "albums":
        data = await to_thread(P.jamendo_search_albums, query, limit, 0, sort)
    elif kind == "artists":
        jam = await to_thread(P.jamendo_search_artists, query, limit)
        aud = await to_thread(P.audius_search_users, query, max(5, limit // 3))
        data = jam + aud
    elif kind == "public_domain":
        data = await to_thread(P.archive_search, query, limit)
    else:
        data = await to_thread(P.search_tracks, query, limit, qual, sources, sort)
    return ok({"ok": True, "type": kind, "query": query, "sort": sort,
               "results": data, "searches_left": max(0, cap - used)})


@require_auth
async def h_home(request, u):
    """Стартовий екран: добірки з різних легальних джерел."""
    uid = int(u["uid"])
    qual = quality_for(uid)
    tag = request.query.get("tag") or ""

    if tag:
        tracks = await to_thread(P.jamendo_tag_tracks, tag, 40, 0, qual)
        return ok({"ok": True, "sections": [
            {"id": f"tag:{tag}", "title": tag.capitalize(), "tracks": tracks}
        ]})

    popular, fresh, trending, playlists = await asyncio.gather(
        to_thread(P.jamendo_tag_tracks, "pop", 20, 0, qual, "popularity_month"),
        to_thread(P.jamendo_tag_tracks, "electronic", 20, 0, qual, "releasedate_desc"),
        to_thread(P.audius_trending, None, 20),
        to_thread(P.discover_playlists, 10),
    )
    recent = await to_thread(db.history_list, uid, 20)
    sections = [
        {"id": "recent", "title": "Нещодавнє", "tracks": recent},
        {"id": "popular", "title": "Популярне цього місяця", "tracks": popular},
        {"id": "fresh", "title": "Свіжі релізи", "tracks": fresh},
        {"id": "trending", "title": "У тренді на Audius", "tracks": trending},
    ]
    return ok({
        "ok": True,
        "sections": [s for s in sections if s["tracks"]],
        "playlists": playlists,
        "genres": [{"tag": t, "label": l} for t, l in P.JAMENDO_TAGS],
    })


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
    url = await to_thread(P.resolve_stream, tid, qual)
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
    body = await request.json()
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
    body = await request.json()
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
    body = await request.json()
    track = body.get("track") or {}
    if track.get("id"):
        await to_thread(db.history_add, int(u["uid"]), track, int(body.get("seconds") or 0))
    return ok({"ok": True})


@require_auth
async def h_history_get(request, u):
    return ok({"ok": True, "tracks": await to_thread(db.history_list, int(u["uid"]), 100)})


@require_auth
async def h_stats(request, u):
    uid = int(u["uid"])
    days = min(int(request.query.get("days", 30)), db.limits_for(uid)["history_days"])
    return ok({"ok": True, "stats": await to_thread(db.stats_for, uid, days)})


@require_auth
async def h_promo(request, u):
    body = await request.json()
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


def build_app():
    app = web.Application(client_max_size=2 * 1024 * 1024)
    r = app.router
    r.add_route("OPTIONS", "/{tail:.*}", h_options)
    r.add_get("/", h_health)
    r.add_get("/api/health", h_health)
    r.add_get("/api/me", h_me)
    r.add_post("/api/settings", h_settings)
    r.add_get("/api/search", h_search)
    r.add_get("/api/home", h_home)
    r.add_get("/api/curated/{pid}", h_curated)
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
    r.add_get("/api/stats", h_stats)
    r.add_post("/api/promo", h_promo)
    r.add_get("/api/download", h_download)
    return app
