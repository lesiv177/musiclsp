"""MusicBrainz metadata-only catalogue client.

This module never downloads or streams audio. It provides artist, release-group,
and recording metadata for discovery workflows. Keep disabled in production
until MusicLSP's commercial-use terms are confirmed.
"""
from __future__ import annotations

import os
import re
from datetime import date
import threading
import time

import requests

BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = os.getenv(
    "MUSICBRAINZ_USER_AGENT",
    "MusicLSP/6.1 (https://github.com/lesiv177/musiclsp)",
).strip()
TIMEOUT = 12
MIN_REQUEST_INTERVAL = 1.0
CACHE_TTL = 900
_CACHE_MAX = 256

_lock = threading.Lock()
_cache_lock = threading.Lock()
_last_request = 0.0
_cache: dict[tuple, tuple[float, object]] = {}


def _throttle() -> None:
    """Respect MusicBrainz's one-request-per-second limit across threads."""
    global _last_request
    with _lock:
        now = time.monotonic()
        delay = MIN_REQUEST_INTERVAL - (now - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()


def _get_json(entity: str, params: dict) -> dict:
    key = (entity, tuple(sorted((k, str(v)) for k, v in params.items())))
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            _cache.pop(key, None)

    _throttle()
    response = requests.get(
        f"{BASE_URL}/{entity}/",
        params={**params, "fmt": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected MusicBrainz response")
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            # Simple bounded cache: discard expired entries, then oldest insertion.
            expired = [k for k, (expiry, _) in _cache.items() if expiry <= now]
            for old_key in expired:
                _cache.pop(old_key, None)
            while len(_cache) >= _CACHE_MAX:
                _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + CACHE_TTL, payload)
    return payload


def _text(value) -> str:
    return str(value or "").strip()


def _artist(item: dict) -> dict | None:
    artist_id = _text(item.get("id"))
    name = _text(item.get("name"))
    if not artist_id or not name:
        return None
    return {
        "id": f"mb:artist:{artist_id}",
        "mbid": artist_id,
        "name": name,
        "type": _text(item.get("type")),
        "country": _text(item.get("country")),
        "disambiguation": _text(item.get("disambiguation")),
        "aliases": [
            _text(alias.get("name")) for alias in item.get("aliases", [])
            if isinstance(alias, dict) and _text(alias.get("name"))
        ],
        "begin_date": _text((item.get("life-span") or {}).get("begin")),
        "end_date": _text((item.get("life-span") or {}).get("end")),
        "source": "musicbrainz",
        "metadata_only": True,
        "source_url": f"https://musicbrainz.org/artist/{artist_id}",
    }


def artist_by_id(artist_mbid: str) -> dict | None:
    """Fetch one artist's metadata by MusicBrainz ID."""
    artist_mbid = _text(artist_mbid).removeprefix("mb:artist:")
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", artist_mbid):
        return None
    data = _get_json(f"artist/{artist_mbid}", {"inc": "aliases"})
    return _artist(data)


def search_artists(query: str, limit: int = 10) -> list[dict]:
    """Search artists by name; returns metadata only."""
    query = _text(query)
    if not query:
        return []
    limit = max(1, min(int(limit), 25))
    data = _get_json("artist", {"query": query, "limit": limit})
    return [artist for row in data.get("artists", []) if (artist := _artist(row))]


def artist_release_groups(artist_mbid: str, limit: int = 25) -> list[dict]:
    """List album/EP/single release groups for an artist."""
    artist_mbid = _text(artist_mbid).removeprefix("mb:artist:")
    if not artist_mbid:
        return []
    limit = max(1, min(int(limit), 100))
    data = _get_json("release-group", {
        "artist": artist_mbid,
        "type": "album|ep|single",
        "limit": limit,
        "fmt": "json",
    })
    results = []
    for row in data.get("release-groups", []):
        release_id = _text(row.get("id"))
        title = _text(row.get("title"))
        if not release_id or not title:
            continue
        first_release = _text(row.get("first-release-date"))
        results.append({
            "id": f"mb:release-group:{release_id}",
            "mbid": release_id,
            "title": title,
            "type": _text(row.get("primary-type")),
            "secondary_types": row.get("secondary-types") or [],
            "first_release_date": first_release,
            "year": first_release[:4] if first_release else "",
            "artist_mbid": artist_mbid,
            "source": "musicbrainz",
            "metadata_only": True,
            "source_url": f"https://musicbrainz.org/release-group/{release_id}",
        })
    return results


def recent_artist_releases(artist_mbid: str, since_date: str, limit: int = 25) -> list[dict]:
    """Find an artist's release groups first released on/after since_date.

    This is metadata discovery only. It does not imply audio availability or
    grant streaming rights. Dates must be complete ISO calendar dates.
    """
    artist_mbid = _text(artist_mbid).removeprefix("mb:artist:")
    since_date = _text(since_date)
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", artist_mbid):
        return []
    try:
        since_date = date.fromisoformat(since_date).isoformat()
    except ValueError:
        return []
    limit = max(1, min(int(limit), 25))
    query = f"arid:{artist_mbid} AND date:[{since_date} TO *]"
    data = _get_json("release-group", {"query": query, "limit": limit})
    results = []
    for row in data.get("release-groups", []):
        release_id = _text(row.get("id"))
        title = _text(row.get("title"))
        first_release = _text(row.get("first-release-date"))
        if not release_id or not title or len(first_release) < 10:
            continue
        try:
            released = date.fromisoformat(first_release[:10])
        except ValueError:
            continue
        if released < date.fromisoformat(since_date):
            continue
        results.append({
            "id": f"mb:release-group:{release_id}",
            "mbid": release_id,
            "title": title,
            "type": _text(row.get("primary-type")),
            "first_release_date": first_release,
            "year": first_release[:4],
            "artist_mbid": artist_mbid,
            "source": "musicbrainz",
            "source_url": f"https://musicbrainz.org/release-group/{release_id}",
        })
    return results


def search_release_groups(query: str, limit: int = 20) -> list[dict]:
    """Search album/EP/single metadata; results do not imply playable audio."""
    query = _text(query)
    if not query:
        return []
    limit = max(1, min(int(limit), 25))
    data = _get_json("release-group", {"query": query, "limit": limit})
    results = []
    for row in data.get("release-groups", []):
        release_id = _text(row.get("id"))
        title = _text(row.get("title"))
        if not release_id or not title:
            continue
        credits = row.get("artist-credit") or []
        artist_names = [
            _text((credit.get("artist") or {}).get("name"))
            for credit in credits if isinstance(credit, dict)
        ]
        first_release = _text(row.get("first-release-date"))
        results.append({
            "id": f"mb:release-group:{release_id}",
            "mbid": release_id,
            "title": title,
            "artist": ", ".join(name for name in artist_names if name),
            "release_date": first_release,
            "year": first_release[:4] if first_release else "",
            "type": _text(row.get("primary-type")),
            "cover": "",
            "source": "musicbrainz",
            "source_label": "MusicBrainz · metadata",
            "metadata_only": True,
            "source_url": f"https://musicbrainz.org/release-group/{release_id}",
        })
    return results


def release_group_cover_url(release_group_mbid: str) -> str:
    """Return official Cover Art Archive front artwork URL when available."""
    release_group_mbid = _text(release_group_mbid).removeprefix("mb:release-group:")
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", release_group_mbid):
        return ""
    try:
        response = requests.get(
            f"https://coverartarchive.org/release-group/{release_group_mbid}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=6,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        payload = response.json()
        images = payload.get("images") if isinstance(payload, dict) else []
        if not isinstance(images, list):
            return ""
        front = next((item for item in images if isinstance(item, dict) and item.get("front")), None)
        selected = front or next((item for item in images if isinstance(item, dict)), None)
        if not selected:
            return ""
        thumbnails = selected.get("thumbnails") or {}
        url = _text(thumbnails.get("500") or thumbnails.get("large") or thumbnails.get("250") or selected.get("image"))
        return url if url.startswith("https://") else ""
    except Exception as exc:
        # Artwork is optional: a CAA outage must not break catalogue metadata.
        logger.warning("Cover Art Archive lookup failed: %s", type(exc).__name__)
        return ""


def release_group_by_id(release_group_mbid: str) -> dict | None:
    """Fetch release-group metadata and one representative release tracklist."""
    release_group_mbid = _text(release_group_mbid).removeprefix("mb:release-group:")
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", release_group_mbid):
        return None
    data = _get_json(f"release-group/{release_group_mbid}", {"inc": "artist-credits+releases"})
    title = _text(data.get("title"))
    if not title:
        return None
    credits = data.get("artist-credit") or []
    artists = [
        _text((credit.get("artist") or {}).get("name"))
        for credit in credits if isinstance(credit, dict)
    ]
    releases = data.get("releases") or []
    representative = next((r for r in releases if isinstance(r, dict) and r.get("id")), None)
    tracks = []
    release_date = _text(data.get("first-release-date"))
    if representative:
        release_date = release_date or _text(representative.get("date"))
        release_data = _get_json(
            f"release/{representative['id']}",
            {"inc": "recordings"},
        )
        for medium_index, medium in enumerate(release_data.get("media") or [], 1):
            for track_index, track in enumerate(medium.get("tracks") or [], 1):
                recording = track.get("recording") or {}
                recording_id = _text(recording.get("id"))
                track_title = _text(track.get("title") or recording.get("title"))
                if not track_title:
                    continue
                tracks.append({
                    "id": f"mb:recording:{recording_id}" if recording_id else f"mb:track:{medium_index}:{track_index}",
                    "title": track_title,
                    "artist": ", ".join(name for name in artists if name),
                    "duration": int((recording.get("length") or track.get("length") or 0) / 1000),
                    "track_number": _text(track.get("number")) or str(track_index),
                    "disc_number": medium_index,
                    "source": "musicbrainz",
                    "source_label": "Metadata only",
                    "metadata_only": True,
                    "source_url": f"https://musicbrainz.org/recording/{recording_id}" if recording_id else "",
                })
    return {
        "id": f"mb:release-group:{release_group_mbid}",
        "mbid": release_group_mbid,
        "title": title,
        "artist": ", ".join(name for name in artists if name),
        "release_date": release_date,
        "type": _text(data.get("primary-type")),
        "cover": release_group_cover_url(release_group_mbid),
        "tracks": tracks,
        "track_count": len(tracks),
        "source": "musicbrainz",
        "source_label": "MusicBrainz · metadata only",
        "metadata_only": True,
        "source_url": f"https://musicbrainz.org/release-group/{release_group_mbid}",
    }


def search_recordings(query: str, limit: int = 20) -> list[dict]:
    """Search recording metadata (not playable audio)."""
    query = _text(query)
    if not query:
        return []
    limit = max(1, min(int(limit), 25))
    data = _get_json("recording", {"query": query, "limit": limit})
    results = []
    for row in data.get("recordings", []):
        recording_id = _text(row.get("id"))
        title = _text(row.get("title"))
        if not recording_id or not title:
            continue
        artists = [
            _text((credit.get("artist") or {}).get("name"))
            for credit in row.get("artist-credit", [])
            if isinstance(credit, dict)
        ]
        results.append({
            "id": f"mb:recording:{recording_id}",
            "mbid": recording_id,
            "title": title,
            "artist": ", ".join(name for name in artists if name),
            "length_ms": row.get("length"),
            "source": "musicbrainz",
            "metadata_only": True,
            "source_url": f"https://musicbrainz.org/recording/{recording_id}",
        })
    return results


def resolve_artist(query: str) -> dict | None:
    """Resolve a query only when MusicBrainz returns one exact name/alias match.

    Ambiguous results are intentionally left unresolved for explicit user choice.
    """
    query = _text(query)
    if not query:
        return None
    from core.musicbrainz_matching import resolve_exact_artist

    candidates = search_artists(query, limit=25)
    return resolve_exact_artist(query, candidates)
