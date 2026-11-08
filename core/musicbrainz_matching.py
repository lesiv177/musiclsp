"""Conservative MusicBrainz artist matching helpers.

Matching is metadata-only. Ambiguous or fuzzy matches are never auto-selected.
"""
from __future__ import annotations

import re
import unicodedata


_MBID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def normalize_artist_name(value: str) -> str:
    """Normalize a display name for conservative exact comparisons."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def rank_artist_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """Rank exact name/alias matches; never guess from partial name similarity."""
    normalized_query = normalize_artist_name(query)
    if not normalized_query:
        return []

    ranked = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        mbid = str(candidate.get("mbid") or candidate.get("id") or "").strip()
        mbid = mbid.removeprefix("mb:artist:")
        if not _MBID_RE.fullmatch(mbid):
            continue

        name = str(candidate.get("name") or "").strip()
        aliases = candidate.get("aliases") or []
        alias_names = [
            str(alias.get("name") or "").strip() if isinstance(alias, dict)
            else str(alias or "").strip()
            for alias in aliases
        ]
        normalized_name = normalize_artist_name(name)
        normalized_aliases = {normalize_artist_name(alias) for alias in alias_names}
        if normalized_query == normalized_name:
            score, match_type = 1.0, "name"
        elif normalized_query in normalized_aliases:
            score, match_type = 0.98, "alias"
        else:
            continue

        ranked.append({
            **candidate,
            "mbid": mbid,
            "match_score": score,
            "match_type": match_type,
        })

    ranked.sort(key=lambda item: (-item["match_score"], item.get("name", "").casefold(), item["mbid"]))
    if ranked:
        top_score = ranked[0]["match_score"]
        top_count = sum(item["match_score"] == top_score for item in ranked)
        for item in ranked:
            item["ambiguous"] = top_count > 1 and item["match_score"] == top_score
    return ranked


def resolve_exact_artist(query: str, candidates: list[dict]) -> dict | None:
    """Return a unique top exact match, or None when absent/ambiguous."""
    ranked = rank_artist_candidates(query, candidates)
    if not ranked or ranked[0]["ambiguous"]:
        return None
    return ranked[0]
