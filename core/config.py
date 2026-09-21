# -*- coding: utf-8 -*-
"""
Спільна конфігурація всіх сервісів.

Кожен сервіс читає ті змінні, які йому потрібні. Якщо змінної немає —
сервіс каже про це в лог і продовжує з розумним замовчуванням,
крім випадків, коли без неї працювати неможливо (токени, ключі).
"""

import os


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _list(name, default=""):
    raw = _env(name, default).replace(" ", "")
    return [x for x in raw.split(",") if x]


# ─── Загальне ────────────────────────────────────────────────────────────────
APP_NAME = "MusicLSP"
APP_VERSION = "5.0"
ENV = _env("ENV", "production")
PORT = _int("PORT", 8080)           # Railway задає сам, у кожного сервісу свій

ADMIN_IDS = [int(x) for x in _list("ADMIN_IDS", "1293055247")]

# ─── Токени ботів ────────────────────────────────────────────────────────────
BOT_TOKEN = _env("BOT_TOKEN")           # основний бот
AUTH_BOT_TOKEN = _env("AUTH_BOT_TOKEN")  # бот авторизації та оплат

# ─── Адреси ──────────────────────────────────────────────────────────────────
WEB_APP_URL = _env("WEB_APP_URL").rstrip("/")   # GitHub Pages, плеєр
API_URL = _env("API_URL").rstrip("/")           # сервіс bot
PANEL_URL = _env("PANEL_URL").rstrip("/")       # сервіс panel

# ─── База даних ──────────────────────────────────────────────────────────────
DATABASE_URL = _env("DATABASE_URL")
SQLITE_PATH = _env("SQLITE_PATH", "musiclsp.db")

# ─── Джерела музики ──────────────────────────────────────────────────────────
JAMENDO_CLIENT_ID = _env("JAMENDO_CLIENT_ID")
JAMENDO_API = "https://api.jamendo.com/v3.0"
AUDIUS_APP_NAME = _env("AUDIUS_APP_NAME", APP_NAME)
AUDIUS_BOOTSTRAP = "https://api.audius.co"
ARCHIVE_ENABLED = _env("ARCHIVE_ENABLED", "1") == "1"

HTTP_TIMEOUT = 12
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# ─── Панель ──────────────────────────────────────────────────────────────────
# Сесія панелі підписується цим ключем. Згенеруйте: python -c
# "import secrets;print(secrets.token_hex(32))"
PANEL_SECRET = _env("PANEL_SECRET", "change-me-in-railway-variables")
PANEL_SESSION_HOURS = _int("PANEL_SESSION_HOURS", 12)

# ─── Воркер ──────────────────────────────────────────────────────────────────
WORKER_INTERVAL = _int("WORKER_INTERVAL", 600)   # секунд між циклами

# ─── Ліміти ──────────────────────────────────────────────────────────────────
FREE_LIMITS = {
    "searches_per_day": 60,
    "playlists": 3,
    "tracks_per_playlist": 50,
    "downloads_per_day": 3,
    "quality": "mp32",
    "radio_length": 20,
    "history_days": 7,
    "themes": ["midnight"],
}

PREMIUM_LIMITS = {
    "searches_per_day": 100000,
    "playlists": 500,
    "tracks_per_playlist": 1000,
    "downloads_per_day": 200,
    "quality": "flac",
    "radio_length": 200,
    "history_days": 3650,
    "themes": ["midnight", "ember", "aurora", "paper", "neon"],
}

PREMIUM_FEATURES = [
    ("hifi", "Hi-Fi звук", "FLAC та 320 kbps там, де джерело дозволяє."),
    ("eq", "Еквалайзер", "10 смуг із пресетами, налаштування зберігається."),
    ("crossfade", "Кросфейд", "Плавний перехід між треками від 1 до 12 секунд."),
    ("sleep", "Таймер сну", "Музика стихає і вимикається сама."),
    ("playlists", "Безлімітні плейлисти", "500 плейлистів по 1000 треків замість 3 по 50."),
    ("radio", "Нескінченне радіо", "Підбирає схоже, поки ви не зупините."),
    ("download", "Завантаження MP3", "Лише для треків, чия ліцензія це дозволяє."),
    ("nolimits", "Без лімітів пошуку", "Безліміт запитів і повна історія."),
    ("stats", "Статистика", "Топ артистів, жанрів і годин прослуховування."),
    ("themes", "Теми оформлення", "П'ять тем плеєра і власний акцент."),
]


def missing(*names):
    """Повертає список незаданих обов'язкових змінних."""
    return [n for n in names if not globals().get(n)]
