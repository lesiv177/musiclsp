# -*- coding: utf-8 -*-
"""
Шар бази даних. PostgreSQL на Railway, SQLite локально.
Одна й та сама схема, різні плейсхолдери (%s / ?).
"""

import json
import time
import random
import string
import logging
import sqlite3
import datetime
from contextlib import contextmanager

from core.config import (
    DATABASE_URL, SQLITE_PATH, FREE_LIMITS, PREMIUM_LIMITS, REFERRAL_REWARD_DAYS,
)

logger = logging.getLogger(__name__)

USE_PG = False
_pg_pool = None
_pg_last_error = ""

try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool as pg_pool
    PSYCOPG_OK = True
except ImportError:
    PSYCOPG_OK = False


def init_engine():
    global USE_PG, _pg_pool, _pg_last_error
    if DATABASE_URL and PSYCOPG_OK:
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        # "require" ламає підключення на внутрішній мережі Railway (postgres.railway.internal),
        # яка часто взагалі не підтримує SSL. "prefer" пробує SSL, а якщо сервер
        # його не підтримує — підключається без нього, замість падати в SQLite.
        for mode in ("prefer", "disable"):
            try:
                _pg_pool = pg_pool.SimpleConnectionPool(1, 8, url, sslmode=mode)
                conn = _pg_pool.getconn()
                _pg_pool.putconn(conn)
                USE_PG = True
                _pg_last_error = ""
                logger.info("База даних: PostgreSQL (sslmode=%s)", mode)
                return
            except Exception as e:
                _pg_last_error = str(e)
                logger.warning("PostgreSQL недоступний з sslmode=%s (%s)", mode, e)
    elif DATABASE_URL and not PSYCOPG_OK:
        _pg_last_error = "psycopg2 не встановлено (перевір requirements.txt)"
    elif not DATABASE_URL:
        _pg_last_error = "змінна DATABASE_URL не задана"
    USE_PG = False
    logger.info("База даних: SQLite (%s)", SQLITE_PATH)


@contextmanager
def conn_cursor():
    if USE_PG:
        conn = _pg_pool.getconn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            yield conn, cur
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            _pg_pool.putconn(conn)
    else:
        conn = sqlite3.connect(SQLITE_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            yield conn, cur
            conn.commit()
            cur.close()
        finally:
            conn.close()


def q(sql):
    """Підганяє плейсхолдери під активний драйвер."""
    return sql if USE_PG else sql.replace("%s", "?")


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


SCHEMA_PG = [
    """CREATE TABLE IF NOT EXISTS users (
        uid BIGINT PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        lang TEXT DEFAULT 'uk',
        premium BOOLEAN DEFAULT FALSE,
        premium_until TIMESTAMP,
        theme TEXT DEFAULT 'midnight',
        accent TEXT DEFAULT '',
        quality TEXT DEFAULT 'mp32',
        eq_settings TEXT DEFAULT '',
        crossfade INTEGER DEFAULT 0,
        searches_today INTEGER DEFAULT 0,
        searches_date TEXT DEFAULT '',
        downloads_today INTEGER DEFAULT 0,
        downloads_date TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS library (
        id SERIAL PRIMARY KEY, uid BIGINT, tid TEXT, title TEXT, artist TEXT,
        cover TEXT, duration INTEGER DEFAULT 0, source TEXT, license TEXT,
        source_url TEXT, added_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS playlists (
        id SERIAL PRIMARY KEY, uid BIGINT, name TEXT, description TEXT DEFAULT '',
        cover TEXT DEFAULT '', share_code TEXT, created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS playlist_tracks (
        id SERIAL PRIMARY KEY, pid INTEGER, tid TEXT, title TEXT, artist TEXT,
        cover TEXT, duration INTEGER DEFAULT 0, source TEXT, license TEXT,
        source_url TEXT, position INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS history (
        id SERIAL PRIMARY KEY, uid BIGINT, tid TEXT, title TEXT, artist TEXT,
        cover TEXT, source TEXT, seconds INTEGER DEFAULT 0,
        played_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY, days INTEGER DEFAULT 30, uses_left INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS login_tokens (
        token TEXT PRIMARY KEY, uid BIGINT, expires_at TIMESTAMP, used INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY, actor BIGINT, action TEXT, target TEXT,
        details TEXT DEFAULT '', created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS auth_events (
        id SERIAL PRIMARY KEY, uid BIGINT, kind TEXT, note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS eq_presets (
        id SERIAL PRIMARY KEY, uid BIGINT, name TEXT, settings TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS blindtest_scores (
        uid BIGINT PRIMARY KEY, name TEXT DEFAULT '', best_streak INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)",
    "CREATE INDEX IF NOT EXISTS idx_lib_uid ON library(uid)",
    "CREATE INDEX IF NOT EXISTS idx_pl_uid ON playlists(uid)",
    "CREATE INDEX IF NOT EXISTS idx_plt_pid ON playlist_tracks(pid)",
    "CREATE INDEX IF NOT EXISTS idx_hist_uid ON history(uid)",
    "CREATE INDEX IF NOT EXISTS idx_eq_uid ON eq_presets(uid)",
]

SCHEMA_SQLITE = [
    s.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
     .replace("TIMESTAMP DEFAULT NOW()", "TEXT")
     .replace("BOOLEAN DEFAULT FALSE", "INTEGER DEFAULT 0")
     .replace("BIGINT", "INTEGER")
     .replace("TIMESTAMP", "TEXT")
    for s in SCHEMA_PG
]

# Пізніші доповнення до вже існуючих таблиць (реферали, серії днів).
# Кожен рядок виконується окремо й толерантно до помилки "колонка вже є".
MIGRATIONS_PG = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_rewarded BOOLEAN DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_count INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_last TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ambient_style TEXT DEFAULT 'blur'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS lang_set BOOLEAN DEFAULT FALSE",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_refcode ON users(ref_code)",
]
MIGRATIONS_SQLITE = [
    "ALTER TABLE users ADD COLUMN ref_code TEXT",
    "ALTER TABLE users ADD COLUMN referred_by INTEGER",
    "ALTER TABLE users ADD COLUMN ref_rewarded INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN streak_count INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN streak_last TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN ambient_style TEXT DEFAULT 'blur'",
    "ALTER TABLE users ADD COLUMN lang_set INTEGER DEFAULT 0",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_refcode ON users(ref_code)",
]


def init_db():
    init_engine()
    schema = SCHEMA_PG if USE_PG else SCHEMA_SQLITE
    migrations = MIGRATIONS_PG if USE_PG else MIGRATIONS_SQLITE
    with conn_cursor() as (conn, cur):
        for stmt in schema:
            try:
                cur.execute(stmt)
            except Exception as e:
                logger.warning("Схема: %s", e)
        for stmt in migrations:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # колонка/індекс уже існує — це нормально при повторному деплої
    logger.info("Схема бази готова")


def today():
    return datetime.date.today().isoformat()


def now_str():
    return datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")


# ─── Користувачі ─────────────────────────────────────────────────────────────

def get_user(uid, username="", first_name=""):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM users WHERE uid=%s"), (uid,))
        u = one(cur)
        if u:
            if username and u.get("username") != username:
                cur.execute(q("UPDATE users SET username=%s WHERE uid=%s"), (username, uid))
                u["username"] = username
            return u
        cur.execute(
            q("INSERT INTO users (uid, username, first_name, created_at) VALUES (%s,%s,%s,%s)"),
            (uid, username, first_name, now_str()),
        )
        cur.execute(q("SELECT * FROM users WHERE uid=%s"), (uid,))
        return one(cur)


def update_user(uid, **fields):
    if not fields:
        return
    allowed = {
        "lang", "theme", "accent", "quality", "eq_settings",
        "crossfade", "premium", "premium_until", "username", "first_name",
        "ambient_style", "lang_set",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=%s" for k in fields)
    with conn_cursor() as (conn, cur):
        cur.execute(q(f"UPDATE users SET {sets} WHERE uid=%s"), (*fields.values(), uid))


def is_premium(uid):
    u = get_user(uid)
    if not u:
        return False
    if not u.get("premium"):
        return False
    until = u.get("premium_until")
    if not until:
        return True
    if isinstance(until, str):
        try:
            until = datetime.datetime.fromisoformat(until.replace("Z", "").strip())
        except ValueError:
            return True
    if isinstance(until, datetime.datetime) and until < datetime.datetime.utcnow():
        update_user(uid, premium=False)
        return False
    return True


def set_premium(uid, on=True, days=30):
    get_user(uid)
    until = (datetime.datetime.utcnow() + datetime.timedelta(days=days)) if on else None
    with conn_cursor() as (conn, cur):
        cur.execute(
            q("UPDATE users SET premium=%s, premium_until=%s WHERE uid=%s"),
            (bool(on) if USE_PG else int(bool(on)),
             until.isoformat(sep=" ", timespec="seconds") if until else None,
             uid),
        )


def limits_for(uid):
    return dict(PREMIUM_LIMITS if is_premium(uid) else FREE_LIMITS)


def extend_premium(uid, days):
    """Додає дні до поточного Premium (або починає з нуля, якщо його не було)."""
    u = get_user(uid)
    now = datetime.datetime.utcnow()
    base = now
    cur_until = u.get("premium_until")
    if cur_until:
        if isinstance(cur_until, str):
            try:
                cur_until = datetime.datetime.fromisoformat(cur_until.replace("Z", "").strip())
            except ValueError:
                cur_until = now
        if isinstance(cur_until, datetime.datetime) and cur_until > now:
            base = cur_until
    until = base + datetime.timedelta(days=days)
    with conn_cursor() as (conn, cur):
        cur.execute(
            q("UPDATE users SET premium=%s, premium_until=%s WHERE uid=%s"),
            (True if USE_PG else 1, until.isoformat(sep=" ", timespec="seconds"), uid),
        )


# ─── Реферальна система ──────────────────────────────────────────────────────

def _gen_ref_code(uid):
    base = format(int(uid) % 1_000_000, "x")
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    return (base + salt)[:9]


def ensure_ref_code(uid):
    u = get_user(uid)
    if u.get("ref_code"):
        return u["ref_code"]
    for _ in range(6):
        code = _gen_ref_code(uid)
        with conn_cursor() as (conn, cur):
            cur.execute(q("SELECT uid FROM users WHERE ref_code=%s"), (code,))
            if one(cur):
                continue
            cur.execute(q("UPDATE users SET ref_code=%s WHERE uid=%s"), (code, uid))
            return code
    return None


def get_user_by_ref_code(code):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM users WHERE ref_code=%s"), (code,))
        return one(cur)


def apply_referral(new_uid, code):
    """Прив'язує нового користувача до того, хто його запросив, і видає бонус обом.
    Спрацьовує рівно один раз на користувача."""
    code = (code or "").strip().lower()
    if not code:
        return False, ""
    new_u = get_user(new_uid)
    if new_u.get("referred_by") or int(new_u.get("ref_rewarded") or 0):
        return False, "already"
    ref = get_user_by_ref_code(code)
    if not ref or int(ref["uid"]) == int(new_uid):
        return False, "invalid"
    with conn_cursor() as (conn, cur):
        cur.execute(
            q("UPDATE users SET referred_by=%s, ref_rewarded=%s WHERE uid=%s"),
            (int(ref["uid"]), True if USE_PG else 1, new_uid),
        )
    extend_premium(new_uid, REFERRAL_REWARD_DAYS)
    extend_premium(int(ref["uid"]), REFERRAL_REWARD_DAYS)
    return True, (ref.get("first_name") or ref.get("username") or "")


def referral_stats(uid):
    code = ensure_ref_code(uid)
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT COUNT(*) AS c FROM users WHERE referred_by=%s"), (uid,))
        invited = int((one(cur) or {}).get("c") or 0)
    return {"code": code, "invited": invited, "reward_days": REFERRAL_REWARD_DAYS}


# ─── Серія днів (streak) ─────────────────────────────────────────────────────

def track_streak(uid):
    """Оновлює серію активних днів. Повертає (streak, це_новий_день)."""
    u = get_user(uid)
    last = u.get("streak_last") or ""
    cur_streak = int(u.get("streak_count") or 0)
    t = today()
    if last == t:
        return cur_streak or 1, False
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    new_streak = cur_streak + 1 if last == yesterday else 1
    with conn_cursor() as (conn, cur):
        cur.execute(q("UPDATE users SET streak_count=%s, streak_last=%s WHERE uid=%s"),
                    (new_streak, t, uid))
    return new_streak, True


# ─── Пресети еквалайзера ─────────────────────────────────────────────────────

def eq_preset_save(uid, name, settings, limit=10):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT COUNT(*) AS c FROM eq_presets WHERE uid=%s"), (uid,))
        c = int((one(cur) or {}).get("c") or 0)
        if c >= limit:
            return None, f"Максимум {limit} пресетів"
        cur.execute(
            q("INSERT INTO eq_presets (uid,name,settings,created_at) VALUES (%s,%s,%s,%s)"),
            (uid, (name or "Пресет")[:40], json.dumps(settings or []), now_str()),
        )
        cur.execute(q("SELECT * FROM eq_presets WHERE uid=%s ORDER BY id DESC LIMIT 1"), (uid,))
        p = one(cur)
    if p:
        try:
            p["settings"] = json.loads(p.get("settings") or "[]")
        except Exception:
            p["settings"] = []
    return p, ""


def eq_preset_list(uid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM eq_presets WHERE uid=%s ORDER BY id DESC"), (uid,))
        out = rows(cur)
    for p in out:
        try:
            p["settings"] = json.loads(p.get("settings") or "[]")
        except Exception:
            p["settings"] = []
    return out


def eq_preset_delete(uid, pid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("DELETE FROM eq_presets WHERE id=%s AND uid=%s"), (pid, uid))
    return True


# ─── "Вгадай трек" — рейтинг ──────────────────────────────────────────────────

def blindtest_submit(uid, name, streak):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT best_streak FROM blindtest_scores WHERE uid=%s"), (uid,))
        r = one(cur)
        if r and int(r["best_streak"] or 0) >= streak:
            return int(r["best_streak"])
        if r:
            cur.execute(
                q("UPDATE blindtest_scores SET best_streak=%s, name=%s, updated_at=%s WHERE uid=%s"),
                (streak, (name or "")[:40], now_str(), uid),
            )
        else:
            cur.execute(
                q("INSERT INTO blindtest_scores (uid,name,best_streak,updated_at) "
                  "VALUES (%s,%s,%s,%s)"),
                (uid, (name or "")[:40], streak, now_str()),
            )
    return streak


def blindtest_top(limit=10):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM blindtest_scores ORDER BY best_streak DESC LIMIT %s"), (limit,))
        return rows(cur)


def bump_counter(uid, kind):
    """kind: 'searches' | 'downloads'. Повертає (дозволено, використано, ліміт)."""
    lim = limits_for(uid)[f"{kind}_per_day"]
    col_n, col_d = f"{kind}_today", f"{kind}_date"
    with conn_cursor() as (conn, cur):
        cur.execute(q(f"SELECT {col_n} AS n, {col_d} AS d FROM users WHERE uid=%s"), (uid,))
        r = one(cur) or {"n": 0, "d": ""}
        n = int(r.get("n") or 0)
        if (r.get("d") or "") != today():
            n = 0
        if n >= lim:
            return False, n, lim
        n += 1
        cur.execute(
            q(f"UPDATE users SET {col_n}=%s, {col_d}=%s WHERE uid=%s"),
            (n, today(), uid),
        )
    return True, n, lim


# ─── Бібліотека ──────────────────────────────────────────────────────────────

def lib_add(uid, t):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT id FROM library WHERE uid=%s AND tid=%s"), (uid, t["id"]))
        if one(cur):
            return False
        cur.execute(q("""INSERT INTO library
            (uid,tid,title,artist,cover,duration,source,license,source_url,added_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""),
            (uid, t["id"], t.get("title", ""), t.get("artist", ""), t.get("cover", ""),
             int(t.get("duration") or 0), t.get("source", ""),
             t.get("license_short", ""), t.get("source_url", ""), now_str()))
    return True


def lib_remove(uid, tid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("DELETE FROM library WHERE uid=%s AND tid=%s"), (uid, tid))


def lib_list(uid, limit=500):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM library WHERE uid=%s ORDER BY id DESC LIMIT %s"),
                    (uid, limit))
        return [_row_to_track(r) for r in rows(cur)]


def lib_has(uid, tid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT id FROM library WHERE uid=%s AND tid=%s"), (uid, tid))
        return one(cur) is not None


def _row_to_track(r):
    return {
        "id": r.get("tid", ""),
        "title": r.get("title", ""),
        "artist": r.get("artist", ""),
        "cover": r.get("cover", ""),
        "duration": int(r.get("duration") or 0),
        "duration_str": f"{int(r.get('duration') or 0)//60}:{int(r.get('duration') or 0)%60:02d}"
                        if r.get("duration") else "",
        "source": r.get("source", ""),
        "source_label": {"jamendo": "Jamendo", "audius": "Audius",
                         "archive": "Internet Archive"}.get(r.get("source", ""), ""),
        "license_short": r.get("license", ""),
        "source_url": r.get("source_url", ""),
        "row_id": r.get("id"),
    }


# ─── Плейлисти ───────────────────────────────────────────────────────────────

def _share_code():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def pl_create(uid, name, description=""):
    lim = limits_for(uid)["playlists"]
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT COUNT(*) AS c FROM playlists WHERE uid=%s"), (uid,))
        c = int((one(cur) or {}).get("c") or 0)
        if c >= lim:
            return None, f"Ліміт плейлистів: {lim}. Premium знімає обмеження."
        code = _share_code()
        cur.execute(
            q("INSERT INTO playlists (uid,name,description,share_code,created_at) "
              "VALUES (%s,%s,%s,%s,%s)"),
            (uid, name[:60], description[:200], code, now_str()),
        )
        cur.execute(q("SELECT * FROM playlists WHERE uid=%s AND share_code=%s"), (uid, code))
        return one(cur), ""


def pl_list(uid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM playlists WHERE uid=%s ORDER BY id DESC"), (uid,))
        pls = rows(cur)
        for p in pls:
            cur.execute(q("SELECT COUNT(*) AS c FROM playlist_tracks WHERE pid=%s"), (p["id"],))
            p["count"] = int((one(cur) or {}).get("c") or 0)
        return pls


def pl_get(pid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM playlists WHERE id=%s"), (pid,))
        p = one(cur)
        if not p:
            return None
        cur.execute(q("SELECT * FROM playlist_tracks WHERE pid=%s ORDER BY position, id"), (pid,))
        p["tracks"] = [_row_to_track(r) for r in rows(cur)]
        p["count"] = len(p["tracks"])
        return p


def pl_get_by_code(code):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT id FROM playlists WHERE share_code=%s"), (code,))
        p = one(cur)
    return pl_get(p["id"]) if p else None


def pl_add_track(uid, pid, t):
    lim = limits_for(uid)["tracks_per_playlist"]
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT uid FROM playlists WHERE id=%s"), (pid,))
        p = one(cur)
        if not p or int(p["uid"]) != int(uid):
            return False, "Плейлист не знайдено"
        cur.execute(q("SELECT COUNT(*) AS c FROM playlist_tracks WHERE pid=%s"), (pid,))
        c = int((one(cur) or {}).get("c") or 0)
        if c >= lim:
            return False, f"У плейлисті максимум {lim} треків"
        cur.execute(q("SELECT id FROM playlist_tracks WHERE pid=%s AND tid=%s"), (pid, t["id"]))
        if one(cur):
            return False, "Трек уже в плейлисті"
        cur.execute(q("""INSERT INTO playlist_tracks
            (pid,tid,title,artist,cover,duration,source,license,source_url,position)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""),
            (pid, t["id"], t.get("title", ""), t.get("artist", ""), t.get("cover", ""),
             int(t.get("duration") or 0), t.get("source", ""),
             t.get("license_short", ""), t.get("source_url", ""), c))
        if c == 0 and t.get("cover"):
            cur.execute(q("UPDATE playlists SET cover=%s WHERE id=%s"), (t["cover"], pid))
    return True, ""


def pl_remove_track(uid, pid, tid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT uid FROM playlists WHERE id=%s"), (pid,))
        p = one(cur)
        if not p or int(p["uid"]) != int(uid):
            return False
        cur.execute(q("DELETE FROM playlist_tracks WHERE pid=%s AND tid=%s"), (pid, tid))
    return True


def pl_delete(uid, pid):
    with conn_cursor() as (conn, cur):
        cur.execute(q("DELETE FROM playlists WHERE id=%s AND uid=%s"), (pid, uid))
        cur.execute(q("DELETE FROM playlist_tracks WHERE pid=%s"), (pid,))
    return True


def pl_copy(uid, source_pid, new_name=None):
    src = pl_get(source_pid)
    if not src:
        return None, "Плейлист не знайдено"
    new, err = pl_create(uid, new_name or src["name"], src.get("description") or "")
    if not new:
        return None, err
    for t in src["tracks"]:
        pl_add_track(uid, new["id"], t)
    return pl_get(new["id"]), ""


# ─── Історія і статистика ────────────────────────────────────────────────────

def history_add(uid, t, seconds=0):
    with conn_cursor() as (conn, cur):
        cur.execute(q("""INSERT INTO history
            (uid,tid,title,artist,cover,source,seconds,played_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"""),
            (uid, t.get("id", ""), t.get("title", ""), t.get("artist", ""),
             t.get("cover", ""), t.get("source", ""), int(seconds or 0), now_str()))


def history_list(uid, limit=100):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM history WHERE uid=%s ORDER BY id DESC LIMIT %s"),
                    (uid, limit))
        seen, out = set(), []
        for r in rows(cur):
            if r["tid"] in seen:
                continue
            seen.add(r["tid"])
            out.append(_row_to_track(r))
        return out


def stats_for(uid, days=30):
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)) \
        .isoformat(sep=" ", timespec="seconds")
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT COUNT(*) AS plays, COALESCE(SUM(seconds),0) AS secs "
                      "FROM history WHERE uid=%s AND played_at>=%s"), (uid, since))
        base = one(cur) or {}
        cur.execute(q("SELECT artist, COUNT(*) AS c FROM history "
                      "WHERE uid=%s AND played_at>=%s GROUP BY artist "
                      "ORDER BY c DESC LIMIT 10"), (uid, since))
        top_artists = rows(cur)
        cur.execute(q("SELECT title, artist, COUNT(*) AS c FROM history "
                      "WHERE uid=%s AND played_at>=%s GROUP BY title, artist "
                      "ORDER BY c DESC LIMIT 10"), (uid, since))
        top_tracks = rows(cur)
        cur.execute(q("SELECT COUNT(*) AS c FROM library WHERE uid=%s"), (uid,))
        lib_count = int((one(cur) or {}).get("c") or 0)
    secs = int(base.get("secs") or 0)
    return {
        "plays": int(base.get("plays") or 0),
        "seconds": secs,
        "hours": round(secs / 3600, 1),
        "top_artists": [{"name": a["artist"], "plays": int(a["c"])} for a in top_artists if a["artist"]],
        "top_tracks": [{"title": t["title"], "artist": t["artist"], "plays": int(t["c"])}
                       for t in top_tracks if t["title"]],
        "library": lib_count,
        "days": days,
    }


def global_stats():
    with conn_cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) AS c FROM users")
        users = int((one(cur) or {}).get("c") or 0)
        cur.execute(q("SELECT COUNT(*) AS c FROM users WHERE premium=%s"),
                    (True if USE_PG else 1,))
        prem = int((one(cur) or {}).get("c") or 0)
        cur.execute("SELECT COUNT(*) AS c FROM history")
        plays = int((one(cur) or {}).get("c") or 0)
        cur.execute("SELECT COUNT(*) AS c FROM playlists")
        pls = int((one(cur) or {}).get("c") or 0)
    engine = "PostgreSQL (постійна)" if USE_PG else "SQLite (тимчасова!)"
    if not USE_PG and _pg_last_error:
        engine += f" — причина: {_pg_last_error}"
    return {
        "users": users, "premium": prem, "plays": plays, "playlists": pls,
        "engine": engine,
    }


def all_user_ids():
    with conn_cursor() as (conn, cur):
        cur.execute("SELECT uid FROM users")
        return [int(r["uid"]) for r in rows(cur)]


# ─── Промокоди ───────────────────────────────────────────────────────────────

def promo_create(code, days=30, uses=1):
    with conn_cursor() as (conn, cur):
        cur.execute(q("INSERT INTO promo_codes (code,days,uses_left,created_at) "
                      "VALUES (%s,%s,%s,%s)"),
                    (code.upper(), days, uses, now_str()))
    return True


def promo_redeem(uid, code):
    code = (code or "").strip().upper()
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM promo_codes WHERE code=%s"), (code,))
        p = one(cur)
        if not p or int(p["uses_left"]) <= 0:
            return False, 0
        cur.execute(q("UPDATE promo_codes SET uses_left=%s WHERE code=%s"),
                    (int(p["uses_left"]) - 1, code))
        days = int(p["days"])
    set_premium(uid, True, days)
    return True, days


# ─── Аудит і події ───────────────────────────────────────────────────────────

def audit(actor, action, target="", details=""):
    """Записує дію адміністратора. Панель без цього працювати не повинна."""
    with conn_cursor() as (conn, cur):
        cur.execute(q("INSERT INTO audit_log (actor,action,target,details,created_at) "
                      "VALUES (%s,%s,%s,%s,%s)"),
                    (int(actor), action[:60], str(target)[:120], str(details)[:400], now_str()))


def audit_list(limit=100):
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s"), (limit,))
        return rows(cur)


def auth_event(uid, kind, note=""):
    with conn_cursor() as (conn, cur):
        cur.execute(q("INSERT INTO auth_events (uid,kind,note,created_at) "
                      "VALUES (%s,%s,%s,%s)"),
                    (int(uid), kind[:40], str(note)[:200], now_str()))


def users_page(offset=0, limit=50, search=""):
    """Сторінка користувачів для панелі."""
    with conn_cursor() as (conn, cur):
        if search:
            like = f"%{search}%"
            cur.execute(q("SELECT * FROM users WHERE username LIKE %s OR first_name LIKE %s "
                          "OR CAST(uid AS TEXT) LIKE %s ORDER BY uid DESC LIMIT %s OFFSET %s"),
                        (like, like, like, limit, offset))
        else:
            cur.execute(q("SELECT * FROM users ORDER BY created_at DESC, uid DESC "
                          "LIMIT %s OFFSET %s"), (limit, offset))
        items = rows(cur)
        cur.execute("SELECT COUNT(*) AS c FROM users")
        total = int((one(cur) or {}).get("c") or 0)
    return items, total


def promo_list():
    with conn_cursor() as (conn, cur):
        cur.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
        return rows(cur)


def promo_delete(code):
    with conn_cursor() as (conn, cur):
        cur.execute(q("DELETE FROM promo_codes WHERE code=%s"), (code.upper(),))


def signups_by_day(days=14):
    with conn_cursor() as (conn, cur):
        cur.execute("SELECT created_at FROM users")
        raw = [r.get("created_at") for r in rows(cur)]
    out = {}
    for v in raw:
        if not v:
            continue
        day = str(v)[:10]
        out[day] = out.get(day, 0) + 1
    return sorted(out.items())[-days:]


# ─── Одноразові токени входу в панель ────────────────────────────────────────

def login_token_create(uid, ttl_seconds=300):
    """Створює одноразовий код входу. Живе 5 хвилин."""
    token = "".join(random.choices(string.ascii_letters + string.digits, k=40))
    exp = (datetime.datetime.utcnow() + datetime.timedelta(seconds=ttl_seconds)) \
        .isoformat(sep=" ", timespec="seconds")
    with conn_cursor() as (conn, cur):
        cur.execute(q("INSERT INTO login_tokens (token,uid,expires_at,used) "
                      "VALUES (%s,%s,%s,%s)"), (token, int(uid), exp, 0))
    return token


def login_token_consume(token):
    """Повертає uid і одразу гасить токен. Повторно використати не вийде."""
    if not token:
        return None
    with conn_cursor() as (conn, cur):
        cur.execute(q("SELECT * FROM login_tokens WHERE token=%s"), (token,))
        r = one(cur)
        if not r or int(r.get("used") or 0):
            return None
        exp = r.get("expires_at")
        if isinstance(exp, str):
            try:
                exp = datetime.datetime.fromisoformat(exp.replace("Z", "").strip())
            except ValueError:
                exp = None
        if isinstance(exp, datetime.datetime) and exp < datetime.datetime.utcnow():
            return None
        cur.execute(q("UPDATE login_tokens SET used=%s WHERE token=%s"), (1, token))
        return int(r["uid"])


def login_tokens_cleanup():
    cutoff = datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    with conn_cursor() as (conn, cur):
        cur.execute(q("DELETE FROM login_tokens WHERE expires_at<%s OR used=%s"), (cutoff, 1))
