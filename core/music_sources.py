"""Registry of the music sources actually used by MusicLSP.

This module contains metadata only. Provider-specific HTTP and normalisation
code stays in core.providers, which consumes DEFAULT_TRACK_SEARCH_ORDER.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SourceCapability:
    key: str
    label: str
    id_prefix: str
    playback_mode: str
    catalogue_types: tuple[str, ...]
    credential_env: tuple[str, ...] = ()
    approval_env: str = ""
    enabled_env: str = ""
    implemented: bool = True

    def status(self) -> dict:
        configured = all(bool(os.getenv(name, "").strip()) for name in self.credential_env)
        enabled = not self.enabled_env or os.getenv(self.enabled_env, "1").strip() == "1"
        approved = not self.approval_env or _truthy(self.approval_env)
        return {
            "label": self.label,
            "id_prefix": self.id_prefix,
            "playback_mode": self.playback_mode,
            "catalogue_types": list(self.catalogue_types),
            "configured": configured,
            "approved": approved,
            "available": bool(self.implemented and configured and enabled and approved),
            "implemented": self.implemented,
        }


# This list contains only integrations with code in core.providers.
# "official_embed" means catalogue discovery only; it is never a server stream.
MUSIC_SOURCES = (
    SourceCapability(
        key="audius", label="Audius", id_prefix="aud",
        playback_mode="provider_stream",
        catalogue_types=("tracks", "albums", "artists", "playlists"),
    ),
    SourceCapability(
        key="archive", label="Internet Archive", id_prefix="arc",
        playback_mode="licensed_file",
        catalogue_types=("tracks",), enabled_env="ARCHIVE_ENABLED",
    ),
    SourceCapability(
        key="ccmixter", label="ccMixter", id_prefix="ccm",
        playback_mode="licensed_file", catalogue_types=("tracks",),
    ),
    SourceCapability(
        key="openverse", label="Openverse", id_prefix="ov",
        playback_mode="licensed_file", catalogue_types=("tracks",),
    ),
    SourceCapability(
        key="wikimedia", label="Wikimedia Commons", id_prefix="wm",
        playback_mode="licensed_file", catalogue_types=("tracks",),
    ),
    SourceCapability(
        key="jamendo", label="Jamendo", id_prefix="jam",
        playback_mode="provider_stream",
        catalogue_types=("tracks", "albums", "artists", "playlists"),
        credential_env=("JAMENDO_CLIENT_ID",),
        approval_env="JAMENDO_COMMERCIAL_APPROVED",
    ),
    SourceCapability(
        key="soundcloud", label="SoundCloud", id_prefix="sc",
        playback_mode="provider_stream", catalogue_types=("tracks", "artists"),
        credential_env=("SOUNDCLOUD_CLIENT_ID", "SOUNDCLOUD_CLIENT_SECRET"),
        approval_env="SOUNDCLOUD_AGGREGATION_APPROVED",
    ),
    SourceCapability(
        key="youtube", label="YouTube", id_prefix="yt",
        playback_mode="official_embed", catalogue_types=("tracks",),
        credential_env=("YOUTUBE_API_KEY",),
    ),
)

SOURCE_BY_KEY = {source.key: source for source in MUSIC_SOURCES}
DEFAULT_TRACK_SEARCH_ORDER = (
    "soundcloud", "jamendo", "audius", "archive",
    "ccmixter", "openverse", "wikimedia", "youtube",
)


def source_status() -> dict:
    """Readiness metadata only; never exposes credentials or probes providers."""
    return {source.key: source.status() for source in MUSIC_SOURCES}


def available_sources() -> tuple[str, ...]:
    status = source_status()
    return tuple(key for key in DEFAULT_TRACK_SEARCH_ORDER if status[key]["available"])
