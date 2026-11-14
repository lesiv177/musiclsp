# -*- coding: utf-8 -*-
"""
СУПЕРДЖЕРЕЛО: єдина точка входу до всіх підключених джерел треків і радіо.

Що робить понад звичайний пошук:
  • збирає треки з усіх доступних джерел (Jamendo, Audius, Internet Archive, ccMixter, Openverse,
    Wikimedia Commons, SoundCloud/YouTube лише коли схвалені) і радіостанції Radio Browser;
  • кожному результату ставить «світлофор безпеки» (safety.level = green | amber) і готовий рядок атрибуції;
  • ВІДКИДАЄ небезпечне для релізу: файли без явної дозволеної ліцензії, не-https потоки
    (порожній stream дозволено: його резолвить /api/stream);
  • дає звіт про джерела (що ввімкнено, що чекає ключа чи схвалення) без розкриття ключів.

Нічого не парсить із YouTube/Spotify/Apple. Аудіо не проксіюється й не зберігається.
"""
from __future__ import annotations

import logging

from core import music_sources, radio_browser

logger = logging.getLogger(__name__)

LICENSED_FILE = "licensed_file"
EMBED = "official_embed"


def _is_https(url) -> bool:
    return isinstance(url, str) and url.startswith("https://")


def _commercial_ok(track) -> bool:
    from core import providers
    return providers._cc_commercially_usable(str(track.get("license") or ""))


def credit_line(track) -> str:
    """Рядок атрибуції для показу в інтерфейсі (корисний для CC BY)."""
    title, artist = str(track.get("title") or "").strip(), str(track.get("artist") or "").strip()
    lic = str(track.get("license_short") or "").strip()
    src = str(track.get("source_label") or track.get("source") or "").strip()
    parts = [p for p in (f"«{title}»" if title else "", artist, lic, f"джерело: {src}" if src else "") if p]
    return " · ".join(parts)


def assess(track):
    """Повертає (level, reason). level: green | amber | red (red = не показувати)."""
    if not isinstance(track, dict) or not str(track.get("title") or "").strip():
        return "red", "порожній запис"
    source = str(track.get("source") or "")
    cap = music_sources.SOURCE_BY_KEY.get(source)
    if source == "radio":
        return ("green", "ефір станції, лише посилання") if _is_https(track.get("stream")) else ("red", "потік не https")
    if cap is None:
        return "red", "невідоме джерело"
    mode = cap.playback_mode
    if mode == EMBED:
        return ("amber", "лише офіційний вбудований плеєр") if track.get("embed_url") or track.get("id") else ("red", "немає ідентифікатора")
    stream = track.get("stream")
    if stream and not _is_https(stream):
        return "red", "потік не https"          # порожній stream нормальний: його резолвить /api/stream
    if mode == LICENSED_FILE:
        return ("green", "відкрита ліцензія, дозволена для комерції") if _commercial_ok(track) else ("red", "ліцензія не підтверджена")
    if source == "jamendo":
        return ("green", "CC-ліцензія, дозволена для комерції") if _commercial_ok(track) else ("red", "ліцензія не підтверджена")
    return "amber", "ліцензію задає автор / умови платформи"


def annotate(tracks):
    out = []
    for t in tracks or []:
        level, reason = assess(t)
        if level == "red":
            continue
        out.append({**t, "safety": {"level": level, "reason": reason}, "credit": credit_line(t)})
    return out


def search(query, limit=30, kind="all", quality="mp32", search_fn=None, radio_fn=None):
    """kind: all | tracks | radio. Помилки окремих джерел не ламають відповідь."""
    query = str(query or "").strip()[:120]
    limit = max(1, min(int(limit or 30), 60))
    kind = kind if kind in ("all", "tracks", "radio") else "all"
    result = {"query": query, "tracks": [], "radio": [], "dropped": 0}
    if not query:
        return result
    if kind in ("all", "tracks"):
        if search_fn is None:
            from core import providers
            search_fn = lambda q, n: providers.search_tracks(q, limit=n, quality=quality)  # noqa: E731
        try:
            raw = [t for t in (search_fn(query, limit) or []) if isinstance(t, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("supersource tracks failed: %s", type(exc).__name__)
            raw = []
        kept = annotate(raw)
        result["tracks"], result["dropped"] = kept, len(raw) - len(kept)
    if kind in ("all", "radio"):
        radio_fn = radio_fn or (lambda q, n: radio_browser.search(q, limit=n))
        try:
            result["radio"] = annotate(radio_fn(query, min(limit, 12)) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("supersource radio failed: %s", type(exc).__name__)
    return result


def sources_report():
    """Статус усіх джерел для адмін-панелі/діагностики. Ключів не містить."""
    status = music_sources.source_status()
    rows = []
    for key, st in status.items():
        cap = music_sources.SOURCE_BY_KEY[key]
        if st["available"]:
            note = "працює"
        elif not st["configured"]:
            note = "потрібен ключ у змінних середовища"
        elif not st["approved"]:
            note = "потрібне письмове схвалення сервісу"
        else:
            note = "вимкнено"
        rows.append({"key": key, "label": st["label"], "mode": cap.playback_mode, "available": st["available"], "note": note})
    rows.append({"key": "radio", "label": "Radio Browser", "mode": "station_link",
                 "available": radio_browser.enabled(), "note": "працює" if radio_browser.enabled() else "вимкнено"})
    return {"sources": rows, "available_count": sum(1 for r in rows if r["available"])}