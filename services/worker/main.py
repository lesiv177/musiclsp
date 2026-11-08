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
import hashlib

from core import db
from core import providers as P
from core.release_radar import is_recent_release
from core.release_radar_matching import release_matches_followed_artist
from core.config import (
    WORKER_INTERVAL, BOT_TOKEN, APP_NAME, APP_VERSION, FREE_LIMITS,
    RENEWAL_REMINDER_HOURS, RELEASE_RADAR_INTERVAL,
)

log = logging.getLogger("worker")


async def notify(uid, text):
    """Тихе повідомлення; повертає True лише після підтвердження Telegram."""
    if not BOT_TOKEN:
        return False
    try:
        from telegram import Bot
        await Bot(BOT_TOKEN).send_message(uid, text)
        return True
    except Exception as e:
        log.debug("Не доставлено %s: %s", uid, e)
        return False


def release_notification_key(album):
    """Стабільний ключ релізу з урахуванням джерела для дедуплікації."""
    if not isinstance(album, dict):
        return ""
    source_id = str(album.get("id") or "").strip()
    source = str(album.get("source") or album.get("provider") or "").strip().lower()
    if source_id:
        identity = f"{source}:{source_id}"
    else:
        identity = "|".join((
            str(album.get("title") or "").strip().casefold(),
            str(album.get("artist") or album.get("artist_name") or "").strip().casefold(),
            str(album.get("release_date") or "")[:10],
            source,
        ))
    if not identity.strip("|"):
        return ""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


_RELEASE_RADAR_TEXT = {
    "uk": ("🎧 Нові релізи від артистів, на яких ви підписані:", "Відкрийте Release Radar у MusicLSP."),
    "pl": ("🎧 Nowe wydania obserwowanych artystów:", "Otwórz Release Radar w MusicLSP."),
    "en": ("🎧 New releases from artists you follow:", "Open Release Radar in MusicLSP."),
    "ru": ("🎧 Новые релизы артистов, на которых вы подписаны:", "Откройте Release Radar в MusicLSP."),
}


async def notify_release_radar():
    """Надсилає годинний дайджест нових релізів і не повторює вже доставлені."""
    if not BOT_TOKEN:
        return 0
    follows = await asyncio.to_thread(db.artist_following_all)
    if not follows:
        return 0

    grouped = {}
    for followed in follows:
        uid = int(followed.get("uid") or 0)
        if uid and len(grouped.get(uid, {}).get("follows", [])) < 30:
            group = grouped.setdefault(uid, {"lang": followed.get("lang", "uk"), "follows": []})
            group["follows"].append(followed)

    semaphore = asyncio.Semaphore(5)
    artist_names = {}
    artist_identities = {}
    for group in grouped.values():
        for followed in group["follows"]:
            artist = followed.get("artist") or {}
            name = str(followed.get("artist_name") or artist.get("name") or "").strip()
            if name:
                key = name.casefold()
                artist_names.setdefault(key, name)
                identity = dict(artist)
                identity.setdefault("name", name)
                artist_identities.setdefault(key, []).append(identity)

    async def lookup(name, identities):
        async with semaphore:
            try:
                albums = await asyncio.to_thread(P.search_albums_smart, name, 8, "newest")
            except Exception as exc:
                log.warning("Release Radar notification lookup failed for %s: %s",
                            name[:80], type(exc).__name__)
                return []
        matched = []
        for album in albums or []:
            if not isinstance(album, dict) or not is_recent_release(album, days=7):
                continue
            if not any(
                release_matches_followed_artist(album, identity)
                for identity in identities
            ):
                continue
            item = dict(album)
            item["followed_artist"] = name
            item["_notification_key"] = release_notification_key(item)
            if item["_notification_key"]:
                matched.append(item)
        return matched

    artist_keys = list(artist_names)
    artist_batches = await asyncio.gather(*(
        lookup(artist_names[key], artist_identities[key]) for key in artist_keys
    ))
    releases_by_artist = dict(zip(artist_keys, artist_batches))

    sent_count = 0
    for uid, group in grouped.items():
        candidates, seen = [], set()
        for followed in group["follows"]:
            artist = followed.get("artist") or {}
            name = str(followed.get("artist_name") or artist.get("name") or "").strip()
            for album in releases_by_artist.get(name.casefold(), []):
                if not release_matches_followed_artist(album, {
                    **dict(artist),
                    "name": artist.get("name") or name,
                }):
                    continue
                key = album["_notification_key"]
                if key not in seen:
                    seen.add(key)
                    candidates.append(album)
        if not candidates:
            continue

        keys = [item["_notification_key"] for item in candidates]
        fresh = set(await asyncio.to_thread(db.release_radar_unnotified, uid, keys))
        selected = [item for item in candidates if item["_notification_key"] in fresh][:5]
        if not selected:
            continue

        lang = str(group.get("lang") or "uk").split("-")[0].lower()
        heading, footer = _RELEASE_RADAR_TEXT.get(lang, _RELEASE_RADAR_TEXT["uk"])
        lines = [heading]
        for album in selected:
            title = str(album.get("title") or "Без назви").strip()[:100]
            artist = str(album.get("artist") or album.get("artist_name")
                         or album.get("followed_artist") or "").strip()[:80]
            released = str(album.get("release_date") or "")[:10]
            lines.append(f"• {title} — {artist}" + (f" ({released})" if released else ""))
        lines.append("")
        lines.append(footer)
        if await notify(uid, "\n".join(lines)):
            await asyncio.to_thread(
                db.release_radar_mark_notified,
                uid, [item["_notification_key"] for item in selected],
            )
            sent_count += 1

    if sent_count:
        log.info("Release Radar: надіслано дайджестів %s", sent_count)
    return sent_count


def normalize_expiry_timestamp(value):
    """Повертає timestamp як naive UTC для однакового порівняння в обох БД."""
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00").strip())
    if not isinstance(value, datetime.datetime):
        raise TypeError("premium_until must be a datetime")
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


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
            try:
                until = normalize_expiry_timestamp(until)
            except (TypeError, ValueError, OverflowError):
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


_last_release_radar_scan = 0


async def cycle():
    global _last_release_radar_scan
    started = time.time()
    try:
        await expire_premium()
        await remind_renewals()
        trim_history()
        clean_promos()
        if time.time() - _last_release_radar_scan >= max(300, RELEASE_RADAR_INTERVAL):
            _last_release_radar_scan = time.time()
            try:
                await notify_release_radar()
            except Exception as exc:
                log.error("Release Radar notifications failed: %s", exc, exc_info=True)
        warm_audius()
    except Exception as e:
        log.error("Цикл завершився помилкою: %s", e, exc_info=True)
    log.info("Цикл за %.1fс, наступний через %sс", time.time() - started, WORKER_INTERVAL)


async def run(initialize=True):
    """Запускає фоновий цикл; бот може передати вже ініціалізоване спільне DB-з'єднання."""
    log.info("%s worker %s запущено, інтервал %sс", APP_NAME, APP_VERSION, WORKER_INTERVAL)
    if initialize:
        db.init_engine()
    while True:
        await cycle()
        await asyncio.sleep(WORKER_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("Зупинено")