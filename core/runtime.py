# -*- coding: utf-8 -*-
"""
Дрібний рантайм-стан, який не варто тримати в config.py (там лише те, що
береться зі змінних середовища при старті й більше не міняється).

Зараз тут лише одне: справжній username бота, визначений автоматично через
Telegram API (bot.get_me()) одразу при старті. Це знімає залежність від
змінної BOT_USERNAME на Railway — якщо адмін забув її задати або вказав
неправильно, реферальні посилання раніше просто не працювали
("Адмін ще не вказав BOT_USERNAME"). Тепер бот сам знає свій @username.
"""

from core.config import BOT_USERNAME as _ENV_BOT_USERNAME

_detected_username = ""


def set_detected_username(username):
    global _detected_username
    _detected_username = (username or "").lstrip("@").strip()


def get_bot_username():
    """Автовизначений username має пріоритет — він завжди правильний,
    бо береться напряму з Telegram, а не зі змінної середовища."""
    return _detected_username or _ENV_BOT_USERNAME
