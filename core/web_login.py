# -*- coding: utf-8 -*-
"""
Вхід у MusicLSP зі звичайної вкладки браузера (ПК) через Telegram Login Widget.

Схема:
  1. Користувач натискає віджет «Log in with Telegram» на сторінці.
  2. Віджет повертає підписані дані (id, first_name, username, auth_date, hash).
     Підпис перевіряємо за документацією Telegram:
         secret = SHA256(bot_token); hash = HMAC_SHA256(secret, data_check_string)
  3. Якщо підпис і свіжість у порядку, видаємо власну підписану сесію
     "mlspweb.<payload>.<підпис>" на 30 діб. Вона передається ТИМ САМИМ каналом, що й initData
     (заголовок X-Telegram-Init-Data / параметр initData), тож стрімінг, Connect та решта API
     працюють без змін.

Нічого, що прийшло від клієнта, не довіряється без підпису. Токени не логуються (див. log_safety).
Відкликати окрему сесію не можна — вона діє до закінчення терміну; змінений BOT_TOKEN анулює всі.
"""

import base64
import hashlib
import hmac
import json
import time

WEB_PREFIX = "mlspweb."
LOGIN_MAX_AGE = 86400          # віджет-дані дійсні добу
SESSION_TTL = 30 * 86400       # сесія — 30 діб
MAX_TOKEN_LEN = 2048
_WIDGET_FIELDS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date")


def is_web_session(raw):
    return isinstance(raw, str) and raw.startswith(WEB_PREFIX)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _session_key(bot_token: str) -> bytes:
    return hmac.new(bot_token.encode("utf-8"), b"musiclsp-web-session-v1", hashlib.sha256).digest()


def verify_widget_login(data, bot_token, now=None, max_age=LOGIN_MAX_AGE):
    """Перевіряє дані Telegram Login Widget. Повертає {"id","username","first_name"} або None."""
    if not isinstance(data, dict) or not bot_token:
        return None
    received = str(data.get("hash") or "")
    if not received:
        return None
    fields = {}
    for key in _WIDGET_FIELDS:
        value = data.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return None
        fields[key] = str(value)
    if "id" not in fields or "auth_date" not in fields:
        return None
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    calc = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received.lower()):
        return None
    try:
        uid = int(fields["id"])
        auth_date = int(fields["auth_date"])
    except ValueError:
        return None
    now = time.time() if now is None else now
    if uid <= 0 or auth_date > now + 60 or now - auth_date > max_age:
        return None
    return {"id": uid, "username": fields.get("username", ""), "first_name": fields.get("first_name", "")}


def issue_session(user, bot_token, now=None, ttl=SESSION_TTL):
    now = time.time() if now is None else now
    payload = {"id": int(user["id"]), "u": str(user.get("username") or "")[:64],
               "n": str(user.get("first_name") or "")[:64], "exp": int(now + ttl)}
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = _b64e(hmac.new(_session_key(bot_token), body.encode("ascii"), hashlib.sha256).digest())
    return f"{WEB_PREFIX}{body}.{sig}"


def verify_session(raw, bot_token, now=None):
    """Повертає {"id","username","first_name"} або None. Ніколи не кидає виняток."""
    try:
        if not bot_token or not is_web_session(raw) or len(raw) > MAX_TOKEN_LEN:
            return None
        body, _, sig = raw[len(WEB_PREFIX):].partition(".")
        if not body or not sig:
            return None
        expected = _b64e(hmac.new(_session_key(bot_token), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        now = time.time() if now is None else now
        if int(payload.get("exp", 0)) < now:
            return None
        uid = int(payload["id"])
        if uid <= 0:
            return None
        return {"id": uid, "username": payload.get("u", ""), "first_name": payload.get("n", "")}
    except Exception:
        return None