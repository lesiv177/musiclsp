# -*- coding: utf-8 -*-
"""
Сервіс авторизації.

Окремий бот, який відповідає за доступ: видає Premium, приймає промокоди,
веде журнал подій. Основний бот про це нічого не знає — він лише читає
з бази поточний стан користувача.

Розділення виправдане тим, що тут лежить інший рівень довіри: цей бот
роздає права, і його логи треба дивитися окремо від музичних запитів.
Якщо згодом додасте оплату, вебхук провайдера приходитиме саме сюди.
"""

import html
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

from core import db
from core.auth import is_admin
from core.config import (
    AUTH_BOT_TOKEN, BOT_TOKEN, ADMIN_IDS, APP_NAME, APP_VERSION,
    PREMIUM_FEATURES, WEB_APP_URL, PANEL_URL,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("auth")


def esc(s):
    return html.escape(str(s or ""))


def menu(uid):
    kb = [
        [InlineKeyboardButton("Ввести промокод", callback_data="redeem")],
        [InlineKeyboardButton("Мій статус", callback_data="status")],
        [InlineKeyboardButton("Що дає Premium", callback_data="what")],
    ]
    if is_admin(uid):
        kb.append([InlineKeyboardButton("Видати доступ", callback_data="grant")])
        kb.append([InlineKeyboardButton("Створити код", callback_data="newcode")])
        kb.append([InlineKeyboardButton("Журнал", callback_data="audit")])
    return InlineKeyboardMarkup(kb)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.get_user(u.id, u.username or "", u.first_name or "")
    db.auth_event(u.id, "start")
    await update.message.reply_text(
        f"<b>{APP_NAME} · доступ</b>\n\n"
        "Тут вмикається Premium і перевіряється статус підписки. "
        "Музика та плеєр — в основному боті.",
        parse_mode=ParseMode.HTML, reply_markup=menu(u.id))


async def show_status(msg, uid):
    u = db.get_user(uid)
    active = db.is_premium(uid)
    until = str(u.get("premium_until") or "")[:16]
    lim = db.limits_for(uid)
    await msg.reply_text(
        f"<b>Статус</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"План: <b>{'Premium' if active else 'Free'}</b>\n"
        + (f"Діє до: {esc(until)}\n" if active and until else "") +
        f"Пошуків на день: {lim['searches_per_day'] if lim['searches_per_day'] < 9999 else 'без ліміту'}\n"
        f"Плейлистів: {lim['playlists']}\n"
        f"Якість: {lim['quality'].upper()}",
        parse_mode=ParseMode.HTML, reply_markup=menu(uid))


async def show_features(msg, uid):
    lines = ["<b>Premium</b>\n"]
    for i, (_, title, desc) in enumerate(PREMIUM_FEATURES, 1):
        lines.append(f"{i}. <b>{esc(title)}</b> — {esc(desc)}")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=menu(uid))


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Формат: /code ВАШ_КОД")
    await redeem(update.message, update.effective_user.id, ctx.args[0])


async def redeem(msg, uid, code):
    done, days = db.promo_redeem(uid, code)
    if done:
        db.auth_event(uid, "promo_redeemed", code.upper())
        db.audit(uid, "redeem", str(uid), f"{code.upper()} / {days}д")
        text = f"Premium увімкнено на {days} днів."
        if WEB_APP_URL:
            text += " Відкрийте плеєр в основному боті — нові функції вже там."
        await msg.reply_text(text, reply_markup=menu(uid))
    else:
        db.auth_event(uid, "promo_failed", code.upper())
        await msg.reply_text("Код недійсний, вичерпаний або вже використаний.",
                             reply_markup=menu(uid))


# ─── Адміністративні дії ─────────────────────────────────────────────────────

async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/grant <uid> <днів>"""
    actor = update.effective_user.id
    if not is_admin(actor):
        return
    if len(ctx.args) < 1:
        return await update.message.reply_text("Формат: /grant USER_ID ДНІВ")
    try:
        target = int(ctx.args[0])
        days = int(ctx.args[1]) if len(ctx.args) > 1 else 30
    except ValueError:
        return await update.message.reply_text("ID і дні мають бути числами.")

    db.get_user(target)
    db.set_premium(target, True, days)
    db.audit(actor, "grant_premium", target, f"{days}д")
    db.auth_event(target, "premium_granted", f"by {actor}")
    await update.message.reply_text(
        f"Premium для <code>{target}</code> на {days} днів.",
        parse_mode=ParseMode.HTML, reply_markup=menu(actor))
    await notify_user(target, f"Вам відкрито Premium на {days} днів.")


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user.id
    if not is_admin(actor) or not ctx.args:
        return
    try:
        target = int(ctx.args[0])
    except ValueError:
        return await update.message.reply_text("Формат: /revoke USER_ID")
    db.set_premium(target, False)
    db.audit(actor, "revoke_premium", target)
    await update.message.reply_text(f"Premium знято з <code>{target}</code>.",
                                    parse_mode=ParseMode.HTML)


async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Видає одноразове посилання на адмінпанель. Діє 5 хвилин, один раз."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not PANEL_URL:
        return await update.message.reply_text(
            "PANEL_URL не заданий у змінних Railway — панель ще не має адреси.")
    token = db.login_token_create(uid)
    db.audit(uid, "panel_login_requested", uid)
    await update.message.reply_text(
        f"Вхід у панель (5 хвилин, одне використання):\n{PANEL_URL}/login?t={token}\n\n"
        "Не пересилайте це посилання — воно відкриває повний доступ.",
        disable_web_page_preview=True)


async def notify_user(uid, text):
    """Пише користувачу через основний бот, бо саме там він слухає музику."""
    if not BOT_TOKEN:
        return
    try:
        from telegram import Bot
        await Bot(BOT_TOKEN).send_message(uid, text)
    except Exception as e:
        log.debug("Не доставлено %s: %s", uid, e)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    state = ctx.user_data.pop("state", "")

    if state == "redeem":
        return await redeem(update.message, uid, text)

    if state == "newcode" and is_admin(uid):
        parts = text.split()
        code = parts[0].upper()
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        uses = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        try:
            db.promo_create(code, days, uses)
            db.audit(uid, "create_promo", code, f"{days}д × {uses}")
            return await update.message.reply_text(
                f"Код <code>{esc(code)}</code>: {days} днів, {uses} активацій.",
                parse_mode=ParseMode.HTML, reply_markup=menu(uid))
        except Exception as e:
            return await update.message.reply_text(f"Не вийшло: {esc(e)}",
                                                   parse_mode=ParseMode.HTML)

    if state == "grant" and is_admin(uid):
        ctx.args = text.split()
        return await cmd_grant(update, ctx)

    # За замовчуванням трактуємо як промокод — так очікує більшість людей
    if text and not text.startswith("/"):
        return await redeem(update.message, uid, text)


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    data = q.data or ""
    await q.answer()

    if data == "status":
        return await show_status(q.message, uid)
    if data == "what":
        return await show_features(q.message, uid)
    if data == "redeem":
        ctx.user_data["state"] = "redeem"
        return await q.message.reply_text("Надішліть промокод одним повідомленням.")

    if not is_admin(uid):
        return

    if data == "grant":
        ctx.user_data["state"] = "grant"
        return await q.message.reply_text(
            "Надішліть: <code>USER_ID ДНІВ</code>\nНаприклад: <code>1293055247 30</code>",
            parse_mode=ParseMode.HTML)
    if data == "newcode":
        ctx.user_data["state"] = "newcode"
        return await q.message.reply_text(
            "Надішліть: <code>КОД ДНІВ АКТИВАЦІЙ</code>\n"
            "Наприклад: <code>LAUNCH30 30 100</code>",
            parse_mode=ParseMode.HTML)
    if data == "audit":
        entries = db.audit_list(15)
        if not entries:
            return await q.message.reply_text("Журнал порожній.", reply_markup=menu(uid))
        lines = ["<b>Останні дії</b>\n"]
        for e in entries:
            lines.append(f"<code>{str(e.get('created_at'))[:16]}</code> "
                         f"{esc(e.get('action'))} → {esc(e.get('target'))} "
                         f"{esc(e.get('details'))}")
        return await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                          reply_markup=menu(uid))


async def on_error(update, ctx):
    log.error("Помилка: %s", ctx.error, exc_info=ctx.error)


async def run():
    if not AUTH_BOT_TOKEN:
        log.error("AUTH_BOT_TOKEN не заданий — сервіс авторизації не має що запускати")
        while True:
            await asyncio.sleep(3600)

    db.init_engine()
    app = Application.builder().token(AUTH_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("%s auth %s запущено", APP_NAME, APP_VERSION)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        log.info("Зупинено")
