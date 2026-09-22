# -*- coding: utf-8 -*-
"""
MusicLSP 4.0 — точка входу.

Запускає Telegram-бота (polling) і HTTP API в одному процесі.
Каталог музики — лише легальні джерела: Jamendo (Creative Commons),
Audius (публікації самих артистів), Internet Archive (public domain).
"""

import io
import os
import time
import html
import json
import asyncio
import logging
import datetime

import requests
from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
    MenuButtonWebApp, InputFile, LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, PreCheckoutQueryHandler,
)

from core import db
from core import runtime as RT
from core import providers as P
from services.bot import api
from core.config import (
    BOT_TOKEN, ADMIN_IDS, WEB_APP_URL, API_URL, PORT,
    JAMENDO_CLIENT_ID, PREMIUM_FEATURES, APP_NAME, APP_VERSION,
    REFERRAL_REWARD_DAYS, STAR_PRICE_MONTH, STAR_PRICE_YEAR,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("musiclsp")

MAX_UPLOAD_MB = 48

# ─── Мови бота ───────────────────────────────────────────────────────────────
# При першому /start бот одразу пропонує обрати мову — це і є перший екран,
# усе інше (привітання, кнопки, оплата) показується вже цією мовою.
BOT_LANGS = [
    ("uk", "🇺🇦 Українська"), ("ru", "🇷🇺 Русский"), ("en", "🇬🇧 English"),
    ("pl", "🇵🇱 Polski"), ("de", "🇩🇪 Deutsch"), ("es", "🇪🇸 Español"),
    ("fr", "🇫🇷 Français"), ("tr", "🇹🇷 Türkçe"),
]
BOT_LANG_CODES = {c for c, _ in BOT_LANGS}

BOT_TR = {
    "uk": {
        "welcome": ("👋 <b>Привіт! Це {name}</b> 🎶\n\n"
            "Тут понад 600 тисяч треків від незалежних артистів і лейблів, які самі "
            "дозволили ділитися своєю музикою — <b>Jamendo</b>, <b>Audius</b> і "
            "<b>Internet Archive</b>. Жодних крадених релізів, тільки чесна музика ✅\n\n"
            "Просто напишіть назву пісні, артиста чи навіть настрій — і я підберу щось "
            "варте прослуховування 🎧. А для повноцінного плеєра з чергою, радіо, "
            "плейлистами й грою «Вгадай трек» — тисніть кнопку нижче 👇"),
        "btn_open_player": "🎧 Відкрити плеєр", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Запросити друга", "btn_admin": "🛠 Адмінка", "btn_back": "← Назад",
        "premium_active_title": "✨ <b>Premium активний</b>", "premium_active_until": "Діє до {until} · дякуємо, що підтримуєте проєкт!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Той самий чесний каталог, але без жодних меж — ось що зміниться:",
        "premium_promo": "🎁 Є промокод? Надішліть <code>/code ВАШ_КОД</code>, і Premium увімкнеться миттєво.",
        "btn_month": "⭐ 1 місяць — {price}⭐", "btn_year": "⭐ 1 рік — {price}⭐ (вигідніше)",
        "pay_status": "✅ <b>Статус: Premium активовано</b>",
        "pay_confirmed": "Оплату отримано. Premium активний до {until}. Відкрийте плеєр — нові можливості вже там 🎧",
        "referral_intro": ("🤝 <b>Запросіть друзів у {name}</b>\n\nЗа кожного друга, який приєднається за "
            "вашим посиланням, ви обидва отримуєте +{days} день Premium."),
        "lang_changed": "✅ Мову змінено на {lang}.",
    },
    "ru": {
        "welcome": ("👋 <b>Привет! Это {name}</b> 🎶\n\n"
            "Здесь более 600 тысяч треков от независимых артистов и лейблов, которые сами "
            "разрешили делиться своей музыкой — <b>Jamendo</b>, <b>Audius</b> и "
            "<b>Internet Archive</b>. Никаких украденных релизов, только честная музыка ✅\n\n"
            "Просто напишите название песни, артиста или даже настроение — и я подберу что-то "
            "стоящее 🎧. А для полноценного плеера с очередью, радио, плейлистами и игрой "
            "«Угадай трек» — нажмите кнопку ниже 👇"),
        "btn_open_player": "🎧 Открыть плеер", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Пригласить друга", "btn_admin": "🛠 Админка", "btn_back": "← Назад",
        "premium_active_title": "✨ <b>Premium активен</b>", "premium_active_until": "Действует до {until} · спасибо, что поддерживаете проект!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Тот же честный каталог, но без всяких границ — вот что изменится:",
        "premium_promo": "🎁 Есть промокод? Отправьте <code>/code ВАШ_КОД</code>, и Premium включится мгновенно.",
        "btn_month": "⭐ 1 месяц — {price}⭐", "btn_year": "⭐ 1 год — {price}⭐ (выгоднее)",
        "pay_status": "✅ <b>Статус: Premium активирован</b>",
        "pay_confirmed": "Оплата получена. Premium активен до {until}. Откройте плеер — новые возможности уже там 🎧",
        "referral_intro": ("🤝 <b>Пригласите друзей в {name}</b>\n\nЗа каждого друга, который присоединится "
            "по вашей ссылке, вы оба получаете +{days} день Premium."),
        "lang_changed": "✅ Язык изменён на {lang}.",
    },
    "en": {
        "welcome": ("👋 <b>Hi! This is {name}</b> 🎶\n\n"
            "Over 600,000 tracks from independent artists and labels who chose to share "
            "their music — <b>Jamendo</b>, <b>Audius</b> and <b>Internet Archive</b>. No "
            "pirated releases, just honest music ✅\n\n"
            "Just type a song, artist or even a mood — I'll find something worth playing "
            "🎧. For the full player with a queue, radio, playlists and the \"Guess the "
            "track\" game — tap the button below 👇"),
        "btn_open_player": "🎧 Open player", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Invite a friend", "btn_admin": "🛠 Admin panel", "btn_back": "← Back",
        "premium_active_title": "✨ <b>Premium is active</b>", "premium_active_until": "Active until {until} · thanks for supporting the project!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Same honest catalog, no limits — here's what changes:",
        "premium_promo": "🎁 Have a promo code? Send <code>/code YOUR_CODE</code> to activate Premium instantly.",
        "btn_month": "⭐ 1 month — {price}⭐", "btn_year": "⭐ 1 year — {price}⭐ (better value)",
        "pay_status": "✅ <b>Status: Premium activated</b>",
        "pay_confirmed": "Payment received. Premium is active until {until}. Open the player — the new features are already there 🎧",
        "referral_intro": ("🤝 <b>Invite friends to {name}</b>\n\nFor every friend who joins with your "
            "link, you both get +{days} day of Premium."),
        "lang_changed": "✅ Language changed to {lang}.",
    },
    "pl": {
        "welcome": ("👋 <b>Cześć! To {name}</b> 🎶\n\n"
            "Ponad 600 tysięcy utworów od niezależnych artystów i wytwórni, którzy sami "
            "zezwolili na udostępnianie swojej muzyki — <b>Jamendo</b>, <b>Audius</b> i "
            "<b>Internet Archive</b>. Żadnych pirackich wydań, tylko uczciwa muzyka ✅\n\n"
            "Wpisz nazwę utworu, artysty albo nastrój — a coś znajdę 🎧. Pełny odtwarzacz "
            "z kolejką, radiem, playlistami i grą „Zgadnij utwór” — przycisk poniżej 👇"),
        "btn_open_player": "🎧 Otwórz odtwarzacz", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Zaproś znajomego", "btn_admin": "🛠 Panel admina", "btn_back": "← Wstecz",
        "premium_active_title": "✨ <b>Premium jest aktywne</b>", "premium_active_until": "Ważne do {until} · dziękujemy za wsparcie!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Ten sam uczciwy katalog, bez ograniczeń — oto co się zmieni:",
        "premium_promo": "🎁 Masz kod promocyjny? Wyślij <code>/code TWÓJ_KOD</code>, a Premium włączy się natychmiast.",
        "btn_month": "⭐ 1 miesiąc — {price}⭐", "btn_year": "⭐ 1 rok — {price}⭐ (korzystniej)",
        "pay_status": "✅ <b>Status: Premium aktywowane</b>",
        "pay_confirmed": "Płatność przyjęta. Premium aktywne do {until}. Otwórz odtwarzacz — nowe funkcje już tam są 🎧",
        "referral_intro": ("🤝 <b>Zaproś znajomych do {name}</b>\n\nZa każdego znajomego, który dołączy z "
            "Twojego linku, oboje otrzymujecie +{days} dzień Premium."),
        "lang_changed": "✅ Język zmieniono na {lang}.",
    },
    "de": {
        "welcome": ("👋 <b>Hallo! Das ist {name}</b> 🎶\n\n"
            "Über 600.000 Tracks von unabhängigen Künstlern und Labels, die ihre Musik "
            "selbst freigegeben haben — <b>Jamendo</b>, <b>Audius</b> und <b>Internet "
            "Archive</b>. Keine Raubkopien, nur ehrliche Musik ✅\n\n"
            "Schreib einfach einen Songtitel, Künstler oder eine Stimmung — ich finde "
            "etwas 🎧. Für den vollen Player mit Warteschlange, Radio, Playlists und dem "
            "„Song erraten“-Spiel — Button unten 👇"),
        "btn_open_player": "🎧 Player öffnen", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Freund einladen", "btn_admin": "🛠 Admin-Panel", "btn_back": "← Zurück",
        "premium_active_title": "✨ <b>Premium ist aktiv</b>", "premium_active_until": "Gültig bis {until} · danke für deine Unterstützung!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Derselbe ehrliche Katalog, aber ohne Grenzen — das ändert sich:",
        "premium_promo": "🎁 Promo-Code? Sende <code>/code DEIN_CODE</code>, Premium aktiviert sich sofort.",
        "btn_month": "⭐ 1 Monat — {price}⭐", "btn_year": "⭐ 1 Jahr — {price}⭐ (günstiger)",
        "pay_status": "✅ <b>Status: Premium aktiviert</b>",
        "pay_confirmed": "Zahlung erhalten. Premium ist aktiv bis {until}. Öffne den Player — die neuen Funktionen sind schon da 🎧",
        "referral_intro": ("🤝 <b>Lade Freunde zu {name} ein</b>\n\nFür jeden Freund, der über deinen Link "
            "beitritt, bekommt ihr beide +{days} Tag Premium."),
        "lang_changed": "✅ Sprache geändert zu {lang}.",
    },
    "es": {
        "welcome": ("👋 <b>¡Hola! Esto es {name}</b> 🎶\n\n"
            "Más de 600 000 pistas de artistas y sellos independientes que decidieron "
            "compartir su música — <b>Jamendo</b>, <b>Audius</b> e <b>Internet Archive</b>. "
            "Nada pirateado, solo música honesta ✅\n\n"
            "Escribe el nombre de una canción, artista o incluso un estado de ánimo — "
            "encontraré algo bueno 🎧. Para el reproductor completo con cola, radio, "
            "listas y el juego «Adivina la canción» — pulsa el botón de abajo 👇"),
        "btn_open_player": "🎧 Abrir reproductor", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Invitar a un amigo", "btn_admin": "🛠 Panel admin", "btn_back": "← Atrás",
        "premium_active_title": "✨ <b>Premium está activo</b>", "premium_active_until": "Activo hasta {until} · ¡gracias por apoyar el proyecto!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "El mismo catálogo honesto, sin límites — esto es lo que cambia:",
        "premium_promo": "🎁 ¿Tienes un código promo? Envía <code>/code TU_CODIGO</code> y Premium se activa al instante.",
        "btn_month": "⭐ 1 mes — {price}⭐", "btn_year": "⭐ 1 año — {price}⭐ (mejor precio)",
        "pay_status": "✅ <b>Estado: Premium activado</b>",
        "pay_confirmed": "Pago recibido. Premium activo hasta {until}. Abre el reproductor — las nuevas funciones ya están ahí 🎧",
        "referral_intro": ("🤝 <b>Invita amigos a {name}</b>\n\nPor cada amigo que se una con tu enlace, "
            "ambos reciben +{days} día de Premium."),
        "lang_changed": "✅ Idioma cambiado a {lang}.",
    },
    "fr": {
        "welcome": ("👋 <b>Salut ! C'est {name}</b> 🎶\n\n"
            "Plus de 600 000 titres d'artistes et labels indépendants qui ont choisi de "
            "partager leur musique — <b>Jamendo</b>, <b>Audius</b> et <b>Internet "
            "Archive</b>. Aucun contenu piraté, que de la musique honnête ✅\n\n"
            "Écris un titre, un artiste ou même une humeur — je trouverai quelque chose "
            "🎧. Pour le lecteur complet avec file d'attente, radio, playlists et le jeu "
            "« Devine le titre » — bouton ci-dessous 👇"),
        "btn_open_player": "🎧 Ouvrir le lecteur", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Inviter un ami", "btn_admin": "🛠 Panneau admin", "btn_back": "← Retour",
        "premium_active_title": "✨ <b>Premium est actif</b>", "premium_active_until": "Actif jusqu'au {until} · merci de soutenir le projet !",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Le même catalogue honnête, mais sans limites — voici ce qui change :",
        "premium_promo": "🎁 Un code promo ? Envoie <code>/code TON_CODE</code>, Premium s'active aussitôt.",
        "btn_month": "⭐ 1 mois — {price}⭐", "btn_year": "⭐ 1 an — {price}⭐ (plus avantageux)",
        "pay_status": "✅ <b>Statut : Premium activé</b>",
        "pay_confirmed": "Paiement reçu. Premium actif jusqu'au {until}. Ouvre le lecteur — les nouveautés sont déjà là 🎧",
        "referral_intro": ("🤝 <b>Invite des amis sur {name}</b>\n\nPour chaque ami qui rejoint avec ton "
            "lien, vous recevez tous les deux +{days} jour de Premium."),
        "lang_changed": "✅ Langue changée pour {lang}.",
    },
    "tr": {
        "welcome": ("👋 <b>Merhaba! Burası {name}</b> 🎶\n\n"
            "Müziklerini paylaşmayı kendileri seçen bağımsız sanatçı ve etiketlerden "
            "600 binden fazla parça — <b>Jamendo</b>, <b>Audius</b> ve <b>Internet "
            "Archive</b>. Korsan içerik yok, sadece dürüst müzik ✅\n\n"
            "Bir şarkı, sanatçı ya da ruh hali yaz — sana bir şeyler bulayım 🎧. Sıra, "
            "radyo, çalma listeleri ve \"Parçayı bil\" oyunuyla tam oynatıcı için aşağıdaki "
            "düğmeye dokun 👇"),
        "btn_open_player": "🎧 Oynatıcıyı aç", "btn_premium": "✨ Premium",
        "btn_invite": "🤝 Arkadaş davet et", "btn_admin": "🛠 Yönetim paneli", "btn_back": "← Geri",
        "premium_active_title": "✨ <b>Premium aktif</b>", "premium_active_until": "{until} tarihine kadar aktif · projeyi desteklediğin için teşekkürler!",
        "premium_inactive_title": "✨ <b>MusicLSP Premium</b>",
        "premium_inactive_sub": "Aynı dürüst katalog, ama sınırsız — işte değişecekler:",
        "premium_promo": "🎁 Promosyon kodun mu var? <code>/code KODUN</code> gönder, Premium hemen açılsın.",
        "btn_month": "⭐ 1 ay — {price}⭐", "btn_year": "⭐ 1 yıl — {price}⭐ (daha avantajlı)",
        "pay_status": "✅ <b>Durum: Premium etkinleştirildi</b>",
        "pay_confirmed": "Ödeme alındı. Premium {until} tarihine kadar aktif. Oynatıcıyı aç — yeni özellikler orada 🎧",
        "referral_intro": ("🤝 <b>Arkadaşlarını {name}'e davet et</b>\n\nBağlantınla katılan her arkadaş "
            "için ikiniz de +{days} gün Premium kazanırsınız."),
        "lang_changed": "✅ Dil {lang} olarak değiştirildi.",
    },
}


def bt(lang, key, **kw):
    lang = lang if lang in BOT_TR else "uk"
    text = BOT_TR[lang].get(key) or BOT_TR["uk"].get(key) or key
    return text.format(**kw) if kw else text


def user_lang(uid):
    u = db.get_user(uid)
    lang = (u.get("lang") or "uk").strip().lower()
    return lang if lang in BOT_LANG_CODES else "uk"

# Telegram кешує сторінку Mini App у своєму WebView досить агресивно — тому
# оновлення webapp/index.html на GitHub Pages не завжди підхоплюються одразу.
# BUILD_TAG унікальний для кожного запуску процесу (тобто для кожного
# деплою на Railway) і додається до посилання, щоб Telegram завжди тягнув
# свіжу версію файлу, а не показував стару з кешу.
BUILD_TAG = str(int(time.time()))


def webapp_url(path=""):
    base = WEB_APP_URL or ""
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}api={API_URL}" if API_URL else base
    url += f"&v={BUILD_TAG}"
    if path:
        url += f"&start={path}"
    return url


def esc(s):
    return html.escape(str(s or ""))


# ─── Клавіатури ──────────────────────────────────────────────────────────────

def main_kb(uid):
    lang = user_lang(uid)
    open_btn = (
        InlineKeyboardButton(bt(lang, "btn_open_player"), web_app=WebAppInfo(url=webapp_url()))
        if WEB_APP_URL else
        InlineKeyboardButton("⚠️ WEB_APP_URL?", callback_data="noop")
    )
    kb = [
        [open_btn],
        [InlineKeyboardButton(bt(lang, "btn_premium"), callback_data="premium"),
         InlineKeyboardButton(bt(lang, "btn_invite"), callback_data="referral")],
    ]
    if uid in ADMIN_IDS:
        kb.append([InlineKeyboardButton(bt(lang, "btn_admin"), callback_data="admin")])
    return InlineKeyboardMarkup(kb)


def back_kb(uid=None):
    lang = user_lang(uid) if uid else "uk"
    return InlineKeyboardMarkup([[InlineKeyboardButton(bt(lang, "btn_back"), callback_data="home")]])


def lang_kb(prefix="setlang"):
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}:{code}")]
            for code, label in BOT_LANGS]
    return InlineKeyboardMarkup(rows)


LANG_PICKER_TEXT = (
    "🌐 Оберіть мову інтерфейсу\n"
    "Choose your language\n"
    "Выберите язык\n"
    "Wybierz język · Sprache wählen · Elige idioma · Choisis la langue · Dil seç"
)


def track_kb(t):
    kb = []
    if WEB_APP_URL:
        kb.append([InlineKeyboardButton(
            "Слухати в плеєрі",
            web_app=WebAppInfo(url=webapp_url(f"track:{t['id']}")),
        )])
    kb.append([InlineKeyboardButton("Зберегти MP3", callback_data=f"dl:{t['id']}")])
    kb.append([InlineKeyboardButton("Сторінка треку", url=t.get("source_url") or "https://jamendo.com")])
    return InlineKeyboardMarkup(kb)


# ─── Команди ─────────────────────────────────────────────────────────────────

async def send_welcome(update: Update, uid, lang):
    await update.message.reply_text(
        bt(lang, "welcome", name=APP_NAME),
        parse_mode=ParseMode.HTML, reply_markup=main_kb(uid),
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = db.get_user(u.id, u.username or "", u.first_name or "")
    arg = ctx.args[0] if ctx.args else ""

    if not int(user.get("lang_set") or 0):
        # Перший запуск — спершу мова, все інше після вибору.
        if arg:
            ctx.user_data["pending_start_arg"] = arg
        return await update.message.reply_text(LANG_PICKER_TEXT, reply_markup=lang_kb())

    lang = user_lang(u.id)
    if arg.startswith("pl_"):
        p = db.pl_get_by_code(arg[3:])
        if p:
            return await show_shared_playlist(update.message, p)
    if arg.startswith("ref_"):
        done, who = await asyncio.to_thread(db.apply_referral, u.id, arg[4:])
        if done:
            await update.message.reply_text(
                f"🎉 {esc(who) or '🎁'} · +{REFERRAL_REWARD_DAYS} Premium",
                parse_mode=ParseMode.HTML,
            )
    await send_welcome(update, u.id, lang)


async def cmd_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(LANG_PICKER_TEXT, reply_markup=lang_kb())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 <b>Команди</b>\n"
        "/start — головне меню\n"
        "/search назва — пошук треку\n"
        "/radio назва — радіо на основі треку\n"
        "/premium — про Premium ✨\n"
        "/referral — запросити друзів і отримати Premium 🤝\n"
        "/code КОД — активувати промокод\n"
        "/stats — ваша статистика 📊\n"
        "/legal — джерела та ліцензії\n\n"
        "Або просто надішліть текст — це теж пошук 🔎.",
        parse_mode=ParseMode.HTML, reply_markup=back_kb(),
    )


async def cmd_legal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await (update.message or update.callback_query.message).reply_text(
        "<b>Звідки музика</b>\n\n"
        "<b>Jamendo</b> — каталог Creative Commons. Офіційний API, стрімінг і показ "
        "метаданих дозволені умовами сервісу.\n"
        "<b>Audius</b> — відкритий протокол, де артисти самі публікують треки й "
        "дозволяють прослуховування.\n"
        "<b>Internet Archive</b> — записи в суспільному надбанні.\n"
        "<b>ccMixter</b> — треки, завантажені самими авторами під Creative "
        "Commons через офіційне API сервісу.\n"
        "<b>Openverse</b> — офіційний агрегатор Creative Commons від "
        "Wikimedia: архівні записи, класика та інші відкриті джерела.\n\n"
        "Кожен трек показує ліцензію й посилання на оригінал — цього вимагає "
        "Creative Commons. Завантаження доступне лише для треків, чия ліцензія "
        "прямо це дозволяє.\n\n"
        "Тут немає обходу DRM і немає контенту закритих сервісів.",
        parse_mode=ParseMode.HTML, reply_markup=back_kb(),
    )


def _fmt_until(val):
    if not val:
        return ""
    if isinstance(val, str):
        try:
            val = datetime.datetime.fromisoformat(val.replace("Z", "").strip())
        except ValueError:
            return str(val)[:10]
    return val.strftime("%d.%m.%Y")


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    uid = update.effective_user.id
    lang = user_lang(uid)
    active = db.is_premium(uid)
    if active:
        u = db.get_user(uid)
        until = _fmt_until(u.get("premium_until"))
        lines = [bt(lang, "premium_active_title"),
                 bt(lang, "premium_active_until", until=until) + "\n"]
    else:
        lines = [bt(lang, "premium_inactive_title") + "\n",
                 bt(lang, "premium_inactive_sub") + "\n"]
    for _, title, desc in PREMIUM_FEATURES:
        lines.append(f"— <b>{esc(title)}</b>: {esc(desc)}")
    lines.append("\n" + bt(lang, "premium_promo"))
    kb = []
    if not active:
        kb = [
            [InlineKeyboardButton(bt(lang, "btn_month", price=STAR_PRICE_MONTH), callback_data="pay_month")],
            [InlineKeyboardButton(bt(lang, "btn_year", price=STAR_PRICE_YEAR), callback_data="pay_year")],
        ]
    kb.append([InlineKeyboardButton(bt(lang, "btn_back"), callback_data="home")])
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(kb))


# ─── Оплата (Telegram Stars) ─────────────────────────────────────────────────
# Stars — вбудована валюта Telegram (XTR), не потребує платіжного провайдера
# чи банківського рахунку. payload визначає, що саме купили — за ним і
# видаємо Premium після оплати.

STAR_PLANS = {
    "pay_month": ("premium_month", STAR_PRICE_MONTH, "Premium — 1 month", 30),
    "pay_year":  ("premium_year", STAR_PRICE_YEAR, "Premium — 1 year", 365),
}


async def send_invoice_for(chat_id, plan_key, ctx: ContextTypes.DEFAULT_TYPE, lang="uk"):
    payload, price, _title, _days = STAR_PLANS[plan_key]
    title = bt(lang, "btn_month" if plan_key == "pay_month" else "btn_year", price=price).replace("⭐", "").strip(" —")
    try:
        await ctx.bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=f"{APP_NAME} Premium",
            payload=payload,
            provider_token="",  # для Stars (XTR) саме порожній рядок, а не відсутнє поле
            currency="XTR",
            prices=[LabeledPrice(title, price)],
        )
    except Exception as e:
        logger.exception("Не вдалося виставити рахунок Stars (%s): %s", plan_key, e)
        await ctx.bot.send_message(chat_id, f"⚠️ {esc(e)}", parse_mode=ParseMode.HTML)


async def on_precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    await q.answer(ok=True)


async def on_successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        sp = update.message.successful_payment
        uid = update.effective_user.id
        lang = user_lang(uid)
        logger.info("Оплата Stars: uid=%s payload=%s amount=%s", uid, sp.invoice_payload, sp.total_amount)

        days = None
        for _key, (payload, _price, _title, plan_days) in STAR_PLANS.items():
            if sp.invoice_payload == payload:
                days = plan_days
                break

        if days:
            await asyncio.to_thread(db.extend_premium, uid, days)
            u = await asyncio.to_thread(db.get_user, uid)
            until = _fmt_until(u.get("premium_until"))
            text = (bt(lang, "pay_status") + "\n" +
                    bt(lang, "pay_confirmed", until=until))
        else:
            # Оплата пройшла, але payload не впізнано — Premium все одно
            # видаємо на місяць, щоб гроші користувача не пропали без сліду,
            # і залишаємо явний слід у логах для розбору.
            logger.warning("Невідомий payload оплати Stars: %s (uid=%s)", sp.invoice_payload, uid)
            await asyncio.to_thread(db.extend_premium, uid, 30)
            u = await asyncio.to_thread(db.get_user, uid)
            until = _fmt_until(u.get("premium_until"))
            text = (bt(lang, "pay_status") + "\n" +
                    bt(lang, "pay_confirmed", until=until))
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_kb(uid))
    except Exception:
        logger.exception("Помилка обробки успішної оплати")


async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    uid = update.effective_user.id
    lang = user_lang(uid)
    stats = await asyncio.to_thread(db.referral_stats, uid)
    username = RT.get_bot_username()
    link = f"https://t.me/{username}?start=ref_{stats['code']}" if username else ""
    text = bt(lang, "referral_intro", name=APP_NAME, days=stats["reward_days"]) + "\n\n"
    text += f"Запрошено / Invited: <b>{stats['invited']}</b>\n"
    text += f"Код / Code: <code>{esc(stats['code'])}</code>\n"
    if link:
        text += f"\n🔗 {esc(link)}"
    else:
        text += "\n⚠️ Спробуйте ще раз за хвилину після перезапуску."
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb(uid))


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Формат: /code ВАШ_КОД")
    done, days = db.promo_redeem(update.effective_user.id, ctx.args[0])
    if done:
        await update.message.reply_text(
            f"✅ Premium активовано на {days} днів. Відкрийте плеєр — нові функції вже там 🎧",
            reply_markup=main_kb(update.effective_user.id))
    else:
        await update.message.reply_text("❌ Код недійсний або вже використаний.")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    uid = update.effective_user.id
    days = db.limits_for(uid)["history_days"]
    s = db.stats_for(uid, min(days, 365))
    text = [
        f"<b>Ваша статистика за {s['days']} днів</b>\n",
        f"Прослуховувань: <b>{s['plays']}</b>",
        f"Годин музики: <b>{s['hours']}</b>",
        f"У бібліотеці: <b>{s['library']}</b> треків",
    ]
    if s["top_artists"]:
        text.append("\n<b>Топ артистів</b>")
        for i, a in enumerate(s["top_artists"][:5], 1):
            text.append(f"{i}. {esc(a['name'])} — {a['plays']}")
    if not db.is_premium(uid):
        text.append("\nPremium показує статистику за весь час і підсумки року.")
    await msg.reply_text("\n".join(text), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args).strip()
    if not query:
        return await update.message.reply_text("Формат: /search назва треку")
    await do_search(update.message, update.effective_user.id, query)


async def cmd_radio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args).strip()
    if not query:
        return await update.message.reply_text("Формат: /radio назва треку")
    msg = await update.message.reply_text("Шукаю з чого почати…")
    uid = update.effective_user.id
    found = await asyncio.to_thread(P.search_tracks, query, 1, db.limits_for(uid)["quality"])
    if not found:
        return await msg.edit_text("Нічого не знайшов. Спробуйте іншу назву.")
    seed = found[0]
    if not WEB_APP_URL:
        return await msg.edit_text("Плеєр не налаштований — радіо працює в Mini App.")
    await msg.edit_text(
        f"Радіо на основі <b>{esc(seed['artist'])} — {esc(seed['title'])}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "Увімкнути радіо",
            web_app=WebAppInfo(url=webapp_url(f"radio:{seed['id']}")))]]),
    )


# ─── Пошук ───────────────────────────────────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    uid = update.effective_user.id
    state = ctx.user_data.pop("state", "")
    if state == "broadcast" and uid in ADMIN_IDS:
        return await do_broadcast(update, ctx, text)
    if state == "promo" and uid in ADMIN_IDS:
        return await do_make_promo(update, text)
    await do_search(update.message, uid, text)


async def do_search(msg, uid, query):
    db.get_user(uid)
    allowed, used, cap = db.bump_counter(uid, "searches")
    if not allowed:
        return await msg.reply_text(
            f"Ліміт пошуку на сьогодні вичерпано ({cap} запитів). "
            "Premium знімає обмеження.", reply_markup=back_kb())

    wait = await msg.reply_text(f"Шукаю «{query}»…")
    quality = db.limits_for(uid)["quality"]
    results = await asyncio.to_thread(P.search_tracks, query, 8, quality)
    if not results:
        return await wait.edit_text(
            "Нічого не знайшов. У каталозі незалежна музика, тому світові хіти "
            "тут відсутні — спробуйте жанр або ім'я інді-артиста.",
            reply_markup=back_kb())

    lines = [f"<b>Знайдено за запитом «{esc(query)}»</b>\n"]
    kb = []
    for i, t in enumerate(results, 1):
        lines.append(
            f"{i}. <b>{esc(t['title'])}</b> — {esc(t['artist'])}"
            f"{' · ' + t['duration_str'] if t['duration_str'] else ''}"
            f" · <i>{esc(t['source_label'])}</i>"
        )
        kb.append([InlineKeyboardButton(
            f"{i}. {t['title'][:28]} — {t['artist'][:18]}",
            callback_data=f"t:{t['id']}")])
    if WEB_APP_URL:
        kb.append([InlineKeyboardButton(
            "Усі результати в плеєрі",
            web_app=WebAppInfo(url=webapp_url(f"search:{query}")))])
    kb.append([InlineKeyboardButton("← Меню", callback_data="home")])
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(kb))


async def show_track(msg, uid, tid):
    t = await asyncio.to_thread(P.get_track, tid, db.limits_for(uid)["quality"])
    if not t:
        return await msg.reply_text("Трек недоступний.")
    caption = (
        f"<b>{esc(t['title'])}</b>\n{esc(t['artist'])}\n"
        f"{esc(t['album']) if t['album'] else ''}\n\n"
        f"Джерело: {esc(t['source_label'])}\n"
        f"Ліцензія: {esc(t['license_short'])}\n"
        f"{'Завантаження дозволене ліцензією' if t['downloadable'] else 'Ліцензія дозволяє лише прослуховування'}"
    )
    if t.get("cover"):
        await msg.reply_photo(t["cover"], caption=caption,
                              parse_mode=ParseMode.HTML, reply_markup=track_kb(t))
    else:
        await msg.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=track_kb(t))


async def show_shared_playlist(msg, p):
    lines = [f"<b>{esc(p['name'])}</b>\n{esc(p.get('description') or '')}\n"]
    for i, t in enumerate(p["tracks"][:15], 1):
        lines.append(f"{i}. {esc(t['title'])} — {esc(t['artist'])}")
    if p["count"] > 15:
        lines.append(f"…і ще {p['count'] - 15}")
    kb = []
    if WEB_APP_URL:
        kb.append([InlineKeyboardButton(
            "Відкрити плейлист",
            web_app=WebAppInfo(url=webapp_url(f"shared:{p['share_code']}")))])
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(kb) if kb else None)


# ─── Завантаження ────────────────────────────────────────────────────────────

async def do_download(query, uid, tid):
    if not db.is_premium(uid):
        return await query.answer("Завантаження доступне у Premium", show_alert=True)
    allowed, used, cap = db.bump_counter(uid, "downloads")
    if not allowed:
        return await query.answer(f"Ліміт завантажень на сьогодні: {cap}", show_alert=True)

    await query.answer("Готую файл…")
    url, t = await asyncio.to_thread(P.resolve_download, tid)
    if not url or not t:
        return await query.message.reply_text(
            "Ліцензія цього треку дозволяє лише прослуховування — завантажити не можу.")

    try:
        data = await asyncio.to_thread(_fetch_bytes, url)
    except Exception as e:
        logger.warning("Завантаження не вдалося: %s", e)
        return await query.message.reply_text("Джерело не віддало файл. Спробуйте пізніше.")

    if not data:
        return await query.message.reply_text("Файл порожній — джерело недоступне.")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return await query.message.reply_text(
            f"Файл більший за {MAX_UPLOAD_MB} МБ — Telegram не пропустить. "
            f"Пряме посилання: {t['source_url']}")

    filename = f"{t['artist']} - {t['title']}.mp3".replace("/", "-")[:120]
    bio = io.BytesIO(data)
    bio.name = filename
    await query.message.reply_audio(
        audio=InputFile(bio, filename=filename),
        title=t["title"][:64], performer=t["artist"][:64],
        duration=t.get("duration") or None,
        caption=f"{esc(t['license_short'])} · <a href=\"{esc(t['source_url'])}\">оригінал</a>",
        parse_mode=ParseMode.HTML,
    )


def _fetch_bytes(url, limit=MAX_UPLOAD_MB * 1024 * 1024 + 1024):
    r = requests.get(url, timeout=90, stream=True,
                     headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(65536):
        buf.write(chunk)
        if buf.tell() > limit:
            break
    return buf.getvalue()


# ─── Адмінка ─────────────────────────────────────────────────────────────────

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Загальна статистика", callback_data="a:stats")],
        [InlineKeyboardButton("Створити промокод", callback_data="a:promo")],
        [InlineKeyboardButton("Розсилка", callback_data="a:broadcast")],
        [InlineKeyboardButton("← Меню", callback_data="home")],
    ])


async def do_make_promo(update, text):
    parts = text.split()
    code = parts[0].upper()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    uses = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    try:
        db.promo_create(code, days, uses)
        await update.message.reply_text(
            f"Промокод <code>{esc(code)}</code> створено: {days} днів, {uses} активацій.",
            parse_mode=ParseMode.HTML, reply_markup=admin_kb())
    except Exception as e:
        await update.message.reply_text(f"Не вийшло: {esc(e)}", parse_mode=ParseMode.HTML)


async def do_broadcast(update, ctx, text):
    uids = db.all_user_ids()
    sent = failed = 0
    note = await update.message.reply_text(f"Надсилаю {len(uids)} користувачам…")
    for uid in uids:
        try:
            await ctx.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await note.edit_text(f"Готово. Доставлено: {sent}, не вдалося: {failed}.",
                         reply_markup=admin_kb())


# ─── Callback ────────────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id

    if data == "noop":
        return await q.answer("Задайте WEB_APP_URL у змінних Railway", show_alert=True)

    if data.startswith("setlang:"):
        code = data.split(":", 1)[1]
        if code not in BOT_LANG_CODES:
            return await q.answer()
        first_time = not int((db.get_user(uid).get("lang_set")) or 0)
        await asyncio.to_thread(db.update_user, uid, lang=code, lang_set=True)
        await q.answer()
        lang_label = dict(BOT_LANGS)[code]
        await q.message.reply_text(bt(code, "lang_changed", lang=lang_label), parse_mode=ParseMode.HTML)
        if first_time:
            arg = ctx.user_data.pop("pending_start_arg", "")
            if arg.startswith("pl_"):
                p = db.pl_get_by_code(arg[3:])
                if p:
                    return await show_shared_playlist(q.message, p)
            if arg.startswith("ref_"):
                done, who = await asyncio.to_thread(db.apply_referral, uid, arg[4:])
                if done:
                    await q.message.reply_text(
                        f"🎉 {esc(who) or '🎁'} · +{REFERRAL_REWARD_DAYS} Premium",
                        parse_mode=ParseMode.HTML)
            await q.message.reply_text(
                bt(code, "welcome", name=APP_NAME),
                parse_mode=ParseMode.HTML, reply_markup=main_kb(uid))
        return

    if data == "home":
        await q.answer()
        lang = user_lang(uid)
        return await q.message.reply_text(
            bt(lang, "welcome", name=APP_NAME),
            parse_mode=ParseMode.HTML, reply_markup=main_kb(uid))

    if data == "search":
        await q.answer()
        return await q.message.reply_text("Надішліть назву треку, артиста або жанр.")

    if data == "trending":
        await q.answer()
        tracks = await asyncio.to_thread(P.audius_trending, None, 8)
        if not tracks:
            tracks = await asyncio.to_thread(P.jamendo_tag_tracks, "pop", 8)
        lines = ["<b>Зараз слухають</b>\n"]
        kb = []
        for i, t in enumerate(tracks, 1):
            lines.append(f"{i}. <b>{esc(t['title'])}</b> — {esc(t['artist'])}")
            kb.append([InlineKeyboardButton(f"{i}. {t['title'][:30]}",
                                            callback_data=f"t:{t['id']}")])
        kb.append([InlineKeyboardButton("← Меню", callback_data="home")])
        return await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                          reply_markup=InlineKeyboardMarkup(kb))

    if data == "premium":
        await q.answer()
        return await cmd_premium(update, ctx)

    if data == "stats":
        await q.answer()
        return await cmd_stats(update, ctx)

    if data == "legal":
        await q.answer()
        return await cmd_legal(update, ctx)

    if data == "referral":
        await q.answer()
        return await cmd_referral(update, ctx)

    if data in STAR_PLANS:
        await q.answer()
        return await send_invoice_for(q.message.chat_id, data, ctx, user_lang(uid))

    if data.startswith("t:"):
        await q.answer()
        return await show_track(q.message, uid, data[2:])

    if data.startswith("dl:"):
        return await do_download(q, uid, data[3:])

    if data == "admin" and uid in ADMIN_IDS:
        await q.answer()
        return await q.message.reply_text("Панель адміністратора", reply_markup=admin_kb())

    if data == "a:stats" and uid in ADMIN_IDS:
        await q.answer()
        s = db.global_stats()
        return await q.message.reply_text(
            f"<b>Статистика сервісу</b>\n\nБаза даних: <b>{esc(s['engine'])}</b>\n\n"
            f"Користувачів: {s['users']}\n"
            f"З Premium: {s['premium']}\nПрослуховувань: {s['plays']}\n"
            f"Плейлистів: {s['playlists']}",
            parse_mode=ParseMode.HTML, reply_markup=admin_kb())

    if data == "a:promo" and uid in ADMIN_IDS:
        await q.answer()
        ctx.user_data["state"] = "promo"
        return await q.message.reply_text(
            "Надішліть: <code>КОД ДНІВ АКТИВАЦІЙ</code>\nНаприклад: <code>LAUNCH30 30 100</code>",
            parse_mode=ParseMode.HTML)

    if data == "a:broadcast" and uid in ADMIN_IDS:
        await q.answer()
        ctx.user_data["state"] = "broadcast"
        return await q.message.reply_text("Надішліть текст розсилки (HTML дозволено).")

    await q.answer()


async def on_error(update, ctx):
    logger.error("Помилка обробника: %s", ctx.error, exc_info=ctx.error)


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def run():
    db.init_db()

    if not JAMENDO_CLIENT_ID:
        logger.warning("JAMENDO_CLIENT_ID не заданий — головне джерело каталогу вимкнене")

    app_web = api.build_app()
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info("HTTP API слухає порт %s", PORT)

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не заданий — працює лише API")
        while True:
            await asyncio.sleep(3600)

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("lang", cmd_lang))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("search", cmd_search))
    application.add_handler(CommandHandler("radio", cmd_radio))
    application.add_handler(CommandHandler("premium", cmd_premium))
    application.add_handler(CommandHandler("code", cmd_code))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("legal", cmd_legal))
    application.add_handler(CommandHandler("referral", cmd_referral))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(PreCheckoutQueryHandler(on_precheckout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)

    await application.initialize()
    try:
        me = await application.bot.get_me()
        if me and me.username:
            RT.set_detected_username(me.username)
            logger.info("Username бота визначено автоматично: @%s", me.username)
    except Exception as e:
        logger.warning("Не вдалося визначити username бота: %s", e)
    if WEB_APP_URL:
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Плеєр",
                                             web_app=WebAppInfo(url=webapp_url())))
        except Exception as e:
            logger.warning("Кнопка меню не встановлена: %s", e)
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("%s %s запущено", APP_NAME, APP_VERSION)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Зупинено")
