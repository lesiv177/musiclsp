# -*- coding: utf-8 -*-
"""
Безкоштовні джерела МЕТАДАНИХ: обкладинки, фото артистів, біографії, дискографія.

Тільки метадані (картинки/текст/списки). Відтворення лишається на джерелах з відкритими
ліцензіями (Jamendo, Audius) — цей модуль аудіо не віддає й не посилається на нього.
Джерела: iTunes Search API, Deezer public API, TheAudioDB (безкоштовний ключ "2"),
Wikipedia REST. Умови використання кожного API власник перевіряє сам (див. docs).

Кожен ланцюг пробує джерела по черзі й повертає перший непорожній результат разом із
назвою джерела. Помилка джерела = пропуск; модуль ніколи не кидає винятків назовні.
HTTP йде через providers._get (пауза після 429, безпечне логування).
Вимкнути джерело: змінна середовища META_SOURCES_OFF="deezer,itunes".
"""
from __future__ import annotations

import logging
import os
import re
import urllib.parse

logger = logging.getLogger(__name__)

ITUNES = "https://itunes.apple.com/search"
DEEZER = "https://api.deezer.com"
AUDIODB = "https://www.theaudiodb.com/api/v1/json/2"
WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ALL_SOURCES = ("deezer", "itunes", "audiodb", "wikipedia")


def _off() -> set:
    return {s.strip().lower() for s in os.getenv("META_SOURCES_OFF", "").split(",") if s.strip()}


def enabled(name: str) -> bool:
    return name in ALL_SOURCES and name not in _off()


def source_health() -> dict:
    return {s: ("on" if enabled(s) else "off") for s in ALL_SOURCES}


def _default_fetch(url, params=None):
    from core import providers
    return providers._get(url, params=params, timeout=8)


def _norm(text) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").casefold()).strip()


def _match(a, b) -> bool:
    """Назви збігаються, якщо рівні або одна повністю містить іншу (без розділових знаків)."""
    x, y = _norm(a), _norm(b)
    return bool(x and y and (x == y or x in y or y in x))


def _https(url):
    url = str(url or "")
    return url if url.startswith("https://") else ""


def _itunes_art(url: str, size: int = 600) -> str:
    return re.sub(r"/\d+x\d+(bb)?\.(jpg|png)$", f"/{size}x{size}bb.jpg", url or "")


# ── обкладинки альбому ──
def cover_deezer(artist, album, fetch):
    d = fetch(f"{DEEZER}/search/album", {"q": f'artist:"{artist}" album:"{album}"', "limit": 5})
    for it in (d or {}).get("data") or []:
        if _match((it.get("artist") or {}).get("name"), artist) and _match(it.get("title"), album):
            return _https(it.get("cover_xl") or it.get("cover_big") or it.get("cover_medium"))
    return ""


def cover_itunes(artist, album, fetch):
    d = fetch(ITUNES, {"term": f"{artist} {album}", "entity": "album", "limit": 5})
    for it in (d or {}).get("results") or []:
        if _match(it.get("artistName"), artist) and _match(it.get("collectionName"), album):
            return _https(_itunes_art(it.get("artworkUrl100")))
    return ""


def cover_audiodb(artist, album, fetch):
    d = fetch(f"{AUDIODB}/searchalbum.php", {"s": artist, "a": album})
    for it in (d or {}).get("album") or []:
        if _match(it.get("strAlbum"), album):
            return _https(it.get("strAlbumThumb"))
    return ""


COVER_CHAIN = (("deezer", cover_deezer), ("itunes", cover_itunes), ("audiodb", cover_audiodb))


# ── фото артиста ──
def image_deezer(artist, fetch):
    d = fetch(f"{DEEZER}/search/artist", {"q": artist, "limit": 5})
    for it in (d or {}).get("data") or []:
        if _match(it.get("name"), artist):
            img = _https(it.get("picture_xl") or it.get("picture_big"))
            if img and "/images/artist//" not in img:      # у Deezer є «порожня» заглушка
                return img
    return ""


def image_audiodb(artist, fetch):
    d = fetch(f"{AUDIODB}/search.php", {"s": artist})
    for it in (d or {}).get("artists") or []:
        if _match(it.get("strArtist"), artist):
            return _https(it.get("strArtistThumb") or it.get("strArtistFanart"))
    return ""


def image_wikipedia(artist, fetch):
    d = fetch(WIKI + urllib.parse.quote(str(artist).replace(" ", "_"), safe=""))
    if isinstance(d, dict) and d.get("type") == "standard":
        return _https((d.get("thumbnail") or {}).get("source"))
    return ""


IMAGE_CHAIN = (("deezer", image_deezer), ("audiodb", image_audiodb), ("wikipedia", image_wikipedia))


# ── біографія ──
def bio_wikipedia(artist, fetch):
    d = fetch(WIKI + urllib.parse.quote(str(artist).replace(" ", "_"), safe=""))
    if isinstance(d, dict) and d.get("type") == "standard" and _match(d.get("title"), artist):
        text = str(d.get("extract") or "").strip()
        return text[:900], _https((d.get("content_urls") or {}).get("desktop", {}).get("page"))
    return "", ""


def bio_audiodb(artist, fetch):
    d = fetch(f"{AUDIODB}/search.php", {"s": artist})
    for it in (d or {}).get("artists") or []:
        if _match(it.get("strArtist"), artist):
            text = str(it.get("strBiographyEN") or "").strip()
            if text:
                return text[:900], ""
    return "", ""


BIO_CHAIN = (("wikipedia", bio_wikipedia), ("audiodb", bio_audiodb))


# ── дискографія (лише список, без аудіо) ──
def discography_deezer(artist, fetch, limit=30):
    d = fetch(f"{DEEZER}/search/artist", {"q": artist, "limit": 5})
    aid = next((it.get("id") for it in (d or {}).get("data") or [] if _match(it.get("name"), artist)), None)
    if not aid:
        return []
    r = fetch(f"{DEEZER}/artist/{int(aid)}/albums", {"limit": limit})
    out, seen = [], set()
    for it in (r or {}).get("data") or []:
        title = str(it.get("title") or "").strip()
        key = _norm(title)
        if not title or key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "year": str(it.get("release_date") or "")[:4],
                    "type": str(it.get("record_type") or "album"), "cover": _https(it.get("cover_big") or it.get("cover_medium"))})
    return sorted(out, key=lambda x: x["year"], reverse=True)


def discography_itunes(artist, fetch, limit=30):
    d = fetch(ITUNES, {"term": artist, "entity": "album", "limit": limit})
    out, seen = [], set()
    for it in (d or {}).get("results") or []:
        title = str(it.get("collectionName") or "").strip()
        key = _norm(title)
        if not title or key in seen or not _match(it.get("artistName"), artist):
            continue
        seen.add(key)
        out.append({"title": title, "year": str(it.get("releaseDate") or "")[:4], "type": "album",
                    "cover": _https(_itunes_art(it.get("artworkUrl100")))})
    return sorted(out, key=lambda x: x["year"], reverse=True)


DISCO_CHAIN = (("deezer", discography_deezer), ("itunes", discography_itunes))


def _run(chain, *args, fetch):
    for name, fn in chain:
        if not enabled(name):
            continue
        try:
            value = fn(*args, fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("meta source %s failed: %s", name, type(exc).__name__)
            continue
        if value and value != ("", ""):
            return name, value
    return "", None


def find_cover(artist, album, fetch=None):
    fetch = fetch or _default_fetch
    if not _norm(artist) or not _norm(album):
        return {"url": "", "source": ""}
    name, url = _run(COVER_CHAIN, artist, album, fetch=fetch)
    return {"url": url or "", "source": name}


def find_artist(artist, fetch=None):
    """Фото + біографія + дискографія артиста. Порожні поля — чесно порожні."""
    fetch = fetch or _default_fetch
    if not _norm(artist):
        return {"name": "", "image": "", "image_source": "", "bio": "", "bio_url": "", "bio_source": "", "albums": [], "albums_source": ""}
    isrc, img = _run(IMAGE_CHAIN, artist, fetch=fetch)
    bsrc, bio = _run(BIO_CHAIN, artist, fetch=fetch)
    dsrc, albums = _run(DISCO_CHAIN, artist, fetch=fetch)
    text, url = bio or ("", "")
    return {"name": str(artist), "image": img or "", "image_source": isrc, "bio": text, "bio_url": url,
            "bio_source": bsrc, "albums": albums or [], "albums_source": dsrc}