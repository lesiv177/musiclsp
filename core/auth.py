# -*- coding: utf-8 -*-
"""
Спільна автентифікація.

Два незалежні механізми:
  1. initData від Telegram Mini App — перевірка підпису HMAC-SHA256.
  2. Сесії адмінпанелі — власний підписаний токен із терміном дії.

Обидва не довіряють нічому, що приходить від клієнта, без підпису.
"""

import hmac
import json
import time
import base64
import hashlib
import logging
import urllib.parse

from core.config import BOT_TOKEN, PANEL_SECRET, PANEL_SESSION_HOURS, ADMIN_IDS

logger = logging.getLogger(__name__)

_cache = {}


# ─── Telegram Mini App ───────────────────────────────────────────────────────

def verify_init_data(init_data, token=None, max_age=86400):
    """
    Перевіряє підпис initData. Повертає dict користувача або None.

    Порядок саме такий, як вимагає Telegram: прибрати hash, відсортувати
    решту полів, зібрати через \\n, підписати ключем HMAC(«WebAppData», token).
    """
    token = token or BOT_TOKEN
    if not init_data or not token:
        return None

    hit = _cache.get(init_data)
    if hit and hit[0] > time.time():
        return hit[1]

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = parsed.pop("hash", "")
        if not received:
            return None

        check = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None

        if max_age and time.time() - int(parsed.get("auth_date", 0)) > max_age:
            return None

        user = json.loads(parsed.get("user", "{}"))
        if not user.get("id"):
            return None

        _cache[init_data] = (time.time() + 600, user)
        if len(_cache) > 2000:
            _cache.clear()
        return user
    except Exception as e:
        logger.warning("initData відхилено: %s", e)
        return None


# ─── Telegram Login Widget (для веб-панелі) ──────────────────────────────────

def verify_login_widget(data, token=None, max_age=86400):
    """
    Перевіряє дані від Telegram Login Widget.
    Відрізняється від initData ключем: тут секрет — це SHA256 самого токена.
    """
    token = token or BOT_TOKEN
    if not token or not data:
        return None
    try:
        payload = {k: v for k, v in data.items() if k != "hash"}
        received = data.get("hash", "")
        check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
        secret = hashlib.sha256(token.encode()).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if max_age and time.time() - int(payload.get("auth_date", 0)) > max_age:
            return None
        return {"id": int(payload["id"]),
                "username": payload.get("username", ""),
                "first_name": payload.get("first_name", "")}
    except Exception as e:
        logger.warning("Login Widget відхилено: %s", e)
        return None


# ─── Сесії панелі ────────────────────────────────────────────────────────────

def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(uid, hours=None):
    """Створює підписаний токен сесії для адмінпанелі."""
    hours = hours or PANEL_SESSION_HOURS
    body = json.dumps({"uid": int(uid), "exp": int(time.time() + hours * 3600)},
                      separators=(",", ":")).encode()
    sig = hmac.new(PANEL_SECRET.encode(), body, hashlib.sha256).digest()
    return f"{_b64e(body)}.{_b64e(sig)}"


def read_session(tokenstr):
    """Повертає uid або None. Прострочений чи підроблений токен — None."""
    if not tokenstr or "." not in tokenstr:
        return None
    try:
        body_s, sig_s = tokenstr.split(".", 1)
        body, sig = _b64d(body_s), _b64d(sig_s)
        expect = hmac.new(PANEL_SECRET.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(body)
        if data.get("exp", 0) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


def is_admin(uid):
    try:
        return int(uid) in ADMIN_IDS
    except (TypeError, ValueError):
        return False
