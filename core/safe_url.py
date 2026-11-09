"""Безпечна перевірка зовнішніх URL з метаданих провайдерів і від користувачів.

Приймає лише абсолютні http(s)-посилання без облікових даних, керуючих символів
і без локальних/приватних адрес. Повертає "" (або None у strict-режимі), якщо URL небезпечний.
"""
import ipaddress
from urllib.parse import urlsplit

URL_MAX = 2048
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".intranet")


def _bad_host(host: str) -> bool:
    host = (host or "").strip("[]").rstrip(".").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # одиночні імена (intranet-host) не публічні
    return not ip.is_global


def public_http_url(value, max_len: int = URL_MAX) -> str:
    """Повертає нормалізований публічний http(s)-URL або порожній рядок."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > max_len:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in value):
        return ""
    try:
        parts = urlsplit(value)
        _ = parts.port  # ValueError на некоректному порту
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return ""
    if _bad_host(parts.hostname or ""):
        return ""
    return value