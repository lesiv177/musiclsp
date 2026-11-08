# -*- coding: utf-8 -*-
"""
Легальні джерела музики.

jam — Jamendo (Creative Commons, офіційний API, стрімінг дозволено)
aud — Audius (відкритий протокол, артисти самі публікують треки)
arc — Internet Archive (суспільне надбання та CC)
ccm — ccMixter (автори самі викладають треки під CC, офіційне API)
ov  — Openverse (офіційний агрегатор CC Creative Commons/Wikimedia:
      Wikimedia Commons, частково Jamendo та інші відкриті джерела)
wm  — Wikimedia Commons напряму (офіційний MediaWiki API, без ключа)

Жоден провайдер не обходить DRM і не скрейпить закриті сервіси.
Кожен трек несе поле `license` і `source_url` — атрибуція обовʼязкова
за умовами Creative Commons.
"""

import re
import time
import random
import hashlib
import logging
import datetime
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait

import requests

from core.config import (
    JAMENDO_CLIENT_ID, JAMENDO_API,
    AUDIUS_APP_NAME, AUDIUS_BOOTSTRAP,
    ARCHIVE_ENABLED, HTTP_TIMEOUT, USER_AGENT,
    SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET,
)
from core.music_sources import DEFAULT_TRACK_SEARCH_ORDER, source_status

logger = logging.getLogger(__name__)
import os as _os
YOUTUBE_API_KEY = _os.environ.get("YOUTUBE_API_KEY", "").strip()   # опційно; без ключа YouTube вимкнено

_thread_http = threading.local()
_PROVIDER_EXECUTOR = ThreadPoolExecutor(max_workers=12, thread_name_prefix="music-provider")
_CATALOGUE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="music-catalogue")


def _http_session():
    """Окрема requests.Session на потік: Session не ділиться між паралельними jobs."""
    session = getattr(_thread_http, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _thread_http.session = session
    return session


def _safe_log_url(url):
    parsed = urllib.parse.urlsplit(str(url or ""))
    return (parsed.netloc + parsed.path)[:180]


def _get(url, params=None, timeout=HTTP_TIMEOUT):
    try:
        r = _http_session().get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            logger.warning("GET %s -> %s", _safe_log_url(url), r.status_code)
            return None
        return r.json()
    except Exception as e:
        logger.warning("GET %s failed: %s", _safe_log_url(url), type(e).__name__)
        return None


def fmt_duration(seconds):
    try:
        seconds = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


# ════════════════════════════════════════════════════════════════════════════
#  JAMENDO
# ════════════════════════════════════════════════════════════════════════════

JAMENDO_TAGS = [
    ("pop", "Pop"), ("rock", "Rock"), ("electronic", "Electronic"),
    ("hiphop", "Hip-Hop"), ("jazz", "Jazz"), ("classical", "Classical"),
    ("lounge", "Lounge"), ("metal", "Metal"), ("soundtrack", "Soundtrack"),
    ("world", "World"), ("chillout", "Chillout"), ("ambient", "Ambient"),
    ("folk", "Folk"), ("funk", "Funk"), ("reggae", "Reggae"),
    ("punk", "Punk"), ("blues", "Blues"), ("house", "House"),
    ("techno", "Techno"), ("acoustic", "Acoustic"),
]


def _approval_enabled(name):
    return _os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def jamendo_enabled():
    # Jamendo's API terms require a separate commercial-use agreement.
    return bool(JAMENDO_CLIENT_ID) and _approval_enabled("JAMENDO_COMMERCIAL_APPROVED")


def _jam_params(**extra):
    p = {"client_id": JAMENDO_CLIENT_ID, "format": "json"}
    p.update(extra)
    return p


def _jam_track(t, audioformat="mp32"):
    """Нормалізує лише треки з явно дозволеною для комерційного використання CC-ліцензією."""
    license_url = (t.get("license_ccurl") or "").strip()
    if not _cc_commercially_usable(license_url):
        return None
    tid = str(t.get("id") or "")
    audio = t.get("audio") or ""
    # Jamendo дозволяє перевизначити формат через параметр у стрім-URL
    if audio and audioformat and "audioformat=" not in audio:
        sep = "&" if "?" in audio else "?"
        audio = f"{audio}{sep}audioformat={audioformat}"
    cover = t.get("album_image") or t.get("image") or ""
    return {
        "id": f"jam:{tid}",
        "title": t.get("name") or "Без назви",
        "artist": t.get("artist_name") or "Невідомий артист",
        "artist_id": f"jam:{t.get('artist_id')}" if t.get("artist_id") else "",
        "album": t.get("album_name") or "",
        "album_id": f"jam:{t.get('album_id')}" if t.get("album_id") else "",
        "cover": cover,
        "duration": int(t.get("duration") or 0),
        "duration_str": fmt_duration(t.get("duration")),
        "source": "jamendo",
        "source_label": "Jamendo",
        "source_url": t.get("shareurl") or f"https://www.jamendo.com/track/{tid}",
        "license": license_url,
        "license_short": _cc_short(license_url),
        "stream": audio,
        "downloadable": bool(int(t.get("audiodownload_allowed") or 0)) if str(
            t.get("audiodownload_allowed", "")
        ) not in ("", "None") else False,
        "download_url": t.get("audiodownload") or "",
    }


def _cc_short(url):
    """Коротка назва ліцензії з CC-URL."""
    if not url:
        return "CC"
    u = url.lower()
    for code, label in (
        ("by-nc-nd", "CC BY-NC-ND"), ("by-nc-sa", "CC BY-NC-SA"),
        ("by-nd", "CC BY-ND"), ("by-nc", "CC BY-NC"),
        ("by-sa", "CC BY-SA"), ("publicdomain", "Public Domain"),
        ("zero", "CC0"), ("by", "CC BY"),
    ):
        if code in u:
            return label
    return "CC"


SORT_TO_JAMENDO_ORDER = {
    "relevance": None,               # без order — Jamendo сам ранжує за збігом
    "popularity": "popularity_total",
    "newest": "releasedate_desc",
}


def jamendo_search_tracks(query, limit=25, offset=0, audioformat="mp32", sort="relevance"):
    if not jamendo_enabled():
        return []
    params = dict(
        search=query, limit=limit, offset=offset,
        include="licenses musicinfo", audioformat=audioformat, imagesize=400,
    )
    order = SORT_TO_JAMENDO_ORDER.get(sort)
    if order:
        params["order"] = order
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(**params))
    if not data:
        return []
    return [track for t in data.get("results", []) if (track := _jam_track(t, audioformat))]


def jamendo_tag_tracks(tag, limit=25, offset=0, audioformat="mp32", order="popularity_month"):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(
        tags=tag, limit=limit, offset=offset, include="licenses",
        audioformat=audioformat, imagesize=400, order=order,
    ))
    if not data:
        return []
    return [track for t in data.get("results", []) if (track := _jam_track(t, audioformat))]


def jamendo_search_albums(query, limit=20, offset=0, sort="relevance"):
    if not jamendo_enabled():
        return []
    params = dict(namesearch=query, limit=limit, offset=offset, imagesize=400)
    order = SORT_TO_JAMENDO_ORDER.get(sort)
    if order:
        params["order"] = order
    data = _get(f"{JAMENDO_API}/albums/", _jam_params(**params))
    if not data:
        return []
    out = []
    for a in data.get("results", []):
        out.append({
            "id": f"jam:{a.get('id')}",
            "title": a.get("name") or "",
            "artist": a.get("artist_name") or "",
            "cover": a.get("image") or "",
            "year": (a.get("releasedate") or "")[:4],
            "release_date": str(a.get("releasedate") or "")[:10],
            "source": "jamendo",
            "source_label": "Jamendo",
            "source_url": a.get("shareurl") or "",
        })
    return out


def jamendo_album_tracks(album_id, audioformat="mp32"):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/albums/tracks/", _jam_params(
        id=album_id, audioformat=audioformat, imagesize=400, include="licenses",
    ))
    if not data or not data.get("results"):
        return []
    album = data["results"][0]
    tracks = []
    for t in album.get("tracks", []):
        t.setdefault("artist_name", album.get("artist_name"))
        t.setdefault("album_name", album.get("name"))
        t.setdefault("album_image", album.get("image"))
        t.setdefault("album_id", album.get("id"))
        track = _jam_track(t, audioformat)
        if track:
            tracks.append(track)
    return tracks


def jamendo_playlists(limit=15):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/playlists/", _jam_params(
        limit=limit, imagesize=400, order="creationdate_desc",
    ))
    if not data:
        return []
    out = []
    for p in data.get("results", []):
        out.append({
            "id": f"jam:{p.get('id')}",
            "title": p.get("name") or "",
            "cover": p.get("image") or "",
            "source": "jamendo",
            "source_label": "Jamendo",
            "source_url": p.get("shareurl") or "",
        })
    return out


def jamendo_playlist_tracks(playlist_id, audioformat="mp32"):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/playlists/tracks/", _jam_params(
        id=playlist_id, audioformat=audioformat, imagesize=400, include="licenses",
    ))
    if not data or not data.get("results"):
        return []
    pl = data["results"][0]
    tracks = []
    for t in pl.get("tracks", []):
        track = _jam_track(t, audioformat)
        if track:
            tracks.append(track)
    return tracks


def jamendo_search_artists(query, limit=20):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/artists/", _jam_params(
        namesearch=query, limit=limit, imagesize=400,
    ))
    if not data:
        return []
    return [{
        "id": f"jam:{a.get('id')}",
        "name": a.get("name") or "",
        "cover": a.get("image") or "",
        "source": "jamendo",
        "source_label": "Jamendo",
        "source_url": a.get("shareurl") or "",
    } for a in data.get("results", [])]


def jamendo_artist_tracks(artist_id, limit=40, audioformat="mp32"):
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/artists/tracks/", _jam_params(
        id=artist_id, limit=1, track_limit=limit,
        audioformat=audioformat, imagesize=400, include="licenses",
    ))
    if not data or not data.get("results"):
        return []
    artist = data["results"][0]
    out = []
    for t in artist.get("tracks", []):
        t.setdefault("artist_name", artist.get("name"))
        t.setdefault("artist_id", artist.get("id"))
        track = _jam_track(t, audioformat)
        if track:
            out.append(track)
    return out


def jamendo_track(track_id, audioformat="mp32"):
    if not jamendo_enabled():
        return None
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(
        id=track_id, audioformat=audioformat, imagesize=400, include="licenses musicinfo",
    ))
    if not data or not data.get("results"):
        return None
    return _jam_track(data["results"][0], audioformat)


def jamendo_similar(track_id, limit=20, audioformat="mp32"):
    """Треки, схожі на заданий — основа Smart Radio."""
    if not jamendo_enabled():
        return []
    data = _get(f"{JAMENDO_API}/tracks/similar/", _jam_params(
        id=track_id, limit=limit, audioformat=audioformat,
        imagesize=400, include="licenses",
    ))
    if not data:
        return []
    return [track for t in data.get("results", []) if (track := _jam_track(t, audioformat))]


# ════════════════════════════════════════════════════════════════════════════
#  AUDIUS
# ════════════════════════════════════════════════════════════════════════════

_audius_host = None
_audius_host_ts = 0
_audius_hosts_pool = []
_audius_dead = {}  # host -> час, коли визнали непридатним (щоб не пробувати одразу знову)
_audius_lock = threading.Lock()


def _audius_fetch_pool():
    data = _get(AUDIUS_BOOTSTRAP, timeout=8)
    return [h.rstrip("/") for h in ((data or {}).get("data") or []) if h]


def _audius_alive(host, timeout=4):
    try:
        r = _http_session().get(f"{host}/health_check", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def audius_host(force_new=False):
    """Обирає живий discovery-node і кешує на 30 хвилин.

    Раніше тут завжди брався перший хост зі списку без перевірки — якщо саме
    він тимчасово лежав, ВСІ треки з Audius ставали недоступні на всі 30 хвилин
    кешу (звідси "багато треків не грає і скіпає"). Тепер хост перевіряється
    health_check-ом перед тим, як його закешувати, а force_new=True (виклик
    після реальної невдачі стріму) миттєво зкидає поточний вибір і пробує іншого."""
    global _audius_host, _audius_host_ts, _audius_hosts_pool
    with _audius_lock:
        if not force_new and _audius_host and time.time() - _audius_host_ts < 1800:
            return _audius_host
        if force_new and _audius_host:
            _audius_dead[_audius_host] = time.time()
        if not _audius_hosts_pool or force_new:
            fresh = _audius_fetch_pool()
            if fresh:
                _audius_hosts_pool = fresh
        pool = [h for h in _audius_hosts_pool if time.time() - _audius_dead.get(h, 0) > 300]
        random.shuffle(pool)
        for host in pool[:6]:
            if _audius_alive(host):
                _audius_host = host
                _audius_host_ts = time.time()
                return _audius_host
        # Жоден кандидат не відповів — лишаємо старий вибір (може, ще працює
        # для вже кешованих запитів), а не глушимо все джерело мовчки.
        return _audius_host


def _aud_content_accessible(item):
    """Reject Audius items whose creator settings disallow public API playback."""
    if not isinstance(item, dict):
        return False
    if item.get("is_available") is False or item.get("is_streamable") is False:
        return False
    if item.get("is_delete") is True or item.get("is_unlisted") is True:
        return False
    if item.get("is_stream_gated") is True or item.get("is_private") is True:
        return False
    return True


def _aud_track(t):
    # Respect creator API/stream restrictions as well as explicit CC-NC terms.
    if not _aud_content_accessible(t):
        return None
    license = str(t.get("license") or "").strip().lower()
    compact_license = re.sub(r"[^a-z]", "", license)
    if "noncommercial" in compact_license or re.search(r"(^|[-_])nc($|[-_])", license):
        return None
    tid = str(t.get("id") or "").strip()
    if not tid:
        return None
    art = t.get("artwork") or {}
    cover = art.get("480x480") or art.get("150x150") or art.get("1000x1000") or ""
    user = t.get("user") or {}
    handle = user.get("handle") or ""
    perm = t.get("permalink") or ""
    return {
        "id": f"aud:{tid}",
        "title": t.get("title") or "Без назви",
        "artist": user.get("name") or handle or "Невідомий артист",
        "artist_id": f"aud:{user.get('id')}" if user.get("id") else "",
        "album": "",
        "album_id": "",
        "cover": cover,
        "duration": int(t.get("duration") or 0),
        "duration_str": fmt_duration(t.get("duration")),
        "source": "audius",
        "source_label": "Audius",
        "source_url": f"https://audius.co{perm}" if perm else "https://audius.co",
        "license": t.get("license") or "Ліцензія артиста (Audius)",
        "license_short": t.get("license") or "Artist licensed",
        "stream": "",  # резолвиться через /api/stream
        "downloadable": bool(t.get("downloadable")),
        "download_url": "",
    }


def _aud_album(item):
    """Normalise Audius albums; Audius represents album releases as playlists."""
    if not _aud_content_accessible(item):
        return None
    is_album = item.get("is_album") is True or str(item.get("type") or "").lower() == "album"
    if not is_album:
        return None
    album_id = str(item.get("id") or "").strip()
    title = str(item.get("playlist_name") or item.get("name") or "").strip()
    if not album_id or not title:
        return None
    user = item.get("user") or {}
    artwork = item.get("artwork") or {}
    cover = artwork.get("480x480") or artwork.get("150x150") or artwork.get("1000x1000") or ""
    permalink = str(item.get("permalink") or "")
    return {
        "id": f"aud:{album_id}",
        "title": title,
        "artist": user.get("name") or user.get("handle") or "",
        "cover": cover,
        "source": "audius",
        "source_label": "Audius",
        "source_url": f"https://audius.co{permalink}" if permalink.startswith("/") else "https://audius.co",
        "year": str(item.get("release_date") or "")[:4],
        "release_date": str(item.get("release_date") or "")[:10],
    }


def audius_search_albums(query, limit=20):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/playlists/search", {
        "query": query, "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    return [album for item in (data.get("data") or [])[:limit]
            if (album := _aud_album(item))]


def audius_album_tracks(album_id, limit=60):
    host = audius_host()
    if not host:
        return []
    params = {"app_name": AUDIUS_APP_NAME}
    meta_data = _get(f"{host}/v1/playlists/{album_id}", params)
    meta = (meta_data or {}).get("data") or {}
    data = _get(f"{host}/v1/playlists/{album_id}/tracks", {**params, "limit": limit})
    if not data:
        return []
    album_name = meta.get("playlist_name") or ""
    user = meta.get("user") or {}
    artist_name = user.get("name") or user.get("handle") or ""
    artwork = meta.get("artwork") or {}
    cover = artwork.get("480x480") or artwork.get("150x150") or artwork.get("1000x1000") or ""
    tracks = []
    for raw in (data.get("data") or [])[:limit]:
        track = _aud_track(raw)
        if not track:
            continue
        track["album"] = album_name
        track["album_id"] = f"aud:{album_id}"
        if artist_name:
            track["artist"] = artist_name
        if cover:
            track["cover"] = cover
        tracks.append(track)
    return tracks


def audius_search_tracks(query, limit=25):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/tracks/search", {
        "query": query, "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    return [track for t in (data.get("data") or [])[:limit] if (track := _aud_track(t))]


def audius_trending(genre=None, limit=25):
    host = audius_host()
    if not host:
        return []
    params = {"app_name": AUDIUS_APP_NAME, "limit": limit, "time": "week"}
    if genre:
        params["genre"] = genre
    data = _get(f"{host}/v1/tracks/trending", params)
    if not data:
        return []
    return [track for t in (data.get("data") or [])[:limit] if (track := _aud_track(t))]


def audius_trending_playlists(limit=15):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/playlists/trending", {
        "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    out = []
    for playlist in (data.get("data") or [])[:limit]:
        if not _aud_content_accessible(playlist):
            continue
        playlist_id = str(playlist.get("id") or "").strip()
        title = str(playlist.get("playlist_name") or "").strip()
        if not playlist_id or not title:
            continue
        art = playlist.get("artwork") or {}
        cover = art.get("480x480") or art.get("150x150") or art.get("1000x1000") or ""
        out.append({
            "id": f"aud:{playlist_id}",
            "title": title,
            "cover": cover,
            "source": "audius",
            "source_label": "Audius",
            "source_url": "https://audius.co",
        })
    return out


def audius_playlist_tracks(playlist_id, limit=60):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/playlists/{playlist_id}/tracks", {
        "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    return [track for t in (data.get("data") or []) if (track := _aud_track(t))][:limit]


def audius_track(track_id):
    host = audius_host()
    if not host:
        return None
    data = _get(f"{host}/v1/tracks/{track_id}", {"app_name": AUDIUS_APP_NAME})
    if not data or not data.get("data"):
        return None
    return _aud_track(data["data"])


def audius_stream_url(track_id, host=None):
    host = host or audius_host()
    if not host:
        return ""
    return f"{host}/v1/tracks/{track_id}/stream?app_name={urllib.parse.quote(AUDIUS_APP_NAME)}"


def _url_playable(url, timeout=5):
    """Легка перевірка, що посилання реально віддає аудіо, а не 404/500 —
    щоб клієнту ніколи не йшов мертвий стрім, який довелось би скіпати."""
    try:
        r = _http_session().head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 405:  # деякі ноди не вміють HEAD — пробуємо GET шматка
            r = _http_session().get(url, timeout=timeout, headers={"Range": "bytes=0-1"},
                              stream=True, allow_redirects=True)
            r.close()
        return r.status_code in (200, 206)
    except Exception:
        return False


def audius_stream_url_verified(track_id, attempts=3):
    """Як audius_stream_url, але перевіряє, що посилання справді грає, і
    пробує інші discovery-ноди, якщо перша не відповіла — саме це раніше
    ламало відразу купу треків, коли кешований хост тимчасово лежав."""
    tried = set()
    for i in range(attempts):
        host = audius_host(force_new=(i > 0))
        if not host or host in tried:
            continue
        tried.add(host)
        url = audius_stream_url(track_id, host)
        if _url_playable(url):
            return url
    return ""


def audius_search_users(query, limit=15):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/users/search", {
        "query": query, "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    out = []
    for u in (data.get("data") or [])[:limit]:
        art = u.get("profile_picture") or {}
        out.append({
            "id": f"aud:{u.get('id')}",
            "name": u.get("name") or u.get("handle") or "",
            "cover": art.get("480x480") or art.get("150x150") or "",
            "source": "audius",
            "source_label": "Audius",
            "source_url": f"https://audius.co/{u.get('handle', '')}",
        })
    return out


def audius_user_tracks(user_id, limit=40):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/users/{user_id}/tracks", {
        "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    return [track for raw in (data.get("data") or [])[:limit]
            if (track := _aud_track(raw))]


# ════════════════════════════════════════════════════════════════════════════
#  INTERNET ARCHIVE — суспільне надбання
# ════════════════════════════════════════════════════════════════════════════

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"


# Колекції Internet Archive, які реально містять легальну музику з чіткою
# ліцензією (а не побічно потрапили в "audio"):
#   78rpm           — оцифровані платівки суспільного надбання
#   audio_music     — загальна музична секція архіву
#   opensource_audio— записи, які автори самі виклали як відкриті
#   netlabels       — тисячі альбомів незалежних нетлейблів під CC —
#                     найбільший приріст каталогу з мінімумом ризику,
#                     бо це саме той самий Internet Archive, яким уже
#                     користуємось, просто ширший фільтр колекцій.
ARCHIVE_COLLECTIONS = ("78rpm", "audio_music", "opensource_audio", "netlabels")

# Короткі підписи для найпоширеніших ліцензій Internet Archive — щоб не
# показувати всьому підряд ярлик "Public Domain", коли насправді це CC-BY
# чи CC-BY-SA нетлейбла (там потрібна атрибуція, це різні речі юридично).
_CC_LICENSE_LABELS = [
    ("publicdomain/zero", "CC0 · Public Domain"),
    ("publicdomain", "Public Domain"),
    ("licenses/by-sa", "CC BY-SA"),
    ("licenses/by-nc-sa", "CC BY-NC-SA"),
    ("licenses/by-nc-nd", "CC BY-NC-ND"),
    ("licenses/by-nc", "CC BY-NC"),
    ("licenses/by-nd", "CC BY-ND"),
    ("licenses/by", "CC BY"),
]


def _cc_commercially_usable(license_url="", license_code=""):
    """Conservative commercial-use allowlist for Creative Commons metadata."""
    code = (license_code or "").strip().lower().replace("https://creativecommons.org/licenses/", "")
    code = code.strip("/").split("/")[0]
    url = (license_url or "").strip().lower()
    if "nc" in code or "-nc" in url or "/by-nc" in url:
        return False
    if code:
        return code in {"cc0", "pdm", "by", "by-sa", "by-nd"}
    if not url:
        return False
    return any(token in url for token in (
        "creativecommons.org/publicdomain/zero/",
        "creativecommons.org/publicdomain/mark/",
        "creativecommons.org/licenses/by/",
        "creativecommons.org/licenses/by-sa/",
        "creativecommons.org/licenses/by-nd/",
    ))

def _archive_commercially_usable(licenseurl):
    """Only explicit public-domain/CC licenses suitable for commercial use pass.

    Missing metadata is not evidence of public domain. NonCommercial variants
    are excluded because MusicLSP may be monetised.
    """
    return _cc_commercially_usable(licenseurl)


def _archive_license(licenseurl):
    url = (licenseurl or "").strip()
    if not url:
        return "", "Ліцензія не вказана"
    lower = url.lower()
    for needle, label in _CC_LICENSE_LABELS:
        if needle in lower:
            return url, label
    return url, "Ліцензію потрібно перевірити"


def archive_search(query, limit=20):
    if not ARCHIVE_ENABLED:
        return []
    coll = " OR ".join(f"collection:({c})" for c in ARCHIVE_COLLECTIONS)
    q = f'({query}) AND mediatype:(audio) AND ({coll})'
    data = _get(ARCHIVE_SEARCH, {
        "q": q, "fl[]": ["identifier", "title", "creator", "year", "licenseurl"],
        "rows": limit, "page": 1, "output": "json", "sort[]": "downloads desc",
    })
    docs = ((data or {}).get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        ident = d.get("identifier") or ""
        raw_license = d.get("licenseurl") or ""
        if not _archive_commercially_usable(raw_license):
            continue
        license_url, license_short = _archive_license(raw_license)
        out.append({
            "id": f"arc:{ident}",
            "title": d.get("title") or ident,
            "artist": creator or "Невідомий виконавець",
            "artist_id": "",
            "album": "",
            "album_id": "",
            "cover": f"https://archive.org/services/img/{ident}",
            "duration": 0,
            "duration_str": "",
            "source": "archive",
            "source_label": "Internet Archive",
            "source_url": f"https://archive.org/details/{ident}",
            "license": license_url,
            "license_short": license_short,
            "stream": "",
            "downloadable": True,
            "download_url": "",
            "year": str(d.get("year") or ""),
        })
    return out


def archive_collection_tracks(collection, limit=40, subject=None):
    """Треки конкретної колекції (напр. 'netlabels') — для добірки на
    головній, без пошукового запиту. Необов'язковий subject звужує вибірку
    до записів із відповідним тегом (напр. 'ukraine'/'russia' — так ми
    дістаємось україно- та російськомовних нетлейблів усередині величезної
    колекції netlabels, не вигадуючи окремих ідентифікаторів колекцій)."""
    if not ARCHIVE_ENABLED:
        return []
    q = f'mediatype:(audio) AND collection:({collection})'
    if subject:
        q += f' AND subject:("{subject}")'
    data = _get(ARCHIVE_SEARCH, {
        "q": q,
        "fl[]": ["identifier", "title", "creator", "year", "licenseurl"],
        "rows": limit, "page": 1, "output": "json", "sort[]": "downloads desc",
    })
    docs = ((data or {}).get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        ident = d.get("identifier") or ""
        raw_license = d.get("licenseurl") or ""
        if not ident or not _archive_commercially_usable(raw_license):
            continue
        license_url, license_short = _archive_license(raw_license)
        out.append({
            "id": f"arc:{ident}",
            "title": d.get("title") or ident,
            "artist": creator or "Невідомий виконавець",
            "artist_id": "", "album": "", "album_id": "",
            "cover": f"https://archive.org/services/img/{ident}",
            "duration": 0, "duration_str": "",
            "source": "archive", "source_label": "Internet Archive",
            "source_url": f"https://archive.org/details/{ident}",
            "license": license_url, "license_short": license_short,
            "stream": "", "downloadable": True, "download_url": "",
            "year": str(d.get("year") or ""),
        })
    return out


def archive_stream_url(identifier):
    """Шукає перший придатний аудіофайл у записі активного архівного джерела."""
    if not ARCHIVE_ENABLED:
        return ""
    meta = _get(f"https://archive.org/metadata/{identifier}")
    if not meta:
        return ""
    metadata = meta.get("metadata") or {}
    license_url = metadata.get("licenseurl") or meta.get("licenseurl") or ""
    if not _archive_commercially_usable(license_url):
        return ""
    files = meta.get("files") or []
    for ext in (".mp3", ".ogg", ".m4a", ".flac"):
        for f in files:
            name = f.get("name") or ""
            if name.lower().endswith(ext):
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  CCMIXTER — офіційне безключове API, треки завантажені самими авторами
#  під Creative Commons (ccmixter.org/query-api). Кожен трек несе посилання
#  на ліцензію й на оригінал сторінки автора.
# ════════════════════════════════════════════════════════════════════════════

CCMIXTER_API = "https://ccmixter.org/api/query"

_ccm_lock = threading.Lock()
_CCM_CACHE = {}


def _ccm_cache_put(t):
    with _ccm_lock:
        _CCM_CACHE[t["id"]] = t
        if len(_CCM_CACHE) > 3000:
            for k in list(_CCM_CACHE)[:1000]:
                _CCM_CACHE.pop(k, None)


def _ccm_cache_get(raw):
    with _ccm_lock:
        return _CCM_CACHE.get(f"ccm:{raw}")


def _ccm_from_json(entry):
    try:
        title = (entry.get("upload_name") or entry.get("title") or "").strip()
        artist = (entry.get("user_name") or entry.get("artist") or "").strip()
        audio_url = ""
        for f in (entry.get("files") or []):
            audio_url = f.get("download_url") or f.get("file_url") or ""
            if audio_url:
                break
        audio_url = audio_url or entry.get("file_url") or entry.get("download_url") or ""
        if not audio_url:
            return None
        license_url = entry.get("license_url") or entry.get("license") or ""
        if not _cc_commercially_usable(license_url):
            return None
        link = entry.get("upload_url") or entry.get("url") or "https://ccmixter.org"
        uid = str(entry.get("upload_id") or entry.get("id") or
                  hashlib.sha1(audio_url.encode()).hexdigest()[:12])
        return {
            "id": f"ccm:{uid}",
            "title": title or "Без назви",
            "artist": artist or "Невідомий артист",
            "artist_id": "", "album": "", "album_id": "",
            "cover": entry.get("art_url") or "",
            "duration": 0, "duration_str": "",
            "source": "ccmixter", "source_label": "ccMixter",
            "source_url": link,
            "license": license_url or "https://creativecommons.org/licenses/",
            "license_short": _cc_short(license_url),
            "stream": audio_url,
            "downloadable": True,
            "download_url": audio_url,
        }
    except Exception:
        return None


def _ccm_from_xml(xml_text, limit):
    """RSS/Atom — офіційний формат ccMixter, коли f=json недоступний."""
    import xml.etree.ElementTree as ET
    out = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for it in items[:limit]:
        def _find(tagname):
            for child in it:
                if child.tag.rsplit("}", 1)[-1] == tagname:
                    return child
            return None
        title_el = _find("title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        creator = ""
        for cand in ("creator", "author"):
            el = _find(cand)
            if el is not None:
                name_el = el.find("name")
                creator = ((name_el.text if name_el is not None else el.text) or "").strip()
                if creator:
                    break
        audio_url, link, license_url = "", "", ""
        for child in it:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "enclosure" and not audio_url:
                audio_url = child.get("url") or child.get("href") or ""
            elif tag == "link":
                href = child.get("href") or (child.text or "")
                rel = child.get("rel") or ""
                if rel == "enclosure" and not audio_url:
                    audio_url = href
                elif not link:
                    link = href
            elif tag == "license":
                license_url = child.get("href") or (child.text or "") or license_url
        if not audio_url or not _cc_commercially_usable(license_url):
            continue
        out.append({
            "id": f"ccm:{hashlib.sha1(audio_url.encode()).hexdigest()[:12]}",
            "title": title or "Без назви",
            "artist": creator or "Невідомий артист",
            "artist_id": "", "album": "", "album_id": "",
            "cover": "", "duration": 0, "duration_str": "",
            "source": "ccmixter", "source_label": "ccMixter",
            "source_url": link or "https://ccmixter.org",
            "license": license_url or "https://creativecommons.org/licenses/",
            "license_short": _cc_short(license_url),
            "stream": audio_url,
            "downloadable": True,
            "download_url": audio_url,
        })
    return out


def ccmixter_search(query, limit=15):
    """Пошук по ccMixter. Спершу пробує JSON-вивід, тоді відкатується на
    RSS/Atom — офіційне API само вирішує, в якому форматі відповісти."""
    if not query:
        return []
    out = []
    try:
        data = _get(CCMIXTER_API, {"search": query, "search_type": "any", "f": "json", "limit": limit})
        if isinstance(data, list):
            for entry in data[:limit]:
                t = _ccm_from_json(entry)
                if t:
                    out.append(t)
    except Exception as e:
        logger.warning("ccMixter JSON запит не вдався: %s", e)
    if not out:
        try:
            r = _http_session().get(CCMIXTER_API, params={"search": query, "search_type": "any", "f": "rss"},
                              timeout=HTTP_TIMEOUT)
            if r.status_code == 200 and r.text:
                out = _ccm_from_xml(r.text, limit)
        except Exception as e:
            logger.warning("ccMixter XML запит не вдався: %s", e)
    for t in out:
        _ccm_cache_put(t)
    return out[:limit]


# ════════════════════════════════════════════════════════════════════════════
#  OPENVERSE — офіційний агрегатор CC-аудіо від Creative Commons/Wikimedia
#  (api.openverse.org). Без ключа для помірного використання; об'єднує
#  Wikimedia Commons, частково Jamendo й інші відкриті колекції.
# ════════════════════════════════════════════════════════════════════════════

OPENVERSE_API = "https://api.openverse.org/v1/audio/"

_ov_lock = threading.Lock()
_OV_CACHE = {}


def _ov_cache_put(t):
    with _ov_lock:
        _OV_CACHE[t["id"]] = t
        if len(_OV_CACHE) > 3000:
            for k in list(_OV_CACHE)[:1000]:
                _OV_CACHE.pop(k, None)


def _ov_cache_get(raw):
    with _ov_lock:
        return _OV_CACHE.get(f"ov:{raw}")


def _ov_track(item):
    try:
        audio_url = item.get("url") or item.get("audio_url") or ""
        if not audio_url:
            for alt in (item.get("alt_files") or []):
                audio_url = alt.get("url") or ""
                if audio_url:
                    break
        if not audio_url:
            return None
        dur_ms = item.get("duration") or 0
        dur_s = int(dur_ms / 1000) if dur_ms else 0
        license_url = item.get("license_url") or ""
        license_code = item.get("license") or ""
        if not license_url or not _cc_commercially_usable(license_url, license_code):
            return None
        provider = item.get("provider") or item.get("source") or "openverse"
        uid = str(item.get("id") or hashlib.sha1(audio_url.encode()).hexdigest()[:12])
        return {
            "id": f"ov:{uid}",
            "title": item.get("title") or "Без назви",
            "artist": item.get("creator") or "Невідомий артист",
            "artist_id": "", "album": "", "album_id": "",
            "cover": item.get("thumbnail") or "",
            "duration": dur_s, "duration_str": fmt_duration(dur_s),
            "source": "openverse",
            "source_label": f"Openverse · {provider}",
            "source_url": item.get("foreign_landing_url") or "https://openverse.org",
            "license": license_url or "https://creativecommons.org/licenses/",
            "license_short": _cc_short(license_url or item.get("license") or ""),
            "stream": audio_url,
            "downloadable": True,
            "download_url": audio_url,
        }
    except Exception:
        return None


def openverse_search(query, limit=15):
    """Пошук по Openverse. Публічний ліміт без ключа помірний — якщо сервіс
    відповість помилкою чи 429, просто повертаємо порожній список, і пошук
    продовжує працювати на інших джерелах."""
    if not query:
        return []
    try:
        data = _get(OPENVERSE_API, {"q": query, "page_size": min(limit, 20)})
    except Exception as e:
        logger.warning("Openverse запит не вдався: %s", e)
        return []
    if not data:
        return []
    out = []
    for item in (data.get("results") or [])[:limit]:
        t = _ov_track(item)
        if t:
            out.append(t)
            _ov_cache_put(t)
    return out


# ════════════════════════════════════════════════════════════════════════════
#  WIKIMEDIA COMMONS — офіційний API MediaWiki (без ключа), напряму
#  (Openverse і так частково тягне звідти, але напряму дістаємо більше
#  й точніше фільтруємо: народні пісні, гімни, класика, історичні записи,
#  все під CC/суспільним надбанням з обов'язковою атрибуцією в extmetadata).
# ════════════════════════════════════════════════════════════════════════════

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

_wm_lock = threading.Lock()
_WM_CACHE = {}


def _wm_cache_put(t):
    with _wm_lock:
        _WM_CACHE[t["id"]] = t
        if len(_WM_CACHE) > 3000:
            for k in list(_WM_CACHE)[:1000]:
                _WM_CACHE.pop(k, None)


def _wm_cache_get(raw):
    with _wm_lock:
        return _WM_CACHE.get(f"wm:{raw}")


def _wm_strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _wm_track(page):
    try:
        infos = page.get("imageinfo") or []
        if not infos:
            return None
        info = infos[0]
        mime = info.get("mime") or ""
        if not mime.startswith("audio/"):
            return None
        url = info.get("url") or ""
        if not url:
            return None
        meta = info.get("extmetadata") or {}

        def _m(key, default=""):
            v = meta.get(key)
            return (v or {}).get("value", default) if isinstance(v, dict) else default

        title = re.sub(r"^File:", "", page.get("title") or "", flags=re.I)
        title = re.sub(r"\.\w{2,4}$", "", title).replace("_", " ").strip() or "Без назви"
        artist = _wm_strip_html(_m("Artist")) or _wm_strip_html(_m("Credit")) or "Wikimedia Commons"
        license_url = _m("LicenseUrl")
        license_short = _m("LicenseShortName") or _cc_short(license_url)
        if not _cc_commercially_usable(license_url):
            return None
        page_url = f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page.get('title') or '')}"
        uid = str(page.get("pageid") or hashlib.sha1(url.encode()).hexdigest()[:12])
        return {
            "id": f"wm:{uid}",
            "title": title[:200],
            "artist": artist[:120] or "Wikimedia Commons",
            "artist_id": "", "album": "", "album_id": "",
            "cover": "",
            "duration": 0, "duration_str": "",
            "source": "wikimedia",
            "source_label": "Wikimedia Commons",
            "source_url": page_url,
            "license": license_url or "https://creativecommons.org/licenses/",
            "license_short": license_short or "CC",
            "stream": url,
            "downloadable": True,
            "download_url": url,
        }
    except Exception:
        return None


def wikimedia_search(query, limit=15):
    """Пошук аудіофайлів на Wikimedia Commons через офіційний MediaWiki API.
    Без ключа, помірні ліміти — якщо сервіс не відповів, просто повертаємо
    порожній список і пошук триває на інших джерелах."""
    if not query:
        return []
    try:
        data = _get(WIKIMEDIA_API, {
            "action": "query", "format": "json",
            "generator": "search", "gsrsearch": f"filetype:audio {query}",            "gsrnamespace": 6, "gsrlimit": min(limit * 2, 40),
            "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
        })
    except Exception as e:
        logger.warning("Wikimedia Commons запит не вдався: %s", e)
        return []
    if not data:
        return []
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    out = []
    for page in pages:
        t = _wm_track(page)
        if t:
            out.append(t)
            _wm_cache_put(t)
        if len(out) >= limit:
            break
    return out


# ════════════════════════════════════════════════════════════════════════════
#  АГРЕГАЦІЯ
# ════════════════════════════════════════════════════════════════════════════

# ═══ SoundCloud (офіційне API, OAuth client_credentials) ════════════════════
# Потрібні SOUNDCLOUD_CLIENT_ID і SOUNDCLOUD_CLIENT_SECRET від зареєстрованого
# застосунку. Без них джерело мовчки вимкнене. Беремо лише треки з прапорцем
# streamable/playable; атрибуція й посилання на оригінал обовʼязкові (source_url).

_SC_API = "https://api.soundcloud.com"
_SC_TOKEN = {"v": "", "exp": 0}
_sc_lock = threading.Lock()
_SC_CACHE = {}


def soundcloud_enabled():
    # SoundCloud restricts third-party aggregated on-demand streaming unless
    # the app has explicit approval for that use case.
    return bool(SOUNDCLOUD_CLIENT_ID and SOUNDCLOUD_CLIENT_SECRET
                and _approval_enabled("SOUNDCLOUD_AGGREGATION_APPROVED"))


def _sc_token():
    with _sc_lock:
        if _SC_TOKEN["v"] and _SC_TOKEN["exp"] > time.time() + 60:
            return _SC_TOKEN["v"]
    try:
        r = _http_session().post("https://secure.soundcloud.com/oauth/token", data={
            "grant_type": "client_credentials",
            "client_id": SOUNDCLOUD_CLIENT_ID, "client_secret": SOUNDCLOUD_CLIENT_SECRET,
        }, headers={"Accept": "application/json; charset=utf-8"}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            logger.warning("SoundCloud token -> %s", r.status_code)
            return ""
        d = r.json()
        with _sc_lock:
            _SC_TOKEN["v"] = d.get("access_token", "")
            _SC_TOKEN["exp"] = time.time() + int(d.get("expires_in") or 3000)
        return _SC_TOKEN["v"]
    except Exception as e:
        logger.warning("SoundCloud token failed: %s", e)
        return ""


def _sc_headers():
    tok = _sc_token()
    return {"Authorization": f"OAuth {tok}", "Accept": "application/json; charset=utf-8"} if tok else None


def _sc_track(t):
    uid = str(t.get("id") or "")
    user = t.get("user") or {}
    art = (t.get("artwork_url") or user.get("avatar_url") or "").replace("-large", "-t500x500")
    lic = (t.get("license") or "").replace("-", " ").strip()
    tr = {
        "id": f"sc:{uid}",
        "title": t.get("title") or "Без назви",
        "artist": user.get("username") or "Невідомий артист",
        "artist_id": f"scu:{user.get('id')}" if user.get("id") else "", "album": "", "album_id": "",
        "cover": art,
        "duration": int((t.get("duration") or 0) / 1000),
        "duration_str": fmt_duration((t.get("duration") or 0) / 1000),
        "source": "soundcloud", "source_label": "SoundCloud",
        "source_url": t.get("permalink_url") or "https://soundcloud.com",
        "license": lic or "Ліцензія автора (SoundCloud)",
        "license_short": lic.upper()[:16] if lic and lic != "all rights reserved" else "SoundCloud",
        "stream": "",                      # резолвиться через /api/stream (потрібен токен)
        "downloadable": False, "download_url": "",
    }
    with _sc_lock:
        _SC_CACHE[tr["id"]] = tr
        if len(_SC_CACHE) > 3000:
            for k in list(_SC_CACHE)[:1000]:
                _SC_CACHE.pop(k, None)
    return tr


def soundcloud_search(query, limit=15):
    if not soundcloud_enabled():
        return []
    h = _sc_headers()
    if not h:
        return []
    try:
        r = _http_session().get(f"{_SC_API}/tracks", headers=h, timeout=HTTP_TIMEOUT,
                         params={"q": query, "access": "playable", "limit": limit, "linked_partitioning": "false"})
        if r.status_code != 200:
            logger.warning("SoundCloud search -> %s", r.status_code)
            return []
        data = r.json()
        if isinstance(data, dict):
            data = data.get("collection") or []
        return [_sc_track(t) for t in data
                if t.get("streamable", True) and t.get("access", "playable") == "playable"][:limit]
    except Exception as e:
        logger.warning("SoundCloud search failed: %s", e)
        return []


def soundcloud_stream_url(raw):
    if not soundcloud_enabled():
        return ""
    h = _sc_headers()
    if not h:
        return ""
    try:
        r = _http_session().get(f"{_SC_API}/tracks/{raw}/streams", headers=h, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            d = r.json()
            return d.get("http_mp3_128_url") or d.get("hls_mp3_128_url") or ""
    except Exception as e:
        logger.warning("SoundCloud streams failed: %s", e)
    return ""


def soundcloud_track(raw):
    if not soundcloud_enabled():
        return None
    with _sc_lock:
        hit = _SC_CACHE.get(f"sc:{raw}")
    if hit:
        return hit
    h = _sc_headers()
    if not h:
        return None
    try:
        r = _http_session().get(f"{_SC_API}/tracks/{raw}", headers=h, timeout=HTTP_TIMEOUT)
        return _sc_track(r.json()) if r.status_code == 200 else None
    except Exception:
        return None


def _sc_artist(user):
    """Normalize an official SoundCloud user record as a metadata artist."""
    raw_id = user.get("urn") or user.get("id")
    if not raw_id:
        return None
    raw_id = str(raw_id)
    # Keep the stable API id for the existing /users/{id} endpoints.
    numeric_id = str(user.get("id") or raw_id.rsplit(":", 1)[-1])
    name = str(user.get("username") or user.get("full_name") or "").strip()
    if not name:
        return None
    avatar = str(user.get("avatar_url") or "").replace("-large", "-t500x500")
    permalink = str(user.get("permalink_url") or "")
    return {
        "id": f"scu:{numeric_id}",
        "name": name,
        "cover": avatar,
        "source": "soundcloud",
        "source_label": "SoundCloud",
        "source_url": permalink or f"https://soundcloud.com/{user.get('permalink') or ''}",
        "metadata_only": False,
    }


def soundcloud_search_users(query, limit=20):
    """Search SoundCloud creators using the official authenticated API."""
    if not str(query or "").strip() or not soundcloud_enabled():
        return []
    headers = _sc_headers()
    if not headers:
        return []
    try:
        response = _http_session().get(
            f"{_SC_API}/users", headers=headers, timeout=HTTP_TIMEOUT,
            params={"q": str(query).strip(), "limit": max(1, min(int(limit), 50))},
        )
        if response.status_code != 200:
            logger.warning("SoundCloud user search -> %s", response.status_code)
            return []
        payload = response.json()
        users = payload.get("collection", []) if isinstance(payload, dict) else payload
        return [artist for item in users[:limit]
                if isinstance(item, dict) and (artist := _sc_artist(item))]
    except Exception as exc:
        logger.warning("SoundCloud user search failed: %s", type(exc).__name__)
        return []


def soundcloud_user_by_id(user_id):
    """Fetch one public SoundCloud creator profile by numeric API ID."""
    if not str(user_id or "").strip() or not soundcloud_enabled():
        return None
    headers = _sc_headers()
    if not headers:
        return None
    try:
        response = _http_session().get(
            f"{_SC_API}/users/{urllib.parse.quote(str(user_id), safe='')}",
            headers=headers, timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning("SoundCloud user detail -> %s", response.status_code)
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return _sc_artist(payload)
    except Exception as exc:
        logger.warning("SoundCloud user detail failed: %s", type(exc).__name__)
        return None


def soundcloud_user_tracks(user_id, limit=50):
    """Return playable tracks published by a SoundCloud user."""
    if not str(user_id or "").strip() or not soundcloud_enabled():
        return []
    headers = _sc_headers()
    if not headers:
        return []
    try:
        response = _http_session().get(
            f"{_SC_API}/users/{urllib.parse.quote(str(user_id), safe='')}/tracks",
            headers=headers, timeout=HTTP_TIMEOUT,
            params={"limit": max(1, min(int(limit), 200)), "access": "playable"},
        )
        if response.status_code != 200:
            logger.warning("SoundCloud user tracks -> %s", response.status_code)
            return []
        payload = response.json()
        tracks = payload.get("collection", []) if isinstance(payload, dict) else payload
        return [_sc_track(item) for item in tracks
                if isinstance(item, dict) and item.get("id")
                and item.get("streamable", True)
                and item.get("access", "playable") == "playable"][:limit]
    except Exception as exc:
        logger.warning("SoundCloud user tracks failed: %s", type(exc).__name__)
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  YouTube Music — ЛИШЕ каталог (офіційний Data API). Звук не проксується:
#  клієнт грає трек через офіційний embed-плеєр YouTube.
# ─────────────────────────────────────────────────────────────────────────────

def youtube_enabled():
    return bool(YOUTUBE_API_KEY)


def youtube_search(query, limit=8):
    if not youtube_enabled():
        return []
    try:
        r = _http_session().get("https://www.googleapis.com/youtube/v3/search", timeout=HTTP_TIMEOUT, params={
            "part": "snippet", "type": "video", "videoCategoryId": "10", "videoEmbeddable": "true",
            "maxResults": max(1, min(int(limit), 15)), "q": query, "key": YOUTUBE_API_KEY})
        if r.status_code != 200:
            logger.warning("YouTube search -> %s", r.status_code)
            return []
        out = []
        for it in r.json().get("items", []):
            vid = (it.get("id") or {}).get("videoId")
            sn = it.get("snippet") or {}
            if not vid:
                continue
            th = sn.get("thumbnails") or {}
            cover = (th.get("high") or th.get("medium") or th.get("default") or {}).get("url", "")
            out.append({
                "id": f"yt:{vid}", "title": sn.get("title") or "Без назви",
                "artist": (sn.get("channelTitle") or "").replace(" - Topic", ""),
                "artist_id": "", "album": "", "album_id": "", "cover": cover,
                "duration": 0, "duration_str": "",
                "source": "youtube", "source_label": "YouTube Music",
                "source_url": f"https://music.youtube.com/watch?v={vid}",
                "license": "YouTube (офіційний плеєр)", "license_short": "YouTube",
                "stream": "", "downloadable": False, "download_url": "", "embed_only": True,
            })
        return out
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
        return []



def split_id(full_id):
    """'jam:123' -> ('jam', '123')"""
    if not full_id or ":" not in full_id:
        return "", full_id or ""
    prefix, _, rest = full_id.partition(":")
    return prefix, rest


def _run_parallel(jobs, timeout=4):
    """Паралельний пошук із глобально обмеженою кількістю потоків.

    Раніше кожен запит створював власний ThreadPoolExecutor і залишав
    повільні мережеві потоки працювати після timeout. Часті пошуки могли
    безконтрольно накопичувати десятки потоків. Тепер пул спільний і обмежений.
    """
    if not jobs:
        return []
    futs = [_PROVIDER_EXECUTOR.submit(job) for job in jobs]
    done, pending = wait(futs, timeout=max(0.1, float(timeout)))
    for future in pending:
        future.cancel()  # скасовує задачі, які ще не почали виконуватись
    out = []
    for future in futs:  # зберігаємо стабільний порядок провайдерів
        if future in done:
            try:
                result = future.result()
                if isinstance(result, list):
                    out.extend(result)
            except Exception as exc:
                logger.warning("Джерело впало: %s", type(exc).__name__)
    return out


def search_tracks(query, limit=30, quality="mp32", sources=None, sort="relevance", genre=None):
    """Search the active source registry in a stable order and deduplicate results."""
    requested = set(DEFAULT_TRACK_SEARCH_ORDER if sources is None else sources)
    searchers = {
        "soundcloud": lambda: soundcloud_search(query, limit=limit),
        "jamendo": lambda: jamendo_search_tracks(query, limit=limit, audioformat=quality, sort=sort),
        "audius": lambda: audius_search_tracks(query, limit=max(10, limit // 2)),
        "archive": lambda: archive_search(query, limit=12),
        "ccmixter": lambda: ccmixter_search(query, limit=10),
        "openverse": lambda: openverse_search(query, limit=10),
        "wikimedia": lambda: wikimedia_search(query, limit=10),
        "youtube": lambda: youtube_search(query, limit=8),
    }
    enabled = {
        "soundcloud": soundcloud_enabled,
        "jamendo": jamendo_enabled,
        "audius": lambda: True,  # discovery nodes are resolved inside the adapter
        "archive": lambda: ARCHIVE_ENABLED,
        "ccmixter": lambda: True,
        "openverse": lambda: True,
        "wikimedia": lambda: True,
        "youtube": youtube_enabled,
    }
    if int(limit) <= 0:
        return []
    provider_keys = [key for key in DEFAULT_TRACK_SEARCH_ORDER
                     if key in requested and key in searchers and enabled[key]()]
    groups = _run_parallel_grouped([searchers[key] for key in provider_keys], timeout=6)

    # Keep configured source priority (SoundCloud first), but rotate through
    # providers so a full first-page response cannot hide every other source.
    seen, out = set(), []
    for track in _interleave_catalogue_groups(groups):
        if not isinstance(track, dict):
            continue
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        key = (_normalize_query(title).casefold(), _normalize_query(artist).casefold())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(track)
        if len(out) >= int(limit):
            break
    return out

def _normalize_query(text):
    """Прибирає пунктуацію й зайві пробіли — щоб пошук був терпимим до одруків."""
    cleaned = re.sub(r"[^\w\s]", " ", text or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _search_query_variants(query, max_variants=4):
    """Build bounded, progressively broader queries without repeating equivalent terms."""
    raw = str(query or "").strip()
    normalized = _normalize_query(raw)
    variants, seen = [], set()

    def add(value):
        value = str(value or "").strip()
        key = value.casefold()
        if value and key not in seen and len(variants) < max_variants:
            seen.add(key)
            variants.append(value)

    add(raw)
    add(normalized)
    # Preserve useful phrase combinations before broad single-token fallbacks.
    words = [word for word in normalized.split() if len(word) > 2]
    if len(words) > 1:
        add(" ".join(words[:2]))
        add(" ".join(words[-2:]))
    for word in words:
        add(word)
    return variants


def _merge_track_search_results(groups, limit):
    """Merge fallback query pages, preserving source order and removing duplicates."""
    seen_ids, seen_names, out = set(), set(), []
    for group in groups:
        for track in group or []:
            if not isinstance(track, dict):
                continue
            track_id = str(track.get("id") or "").strip().casefold()
            title = _normalize_query(track.get("title") or "").casefold()
            artist = _normalize_query(track.get("artist") or "").casefold()
            if not title:
                continue
            name_key = (title, artist)
            if (track_id and track_id in seen_ids) or name_key in seen_names:
                continue
            if track_id:
                seen_ids.add(track_id)
            seen_names.add(name_key)
            out.append(track)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def search_tracks_smart(query, limit=30, quality="mp32", sources=None, sort="relevance"):
    """Expanded search: combine exact, normalized and bounded broad fallback queries.

    Unlike the old implementation, a small exact-match page no longer prevents
    fallback queries from filling the remaining slots.
    """
    if int(limit) <= 0 or not str(query or "").strip():
        return []
    variants = _search_query_variants(query)
    groups = []
    for variant in variants:
        try:
            groups.append(search_tracks(variant, limit, quality, sources, sort))
        except Exception as exc:
            logger.warning("Expanded track search failed: %s", type(exc).__name__)
            groups.append([])
        if len(_merge_track_search_results(groups, limit)) >= int(limit):
            break
    return _merge_track_search_results(groups, limit)


def _run_parallel_grouped(jobs, timeout=6):
    """Run provider jobs concurrently while retaining each provider's result group."""
    if not jobs:
        return []
    futures = [_PROVIDER_EXECUTOR.submit(job) for job in jobs]
    done, pending = wait(futures, timeout=max(0.1, float(timeout)))
    for future in pending:
        future.cancel()
    grouped = []
    for future in futures:
        if future not in done:
            grouped.append([])
            continue
        try:
            result = future.result()
            grouped.append(result if isinstance(result, list) else [])
        except Exception as exc:
            logger.warning("Catalogue provider failed: %s", type(exc).__name__)
            grouped.append([])
    return grouped


def _interleave_catalogue_groups(groups):
    """Round-robin provider results so an early full catalogue cannot starve others."""
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group):
                yield group[index]


def search_albums(query, limit=20, sort="relevance"):
    """Merge album catalogues fairly and collapse duplicate releases across providers."""
    if int(limit) <= 0:
        return []
    def musicbrainz_albums():
        try:
            from core.musicbrainz import search_release_groups
            return search_release_groups(query, limit=limit)
        except Exception as exc:
            logger.warning("MusicBrainz album metadata search failed: %s", type(exc).__name__)
            return []

    groups = _run_parallel_grouped([
        lambda: jamendo_search_albums(query, limit=limit, sort=sort),
        lambda: audius_search_albums(query, limit=limit),
        musicbrainz_albums,
    ], timeout=6)
    seen, unique_groups = set(), []
    for group in groups:
        unique = []
        for album in group:
            if not isinstance(album, dict) or not album.get("id") or not album.get("title"):
                continue
            title = _normalize_query(str(album.get("title") or "")).casefold()
            artist = _normalize_query(str(album.get("artist") or "")).casefold()
            key = ("release", title, artist) if artist else ("id", str(album["id"]).casefold())
            if key in seen:
                continue
            seen.add(key)
            unique.append(album)
        unique_groups.append(unique)
    return list(_interleave_catalogue_groups(unique_groups))[:int(limit)]


def search_albums_smart(query, limit=20, sort="relevance"):
    """Search albums with conservative query normalisation fallback."""
    out = search_albums(query, limit=limit, sort=sort)
    if out:
        return out
    norm = _normalize_query(query)
    if norm and norm.casefold() != (query or "").strip().casefold():
        out = search_albums(norm, limit=limit, sort=sort)
        if out:
            return out
    for word in [word for word in norm.split() if len(word) > 2]:
        out = search_albums(word, limit=limit, sort=sort)
        if out:
            return out
    return []


def search_artists(query, limit=20):
    """Merge artist catalogues fairly without allowing one provider to starve another."""
    if int(limit) <= 0:
        return []
    def musicbrainz_artists():
        try:
            from core.musicbrainz import search_artists as mb_search_artists
            return mb_search_artists(query, limit=limit)
        except Exception as exc:
            logger.warning("MusicBrainz artist metadata search failed: %s", type(exc).__name__)
            return []

    groups = _run_parallel_grouped([
        lambda: soundcloud_search_users(query, limit=limit),
        lambda: jamendo_search_artists(query, limit=limit),
        lambda: audius_search_users(query, limit=limit),
        musicbrainz_artists,
    ], timeout=6)
    seen, out = set(), []
    for artist in _interleave_catalogue_groups(groups):
        if not isinstance(artist, dict) or not artist.get("id") or not artist.get("name"):
            continue
        key = str(artist["id"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(artist)
        if len(out) >= max(1, int(limit)):
            break
    return out


def search_artists_smart(query, limit=20):
    """Merge a bounded set of artist-query fallbacks without duplicate IDs."""
    if int(limit) <= 0 or not str(query or "").strip():
        return []
    merged, seen = [], set()
    for variant in _search_query_variants(query, max_variants=4):
        try:
            results = search_artists(variant, limit=limit)
        except Exception as exc:
            logger.warning("Artist search variant failed: %s", type(exc).__name__)
            continue
        for artist in results or []:
            if not isinstance(artist, dict) or not artist.get("id") or not artist.get("name"):
                continue
            key = str(artist["id"]).casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(artist)
            if len(merged) >= int(limit):
                return merged
    return merged


def search_everything(query, limit=12, quality="mp32", sources=None, sort="relevance"):
    """Bounded global catalogue search across tracks, albums and artists."""
    # Each entity family uses one aggregator and the same bounded provider pool.
    futures = [
        _CATALOGUE_EXECUTOR.submit(search_tracks_smart, query, limit=limit, quality=quality,
                                   sources=DEFAULT_TRACK_SEARCH_ORDER if sources is None else sources, sort=sort),
        _CATALOGUE_EXECUTOR.submit(search_albums_smart, query, limit=limit, sort=sort),
        _CATALOGUE_EXECUTOR.submit(search_artists_smart, query, limit=limit),
    ]
    done, pending = wait(futures, timeout=7)
    for future in pending:
        future.cancel()
    values = []
    for future in futures:
        if future not in done:
            values.append([])
            continue
        try:
            values.append(future.result() or [])
        except Exception as exc:
            logger.warning("Catalogue lookup failed: %s", type(exc).__name__)
            values.append([])
    tracks, albums, artists = values
    return {"tracks": tracks, "albums": albums, "artists": artists}

MOOD_MAP = {
    "chill":  {"tags": ["chillout", "lounge", "acoustic"], "label": "Спокій"},
    "energy": {"tags": ["electronic", "house", "techno"], "label": "Енергія"},
    "focus":  {"tags": ["ambient", "classical"], "label": "Фокус"},
    "sad":    {"tags": ["blues", "folk"], "label": "Сум"},
    "happy":  {"tags": ["pop", "funk"], "label": "Радість"},
    "party":  {"tags": ["house", "hiphop"], "label": "Вечірка"},
}


def mood_mix(mood, limit=30):
    """Мікс треків під настрій — з перевірених жанрових тегів Jamendo."""
    info = MOOD_MAP.get(mood)
    if not info:
        return []
    per_tag = max(6, limit // len(info["tags"]))
    out = []
    for tag in info["tags"]:
        try:
            out += jamendo_tag_tracks(tag, limit=per_tag)
        except Exception:
            pass
    seen, dedup = set(), []
    for t in out:
        key = (t["title"].strip().lower(), t["artist"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        dedup.append(t)
    random.shuffle(dedup)
    return dedup[:limit]


def blindtest_pool(limit=20):
    """Пул треків для міні-гри 'Вгадай трек' — популярне з обох джерел."""
    pool = []
    try:
        pool += jamendo_tag_tracks("pop", limit=15)
    except Exception:
        pass
    try:
        pool += audius_trending(limit=15)
    except Exception:
        pass
    random.shuffle(pool)
    return pool[:limit]


def daily_track(offset_days=0):
    """'Трек дня' — той самий для всіх користувачів протягом доби.
    Детермінований вибір із пулу популярного/трендового за датою (можна заглянути наперед)."""
    seed = (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()
    pool = []
    try:
        pool += jamendo_tag_tracks("pop", limit=25)
    except Exception:
        pass
    try:
        pool += audius_trending(limit=25)
    except Exception:
        pass
    if not pool:
        return None
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
    track = dict(pool[idx])
    track["daily_seed"] = seed
    return track


_CURATED_FALLBACK_TAGS = [
    ("chillout", "Chillout"), ("electronic", "Electronic"), ("jazz", "Jazz"),
    ("lounge", "Lounge"), ("acoustic", "Acoustic"), ("hiphop", "Hip-Hop"),
    ("classical", "Класика"), ("rock", "Rock"), ("ambient", "Ambient"),
    ("folk", "Folk"), ("metal", "Metal"), ("pop", "Pop"),
    ("reggae", "Reggae"), ("soundtrack", "Soundtrack"),
]

# Готові добірки прямо з окремих колекцій Internet Archive — на відміну від
# жанрових тегів Jamendo вище, це не запасний варіант "на випадок", а завжди
# доступний шар каталогу: тисячі альбомів під CC, які інакше губляться серед
# звичайного пошуку.
_ARCHIVE_CURATED_COLLECTIONS = [
    ("netlabels", None, "Нетлейбли"),
    ("opensource_audio", None, "Відкриті записи"),
    # Всередині величезної колекції netlabels Internet Archive є сотні
    # альбомів із тегами "ukraine"/"russia" — реальні, вже перевірені CC-
    # видання нетлейблів пострадянського простору (напр. Cian Orbe та інші),
    # а не мейнстрим-поп, який під такою ліцензією ніхто не видає.
    ("netlabels", "ukraine", "Українська музика (CC)"),
    ("netlabels", "russia", "Русскомовна музика (CC)"),
]


def _synthetic_collections(limit):
    """Build Jamendo-tag collections only when its approved catalogue is enabled."""
    if not jamendo_enabled():
        return []
    out = []
    for tag, label in _CURATED_FALLBACK_TAGS[:limit]:
        cover = ""
        try:
            preview = jamendo_tag_tracks(tag, 1, 0, "mp32")
            if preview:
                cover = preview[0].get("cover") or ""
        except Exception:
            pass
        out.append({
            "id": f"tag:{tag}", "title": label, "cover": cover,
            "source": "curated", "source_label": "MusicLSP",
            "source_url": "",
        })
    return out


def _archive_curated_tiles(limit=2):
    if not ARCHIVE_ENABLED:
        return []
    out = []
    for coll, subject, label in _ARCHIVE_CURATED_COLLECTIONS[:limit]:
        cover = ""
        try:
            preview = archive_collection_tracks(coll, limit=1, subject=subject)
            if preview:
                cover = preview[0].get("cover") or ""
        except Exception:
            pass
        raw_id = f"{coll}:{subject}" if subject else coll
        out.append({
            "id": f"arccol:{raw_id}", "title": label, "cover": cover,
            "source": "archive", "source_label": "Internet Archive",
            "source_url": f"https://archive.org/details/{coll}",
        })
    return out


def discover_playlists(limit=10):
    """Кураторські плейлисти — Jamendo/Audius плюс завжди доступний шар
    з великих CC-колекцій Internet Archive (не запасний варіант, а
    постійне розширення бібліотеки)."""
    n_archive = min(len(_ARCHIVE_CURATED_COLLECTIONS), limit)
    n_main = max(0, limit - n_archive)
    half = max(4, n_main // 2)
    out = []
    try:
        out += jamendo_playlists(limit=half)
    except Exception:
        pass
    try:
        out += audius_trending_playlists(limit=half)
    except Exception:
        pass
    if not out:
        # Обидва джерела порожні (частий випадок для Jamendo — офіційні
        # плейлисти публікують нерегулярно) — показуємо жанрові добірки,
        # аби розділ "Добірки" ніколи не був просто порожнім/невидимим.
        out = _synthetic_collections(n_main)
    out = out[:n_main]
    try:
        out += _archive_curated_tiles(limit=n_archive)
    except Exception:
        pass
    return out[:limit]


def curated_playlist_tracks(full_id, quality="mp32"):
    """Треки кураторського плейлиста за повним id ('jam:123' / 'aud:456' /
    'tag:electronic' для запасних жанрових добірок / 'arccol:netlabels'
    для добірок прямо з колекцій Internet Archive)."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        return jamendo_playlist_tracks(raw, audioformat=quality)
    if prefix == "aud":
        return audius_playlist_tracks(raw)
    if prefix == "tag":
        return jamendo_tag_tracks(raw, 40, 0, quality)
    if prefix == "arccol":
        if ":" in raw:
            coll, subject = raw.split(":", 1)
            return archive_collection_tracks(coll, limit=40, subject=subject)
        return archive_collection_tracks(raw, limit=40)
    return []


def ccmixter_track(raw_id):
    """Reload a ccMixter track by its stable upload ID after a process restart."""
    raw_id = str(raw_id or "")
    if not raw_id.isdigit():
        return None
    cached = _ccm_cache_get(raw_id)
    if cached:
        return cached
    data = _get(CCMIXTER_API, {"ids": raw_id, "f": "json"})
    entries = data if isinstance(data, list) else (
        (data or {}).get("results") or (data or {}).get("data") or []
    )
    for entry in entries:
        track = _ccm_from_json(entry)
        if track and track.get("id") == f"ccm:{raw_id}":
            _ccm_cache_put(track)
            return track
    return None


def openverse_track(raw_id):
    """Reload an Openverse audio record by UUID, rechecking its licence."""
    raw_id = str(raw_id or "")
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw_id):
        return None
    cached = _ov_cache_get(raw_id)
    if cached:
        return cached
    item = _get(f"{OPENVERSE_API}{urllib.parse.quote(raw_id)}/")
    track = _ov_track(item) if isinstance(item, dict) else None
    if not track or track.get("id") != f"ov:{raw_id}":
        return None
    _ov_cache_put(track)
    return track


def wikimedia_track(raw_id):
    """Reload a Wikimedia audio file by stable MediaWiki page ID and licence."""
    raw_id = str(raw_id or "")
    if not raw_id.isdigit():
        return None
    cached = _wm_cache_get(raw_id)
    if cached:
        return cached
    data = _get(WIKIMEDIA_API, {
        "action": "query", "format": "json", "pageids": raw_id,
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
    })
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        track = _wm_track(page)
        if track and track.get("id") == f"wm:{raw_id}":
            _wm_cache_put(track)
            return track
    return None


def get_track(full_id, quality="mp32"):
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        return jamendo_track(raw, audioformat=quality)
    if prefix == "aud":
        return audius_track(raw)
    if prefix == "arc":
        res = archive_search(raw, limit=1)
        return res[0] if res else None
    if prefix == "ccm":
        return ccmixter_track(raw)
    if prefix == "ov":
        return openverse_track(raw)
    if prefix == "wm":
        return wikimedia_track(raw)
    if prefix == "sc":
        return soundcloud_track(raw)
    return None


def resolve_stream(full_id, quality="mp32"):
    """Повертає прямий URL аудіо у легального джерела."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        t = jamendo_track(raw, audioformat=quality)
        return t["stream"] if t else ""
    if prefix == "aud":
        # Re-fetch metadata at resolution so an explicitly NonCommercial
        # track cannot bypass the catalogue filter via a stale/direct ID.
        if not audius_track(raw):
            return ""
        return audius_stream_url_verified(raw)
    if prefix == "arc":
        return archive_stream_url(raw)
    if prefix in {"ccm", "ov", "wm"}:
        track = get_track(full_id, quality)
        return track["stream"] if track else ""
    if prefix == "sc":
        return soundcloud_stream_url(raw)
    return ""


def resolve_download(full_id):
    """URL для завантаження. Порожньо, якщо ліцензія забороняє."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        t = jamendo_track(raw)
        if t and t["downloadable"] and t["download_url"]:
            return t["download_url"], t
        return "", t
    if prefix == "aud":
        t = audius_track(raw)
        if t and t["downloadable"]:
            return audius_stream_url_verified(raw), t
        return "", t
    if prefix == "arc":
        url = archive_stream_url(raw)
        res = archive_search(raw, limit=1)
        return url, (res[0] if res else None)
    if prefix in {"ccm", "ov", "wm"}:
        t = get_track(full_id)
        return (t["download_url"], t) if t and t.get("downloadable") else ("", t)
    return "", None


def build_radio(seed_track_id, length=20, quality="mp32"):
    """Smart Radio: схожі треки Jamendo + trending Audius як добивка."""
    prefix, raw = split_id(seed_track_id)
    queue = []
    if prefix == "jam":
        queue += jamendo_similar(raw, limit=min(length, 50), audioformat=quality)
    seed = get_track(seed_track_id, quality)
    if len(queue) < length and seed:
        queue += jamendo_search_tracks(seed["artist"], limit=length, audioformat=quality)
    if len(queue) < length:
        queue += audius_trending(limit=length - len(queue))

    seen, out = set(), []
    for t in queue:
        if t["id"] == seed_track_id or t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append(t)
    return out[:length]