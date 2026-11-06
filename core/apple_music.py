"""Apple Music public catalogue search adapter.

This module exposes catalogue metadata only. It deliberately does not expose
preview audio as a playable stream; full playback must happen through MusicKit
in an authorised client session.
"""
from __future__ import annotations

import os
import re

import requests


API_ROOT = "https://api.music.apple.com/v1"
_TIMEOUT = (3, 8)
_STOREFRONT_RE = re.compile(r"^[a-z]{2}$")


class AppleMusicError(RuntimeError):
    """Raised when the Apple Music catalogue cannot be queried."""


def apple_catalog_configured() -> bool:
    return bool(os.getenv("APPLE_MUSIC_DEVELOPER_TOKEN", "").strip())


def _artwork_url(attributes: dict) -> str:
    artwork = attributes.get("artwork") or {}
    url = artwork.get("url") or ""
    if not isinstance(url, str) or not url.startswith("https://"):
        return ""
    return url.replace("{w}x{h}", "300x300").replace("{f}", "jpg")


def _normalise(item: dict, kind: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    item_id = str(item.get("id") or "").strip()
    title = str(attrs.get("name") or "").strip()
    if not item_id or not title:
        return None
    artist = str(attrs.get("artistName") or "").strip()
    album = str(attrs.get("albumName") or "").strip()
    url = attrs.get("url") or ""
    if not isinstance(url, str) or not url.startswith("https://"):
        url = ""
    duration = attrs.get("durationInMillis")
    if not isinstance(duration, int) or duration < 0:
        duration = None
    return {
        "id": f"apple_music:{kind}:{item_id}",
        "provider": "apple_music",
        "kind": kind,
        "title": title[:200],
        "artist": artist[:200],
        "album": album[:200],
        "artwork": _artwork_url(attrs),
        "external_url": url,
        "duration_ms": duration,
        "release_date": str(attrs.get("releaseDate") or "")[:10],
    }


def apple_catalog_search(term: str, storefront: str = "pl", limit: int = 20) -> list[dict]:
    """Return normalised songs, albums and artists from Apple's public catalog."""
    query = str(term or "").strip()
    market = str(storefront or "pl").strip().lower()
    if not 2 <= len(query) <= 100:
        raise ValueError("query length must be between 2 and 100")
    if not _STOREFRONT_RE.fullmatch(market):
        raise ValueError("invalid storefront")
    try:
        bounded_limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid limit") from None

    token = os.getenv("APPLE_MUSIC_DEVELOPER_TOKEN", "").strip()
    if not token:
        raise AppleMusicError("Apple Music developer token is not configured")

    try:
        response = requests.get(
            f"{API_ROOT}/catalog/{market}/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"term": query, "types": "songs,albums,artists", "limit": bounded_limit},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise AppleMusicError("Apple Music catalogue request failed") from exc

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        return []
    normalised = []
    for kind in ("songs", "albums", "artists"):
        collection = results.get(kind) or {}
        items = collection.get("data") if isinstance(collection, dict) else []
        if not isinstance(items, list):
            continue
        output_kind = kind[:-1] if kind.endswith("s") else kind
        for item in items[:bounded_limit]:
            mapped = _normalise(item, output_kind)
            if mapped:
                normalised.append(mapped)
    return normalised
