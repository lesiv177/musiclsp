[README.md](https://github.com/user-attachments/files/32473011/README.md)
# MusicLSP 5.0

Один репозиторій, чотири сервіси на Railway, спільне ядро.

```
core/                спільний код — база, джерела музики, автентифікація
├── config.py        усі змінні середовища в одному місці
├── db.py            схема і запити (PostgreSQL або SQLite)
├── providers.py     Jamendo, Audius, Internet Archive
└── auth.py          підпис Telegram, сесії панелі

services/
├── bot/             основний бот + API для Mini App
├── auth/            бот доступу: Premium, промокоди, вхід у панель
├── panel/           веб-адмінка
├── worker/          фонові задачі
└── migrate/         міграції, окремо від запуску

webapp/index.html    плеєр → GitHub Pages
```

## Чому один репозиторій, а не чотири

Усі сервіси працюють з однією схемою бази. Додали колонку в `users` — у монорепо
це один коміт, у чотирьох репозиторіях це чотири синхронні PR, між якими частина
сервісів лежить зі зламаним SQL. Railway вміє піднімати кілька сервісів з одного
репо: вони відрізняються лише командою запуску.

---

## Крок 1. Підготовка

Старі репозиторії **заархівуйте, не видаляйте** (Settings → Archive this repository).
Перед цим зробіть локальні копії:

```bash
git clone --mirror https://github.com/ВАШ_НІК/старий-репо.git backup/старий.git
```

Потрібні токени й ключі:

| Що | Де взяти |
|---|---|
| `BOT_TOKEN` | @BotFather → новий бот |
| `AUTH_BOT_TOKEN` | @BotFather → ще один бот, окремий |
| `JAMENDO_CLIENT_ID` | devportal.jamendo.com, безкоштовно |
| `PANEL_SECRET` | `python -c "import secrets;print(secrets.token_hex(32))"` |
| `ADMIN_IDS` | ваш Telegram ID (@userinfobot) |

## Крок 2. GitHub

```bash
git init
git add .
git commit -m "MusicLSP 5.0 — monorepo"
git remote add origin https://github.com/ВАШ_НІК/musiclsp.git
git push -u origin main
```

**Settings → Pages → Deploy from branch → main → /root.**
Плеєр опиниться на `https://ВАШ_НІК.github.io/musiclsp/webapp/`.

## Крок 3. База даних

У Railway: **New Project → Deploy from GitHub repo**, далі
**+ New → Database → PostgreSQL**. Базу створюйте **до** сервісів.

## Крок 4. Чотири сервіси

Для кожного: **+ New → GitHub Repo → той самий репозиторій**. Відрізняються
лише **Settings → Deploy → Start Command**:

| Сервіс | Start Command | Домен |
|---|---|---|
| `bot` | `python -m services.bot.main` | так |
| `auth` | `python -m services.auth.main` | ні |
| `panel` | `python -m services.panel.main` | так |
| `worker` | `python -m services.worker.main` | ні |

Домен: **Settings → Networking → Generate Domain**. Воркеру й боту авторизації
домен не потрібен — вони нічого не слухають.

Сервісу `bot` додайте **Pre-deploy Command**:

```
python -m services.migrate.main
```

Це єдине місце, де створюється схема. Решта сервісів її не чіпають — інакше
чотири процеси почнуть створювати таблиці одночасно й ловитимуть гонки.

## Крок 5. Змінні

`DATABASE_URL` підключайте **Reference Variable** (кнопка в Railway), а не копією.
Скопійований рядок застаріє, щойно Railway оновить пароль бази.

Спільні для всіх чотирьох: `DATABASE_URL`, `ADMIN_IDS`, `JAMENDO_CLIENT_ID`, `ENV=production`.

| Сервіс | Додатково |
|---|---|
| bot | `BOT_TOKEN`, `WEB_APP_URL`, `API_URL` |
| auth | `AUTH_BOT_TOKEN`, `BOT_TOKEN`, `PANEL_URL` |
| panel | `PANEL_SECRET`, `PANEL_SESSION_HOURS=12` |
| worker | `BOT_TOKEN`, `WORKER_INTERVAL=600` |

`API_URL` — домен сервісу `bot`. `PANEL_URL` — домен сервісу `panel`.
Слеш у кінці не ставте.

## Крок 6. Перевірка

```
https://bot-домен.up.railway.app/api/health
{"ok":true,"version":"5.0","sources":{"jamendo":true,"audius":true,"archive":true}}

https://panel-домен.up.railway.app/healthz
{"ok":true,"service":"panel","version":"5.0"}
```

`jamendo:false` — не підхопився ключ. `audius:false` — тимчасово недоступні
discovery-ноди, минає само.

## Як увійти в панель

У боті авторизації надішліть **/panel**. Він видасть посилання, яке діє
п'ять хвилин і спрацьовує **один раз**. Пароля немає взагалі — це навмисно:
паролі витікають, а тут доступ прив'язаний до Telegram-акаунта зі списку
`ADMIN_IDS`. Посилання нікому не пересилайте.

Панель уміє: огляд із графіком реєстрацій, пошук користувачів за ID або іменем,
видача та зняття Premium, промокоди, журнал дій. Кожна зміна прав пишеться
в аудит і з інтерфейсу не стирається.

## Локально

```bash
pip install -r requirements.txt
cp .env.example .env          # заповніть
export $(grep -v '^#' .env | xargs)
python -m services.migrate.main
python -m services.bot.main
```

Без `DATABASE_URL` працює SQLite у файлі `musiclsp.db`.

## Порядок деплою при зміні схеми

1. Спершу міграція (redeploy сервісу `bot` — pre-deploy виконається сам).
2. Потім решта сервісів.

Навпаки робити не можна: сервіс із новим кодом звернеться до колонки,
якої ще немає.

## Про каталог

Jamendo — незалежні артисти під Creative Commons, а не світові чарти.
Мейнстриму там не буде, і це не помилка інтеграції. Кожен трек показує
ліцензію й посилання на оригінал, завантаження доступне лише там, де автор
його дозволив. Ліцензії з позначкою NC забороняють комерційне використання —
якщо робитимете платну підписку, беріть гроші за функції плеєра,
а не за доступ до музики.

## Ліцензія

Код — MIT. Музика належить своїм авторам.
