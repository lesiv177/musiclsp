# -*- coding: utf-8 -*-
"""
MusicLSP Connect — передача відтворення між пристроями, як Spotify Connect.

Модель (та сама, що в Spotify):
  • Кожен відкритий плеєр = «пристрій» зі своїм WebSocket-зʼєднанням. Пристрій
    «у мережі», доки зʼєднання живе — жодних таймерів «активності».
  • На акаунті є рівно один АКТИВНИЙ пристрій (owner): саме він відтворює звук і
    періодично повідомляє стан (трек, черга, секунда, пауза).
  • Решта пристроїв — пульти: бачать той самий стан у реальному часі й
    надсилають команди (play/pause/next/previous/seek/volume) активному пристрою.
  • Передача (transfer): цільовий пристрій отримує повний стан, запускає трек із
    тієї ж секунди й підтверджує (ack). Лише ПІСЛЯ підтвердження він стає
    активним, а попередній ставиться на паузу — музика не обривається, якщо
    цільовий пристрій не зміг стартувати (iOS заборонив автоплей тощо).
  • Якщо користувач сам запускає трек на пристрої (claim) — цей пристрій одразу
    стає активним, а інші перетворюються на пульти.
  • Якщо активний пристрій зник, стан лишається («Остання сесія»): будь-який
    інший пристрій може продовжити з тієї ж секунди.

Модуль не залежить від aiohttp: транспорт — це функція send(dict), яку дає
шар API. Тому логіку можна тестувати без мережі.
"""

import time
import math
import asyncio
import logging

logger = logging.getLogger(__name__)

MAX_QUEUE = 30
MAX_DEVICES = 8
TRANSFER_TIMEOUT = 20          # с, поки цільовий пристрій має підтвердити старт
TRANSFER_PENDING_TIMEOUT = 75  # с, якщо пристрій просить користувача торкнутись екрана
PERSIST_EVERY = 15             # с, не частіше записуємо стан у БД

ALLOWED_ACTIONS = {"transfer", "play", "pause", "next", "previous", "seek",
                   "volume", "playqueue", "resume"}

_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_TRACK_STR = ("title", "artist", "album", "cover", "duration_str", "source",
              "source_label", "license", "license_short", "source_url")


def valid_device_id(value):
    value = str(value or "")
    return bool(value and len(value) <= 80 and all(ch in _ID_CHARS for ch in value))


def _num(value, default=0.0, lo=None, hi=None):
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(v):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def clean_track(raw):
    """Лише безпечні поля треку — решту (і довільні ключі) відкидаємо."""
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    out = {"id": str(raw["id"])[:160]}
    for key in _TRACK_STR:
        val = raw.get(key)
        if isinstance(val, str):
            out[key] = val[:2000 if key in ("cover", "source_url") else 240]
    out["duration"] = int(_num(raw.get("duration"), 0, 0, 86400))
    out["downloadable"] = bool(raw.get("downloadable"))
    return out


def clean_state(raw):
    """Стан відтворення з довільного JSON → гарантовано безпечний словник."""
    if not isinstance(raw, dict):
        return None
    queue_raw = raw.get("queue") if isinstance(raw.get("queue"), list) else []
    queue = [t for t in (clean_track(x) for x in queue_raw[:MAX_QUEUE]) if t]
    track = clean_track(raw.get("track"))
    index = int(_num(raw.get("index"), 0, 0, max(0, len(queue) - 1)))
    if queue and (track is None or queue[index]["id"] != track["id"]):
        # довіряємо черзі+індексу, якщо трек не збігається
        found = next((i for i, t in enumerate(queue) if track and t["id"] == track["id"]), None)
        if found is not None:
            index = found
        elif track is None:
            track = queue[index]
        else:
            queue = [track]
            index = 0
    if track is None:
        return None
    if not queue:
        queue = [track]
        index = 0
    return {
        "track": track, "queue": queue, "index": index,
        "position": _num(raw.get("position"), 0, 0, 86400),
        "duration": _num(raw.get("duration"), track.get("duration", 0), 0, 86400),
        "playing": raw.get("playing") is True,
        "volume": _num(raw.get("volume"), 1, 0, 1),
        "volume_ok": raw.get("volume_ok") is not False,
        "shuffle": raw.get("shuffle") is True,
        "repeat": raw.get("repeat") if raw.get("repeat") in ("off", "all", "one") else "off",
    }


class Device:
    __slots__ = ("id", "name", "platform", "send", "volume_ok", "since")

    def __init__(self, dev_id, name, platform, send, volume_ok=True):
        self.id = dev_id
        self.name = (name or "Пристрій")[:60]
        self.platform = (platform or "web")[:40]
        self.send = send
        self.volume_ok = volume_ok
        self.since = time.time()


class Account:
    def __init__(self, uid):
        self.uid = uid
        self.devices = {}        # dev_id -> Device
        self.owner = ""          # активний пристрій ("" — активного немає)
        self.state = None        # останній відомий стан (clean_state)
        self.state_at = 0.0      # монотонний час отримання стану
        self.pending = None      # передача, що триває
        self.persisted_at = 0.0


class Hub:
    def __init__(self, load=None, persist=None, clock=time.monotonic):
        self.accounts = {}
        self._load = load            # async (uid) -> dict|None
        self._persist = persist      # async (uid, state_dict|None) -> None
        self._now = clock
        self._locks = {}
        self._transfer_seq = 0

    # ── допоміжне ────────────────────────────────────────────────────────────

    def _lock(self, uid):
        lock = self._locks.get(uid)
        if lock is None:
            lock = self._locks[uid] = asyncio.Lock()
        return lock

    async def _account(self, uid):
        acc = self.accounts.get(uid)
        if acc is None:
            acc = self.accounts[uid] = Account(uid)
            if self._load:
                try:
                    saved = await self._load(uid)
                    state = clean_state(saved)
                    if state:
                        state["playing"] = False       # після рестарту нічого не грає
                        acc.state, acc.state_at = state, self._now()
                except Exception as e:                 # БД не повинна ламати зʼєднання
                    logger.warning("Connect: не вдалося прочитати збережений стан: %s", e)
        return acc

    def _position_now(self, acc):
        """Позиція зараз із урахуванням часу, що минув від останнього стану."""
        st = acc.state
        if not st:
            return 0.0
        pos = st["position"]
        if st["playing"]:
            pos += max(0.0, self._now() - acc.state_at)
        dur = st.get("duration") or 0
        return min(pos, dur) if dur else pos

    def _device_view(self, acc):
        return [{"id": d.id, "name": d.name, "platform": d.platform,
                 "owner": d.id == acc.owner, "volume_ok": d.volume_ok}
                for d in sorted(acc.devices.values(), key=lambda x: x.since)]

    def _snapshot(self, acc):
        st = dict(acc.state) if acc.state else None
        if st is not None:
            st["position"] = self._position_now(acc)       # вже «на зараз»
        return {"type": "snapshot", "devices": self._device_view(acc),
                "owner": acc.owner, "state": st,
                "transfer": acc.pending["to"] if acc.pending else ""}

    async def _send(self, acc, dev, msg):
        try:
            await dev.send(msg)
            return True
        except Exception:
            if acc.devices.get(dev.id) is dev:
                acc.devices.pop(dev.id, None)
            return False

    async def _broadcast(self, acc, msg, skip=None):
        for dev in list(acc.devices.values()):
            if dev.id != skip:
                await self._send(acc, dev, msg)

    async def _broadcast_snapshot(self, acc):
        await self._broadcast(acc, self._snapshot(acc))

    async def _error(self, acc, dev_id, code, message, **extra):
        dev = acc.devices.get(dev_id)
        if dev:
            await self._send(acc, dev, {"type": "error", "code": code,
                                        "message": message, **extra})

    def _save(self, acc, force=False):
        if not self._persist:
            return
        now = time.time()
        if not force and now - acc.persisted_at < PERSIST_EVERY:
            return
        acc.persisted_at = now
        st = None
        if acc.state:
            st = dict(acc.state)
            st["position"] = self._position_now(acc)
        try:
            asyncio.get_running_loop().create_task(self._persist(acc.uid, st))
        except RuntimeError:
            pass

    # ── зʼєднання ────────────────────────────────────────────────────────────

    async def connect(self, uid, dev_id, name, platform, send, volume_ok=True):
        async with self._lock(uid):
            acc = await self._account(uid)
            old = acc.devices.get(dev_id)
            dev = Device(dev_id, name, platform, send, volume_ok)
            if old:
                dev.since = old.since
            acc.devices[dev_id] = dev
            while len(acc.devices) > MAX_DEVICES:        # найстарші відвалюються
                victim = min((d for d in acc.devices.values() if d.id not in (dev_id, acc.owner)),
                             key=lambda d: d.since, default=None)
                if not victim:
                    break
                acc.devices.pop(victim.id, None)
            await self._send(acc, dev, {"type": "ready", "device_id": dev_id})
            await self._broadcast_snapshot(acc)
            return dev

    async def disconnect(self, uid, dev_id, send):
        async with self._lock(uid):
            acc = self.accounts.get(uid)
            if not acc:
                return
            dev = acc.devices.get(dev_id)
            if not dev or dev.send != send:          # вже замінений новим зʼєднанням
                return
            acc.devices.pop(dev_id, None)
            if acc.pending and acc.pending["to"] == dev_id:
                await self._fail_transfer(acc, "target_lost", "Пристрій відключився")
            if acc.owner == dev_id:
                # Активний пристрій зник: стан лишаємо, але без «активного».
                if acc.state:
                    acc.state["position"] = self._position_now(acc)
                    acc.state["playing"] = False
                    acc.state_at = self._now()
                acc.owner = ""
                self._save(acc, force=True)
            if acc.devices:
                await self._broadcast_snapshot(acc)
            else:
                self.accounts.pop(uid, None)
                self._locks.pop(uid, None)

    # ── повідомлення від пристроїв ───────────────────────────────────────────

    async def handle(self, uid, dev_id, msg):
        """Єдина точка входу для повідомлень зі зʼєднання."""
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        async with self._lock(uid):
            acc = self.accounts.get(uid)
            if not acc or dev_id not in acc.devices:
                return
            if kind == "state":
                await self._on_state(acc, dev_id, msg.get("state"))
            elif kind == "claim":
                await self._on_claim(acc, dev_id, msg.get("state"))
            elif kind == "cmd":
                await self._on_cmd(acc, dev_id, msg)
            elif kind == "ack":
                await self._on_ack(acc, dev_id, msg)
            elif kind == "hello":
                dev = acc.devices[dev_id]
                dev.name = str(msg.get("name") or dev.name)[:60]
                dev.volume_ok = msg.get("volume_ok") is not False
                await self._broadcast_snapshot(acc)

    async def _on_state(self, acc, dev_id, raw):
        if acc.owner != dev_id:
            return                                       # лише активний пристрій пише стан
        new = clean_state(raw)
        if not new:
            return
        changed = (not acc.state or acc.state["track"]["id"] != new["track"]["id"]
                   or acc.state["playing"] != new["playing"])
        acc.state, acc.state_at = new, self._now()
        await self._broadcast(acc, {"type": "state", "owner": dev_id, "state": new}, skip=dev_id)
        self._save(acc, force=changed)

    async def _on_claim(self, acc, dev_id, raw):
        """Користувач сам запустив відтворення на цьому пристрої — він стає активним."""
        new = clean_state(raw)
        if not new:
            return
        old = acc.owner
        if acc.pending:
            await self._fail_transfer(acc, "superseded", "Відтворення запущено на іншому пристрої")
        acc.owner, acc.state, acc.state_at = dev_id, new, self._now()
        if old and old != dev_id and old in acc.devices:
            await self._send(acc, acc.devices[old], {"type": "exec", "action": "stop", "payload": {}})
        await self._broadcast_snapshot(acc)
        self._save(acc, force=True)

    async def _on_cmd(self, acc, dev_id, msg):
        action = msg.get("action")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        cid = str(msg.get("id") or "")[:40]
        if action not in ALLOWED_ACTIONS:
            return await self._error(acc, dev_id, "bad_action", "Невідома команда", id=cid)

        if action in ("transfer", "resume"):
            to = dev_id if action == "resume" else str(payload.get("to") or "")
            return await self._start_transfer(acc, dev_id, to, cid)

        owner = acc.devices.get(acc.owner) if acc.owner else None
        if owner is None:
            return await self._error(acc, dev_id, "no_active_device",
                                     "Немає активного пристрою", id=cid)
        if owner.id == dev_id:
            return                                       # власні команди виконуються локально

        # Оптимістичне оновлення, щоб пульти реагували миттєво.
        st = acc.state
        if st:
            if action in ("pause", "play"):
                st["position"] = self._position_now(acc)
                st["playing"] = action == "play"
                acc.state_at = self._now()
            elif action == "seek":
                st["position"] = _num(payload.get("position"), 0, 0, 86400)
                acc.state_at = self._now()
            elif action == "volume":
                st["volume"] = _num(payload.get("volume"), 1, 0, 1)
            await self._broadcast(acc, {"type": "state", "owner": owner.id, "state": st}, skip=owner.id)

        safe = {}
        if action == "seek":
            safe["position"] = _num(payload.get("position"), 0, 0, 86400)
        elif action == "volume":
            safe["volume"] = _num(payload.get("volume"), 1, 0, 1)
        elif action == "playqueue":
            new = clean_state({**payload, "playing": True})
            if not new:
                return await self._error(acc, dev_id, "bad_payload", "Немає треку", id=cid)
            safe = {"state": new}
        await self._send(acc, owner, {"type": "exec", "id": cid, "action": action, "payload": safe})

    # ── передача відтворення ─────────────────────────────────────────────────

    async def _start_transfer(self, acc, requester, to, cid):
        if to not in acc.devices:
            return await self._error(acc, requester, "device_offline",
                                     "Пристрій зараз не в мережі", id=cid)
        if to == acc.owner and acc.state and acc.state["playing"]:
            return await self._broadcast_snapshot(acc)    # уже там і грає
        if not acc.state:
            return await self._error(acc, requester, "nothing_to_play",
                                     "Спершу увімкніть якийсь трек", id=cid)
        if acc.pending:
            await self._fail_transfer(acc, "superseded", "Нова передача")
        payload = dict(acc.state)
        payload["position"] = self._position_now(acc)
        # Грало — продовжуємо грати; було на паузі — відновлюємо з паузою? Spotify
        # завжди запускає відтворення на цільовому пристрої, робимо так само.
        payload["playing"] = True
        # A per-hub monotonic sequence prevents same-millisecond transfer ID
        # collisions, so a late ACK from a superseded attempt cannot complete
        # a newer handoff to the same device.
        self._transfer_seq += 1
        tid = f"t{self._transfer_seq:x}"
        deadline = self._now() + TRANSFER_TIMEOUT
        acc.pending = {"id": tid, "to": to, "requester": requester, "cid": cid,
                       "deadline": deadline}
        acc.pending["task"] = asyncio.get_running_loop().create_task(self._watch(acc, tid))
        await self._send(acc, acc.devices[to], {"type": "exec", "id": tid, "action": "start",
                                                "payload": {"state": payload}})
        await self._broadcast_snapshot(acc)               # UI показує «Передаю…»

    async def _watch(self, acc, tid):
        try:
            while True:
                p = acc.pending
                if not p or p["id"] != tid:
                    return
                left = p["deadline"] - self._now()
                if left <= 0:
                    break
                await asyncio.sleep(min(left, 1.0))
        except asyncio.CancelledError:
            return
        async with self._lock(acc.uid):
            if acc.pending and acc.pending["id"] == tid:
                await self._fail_transfer(acc, "timeout", "Пристрій не відповів")
                await self._broadcast_snapshot(acc)

    async def _fail_transfer(self, acc, code, message):
        p = acc.pending
        if not p:
            return
        acc.pending = None
        task = p.get("task")
        if task and task is not asyncio.current_task():
            task.cancel()
        await self._error(acc, p["requester"], code, message, id=p.get("cid", ""), transfer=True)
        target = acc.devices.get(p["to"])
        if target and code in ("timeout", "superseded"):
            await self._send(acc, target, {"type": "exec", "action": "cancel_start",
                                           "payload": {"id": p["id"]}})

    async def _on_ack(self, acc, dev_id, msg):
        p = acc.pending
        if not p or p["id"] != str(msg.get("id") or "") or p["to"] != dev_id:
            return
        if msg.get("pending") is True:                   # потрібен дотик користувача
            p["deadline"] = self._now() + TRANSFER_PENDING_TIMEOUT
            await self._error(acc, p["requester"], "needs_tap",
                              "Торкніться екрана на цьому пристрої, щоб почати відтворення",
                              id=p.get("cid", ""), transfer=True, info=True, device=dev_id)
            return
        if msg.get("ok") is not True:
            reason = str(msg.get("reason") or "failed")[:40]
            await self._fail_transfer(acc, reason, "Не вдалося почати відтворення на пристрої")
            await self._broadcast_snapshot(acc)
            return
        # Успіх: цільовий пристрій реально грає — тепер він активний.
        old = acc.owner
        task = p.get("task")
        acc.pending = None
        if task:
            task.cancel()
        new = clean_state(msg.get("state"))
        if new:
            acc.state = new
        elif acc.state:
            acc.state["playing"] = True
        acc.state_at = self._now()
        acc.owner = dev_id
        if old and old != dev_id and old in acc.devices:
            await self._send(acc, acc.devices[old], {"type": "exec", "action": "stop", "payload": {}})
        await self._broadcast_snapshot(acc)
        self._save(acc, force=True)

    # ── службове ─────────────────────────────────────────────────────────────

    def devices_for(self, uid):
        acc = self.accounts.get(uid)
        return self._snapshot(acc) if acc else {"type": "snapshot", "devices": [],
                                                "owner": "", "state": None, "transfer": ""}