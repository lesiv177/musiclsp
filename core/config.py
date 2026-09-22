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
BOT_USERNAME = _env("BOT_USERNAME")     # без @, для реферальних посилань t.me/<...>

# ─── Реферальна система ──────────────────────────────────────────────────────
REFERRAL_REWARD_DAYS = _int("REFERRAL_REWARD_DAYS", 1)  # днів Premium запрошеному й тому, хто запросив

# ─── Ціни в Telegram Stars (XTR) ─────────────────────────────────────────────
# Орієнтовні ціни для майбутньої реальної покупки Premium (ще не увімкнено —
# зараз працює лише тестова кнопка на 1 зірку). 1 Star ≈ $0.01-0.02 залежно
# від регіону покупки, тому це приблизно $1.5-3 за місяць — типовий рівень
# для нішевого підписного сервісу.
STAR_PRICE_MONTH = _int("STAR_PRICE_MONTH", 149)
STAR_PRICE_YEAR = _int("STAR_PRICE_YEAR", 1290)

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
PANEL_SECRET = _env("PANEL_SECRET", "lesivlsp")
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
    "eq_presets": 1,
    "offline_pack": False,
    "search_results_max": 40,
    "blindtest_per_day": 5,
    "ambient_styles": ["blur"],
    "moods": ["chill", "energy", "focus", "sad"],
    "wrapped": False,
    "daily_preview": False,
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
    "eq_presets": 10,
    "offline_pack": True,
    "search_results_max": 100,
    "blindtest_per_day": 100000,
    "ambient_styles": ["blur", "vinyl", "particles"],
    "moods": ["chill", "energy", "focus", "sad", "happy", "party"],
    "wrapped": True,
    "daily_preview": True,
}

PREMIUM_FEATURES = [
    ("hifi", "Hi-Fi звук", "FLAC та 320 kbps там, де джерело дозволяє."),
    ("eq", "Еквалайзер", "10 смуг із пресетами, налаштування зберігається."),
    ("eqpresets", "Пресети еквалайзера", "До 10 власних збережених пресетів замість одного."),
    ("crossfade", "Кросфейд", "Плавний перехід між треками від 1 до 12 секунд."),
    ("sleep", "Таймер сну", "Музика стихає і вимикається сама."),
    ("playlists", "Безлімітні плейлисти", "500 плейлистів по 1000 треків замість 3 по 50."),
    ("offline", "Офлайн-пакет", "Завантажте весь плейлист одним ZIP-архівом."),
    ("radio", "Нескінченне радіо", "Підбирає схоже, поки ви не зупините."),
    ("download", "Завантаження MP3", "Лише для треків, чия ліцензія це дозволяє."),
    ("nolimits", "Без лімітів пошуку", "Безліміт запитів, до 100 результатів і повна історія."),
    ("stats", "Статистика", "Топ артистів, жанрів і годин прослуховування."),
    ("themes", "Теми оформлення", "П'ять тем плеєра і власний акцент."),
    ("ambient", "Живі обкладинки", "Стиль вінілу й часток замість звичайного розмиття."),
    ("moods", "Розширені настрої", "6 настроєвих міксів замість 4, і власні пресети."),
    ("blindtest", "Вгадай трек без лімітів", "Необмежені раунди гри і місце в загальному рейтингу."),
    ("wrapped", "Мій рік у музиці", "Персональна підсумкова картка — улюблені артисти й треки, щоб поділитися."),
    ("dailypreview", "Трек дня наперед", "Дізнайтесь завтрашній спільний трек дня вже сьогодні."),
]


def missing(*names):
    """Повертає список незаданих обов'язкових змінних."""
    return [n for n in names if not globals().get(n)]
