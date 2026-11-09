# -*- coding: utf-8 -*-
"""Адитивне збагачення відповідей /api/artist та /api/album канонічним каталогом.

Додає лише нові поля (``catalog``, ``availability``); наявні поля не змінюються.
Будь-яка помилка каталогу не ламає сторінку — повертається початкова відповідь.
"""
from __future__ import annotations

import logging

from core import catalog

logger = logging.getLogger(__name__)

MAX_LINKED_TRACKS = 60
PREFIX_PROVIDER = {"jam": "jamendo", "aud": "audius", "scu": "soundcloud", "mb": "musicbrainz"}


def enrich_artist(prefix: str, raw: str, payload: dict) -> dict:
    provider = PREFIX_PROVIDER.get(prefix)
    artist = payload.get("artist") if isinstance(payload, dict) else None
    if not provider or not isinstance(artist, dict) or not artist.get("name"):
        return payload
    try:
        from core import catalog_store
        raw = str(raw).removeprefix("artist:")
        record = {
            "provider": provider, "id": raw, "name": artist.get("name"),
            "mbid": artist.get("mbid") or (raw if provider == "musicbrainz" else ""),
            "website": artist.get("website") or "", "country": artist.get("country") or "",
            "aliases": [a.get("name") if isinstance(a, dict) else a for a in artist.get("aliases") or []],
            "tracks": [t.get("title") for t in payload.get("tracks") or [] if isinstance(t, dict)],
            "source_url": artist.get("source_url") or "",
        }
        link = catalog_store.link_artist(record)
        related = [m for m in catalog_store.mappings_for(link["canonical_id"])
                   if not (m["provider"] == provider and m["external_id"] == raw)]
        return {**payload, "catalog": {
            "canonical_id": link["canonical_id"], "status": link["status"],
            "confidence": link["confidence"], "related": related,
            "needs_review": link["status"] == "review",
        }}
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog enrich artist failed: %s", exc)
        return payload


def _album_availability(payload: dict, tracks: list) -> dict:
    if payload.get("metadata_only"):  # metadata-only ніколи не отримує Play
        return {"state": "metadata_only", "playable": 0, "total": len(tracks), "can_play": False}
    # Для провайдерських альбомів трек має id лише якщо його можна запустити.
    marked = [{**t, "playable": bool(t.get("id"))} for t in tracks if isinstance(t, dict)]
    return catalog.album_availability(marked)


def _link_album_catalog(prefix, raw, payload, tracks):
    provider = PREFIX_PROVIDER.get(prefix)
    album = payload.get("album") if isinstance(payload.get("album"), dict) else {}
    title, artist = album.get("title") or "", album.get("artist") or ""
    if isinstance(artist, dict):
        artist = artist.get("name") or ""
    if not provider or not raw or not title or not artist:
        return None, tracks
    from core import catalog_store
    raw = str(raw).removeprefix("release-group:")
    link = catalog_store.link_album({
        "provider": provider, "id": raw, "title": title, "artist": artist,
        "mbid": album.get("mbid") or (raw if provider == "musicbrainz" else ""),
        "year": album.get("year") or str(album.get("date") or "")[:4],
        "track_count": len(tracks) or album.get("track_count") or 0,
        "url": album.get("source_url") or "",
    })
    related = [m for m in catalog_store.mappings_for(link["canonical_id"])
               if not (m["provider"] == provider and m["external_id"] == raw)]
    info = {"canonical_id": link["canonical_id"], "status": link["status"],
            "confidence": link["confidence"], "related": related,
            "needs_review": link["status"] == "review"}
    return info, tracks


def _link_track_ids(tracks: list, fallback_artist: str) -> list:
    """Додає адитивне поле catalog_id до playable-треків; помилка одного треку не ламає решту."""
    from core import catalog_store
    out = []
    for t in tracks[:MAX_LINKED_TRACKS]:
        try:
            prov, _, ext = str(t.get("id") or "").partition(":")
            provider = PREFIX_PROVIDER.get(prov)
            artist = t.get("artist") or fallback_artist
            if provider and ext and t.get("title") and artist:
                link = catalog_store.link_track({
                    "provider": provider, "id": ext, "title": t["title"], "artist": artist,
                    "duration": t.get("duration") or 0, "isrc": t.get("isrc") or ""})
                t = {**t, "catalog_id": link["canonical_id"]}
        except Exception as exc:  # noqa: BLE001
            logger.debug("track link skipped: %s", exc)
        out.append(t)
    return out + list(tracks[MAX_LINKED_TRACKS:])


def enrich_album(payload: dict, prefix: str = "", raw: str = "") -> dict:
    if not isinstance(payload, dict) or "tracks" not in payload:
        return payload
    tracks = payload.get("tracks") or []
    result = {**payload, "availability": _album_availability(payload, tracks)}
    if not prefix:
        return result
    try:
        info, _ = _link_album_catalog(prefix, raw, payload, tracks)
        if info:
            result["catalog"] = info
        if tracks and not payload.get("metadata_only"):
            album = payload.get("album") if isinstance(payload.get("album"), dict) else {}
            result["tracks"] = _link_track_ids([t for t in tracks if isinstance(t, dict)],
                                               str(album.get("artist") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog enrich album failed: %s", exc)
        return {**payload, "availability": result["availability"]}
    return result


def _split(item_id) -> tuple:
    prefix, _, raw = str(item_id or "").partition(":")
    provider = PREFIX_PROVIDER.get(prefix)
    for strip in ("artist:", "release-group:"):
        raw = raw.removeprefix(strip)
    return provider, raw


def annotate_list(kind: str, items):
    """Додати ``catalog_id`` до результатів пошуку, якщо об'єкт уже відомий каталогу.

    Лише читання: пошук не створює записів і не наповнює чергу review здогадками.
    """
    if not isinstance(items, list) or not items:
        return items
    try:
        from core import catalog_store
        keys = [_split(i.get("id")) for i in items if isinstance(i, dict)]
        known = catalog_store.resolve_many(kind, [k for k in keys if k[0] and k[1]])
        if not known:
            return items
        out = []
        for item in items:
            cid = known.get(_split(item.get("id"))) if isinstance(item, dict) else None
            out.append({**item, "catalog_id": cid} if cid else item)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog annotate %s failed: %s", kind, exc)
        return items


def annotate_search(kind: str, data):
    """kind: albums | artists | all (dict із albums/artists). Інші типи не змінюються."""
    if kind == "all" and isinstance(data, dict):
        return {**data, "albums": annotate_list("album", data.get("albums")),
                "artists": annotate_list("artist", data.get("artists"))}
    if kind in ("albums", "artists"):
        return annotate_list(kind[:-1], data)
    return data