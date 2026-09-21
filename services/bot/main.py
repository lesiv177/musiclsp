# -*- coding: utf-8 -*-
"""
MusicLSP 4.0 — точка входу.

Запускає Telegram-бота (polling) і HTTP API в одному процесі.
Каталог музики — лише легальні джерела: Jamendo (Creative Commons),
Audius (публікації самих артистів), Internet Archive (public domain).
"""

import io
import os
import html
import json
import asyncio
import logging

import requests
from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
    MenuButtonWebApp, InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from core import db
from core import providers as P
from services.bot import api
from core.config import (
    BOT_TOKEN, ADMIN_IDS, WEB_APP_URL, API_URL, PORT,
    JAMENDO_CLIENT_ID, PREMIUM_FEATURES, APP_NAME, APP_VERSION,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("musiclsp")

MAX_UPLOAD_MB = 48


def webapp_url(path=""):
    base = WEB_APP_URL or ""
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}api={API_URL}" if API_URL else base
    if path:
        url += f"&start={path}"
    return url


def esc(s):
    return html.escape(str(s or ""))


# ─── Клавіатури ──────────────────────────────────────────────────────────────

def main_kb(uid):
    open_btn = (
        InlineKeyboardButton("Відкрити плеєр", web_app=WebAppInfo(url=webapp_url()))
        if WEB_APP_URL else
        InlineKeyboardButton("Плеєр не налаштований", callback_data="noop")
    )
    kb = [
        [open_btn],
        [InlineKeyboardButton("Знайти трек", callback_data="search"),
         InlineKeyboardButton("Що слухають", callback_data="trending")],
        [InlineKeyboardButton("Premium", callback_data="premium"),
         InlineKeyboardButton("Статистика", callback_data="stats")],
        [InlineKeyboardButton("Джерела та ліцензії", callback_data="legal")],
    ]
    if uid in ADMIN_IDS:
        kb.append([InlineKeyboardButton("Адмінка", callback_data="admin")])
    return InlineKeyboardMarkup(kb)


def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="home")]])


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

WELCOME = (
    "<b>{name} {ver}</b>\n"
    "Музика в Telegram із джерел, які дозволяють стрімінг.\n\n"
    "Каталог: <b>Jamendo</b> (Creative Commons), <b>Audius</b> (релізи самих артистів), "
    "<b>Internet Archive</b> (суспільне надбання). "
    "Понад 600 тисяч треків, які можна слухати і поширювати легально.\n\n"
    "Надішліть назву треку або артиста — знайду. "
    "Повний плеєр із чергою, радіо і плейлистами — у кнопці нижче."
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.get_user(u.id, u.username or "", u.first_name or "")
    arg = ctx.args[0] if ctx.args else ""
    if arg.startswith("pl_"):
        p = db.pl_get_by_code(arg[3:])
        if p:
            return await show_shared_playlist(update.message, p)
    await update.message.reply_text(
        WELCOME.format(name=APP_NAME, ver=APP_VERSION),
        parse_mode=ParseMode.HTML, reply_markup=main_kb(u.id),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Команди</b>\n"
        "/start — головне меню\n"
        "/search назва — пошук треку\n"
        "/radio назва — радіо на основі треку\n"
        "/premium — про Premium\n"
        "/code КОД — активувати промокод\n"
        "/stats — ваша статистика\n"
        "/legal — джерела та ліцензії\n\n"
        "Або просто надішліть текст — це теж пошук.",
        parse_mode=ParseMode.HTML, reply_markup=back_kb(),
    )


async def cmd_legal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await (update.message or update.callback_query.message).reply_text(
        "<b>Звідки музика</b>\n\n"
        "<b>Jamendo</b> — каталог Creative Commons. Офіційний API, стрімінг і показ "
        "метаданих дозволені умовами сервісу.\n"
        "<b>Audius</b> — відкритий протокол, де артисти самі публікують треки й "
        "дозволяють прослуховування.\n"
        "<b>Internet Archive</b> — записи в суспільному надбанні.\n\n"
        "Кожен трек показує ліцензію й посилання на оригінал — цього вимагає "
        "Creative Commons. Завантаження доступне лише для треків, чия ліцензія "
        "прямо це дозволяє.\n\n"
        "Тут немає обходу DRM і немає контенту закритих сервісів.",
        parse_mode=ParseMode.HTML, reply_markup=back_kb(),
    )


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    uid = update.effective_user.id
    active = db.is_premium(uid)
    lines = [f"<b>{'Premium активний' if active else 'MusicLSP Premium'}</b>\n"]
    for i, (_, title, desc) in enumerate(PREMIUM_FEATURES, 1):
        lines.append(f"{i}. <b>{esc(title)}</b> — {esc(desc)}")
    lines.append("\nМаєте промокод? Надішліть <code>/code ВАШ_КОД</code>")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=back_kb())


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Формат: /code ВАШ_КОД")
    done, days = db.promo_redeem(update.effective_user.id, ctx.args[0])
    if done:
        await update.message.reply_text(
            f"Premium активовано на {days} днів. Відкрийте плеєр — нові функції вже там.",
            reply_markup=main_kb(update.effective_user.id))
    else:
        await update.message.reply_text("Код недійсний або вже використаний.")


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

    if data == "home":
        await q.answer()
        return await q.message.reply_text(
            WELCOME.format(name=APP_NAME, ver=APP_VERSION),
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
            f"<b>Статистика сервісу</b>\n\nКористувачів: {s['users']}\n"
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
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("search", cmd_search))
    application.add_handler(CommandHandler("radio", cmd_radio))
    application.add_handler(CommandHandler("premium", cmd_premium))
    application.add_handler(CommandHandler("code", cmd_code))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("legal", cmd_legal))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)

    await application.initialize()
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
