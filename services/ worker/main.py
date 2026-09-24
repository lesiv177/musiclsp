# -*- coding: utf-8 -*-
"""
Фоновий воркер.

Робить те, що не має жити всередині обробника запиту:
  • знімає прострочений Premium і повідомляє користувача;
  • чистить історію понад ліміт плану;
  • прибирає вичерпані промокоди;
  • тримає discovery-ноду Audius теплою, щоб перший пошук не гальмував.

Цикл щохвилини-десять (WORKER_INTERVAL). Жодних вебхуків і портів —
Railway для такого сервісу домен видавати не треба.
"""

import time
import asyncio
import logging
import datetime

from core import db
from core import providers as P
from core.config import (
    WORKER_INTERVAL, BOT_TOKEN, APP_NAME, APP_VERSION, FREE_LIMITS,
    RENEWAL_REMINDER_HOURS,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("worker")


async def notify(uid, text):
    """Тихе повідомлення. Якщо бот не налаштований — просто пропускаємо."""
    if not BOT_TOKEN:
        return
    try:
        from telegram import Bot
        await Bot(BOT_TOKEN).send_message(uid, text)
    except Exception as e:
        log.debug("Не доставлено %s: %s", uid, e)


async def expire_premium():
    """Знімає Premium, у якого вийшов термін."""
    now = datetime.datetime.utcnow()
    expired = []
    with db.conn_cursor() as (conn, cur):
        cur.execute(db.q("SELECT uid, premium_until FROM users WHERE premium=%s"),
                    (True if db.USE_PG else 1,))
        for r in db.rows(cur):
            until = r.get("premium_until")
            if not until:
                continue
            if isinstance(until, str):
                try:
                    until = datetime.datetime.fromisoformat(until.replace("Z", "").strip())
                except ValueError:
                    continue
            if until < now:
                expired.append(int(r["uid"]))

    for uid in expired:
        db.set_premium(uid, False)
        db.auth_event(uid, "premium_expired")
        await notify(uid, "Термін Premium добіг кінця. Бібліотека й плейлисти "
                          "лишаються на місці — обмежились тільки ліміти. "
                          "Продовжити: /premium")
    if expired:
        log.info("Premium знято: %s", len(expired))
    return len(expired)


async def remind_renewals():
    """Нагадування про закінчення Premium заздалегідь — оплата разова
    (не автопродовження), тому без нагадування юзер просто забуває
    продовжити й тихо йде назавжди."""
    users = await asyncio.to_thread(db.users_expiring_soon, RENEWAL_REMINDER_HOURS)
    for u in users:
        uid = int(u["uid"])
        await notify(uid, "⏳ Ваш Premium закінчується менш ніж за добу. "
                          "Продовжити зараз, щоб не втратити Hi-Fi, офлайн-пак "
                          "і безлімітні завантаження: /premium")
        await asyncio.to_thread(db.mark_renewal_notified, uid)
    if users:
        log.info("Нагадування про продовження надіслано: %s", len(users))
    return len(users)


def trim_history():
    """Чистить історію глибше за ліміт плану користувача."""
    cutoff = (datetime.datetime.utcnow()
              - datetime.timedelta(days=FREE_LIMITS["history_days"])
              ).isoformat(sep=" ", timespec="seconds")
    removed = 0
    with db.conn_cursor() as (conn, cur):
        cur.execute(db.q("SELECT uid FROM users WHERE premium=%s"),
                    (False if db.USE_PG else 0,))
        free_users = [int(r["uid"]) for r in db.rows(cur)]
        for uid in free_users:
            cur.execute(db.q("DELETE FROM history WHERE uid=%s AND played_at<%s"),
                        (uid, cutoff))
            removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if removed:
        log.info("Історія почищена: %s записів", removed)
    return removed


def clean_promos():
    with db.conn_cursor() as (conn, cur):
        cur.execute(db.q("DELETE FROM promo_codes WHERE uses_left<=%s"), (0,))
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if n:
        log.info("Вичерпані промокоди прибрані: %s", n)
    return n


def warm_audius():
    host = P.audius_host()
    log.debug("Audius нода: %s", host or "недоступна")
    return bool(host)


async def cycle():
    started = time.time()
    try:
        await expire_premium()
        await remind_renewals()
        trim_history()
        clean_promos()
        warm_audius()
    except Exception as e:
        log.error("Цикл завершився помилкою: %s", e, exc_info=True)
    log.info("Цикл за %.1fс, наступний через %sс", time.time() - started, WORKER_INTERVAL)


async def run():
    log.info("%s worker %s запущено, інтервал %sс", APP_NAME, APP_VERSION, WORKER_INTERVAL)
    db.init_engine()
    while True:
        await cycle()
        await asyncio.sleep(WORKER_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("Зупинено")
