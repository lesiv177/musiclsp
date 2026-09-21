# -*- coding: utf-8 -*-
"""
Легальні джерела музики.

jam — Jamendo (Creative Commons, офіційний API, стрімінг дозволено)
aud — Audius (відкритий протокол, артисти самі публікують треки)
arc — Internet Archive (суспільне надбання та CC)

Жоден провайдер не обходить DRM і не скрейпить закриті сервіси.
Кожен трек несе поле `license` і `source_url` — атрибуція обовʼязкова
за умовами Creative Commons.
"""

import time
import logging
import threading
import urllib.parse

import requests

from core.config import (
    JAMENDO_CLIENT_ID, JAMENDO_API,
    AUDIUS_APP_NAME, AUDIUS_BOOTSTRAP,
    ARCHIVE_ENABLED, HTTP_TIMEOUT, USER_AGENT,
)

logger = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get(url, params=None, timeout=HTTP_TIMEOUT):
    try:
        r = _session.get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            logger.warning("GET %s -> %s", url, r.status_code)
            return None
        return r.json()
    except Exception as e:
        logger.warning("GET %s failed: %s", url, e)
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


def _jam_params(**extra):
    p = {"client_id": JAMENDO_CLIENT_ID, "format": "json"}
    p.update(extra)
    return p


def _jam_track(t, audioformat="mp32"):
    """Нормалізує трек Jamendo у внутрішній формат."""
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
        "license": t.get("license_ccurl") or "https://creativecommons.org/licenses/",
        "license_short": _cc_short(t.get("license_ccurl")),
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


def jamendo_search_tracks(query, limit=25, offset=0, audioformat="mp32"):
    if not JAMENDO_CLIENT_ID:
        return []
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(
        search=query, limit=limit, offset=offset,
        include="licenses musicinfo", audioformat=audioformat,
        imagesize=400, order="popularity_total",
    ))
    if not data:
        return []
    return [_jam_track(t, audioformat) for t in data.get("results", [])]


def jamendo_tag_tracks(tag, limit=25, offset=0, audioformat="mp32", order="popularity_month"):
    if not JAMENDO_CLIENT_ID:
        return []
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(
        tags=tag, limit=limit, offset=offset, include="licenses",
        audioformat=audioformat, imagesize=400, order=order,
    ))
    if not data:
        return []
    return [_jam_track(t, audioformat) for t in data.get("results", [])]


def jamendo_search_albums(query, limit=20, offset=0):
    if not JAMENDO_CLIENT_ID:
        return []
    data = _get(f"{JAMENDO_API}/albums/", _jam_params(
        namesearch=query, limit=limit, offset=offset, imagesize=400,
    ))
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
            "source": "jamendo",
            "source_label": "Jamendo",
            "source_url": a.get("shareurl") or "",
        })
    return out


def jamendo_album_tracks(album_id, audioformat="mp32"):
    if not JAMENDO_CLIENT_ID:
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
        tracks.append(_jam_track(t, audioformat))
    return tracks


def jamendo_search_artists(query, limit=20):
    if not JAMENDO_CLIENT_ID:
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
    if not JAMENDO_CLIENT_ID:
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
        out.append(_jam_track(t, audioformat))
    return out


def jamendo_track(track_id, audioformat="mp32"):
    if not JAMENDO_CLIENT_ID:
        return None
    data = _get(f"{JAMENDO_API}/tracks/", _jam_params(
        id=track_id, audioformat=audioformat, imagesize=400, include="licenses musicinfo",
    ))
    if not data or not data.get("results"):
        return None
    return _jam_track(data["results"][0], audioformat)


def jamendo_similar(track_id, limit=20, audioformat="mp32"):
    """Треки, схожі на заданий — основа Smart Radio."""
    if not JAMENDO_CLIENT_ID:
        return []
    data = _get(f"{JAMENDO_API}/tracks/similar/", _jam_params(
        id=track_id, limit=limit, audioformat=audioformat,
        imagesize=400, include="licenses",
    ))
    if not data:
        return []
    return [_jam_track(t, audioformat) for t in data.get("results", [])]


# ════════════════════════════════════════════════════════════════════════════
#  AUDIUS
# ════════════════════════════════════════════════════════════════════════════

_audius_host = None
_audius_host_ts = 0
_audius_lock = threading.Lock()


def audius_host():
    """Обирає живий discovery-node. Кешує на 30 хвилин."""
    global _audius_host, _audius_host_ts
    with _audius_lock:
        if _audius_host and time.time() - _audius_host_ts < 1800:
            return _audius_host
        data = _get(AUDIUS_BOOTSTRAP, timeout=8)
        hosts = (data or {}).get("data") or []
        if hosts:
            _audius_host = hosts[0].rstrip("/")
            _audius_host_ts = time.time()
        return _audius_host


def _aud_track(t):
    tid = str(t.get("id") or "")
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


def audius_search_tracks(query, limit=25):
    host = audius_host()
    if not host:
        return []
    data = _get(f"{host}/v1/tracks/search", {
        "query": query, "app_name": AUDIUS_APP_NAME, "limit": limit,
    })
    if not data:
        return []
    return [_aud_track(t) for t in (data.get("data") or [])][:limit]


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
    return [_aud_track(t) for t in (data.get("data") or [])][:limit]


def audius_track(track_id):
    host = audius_host()
    if not host:
        return None
    data = _get(f"{host}/v1/tracks/{track_id}", {"app_name": AUDIUS_APP_NAME})
    if not data or not data.get("data"):
        return None
    return _aud_track(data["data"])


def audius_stream_url(track_id):
    host = audius_host()
    if not host:
        return ""
    return f"{host}/v1/tracks/{track_id}/stream?app_name={urllib.parse.quote(AUDIUS_APP_NAME)}"


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
    return [_aud_track(t) for t in (data.get("data") or [])]


# ════════════════════════════════════════════════════════════════════════════
#  INTERNET ARCHIVE — суспільне надбання
# ════════════════════════════════════════════════════════════════════════════

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"


def archive_search(query, limit=20):
    if not ARCHIVE_ENABLED:
        return []
    q = (
        f'({query}) AND mediatype:(audio) AND '
        f'(collection:(78rpm) OR collection:(audio_music) OR collection:(opensource_audio))'
    )
    data = _get(ARCHIVE_SEARCH, {
        "q": q, "fl[]": ["identifier", "title", "creator", "year"],
        "rows": limit, "page": 1, "output": "json", "sort[]": "downloads desc",
    })
    docs = ((data or {}).get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        ident = d.get("identifier") or ""
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
            "license": "https://creativecommons.org/publicdomain/mark/1.0/",
            "license_short": "Public Domain",
            "stream": "",
            "downloadable": True,
            "download_url": "",
            "year": str(d.get("year") or ""),
        })
    return out


def archive_stream_url(identifier):
    """Шукає перший придатний аудіофайл у записі архіву."""
    meta = _get(f"https://archive.org/metadata/{identifier}")
    if not meta:
        return ""
    files = meta.get("files") or []
    for ext in (".mp3", ".ogg", ".m4a", ".flac"):
        for f in files:
            name = f.get("name") or ""
            if name.lower().endswith(ext):
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  АГРЕГАЦІЯ
# ════════════════════════════════════════════════════════════════════════════

def split_id(full_id):
    """'jam:123' -> ('jam', '123')"""
    if not full_id or ":" not in full_id:
        return "", full_id or ""
    prefix, _, rest = full_id.partition(":")
    return prefix, rest


def search_tracks(query, limit=30, quality="mp32", sources=None):
    """Змішаний пошук по всіх легальних джерелах з дедуплікацією."""
    sources = sources or ["jamendo", "audius"]
    results = []
    if "jamendo" in sources:
        results += jamendo_search_tracks(query, limit=limit, audioformat=quality)
    if "audius" in sources:
        results += audius_search_tracks(query, limit=max(10, limit // 2))
    if "archive" in sources:
        results += archive_search(query, limit=12)

    seen, out = set(), []
    for t in results:
        key = (t["title"].strip().lower(), t["artist"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:limit]


def get_track(full_id, quality="mp32"):
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        return jamendo_track(raw, audioformat=quality)
    if prefix == "aud":
        return audius_track(raw)
    if prefix == "arc":
        res = archive_search(raw, limit=1)
        return res[0] if res else None
    return None


def resolve_stream(full_id, quality="mp32"):
    """Повертає прямий URL аудіо у легального джерела."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        t = jamendo_track(raw, audioformat=quality)
        return t["stream"] if t else ""
    if prefix == "aud":
        return audius_stream_url(raw)
    if prefix == "arc":
        return archive_stream_url(raw)
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
            return audius_stream_url(raw), t
        return "", t
    if prefix == "arc":
        url = archive_stream_url(raw)
        res = archive_search(raw, limit=1)
        return url, (res[0] if res else None)
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
