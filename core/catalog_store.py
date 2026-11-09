# -*- coding: utf-8 -*-
"""Сховище канонічного каталогу. Лише нові таблиці — наявні не змінюються."""
from __future__ import annotations

import json
import logging

from core import catalog
from core.db import conn_cursor, now_str, one, q, rows

logger = logging.getLogger(__name__)

KINDS = ("artist", "album", "track")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS catalog_entities (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL,
        mbid TEXT DEFAULT '', data TEXT DEFAULT '{}', provenance TEXT DEFAULT '{}',
        created_at TEXT, updated_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_catalog_entities_key ON catalog_entities(kind, name_key)",
    """CREATE TABLE IF NOT EXISTS catalog_mappings (
        provider TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
        canonical_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'linked',
        confidence REAL DEFAULT 1, reasons TEXT DEFAULT '[]', created_at TEXT,
        PRIMARY KEY (provider, kind, external_id))""",
    "CREATE INDEX IF NOT EXISTS idx_catalog_mappings_canon ON catalog_mappings(canonical_id)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_mappings_status ON catalog_mappings(status)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_entities_mbid ON catalog_entities(mbid)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_entities_kind ON catalog_entities(kind)",
]


def ensure_schema():
    for stmt in SCHEMA:
        try:
            with conn_cursor() as (_conn, cur):
                cur.execute(stmt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog schema: %s", exc)


def resolve(provider: str, kind: str, external_id: str):
    """Canonical ID для provider-ID або None. Записи зі статусом review не вважаються зв'язаними."""
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT canonical_id FROM catalog_mappings WHERE provider=%s AND kind=%s "
                      "AND external_id=%s AND status='linked'"), (provider, kind, str(external_id)))
        row = one(cur)
    return row["canonical_id"] if row else None


def resolve_many(kind: str, pairs) -> dict:
    """Лише читання: {(provider, external_id): canonical_id} для підтверджених (linked) зв'язків."""
    pairs = list({(str(p), str(e)) for p, e in pairs if p and e})[:200]
    if not pairs:
        return {}
    out = {}
    for provider in {p for p, _ in pairs}:
        ids = [e for p, e in pairs if p == provider]
        marks = ",".join(["%s"] * len(ids))
        with conn_cursor() as (_c, cur):
            cur.execute(q("SELECT external_id, canonical_id FROM catalog_mappings WHERE provider=%s AND kind=%s "
                          "AND status='linked' AND external_id IN (" + marks + ")"), (provider, kind, *ids))
            for row in rows(cur):
                out[(provider, row["external_id"])] = row["canonical_id"]
    return out


def _upsert_mapping(provider, kind, external_id, canonical_id, status, confidence, reasons):
    with conn_cursor() as (_c, cur):
        cur.execute(q("DELETE FROM catalog_mappings WHERE provider=%s AND kind=%s AND external_id=%s"),
                    (provider, kind, str(external_id)))
        cur.execute(q("INSERT INTO catalog_mappings (provider, kind, external_id, canonical_id, status, "
                      "confidence, reasons, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"),
                    (provider, kind, str(external_id), canonical_id, status, confidence,
                     json.dumps(reasons), now_str()))


def _create_entity(kind, record, cid, provenance, name_key=None):
    with conn_cursor() as (_c, cur):
        cur.execute(q("INSERT INTO catalog_entities (id, kind, name, name_key, mbid, data, provenance, "
                      "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                    (cid, kind, record.get("name") or record.get("title") or "",
                     name_key if name_key is not None else catalog.artist_key(record.get("name") or record.get("title")),
                     catalog.valid_mbid(record.get("mbid")), json.dumps(record, ensure_ascii=False),
                     json.dumps(provenance, ensure_ascii=False), now_str(), now_str()))


def candidates(kind: str, name: str):
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT id, name, mbid, data FROM catalog_entities WHERE kind=%s AND name_key=%s"),
                    (kind, catalog.artist_key(name)))
        return rows(cur)


def link_artist(record: dict) -> dict:
    """Знайти/створити canonical-артиста для provider-запису.

    Результат: {canonical_id, status: linked|created|review, confidence, reasons}.
    При неоднозначності запис потрапляє в чергу review і НЕ зливається.
    """
    provider, ext = str(record.get("provider") or ""), str(record.get("id") or "")
    if not provider or not ext:
        raise ValueError("record потребує provider та id")
    existing = resolve(provider, "artist", ext)
    if existing:
        return {"canonical_id": existing, "status": "linked", "confidence": 1.0, "reasons": ["existing_mapping"]}

    best, review = None, []
    for cand in candidates("artist", record.get("name")):
        other = {**json.loads(cand.get("data") or "{}"), "mbid": cand.get("mbid")}
        verdict = catalog.match_artists(record, other)
        if verdict["decision"] == "same":
            if best is None or verdict["score"] > best[1]["score"]:
                best = (cand["id"], verdict)
        elif verdict["decision"] == "review":
            review.append((cand["id"], verdict))

    if best and not [r for r in review if r[1]["score"] >= best[1]["score"]]:
        cid, verdict = best
        _upsert_mapping(provider, "artist", ext, cid, "linked", verdict["score"], verdict["reasons"])
        return {"canonical_id": cid, "status": "linked", "confidence": verdict["score"],
                "reasons": verdict["reasons"]}

    cid = catalog.canonical_id("artist", record.get("mbid") or "", f"{provider}:{ext}")
    _create_entity("artist", record, cid, catalog.merge_artist_profiles([record])["provenance"])
    if review:
        top = max(review, key=lambda r: r[1]["score"])
        # Запис лишається окремим, але позначається для ручної перевірки з кандидатом.
        _upsert_mapping(provider, "artist", ext, cid, "review", top[1]["score"],
                        top[1]["reasons"] + [f"candidate:{top[0]}"])
        return {"canonical_id": cid, "status": "review", "confidence": top[1]["score"],
                "reasons": top[1]["reasons"], "candidate": top[0]}
    _upsert_mapping(provider, "artist", ext, cid, "linked", 1.0, ["new_entity"])
    return {"canonical_id": cid, "status": "created", "confidence": 1.0, "reasons": ["new_entity"]}


def _candidates_by_key(kind: str, key: str):
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT id, name, mbid, data FROM catalog_entities WHERE kind=%s AND name_key=%s"),
                    (kind, key))
        return rows(cur)


def _link_release_like(kind: str, record: dict, matcher) -> dict:
    """Спільна логіка для album/track: ті самі гарантії, що й для артистів —
    автозлиття лише при score>=0.95 з підтвердженням, неоднозначне йде в review."""
    provider, ext = str(record.get("provider") or ""), str(record.get("id") or "")
    if not provider or not ext:
        raise ValueError("record потребує provider та id")
    if not catalog.title_key(record.get("title")) or not catalog.artist_key(record.get("artist")):
        raise ValueError("record потребує title та artist")
    existing = resolve(provider, kind, ext)
    if existing:
        return {"canonical_id": existing, "status": "linked", "confidence": 1.0, "reasons": ["existing_mapping"]}

    best, review = None, []
    for cand in _candidates_by_key(kind, catalog.title_key(record.get("title"))):
        other = {**json.loads(cand.get("data") or "{}"), "mbid": cand.get("mbid") or ""}
        verdict = matcher(record, other)
        if verdict["decision"] == "same":
            if best is None or verdict["score"] > best[1]["score"]:
                best = (cand["id"], verdict)
        elif verdict["decision"] == "review":
            review.append((cand["id"], verdict))

    if best and not [r for r in review if r[1]["score"] >= best[1]["score"]]:
        cid, verdict = best
        _upsert_mapping(provider, kind, ext, cid, "linked", verdict["score"], verdict["reasons"])
        return {"canonical_id": cid, "status": "linked", "confidence": verdict["score"], "reasons": verdict["reasons"]}

    cid = catalog.canonical_id(kind, record.get("mbid") or "", f"{provider}:{ext}")
    prov = {"source": provider, "id": ext, "url": record.get("url") or ""}
    _create_entity(kind, record, cid, prov, name_key=catalog.title_key(record.get("title")))
    if review:
        top = max(review, key=lambda r: r[1]["score"])
        _upsert_mapping(provider, kind, ext, cid, "review", top[1]["score"],
                        top[1]["reasons"] + [f"candidate:{top[0]}"])
        return {"canonical_id": cid, "status": "review", "confidence": top[1]["score"],
                "reasons": top[1]["reasons"], "candidate": top[0]}
    _upsert_mapping(provider, kind, ext, cid, "linked", 1.0, ["new_entity"])
    return {"canonical_id": cid, "status": "created", "confidence": 1.0, "reasons": ["new_entity"]}


def link_album(record: dict) -> dict:
    return _link_release_like("album", record, catalog.match_albums)


def link_track(record: dict) -> dict:
    return _link_release_like("track", record, catalog.match_tracks)


def review_queue(limit: int = 50):
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT * FROM catalog_mappings WHERE status='review' ORDER BY created_at DESC LIMIT %s"),
                    (int(limit),))
        return rows(cur)


def confirm_merge(provider: str, kind: str, external_id: str, canonical_id: str) -> bool:
    """Ручне підтвердження з черги review: прив'язати provider-ID до вказаного canonical."""
    _upsert_mapping(provider, kind, external_id, canonical_id, "linked", 1.0, ["manual_confirm"])
    return True


def mappings_for(canonical_id: str):
    """Усі підтверджені provider-ID одного canonical-об'єкта."""
    with conn_cursor() as (_c, cur):
        cur.execute(q("SELECT provider, kind, external_id FROM catalog_mappings "
                      "WHERE canonical_id=%s AND status='linked' ORDER BY provider, external_id"),
                    (canonical_id,))
        return rows(cur)