# -*- coding: utf-8 -*-
"""Канонічний каталог (Етап 2): нормалізація, обережне зіставлення, provenance.

Модуль чистий (без мережі та БД) і нічого не знає про плеєр чи Connect.
Принципи з плану:
  * неоднозначні збіги НЕ зливаються автоматично — повертається ``review``;
  * лише збіг назви (навіть ідеальний) ніколи не дає автозлиття;
  * метадані відокремлені від фактичної доступності аудіо;
  * кожне поле профілю має provenance (джерело + посилання).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlparse

# Пороги рішень.
SAME_THRESHOLD = 0.95      # автозлиття дозволене лише тут І за наявності підтвердження
REVIEW_THRESHOLD = 0.6     # нижче — різні сутності, між — потрібна ручна перевірка

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia", "ъ": "", "ы": "y", "э": "e", "ё": "io",
}
_MBID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EDITION_RE = re.compile(
    r"[\(\[]\s*(?:deluxe|expanded|remaster(?:ed)?|anniversary|special|bonus|explicit|"
    r"clean|edition|version|ep|single)[^\)\]]*[\)\]]",
    re.I,
)
_FEAT_RE = re.compile(r"[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?", re.I)


def fold_text(value) -> str:
    """Регістр, діакритика, пунктуація, кирилиця→латиниця (для порівняння, не для показу)."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(_CYR.get(ch, ch) for ch in text)
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def artist_key(name) -> str:
    key = fold_text(name)
    return key[4:] if key.startswith("the ") else key


def title_key(title, strip_edition: bool = True) -> str:
    raw = str(title or "")
    raw = _FEAT_RE.sub(" ", raw)
    if strip_edition:
        raw = _EDITION_RE.sub(" ", raw)
    return fold_text(raw)


def valid_mbid(value) -> str:
    value = str(value or "").strip().lower()
    for prefix in ("mb:artist:", "mb:release-group:", "mb:"):
        value = value.removeprefix(prefix)
    return value if _MBID_RE.fullmatch(value) else ""


def _url_identity(url) -> str:
    try:
        p = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/').lower()}"


def canonical_id(kind: str, mbid: str = "", seed: str = "") -> str:
    """Стабільний canonical ID: за MBID, якщо є, інакше за seed (provider:external_id)."""
    base = valid_mbid(mbid) or str(seed or "").strip().lower()
    if not base:
        raise ValueError("canonical_id потребує mbid або seed")
    return f"cat:{kind}:{hashlib.sha1(base.encode('utf-8')).hexdigest()[:16]}"


def _decision(score: float, confirmed: bool, reasons: list) -> dict:
    if score >= SAME_THRESHOLD and confirmed:
        decision = "same"
    elif score >= REVIEW_THRESHOLD:
        decision = "review"
    else:
        decision = "different"
    return {"score": round(max(0.0, min(1.0, score)), 3), "decision": decision, "reasons": reasons}


def match_artists(a: dict, b: dict) -> dict:
    """Порівняти два профілі артистів. Поля: name, mbid, provider, id, website, urls,
    country, aliases, tracks (список назв відомих треків)."""
    reasons: list = []
    mb_a, mb_b = valid_mbid(a.get("mbid")), valid_mbid(b.get("mbid"))
    if mb_a and mb_b:
        if mb_a == mb_b:
            return _decision(1.0, True, ["same_mbid"])
        return _decision(0.0, False, ["conflicting_mbid"])
    if a.get("provider") and a.get("provider") == b.get("provider") and a.get("id") and str(a["id"]) == str(b.get("id")):
        return _decision(1.0, True, ["same_provider_id"])

    names_a = {artist_key(a.get("name"))} | {artist_key(x) for x in a.get("aliases") or []}
    names_b = {artist_key(b.get("name"))} | {artist_key(x) for x in b.get("aliases") or []}
    names_a.discard(""), names_b.discard("")
    if not names_a or not names_b or not (names_a & names_b):
        return _decision(0.0, False, ["name_mismatch"])

    score = 0.6 if artist_key(a.get("name")) == artist_key(b.get("name")) else 0.55
    reasons.append("name")
    confirmed = False

    urls_a = {_url_identity(u) for u in [a.get("website"), *(a.get("urls") or [])]} - {""}
    urls_b = {_url_identity(u) for u in [b.get("website"), *(b.get("urls") or [])]} - {""}
    if urls_a & urls_b:
        score += 0.35
        confirmed = True
        reasons.append("shared_url")

    ta = {title_key(t) for t in a.get("tracks") or []} - {""}
    tb = {title_key(t) for t in b.get("tracks") or []} - {""}
    overlap = len(ta & tb)
    if overlap >= 2:  # два й більше спільних треків — достатнє підтвердження разом з іменем
        score += 0.35
        confirmed = True
        reasons.append(f"shared_tracks:{overlap}")
    elif overlap == 1:  # один збіг назви — лише натяк, автозлиття неможливе
        score += 0.1
        reasons.append("shared_track:1")

    ca, cb = str(a.get("country") or "").upper(), str(b.get("country") or "").upper()
    if ca and cb:
        if ca == cb:
            score += 0.05
            reasons.append("same_country")
        else:
            score -= 0.3
            reasons.append("country_conflict")
    return _decision(score, confirmed, reasons)


def match_albums(a: dict, b: dict) -> dict:
    """Порівняти два релізи. Поля: title, artist, mbid, upc, year, track_count, provider, id."""
    mb_a, mb_b = valid_mbid(a.get("mbid")), valid_mbid(b.get("mbid"))
    if mb_a and mb_b:
        return _decision(1.0, True, ["same_mbid"]) if mb_a == mb_b else _decision(0.0, False, ["conflicting_mbid"])
    upc_a, upc_b = re.sub(r"\D", "", str(a.get("upc") or "")), re.sub(r"\D", "", str(b.get("upc") or ""))
    if upc_a and upc_a == upc_b:
        return _decision(1.0, True, ["same_upc"])
    if a.get("provider") and a.get("provider") == b.get("provider") and a.get("id") and str(a["id"]) == str(b.get("id")):
        return _decision(1.0, True, ["same_provider_id"])

    if artist_key(a.get("artist")) != artist_key(b.get("artist")) or not artist_key(a.get("artist")):
        return _decision(0.0, False, ["artist_mismatch"])
    if title_key(a.get("title")) != title_key(b.get("title")) or not title_key(a.get("title")):
        return _decision(0.0, False, ["title_mismatch"])

    score, reasons, confirmed = 0.7, ["artist", "title"], False
    if title_key(a.get("title"), False) == title_key(b.get("title"), False):
        score += 0.05
        reasons.append("exact_title")
    ya, yb = _int(a.get("year")), _int(b.get("year"))
    if ya and yb:
        if abs(ya - yb) <= 1:
            score += 0.15
            confirmed = True
            reasons.append("year")
        else:
            score -= 0.3
            reasons.append("year_conflict")
    na, nb = _int(a.get("track_count")), _int(b.get("track_count"))
    if na and nb:
        if na == nb:
            score += 0.1
            reasons.append("track_count")
        elif abs(na - nb) > 2:
            score -= 0.2
            reasons.append("track_count_conflict")
    return _decision(score, confirmed, reasons)


def match_tracks(a: dict, b: dict) -> dict:
    """Порівняти два треки. Поля: title, artist, mbid, isrc, duration (сек), provider, id."""
    mb_a, mb_b = valid_mbid(a.get("mbid")), valid_mbid(b.get("mbid"))
    if mb_a and mb_b:
        return _decision(1.0, True, ["same_mbid"]) if mb_a == mb_b else _decision(0.0, False, ["conflicting_mbid"])
    isrc_a = re.sub(r"[^A-Z0-9]", "", str(a.get("isrc") or "").upper())
    isrc_b = re.sub(r"[^A-Z0-9]", "", str(b.get("isrc") or "").upper())
    if len(isrc_a) == 12 and isrc_a == isrc_b:
        return _decision(1.0, True, ["same_isrc"])
    if a.get("provider") and a.get("provider") == b.get("provider") and a.get("id") and str(a["id"]) == str(b.get("id")):
        return _decision(1.0, True, ["same_provider_id"])
    if not artist_key(a.get("artist")) or artist_key(a.get("artist")) != artist_key(b.get("artist")):
        return _decision(0.0, False, ["artist_mismatch"])
    if not title_key(a.get("title")) or title_key(a.get("title")) != title_key(b.get("title")):
        return _decision(0.0, False, ["title_mismatch"])
    score, reasons, confirmed = 0.75, ["artist", "title"], False
    da, db_ = _int_num(a.get("duration")), _int_num(b.get("duration"))
    if da and db_:
        if abs(da - db_) <= 3:
            score += 0.2
            confirmed = True
            reasons.append("duration")
        elif abs(da - db_) > 15:
            score -= 0.3
            reasons.append("duration_conflict")
    return _decision(score, confirmed, reasons)


def _int_num(v) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _int(v) -> int:
    try:
        return int(str(v)[:4]) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


# ── Доступність аудіо (A / B / C з плану) ──────────────────────────────────

def album_availability(tracks) -> dict:
    """all — усі треки грають; partial — частина; metadata_only — лише метадані.
    ``can_play`` істинний тільки якщо є хоч один реально відтворюваний трек."""
    tracks = [t for t in tracks or [] if isinstance(t, dict)]
    playable = sum(1 for t in tracks if t.get("playable") or (t.get("id") and t.get("stream_available")))
    total = len(tracks)
    if total and playable == total:
        state = "all"
    elif playable:
        state = "partial"
    else:
        state = "metadata_only"
    return {"state": state, "playable": playable, "total": total, "can_play": playable > 0}


# ── Provenance та злиття профілю ───────────────────────────────────────────

# Чим менше число, тим вищий пріоритет джерела для текстових метаданих.
SOURCE_PRIORITY = {"musicbrainz": 0, "audius": 1, "soundcloud": 2, "jamendo": 3}
PROFILE_FIELDS = ("name", "country", "bio", "image", "website", "type", "genres")


def merge_artist_profiles(records: list) -> dict:
    """Злити профілі одного артиста. Повертає {fields, provenance}.
    Порожні значення ніколи не перезаписують заповнені; кожне поле має source/url."""
    ordered = sorted(
        [r for r in records or [] if isinstance(r, dict)],
        key=lambda r: SOURCE_PRIORITY.get(str(r.get("provider") or "").lower(), 99),
    )
    fields, provenance = {}, {}
    for rec in ordered:
        for key in PROFILE_FIELDS:
            val = rec.get(key)
            if val in (None, "", [], {}) or key in fields:
                continue
            fields[key] = val
            provenance[key] = {"source": rec.get("provider") or "unknown", "url": rec.get("source_url") or ""}
    return {"fields": fields, "provenance": provenance}