"""Capability registry for optional, rights-aware music catalogue integrations.

A provider is never advertised as playable merely because its catalogue API works.
Playback modes describe the authorised playback surface; they are not stream URLs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SourceCapability:
    key: str
    label: str
    category: str
    playback_mode: str
    env_required: tuple[str, ...] = ()
    approval_required: bool = False
    implemented: bool = False
    note: str = ""

    def configured(self) -> bool:
        return all(bool(os.getenv(name, "").strip()) for name in self.env_required)

    def available(self) -> bool:
        if not self.implemented or not self.configured():
            return False
        if self.approval_required and not _enabled(f"{self.key.upper()}_APPROVED"):
            return False
        return True


# Optional commercial services remain disabled until credentials AND the
# provider's contractual/API approval are in place. No undocumented stream
# extraction or proxying is attempted.
OPTIONAL_SOURCES = (
    SourceCapability(
        key="apple_music", label="Apple Music", category="catalogue",
        playback_mode="official_client",
        env_required=("APPLE_MUSIC_DEVELOPER_TOKEN",),
        note="Catalogue API plus MusicKit playback in an authorised client; no server-side audio stream.",
    ),
    SourceCapability(
        key="tidal", label="TIDAL", category="catalogue",
        playback_mode="provider_authorised",
        env_required=("TIDAL_CLIENT_ID", "TIDAL_CLIENT_SECRET"),
        approval_required=True,
        note="Requires approved API access and playback rights for the intended product.",
    ),
    SourceCapability(
        key="seven_digital", label="7digital", category="licensed_catalogue",
        playback_mode="licensed_delivery",
        env_required=("SEVENDIGITAL_API_KEY", "SEVENDIGITAL_PARTNER_ID"),
        approval_required=True,
        note="Commercial partner integration; availability and delivery depend on contract.",
    ),
    SourceCapability(
        key="amazon_music", label="Amazon Music", category="catalogue",
        playback_mode="provider_authorised",
        env_required=("AMAZON_MUSIC_CLIENT_ID", "AMAZON_MUSIC_CLIENT_SECRET"),
        approval_required=True,
        note="Requires an approved developer programme and explicit playback permissions.",
    ),
    SourceCapability(
        key="epidemic_sound", label="Epidemic Sound", category="licensed_catalogue",
        playback_mode="licensed_delivery",
        env_required=("EPIDEMIC_SOUND_API_TOKEN",),
        approval_required=True,
        note="Requires a commercial agreement covering the MusicLSP use case.",
    ),
)


def source_status() -> dict:
    """Return safe, non-secret readiness information for diagnostics."""
    result = {}
    for source in OPTIONAL_SOURCES:
        result[source.key] = {
            "label": source.label,
            "category": source.category,
            "playback_mode": source.playback_mode,
            "configured": source.configured(),
            "available": source.available(),
            "implemented": source.implemented,
            "approval_required": source.approval_required,
            "note": source.note,
        }
    return result


def available_sources() -> tuple[str, ...]:
    return tuple(source.key for source in OPTIONAL_SOURCES if source.available())
