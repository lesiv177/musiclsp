# -*- coding: utf-8 -*-
"""
Міграції. Запускається як Pre-deploy Command або вручну.

Жоден інший сервіс схему не чіпає — інакше чотири процеси почнуть
створювати таблиці одночасно на старті й будуть ловити гонки.
"""

import sys
import logging

from core import db
from core.config import DATABASE_URL, APP_NAME, APP_VERSION

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("migrate")


def main():
    log.info("%s %s — міграція схеми", APP_NAME, APP_VERSION)
    log.info("Ціль: %s", "PostgreSQL" if DATABASE_URL else "SQLite (локальний файл)")
    try:
        db.init_db()
    except Exception as e:
        log.error("Міграція не вдалася: %s", e)
        sys.exit(1)

    with db.conn_cursor() as (conn, cur):
        found = []
        for t in ("users", "library", "playlists", "playlist_tracks",
                  "history", "promo_codes", "audit_log", "auth_events"):
            try:
                cur.execute(f"SELECT COUNT(*) AS c FROM {t}")
                found.append(f"{t}={int((db.one(cur) or {}).get('c') or 0)}")
            except Exception as e:
                log.error("Таблиця %s недоступна: %s", t, e)
                sys.exit(1)
    log.info("Готово. %s", ", ".join(found))


if __name__ == "__main__":
    main()
