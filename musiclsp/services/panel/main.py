# -*- coding: utf-8 -*-
"""
Адмінпанель.

Вхід — одноразове посилання з бота авторизації (/panel). Далі підписана
cookie-сесія на PANEL_SESSION_HOURS годин.

Сторінки рендеряться на сервері: панель для двох адміністраторів не варта
окремого фронтенд-білду, а кожна дія тут пише в журнал аудиту.
"""

import html
import asyncio
import logging
import datetime

from aiohttp import web

from core import db
from core.auth import issue_session, read_session, is_admin
from core.config import (
    PORT, PANEL_SECRET, PANEL_SESSION_HOURS, APP_NAME, APP_VERSION, ENV,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("panel")

COOKIE = "mlsp_session"


def esc(s):
    return html.escape(str(s if s is not None else ""))


# ─── Шаблон ──────────────────────────────────────────────────────────────────

def page(title, body, uid=None, active=""):
    nav = ""
    if uid:
        items = [("/", "Огляд"), ("/users", "Користувачі"),
                 ("/promo", "Промокоди"), ("/audit", "Журнал")]
        nav = "".join(
            f'<a href="{h}" class="{"on" if active == h else ""}">{esc(t)}</a>'
            for h, t in items
        ) + f'<span class="who">{uid}</span><a href="/logout" class="out">Вийти</a>'

    return web.Response(content_type="text/html", text=f"""<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {APP_NAME}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 background:#0F1117;color:#E8EAF0}}
a{{color:#9FC5FF;text-decoration:none}}
header{{border-bottom:1px solid #222736;background:#141824;position:sticky;top:0;z-index:5}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}
.brandrow{{display:flex;align-items:center;gap:14px;padding:16px 0}}
.brand{{font-weight:700;font-size:17px;letter-spacing:-.01em}}
.tag{{font-size:11px;color:#7C8398;border:1px solid #2A3145;padding:2px 8px;border-radius:99px}}
nav{{display:flex;gap:4px;align-items:center;padding-bottom:12px;flex-wrap:wrap}}
nav a{{padding:7px 13px;border-radius:8px;color:#A8B0C4;font-size:14px}}
nav a.on{{background:#212838;color:#fff}}
nav .who{{margin-left:auto;font-size:12px;color:#6C7387}}
nav a.out{{color:#E88}}
main{{padding:28px 0 60px}}
h1{{font-size:22px;margin-bottom:20px;letter-spacing:-.02em}}
h2{{font-size:15px;margin:26px 0 12px;color:#A8B0C4;font-weight:600}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.card{{background:#161B27;border:1px solid #222736;border-radius:11px;padding:18px}}
.card b{{display:block;font-size:26px;font-weight:700;letter-spacing:-.02em}}
.card span{{font-size:12px;color:#7C8398;margin-top:4px;display:block}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}}
th{{text-align:left;font-size:11px;text-transform:none;color:#7C8398;font-weight:600;
 padding:9px 10px;border-bottom:1px solid #222736}}
td{{padding:10px;border-bottom:1px solid #1B2030;vertical-align:middle}}
tr:hover td{{background:#141824}}
.pill{{font-size:11px;padding:3px 9px;border-radius:99px;border:1px solid #2A3145;color:#8891A6}}
.pill.pro{{background:#2D2410;border-color:#5A4718;color:#FFD166}}
form.inline{{display:inline-flex;gap:6px;align-items:center}}
input,select{{background:#0F1320;border:1px solid #2A3145;color:#E8EAF0;
 padding:8px 11px;border-radius:8px;font:inherit;font-size:13px}}
input:focus,select:focus{{outline:none;border-color:#4A6BFF}}
button{{background:#3D5AFE;color:#fff;border:0;padding:8px 14px;border-radius:8px;
 font:inherit;font-size:13px;font-weight:600;cursor:pointer}}
button.ghost{{background:transparent;border:1px solid #2A3145;color:#A8B0C4}}
button.warn{{background:#7A2331}}
.bar{{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-bottom:16px}}
.note{{color:#7C8398;font-size:13px;margin-top:10px;line-height:1.6}}
.login{{max-width:420px;margin:14vh auto;text-align:center;padding:0 20px}}
.login .card{{padding:32px 26px}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#8891A6}}
.spark{{display:flex;gap:3px;align-items:flex-end;height:54px;margin-top:10px}}
.spark div{{flex:1;background:#3D5AFE;border-radius:2px 2px 0 0;min-height:2px}}
</style></head><body>
{"<header><div class='wrap'><div class='brandrow'><span class='brand'>" + APP_NAME +
 " панель</span><span class='tag'>" + APP_VERSION + "</span><span class='tag'>" + ENV +
 "</span></div><nav>" + nav + "</nav></div></header>" if uid else ""}
<main><div class="wrap">{body}</div></main>
</body></html>""")


# ─── Доступ ──────────────────────────────────────────────────────────────────

def session_uid(request):
    uid = read_session(request.cookies.get(COOKIE, ""))
    return uid if uid and is_admin(uid) else None


def guard(handler):
    async def wrapper(request):
        uid = session_uid(request)
        if not uid:
            raise web.HTTPFound("/login")
        return await handler(request, uid)
    return wrapper


async def to_thread(fn, *a, **kw):
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **kw))


# ─── Вхід і вихід ────────────────────────────────────────────────────────────

async def h_login(request):
    token = request.query.get("t", "")
    if not token:
        return page("Вхід", """<div class="login"><div class="card">
          <h1>Потрібен одноразовий код</h1>
          <p class="note">Відкрийте бот авторизації та надішліть команду
          <b>/panel</b>. Він видасть посилання, яке діє п'ять хвилин
          і спрацьовує один раз.</p></div></div>""")

    uid = await to_thread(db.login_token_consume, token)
    if not uid or not is_admin(uid):
        return page("Вхід", """<div class="login"><div class="card">
          <h1>Посилання не спрацювало</h1>
          <p class="note">Код прострочений, уже використаний або виданий
          не адміністратору. Запросіть новий через /panel.</p></div></div>""")

    await to_thread(db.audit, uid, "panel_login", uid)
    resp = web.HTTPFound("/")
    resp.set_cookie(COOKIE, issue_session(uid), httponly=True, samesite="Lax",
                    max_age=PANEL_SESSION_HOURS * 3600,
                    secure=(ENV == "production"))
    raise resp


async def h_logout(request):
    uid = session_uid(request)
    if uid:
        await to_thread(db.audit, uid, "panel_logout", uid)
    resp = web.HTTPFound("/login")
    resp.del_cookie(COOKIE)
    raise resp


# ─── Огляд ───────────────────────────────────────────────────────────────────

@guard
async def h_dashboard(request, uid):
    stats = await to_thread(db.global_stats)
    signups = await to_thread(db.signups_by_day, 14)
    recent = await to_thread(db.audit_list, 8)

    peak = max([n for _, n in signups] or [1])
    spark = "".join(f'<div style="height:{max(4, int(n / peak * 54))}px" '
                    f'title="{esc(d)}: {n}"></div>' for d, n in signups) or \
        '<div style="height:4px"></div>'

    conv = round(stats["premium"] / stats["users"] * 100, 1) if stats["users"] else 0

    body = f"""<h1>Огляд</h1>
    <div class="cards">
      <div class="card"><b>{stats['users']}</b><span>користувачів</span></div>
      <div class="card"><b>{stats['premium']}</b><span>з Premium · {conv}%</span></div>
      <div class="card"><b>{stats['plays']}</b><span>прослуховувань</span></div>
      <div class="card"><b>{stats['playlists']}</b><span>плейлистів</span></div>
    </div>

    <h2>Нові користувачі, 14 днів</h2>
    <div class="card"><div class="spark">{spark}</div>
      <div class="note">Наведіть на стовпчик, щоб побачити дату й кількість.</div></div>

    <h2>Останні дії</h2>
    <table><tr><th>Коли</th><th>Хто</th><th>Дія</th><th>Ціль</th></tr>""" + \
        "".join(f"<tr><td class='mono'>{esc(str(e.get('created_at'))[:16])}</td>"
                f"<td class='mono'>{esc(e.get('actor'))}</td>"
                f"<td>{esc(e.get('action'))}</td>"
                f"<td class='mono'>{esc(e.get('target'))} {esc(e.get('details'))}</td></tr>"
                for e in recent) + """</table>"""
    return page("Огляд", body, uid, "/")


# ─── Користувачі ─────────────────────────────────────────────────────────────

@guard
async def h_users(request, uid):
    search = request.query.get("q", "").strip()
    page_n = max(0, int(request.query.get("p", 0) or 0))
    items, total = await to_thread(db.users_page, page_n * 50, 50, search)

    rows_html = ""
    for u in items:
        pro = db.is_premium(int(u["uid"]))
        until = str(u.get("premium_until") or "")[:10]
        rows_html += f"""<tr>
          <td class="mono">{esc(u['uid'])}</td>
          <td>{esc(u.get('first_name'))}
            {'<span class="mono">@' + esc(u.get('username')) + '</span>' if u.get('username') else ''}</td>
          <td><span class="pill {'pro' if pro else ''}">{'Premium' if pro else 'Free'}</span>
            {('<div class="note">до ' + esc(until) + '</div>') if pro and until else ''}</td>
          <td class="mono">{esc(str(u.get('created_at'))[:10])}</td>
          <td>
            <form class="inline" method="post" action="/users/premium">
              <input type="hidden" name="uid" value="{esc(u['uid'])}">
              <input type="number" name="days" value="30" min="1" max="3650" style="width:76px">
              <button>Видати</button>
            </form>
            <form class="inline" method="post" action="/users/revoke">
              <input type="hidden" name="uid" value="{esc(u['uid'])}">
              <button class="ghost">Зняти</button>
            </form>
          </td></tr>"""

    pages = ""
    if total > 50:
        last = (total - 1) // 50
        pages = f"""<div class="bar" style="margin-top:16px">
          {'<a href="/users?p=' + str(page_n-1) + '&q=' + esc(search) + '"><button class="ghost">← Назад</button></a>' if page_n > 0 else ''}
          <span class="note">сторінка {page_n+1} з {last+1}</span>
          {'<a href="/users?p=' + str(page_n+1) + '&q=' + esc(search) + '"><button class="ghost">Далі →</button></a>' if page_n < last else ''}
        </div>"""

    body = f"""<h1>Користувачі · {total}</h1>
    <form class="bar" method="get" action="/users">
      <input name="q" placeholder="ID, ім'я або @username" value="{esc(search)}" style="min-width:260px">
      <button>Знайти</button>
      {'<a href="/users"><button class="ghost" type="button">Скинути</button></a>' if search else ''}
    </form>
    <table><tr><th>ID</th><th>Хто</th><th>План</th><th>Реєстрація</th><th>Дії</th></tr>
    {rows_html or '<tr><td colspan="5" class="note">Нікого не знайдено.</td></tr>'}</table>
    {pages}"""
    return page("Користувачі", body, uid, "/users")


@guard
async def h_users_premium(request, uid):
    data = await request.post()
    target = int(data.get("uid", 0))
    days = max(1, min(3650, int(data.get("days", 30))))
    await to_thread(db.get_user, target)
    await to_thread(db.set_premium, target, True, days)
    await to_thread(db.audit, uid, "grant_premium", target, f"{days}д")
    await to_thread(db.auth_event, target, "premium_granted", f"panel {uid}")
    raise web.HTTPFound("/users")


@guard
async def h_users_revoke(request, uid):
    data = await request.post()
    target = int(data.get("uid", 0))
    await to_thread(db.set_premium, target, False)
    await to_thread(db.audit, uid, "revoke_premium", target)
    raise web.HTTPFound("/users")


# ─── Промокоди ───────────────────────────────────────────────────────────────

@guard
async def h_promo(request, uid):
    codes = await to_thread(db.promo_list)
    rows_html = "".join(f"""<tr>
      <td class="mono">{esc(c['code'])}</td>
      <td>{esc(c['days'])} днів</td>
      <td>{esc(c['uses_left'])}</td>
      <td class="mono">{esc(str(c.get('created_at'))[:16])}</td>
      <td><form class="inline" method="post" action="/promo/delete">
        <input type="hidden" name="code" value="{esc(c['code'])}">
        <button class="warn">Видалити</button></form></td></tr>""" for c in codes)

    body = f"""<h1>Промокоди</h1>
    <form class="bar" method="post" action="/promo/create">
      <input name="code" placeholder="LAUNCH30" required style="text-transform:uppercase">
      <input name="days" type="number" value="30" min="1" max="3650" style="width:90px" title="днів">
      <input name="uses" type="number" value="100" min="1" max="100000" style="width:96px" title="активацій">
      <button>Створити</button>
    </form>
    <table><tr><th>Код</th><th>Дає</th><th>Лишилось</th><th>Створено</th><th></th></tr>
    {rows_html or '<tr><td colspan="5" class="note">Кодів немає.</td></tr>'}</table>
    <div class="note">Вичерпані коди воркер прибирає автоматично.</div>"""
    return page("Промокоди", body, uid, "/promo")


@guard
async def h_promo_create(request, uid):
    data = await request.post()
    code = str(data.get("code", "")).strip().upper()
    if code:
        days = max(1, int(data.get("days", 30)))
        uses = max(1, int(data.get("uses", 1)))
        try:
            await to_thread(db.promo_create, code, days, uses)
            await to_thread(db.audit, uid, "create_promo", code, f"{days}д × {uses}")
        except Exception as e:
            log.warning("Код %s не створено: %s", code, e)
    raise web.HTTPFound("/promo")


@guard
async def h_promo_delete(request, uid):
    data = await request.post()
    code = str(data.get("code", ""))
    await to_thread(db.promo_delete, code)
    await to_thread(db.audit, uid, "delete_promo", code)
    raise web.HTTPFound("/promo")


# ─── Журнал ──────────────────────────────────────────────────────────────────

@guard
async def h_audit(request, uid):
    entries = await to_thread(db.audit_list, 200)
    rows_html = "".join(f"""<tr>
      <td class="mono">{esc(str(e.get('created_at'))[:16])}</td>
      <td class="mono">{esc(e.get('actor'))}</td>
      <td>{esc(e.get('action'))}</td>
      <td class="mono">{esc(e.get('target'))}</td>
      <td class="note">{esc(e.get('details'))}</td></tr>""" for e in entries)
    body = f"""<h1>Журнал дій</h1>
    <table><tr><th>Коли</th><th>Хто</th><th>Дія</th><th>Ціль</th><th>Деталі</th></tr>
    {rows_html or '<tr><td colspan="5" class="note">Журнал порожній.</td></tr>'}</table>
    <div class="note">Записується кожна зміна прав і кожен вхід у панель.
    Журнал не редагується з інтерфейсу — це і є його сенс.</div>"""
    return page("Журнал", body, uid, "/audit")


async def h_health(request):
    return web.json_response({"ok": True, "service": "panel", "version": APP_VERSION})


def build_app():
    app = web.Application()
    r = app.router
    r.add_get("/healthz", h_health)
    r.add_get("/login", h_login)
    r.add_get("/logout", h_logout)
    r.add_get("/", h_dashboard)
    r.add_get("/users", h_users)
    r.add_post("/users/premium", h_users_premium)
    r.add_post("/users/revoke", h_users_revoke)
    r.add_get("/promo", h_promo)
    r.add_post("/promo/create", h_promo_create)
    r.add_post("/promo/delete", h_promo_delete)
    r.add_get("/audit", h_audit)
    return app


def main():
    if PANEL_SECRET == "change-me-in-railway-variables":
        log.warning("PANEL_SECRET не змінений — сесії можна підробити. "
                    "Згенеруйте: python -c \"import secrets;print(secrets.token_hex(32))\"")
    db.init_engine()
    log.info("%s panel %s на порту %s", APP_NAME, APP_VERSION, PORT)
    web.run_app(build_app(), host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
