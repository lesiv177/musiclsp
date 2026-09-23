# -*- coding: utf-8 -*-
"""
Легальні джерела музики.

jam — Jamendo (Creative Commons, офіційний API, стрімінг дозволено)
aud — Audius (відкритий протокол, артисти самі публікують треки)
arc — Internet Archive (суспільне надбання та CC)
ccm — ccMixter (автори самі викладають треки під CC, офіційне API)
ov  — Openverse (офіційний агрегатор CC Creative Commons/Wikimedia:
      Wikimedia Commons, частково Jamendo та інші відкриті джерела)

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


SORT_TO_JAMENDO_ORDER = {
    "relevance": None,               # без order — Jamendo сам ранжує за збігом
    "popularity": "popularity_total",
    "newest": "releasedate_desc",
}


def jamendo_search_tracks(query, limit=25, offset=0, audioformat="mp32", sort="relevance"):
    if not JAMENDO_CLIENT_ID:
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


def jamendo_search_albums(query, limit=20, offset=0, sort="relevance"):
    if not JAMENDO_CLIENT_ID:
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


def jamendo_playlists(limit=15):
    if not JAMENDO_CLIENT_ID:
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
    if not JAMENDO_CLIENT_ID:
        return []
    data = _get(f"{JAMENDO_API}/playlists/tracks/", _jam_params(
        id=playlist_id, audioformat=audioformat, imagesize=400, include="licenses",
    ))
    if not data or not data.get("results"):
        return []
    pl = data["results"][0]
    tracks = []
    for t in pl.get("tracks", []):
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
_audius_hosts_pool = []
_audius_dead = {}  # host -> час, коли визнали непридатним (щоб не пробувати одразу знову)
_audius_lock = threading.Lock()


def _audius_fetch_pool():
    data = _get(AUDIUS_BOOTSTRAP, timeout=8)
    return [h.rstrip("/") for h in ((data or {}).get("data") or []) if h]


def _audius_alive(host, timeout=4):
    try:
        r = _session.get(f"{host}/health_check", timeout=timeout)
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
    for p in (data.get("data") or [])[:limit]:
        art = p.get("artwork") or {}
        cover = art.get("480x480") or art.get("150x150") or art.get("1000x1000") or ""
        out.append({
            "id": f"aud:{p.get('id')}",
            "title": p.get("playlist_name") or "",
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
    return [_aud_track(t) for t in (data.get("data") or [])][:limit]


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
        r = _session.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 405:  # деякі ноди не вміють HEAD — пробуємо GET шматка
            r = _session.get(url, timeout=timeout, headers={"Range": "bytes=0-1"},
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
#  CCMIXTER — офіційне безключове API, треки завантажені самими авторами
#  під Creative Commons (ccmixter.org/query-api). Кожен трек несе посилання
#  на ліцензію й на оригінал сторінки автора.
# ════════════════════════════════════════════════════════════════════════════

CCMIXTER_API = "http://ccmixter.org/api/query"

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
        if not audio_url:
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
        data = _get(CCMIXTER_API, {"query": query, "type": "any", "f": "json", "limit": limit})
        if isinstance(data, list):
            for entry in data[:limit]:
                t = _ccm_from_json(entry)
                if t:
                    out.append(t)
    except Exception as e:
        logger.warning("ccMixter JSON запит не вдався: %s", e)
    if not out:
        try:
            r = _session.get(CCMIXTER_API, params={"query": query, "type": "any"},
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
#  АГРЕГАЦІЯ
# ════════════════════════════════════════════════════════════════════════════

def split_id(full_id):
    """'jam:123' -> ('jam', '123')"""
    if not full_id or ":" not in full_id:
        return "", full_id or ""
    prefix, _, rest = full_id.partition(":")
    return prefix, rest


def search_tracks(query, limit=30, quality="mp32", sources=None, sort="relevance", genre=None):
    """Змішаний пошук по всіх легальних джерелах з дедуплікацією."""
    sources = sources or ["jamendo", "audius"]
    results = []
    if "jamendo" in sources:
        results += jamendo_search_tracks(query, limit=limit, audioformat=quality, sort=sort)
    if "audius" in sources:
        results += audius_search_tracks(query, limit=max(10, limit // 2))
    if "archive" in sources:
        results += archive_search(query, limit=12)
    if "ccmixter" in sources:
        results += ccmixter_search(query, limit=10)
    if "openverse" in sources:
        results += openverse_search(query, limit=10)

    seen, out = set(), []
    for t in results:
        key = (t["title"].strip().lower(), t["artist"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:limit]


def _normalize_query(text):
    """Прибирає пунктуацію й зайві пробіли — щоб пошук був терпимим до одруків."""
    cleaned = re.sub(r"[^\w\s]", " ", text or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def search_tracks_smart(query, limit=30, quality="mp32", sources=None, sort="relevance"):
    """Той самий пошук треків, але з відкатом: якщо точний запит не дав нічого,
    пробує очищений запит, а тоді — по одному значущому слову з нього."""
    out = search_tracks(query, limit, quality, sources, sort)
    if out:
        return out
    norm = _normalize_query(query)
    if norm and norm.lower() != (query or "").strip().lower():
        out = search_tracks(norm, limit, quality, sources, sort)
        if out:
            return out
    for word in [w for w in norm.split() if len(w) > 2]:
        out = search_tracks(word, limit, quality, sources, sort)
        if out:
            return out
    return []


def jamendo_search_albums_smart(query, limit=20, sort="relevance"):
    """Той самий пошук альбомів, з відкатом на очищений запит і окремі слова."""
    out = jamendo_search_albums(query, limit=limit, sort=sort)
    if out:
        return out
    norm = _normalize_query(query)
    if norm and norm.lower() != (query or "").strip().lower():
        out = jamendo_search_albums(norm, limit=limit, sort=sort)
        if out:
            return out
    for word in [w for w in norm.split() if len(w) > 2]:
        out = jamendo_search_albums(word, limit=limit, sort=sort)
        if out:
            return out
    return []


def search_everything(query, limit=12):
    """Об'єднаний пошук: треки + альбоми + артисти обох джерел одним запитом."""
    tracks = search_tracks_smart(query, limit=limit)
    albums = jamendo_search_albums_smart(query, limit=limit)
    jam_artists = jamendo_search_artists(query, limit=limit)
    aud_artists = audius_search_users(query, limit=max(4, limit // 3))
    return {
        "tracks": tracks,
        "albums": albums,
        "artists": jam_artists + aud_artists,
    }


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
]


def _synthetic_collections(limit):
    """Запасний варіант, коли офіційні плейлисти Jamendo/Audius порожні
    (лімітований ключ, нема куратора цього тижня, тимчасовий збій тощо).
    Пакуємо вже наявний каталог у добірки за жанром — це той самий легальний
    контент, просто інше групування, тому нічого юридично не змінюється."""
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


def discover_playlists(limit=10):
    """Кураторські плейлисти з обох джерел — для головного екрана."""
    half = max(4, limit // 2)
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
        out = _synthetic_collections(limit)
    return out[:limit]


def curated_playlist_tracks(full_id, quality="mp32"):
    """Треки кураторського плейлиста за повним id ('jam:123' / 'aud:456' /
    'tag:electronic' для запасних жанрових добірок)."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        return jamendo_playlist_tracks(raw, audioformat=quality)
    if prefix == "aud":
        return audius_playlist_tracks(raw)
    if prefix == "tag":
        return jamendo_tag_tracks(raw, 40, 0, quality)
    return []


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
        return _ccm_cache_get(raw)
    if prefix == "ov":
        return _ov_cache_get(raw)
    return None


def resolve_stream(full_id, quality="mp32"):
    """Повертає прямий URL аудіо у легального джерела."""
    prefix, raw = split_id(full_id)
    if prefix == "jam":
        t = jamendo_track(raw, audioformat=quality)
        return t["stream"] if t else ""
    if prefix == "aud":
        return audius_stream_url_verified(raw)
    if prefix == "arc":
        return archive_stream_url(raw)
    if prefix == "ccm":
        t = _ccm_cache_get(raw)
        return t["stream"] if t else ""
    if prefix == "ov":
        t = _ov_cache_get(raw)
        return t["stream"] if t else ""
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
    if prefix == "ccm":
        t = _ccm_cache_get(raw)
        return (t["download_url"], t) if t else ("", None)
    if prefix == "ov":
        t = _ov_cache_get(raw)
        return (t["download_url"], t) if t else ("", None)
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
