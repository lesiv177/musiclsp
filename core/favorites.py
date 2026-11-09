# -*- coding: utf-8 -*-
"""Universal Favorites: обране з будь-якого провайдера зіставляється з canonical-каталогом.

* При збереженні (явна дія користувача) — зв'язуємо provider-ID з canonical (link_*).
* При читанні — лише читання (resolve_many): додаємо ``catalog_id`` і згортаємо дублікати
  одного й того ж об'єкта з різних джерел. Записи в БД НЕ видаляються: якщо джерело
  стане недоступним, збережений елемент лишається в бібліотеці.
"""
from __future__ import annotations

import logging

from core.catalog_api import _split

logger = logging.getLogger(__name__)


def link_on_save(kind: str, item_id: str, data: dict) -> str:
    """Повертає canonical_id або "" (помилка каталогу ніколи не блокує збереження)."""
    try:
        from core import catalog_store
        provider, raw = _split(item_id)
        if not provider or not raw or not isinstance(data, dict):
            return ""
        title = data.get("title") or data.get("name") or ""
        artist = data.get("artist") or ""
        if isinstance(artist, dict):
            artist = artist.get("name") or ""
        if kind == "artist":
            return catalog_store.link_artist({"provider": provider, "id": raw, "name": title,
                                              "mbid": data.get("mbid") or ""})["canonical_id"] if title else ""
        if not title or not artist:
            return ""
        rec = {"provider": provider, "id": raw, "title": title, "artist": artist,
               "mbid": data.get("mbid") or "", "year": data.get("year") or "",
               "track_count": data.get("track_count") or 0, "duration": data.get("duration") or 0,
               "isrc": data.get("isrc") or ""}
        fn = catalog_store.link_track if kind == "track" else catalog_store.link_album
        return fn(rec)["canonical_id"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("favorite link skipped (%s): %s", kind, exc)
        return ""


def collapse(kind: str, items: list, id_of=lambda i: i.get("id")) -> list:
    """Додати catalog_id і згорнути дублікати (перший = найновіший). Порядок зберігається.

    Згортання лише за підтвердженим (linked) canonical_id; неоднозначні (review) не зливаються.
    """
    items = [i for i in items or [] if isinstance(i, dict)]
    try:
        from core import catalog_store
        keys = [_split(id_of(i)) for i in items]
        known = catalog_store.resolve_many(kind, [k for k in keys if k[0] and k[1]])
    except Exception as exc:  # noqa: BLE001
        logger.warning("favorites collapse skipped (%s): %s", kind, exc)
        return items
    out, first = [], {}
    for item, key in zip(items, keys):
        cid = known.get(key)
        if not cid:
            out.append(item)
        elif cid in first:
            first[cid].setdefault("also_saved", []).append(id_of(item))
        else:
            entry = {**item, "catalog_id": cid}
            first[cid] = entry
            out.append(entry)
    return out