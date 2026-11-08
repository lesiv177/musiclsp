"""Date filtering and conservative artist identity helpers for Release Radar."""
from datetime import date, timedelta
import re
import unicodedata


def _normalize_artist_name(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def release_matches_followed_artist(release, followed_artist):
    """Match release to follow by MBID when both exist; otherwise exact normalized name.

    Substring matches are deliberately rejected to avoid confusing similarly named artists.
    """
    if not isinstance(release, dict) or not isinstance(followed_artist, dict):
        return False

    followed_mbid = str(
        followed_artist.get("mbid") or followed_artist.get("artist_mbid") or ""
    ).strip().removeprefix("mb:artist:")
    release_mbid = str(
        release.get("artist_mbid") or ""
    ).strip().removeprefix("mb:artist:")

    if followed_mbid and release_mbid:
        return followed_mbid.casefold() == release_mbid.casefold()

    followed_name = _normalize_artist_name(
        followed_artist.get("name") or followed_artist.get("artist_name")
    )
    release_name = _normalize_artist_name(
        release.get("artist") or release.get("artist_name")
    )
    return bool(followed_name and release_name and followed_name == release_name)
