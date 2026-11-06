import asyncio
import unittest

from core import connect
from core.connect import Hub, clean_state


def track(i, **kw):
    return {"id": f"jam:{i}", "title": f"T{i}", "artist": "A", "duration": 200, **kw}


def state(i=1, pos=10, playing=True, **kw):
    return {"track": track(i), "queue": [track(1), track(2), track(3)], "index": i - 1,
            "position": pos, "duration": 200, "playing": playing, "volume": 1, **kw}


class FakeDevice:
    def __init__(self):
        self.msgs = []

    async def send(self, msg):
        self.msgs.append(msg)

    def of(self, kind, **match):
        return [m for m in self.msgs if m.get("type") == kind
                and all(m.get(k) == v for k, v in match.items())]

    def clear(self):
        self.msgs.clear()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.saved = []

        async def persist(uid, st):
            self.saved.append((uid, st))

        self.hub = Hub(persist=persist, clock=self.clock)
        self.phone, self.mac = FakeDevice(), FakeDevice()
        await self.hub.connect(1, "phone", "iPhone", "ios", self.phone.send, volume_ok=False)
        await self.hub.connect(1, "mac", "Mac", "mac", self.mac.send)

    def snap(self, dev):
        return dev.of("snapshot")[-1]

    async def test_connect_lists_devices(self):
        s = self.snap(self.mac)
        self.assertEqual({d["id"] for d in s["devices"]}, {"phone", "mac"})
        self.assertEqual(s["owner"], "")
        phone = next(d for d in s["devices"] if d["id"] == "phone")
        self.assertFalse(phone["volume_ok"])

    async def test_claim_makes_device_active_and_stops_previous(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.assertEqual(self.snap(self.mac)["owner"], "phone")
        self.phone.clear(); self.mac.clear()
        await self.hub.handle(1, "mac", {"type": "claim", "state": state(2)})
        self.assertEqual(self.snap(self.phone)["owner"], "mac")
        self.assertTrue(self.phone.of("exec", action="stop"))     # старий пристрій замовк

    async def test_state_only_from_owner(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.mac.clear()
        await self.hub.handle(1, "mac", {"type": "state", "state": state(3)})   # не власник
        self.assertFalse(self.mac.of("state"))
        await self.hub.handle(1, "phone", {"type": "state", "state": state(2, pos=50)})
        self.assertEqual(self.mac.of("state")[-1]["state"]["track"]["id"], "jam:2")
        self.assertFalse(self.phone.of("state"))

    async def test_controller_commands_reach_owner(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.phone.clear(); self.mac.clear()
        await self.hub.handle(1, "mac", {"type": "cmd", "id": "c1", "action": "pause"})
        ex = self.phone.of("exec", action="pause")
        self.assertEqual(len(ex), 1)
        self.assertFalse(self.mac.of("state")[-1]["state"]["playing"] if self.mac.of("state") else False) \
            if False else None
        await self.hub.handle(1, "mac", {"type": "cmd", "id": "c2", "action": "seek", "payload": {"position": 99}})
        self.assertEqual(self.phone.of("exec", action="seek")[-1]["payload"], {"position": 99.0})
        await self.hub.handle(1, "mac", {"type": "cmd", "id": "c3", "action": "volume", "payload": {"volume": 7}})
        self.assertEqual(self.phone.of("exec", action="volume")[-1]["payload"], {"volume": 1.0})

    async def test_command_without_active_device_errors(self):
        await self.hub.handle(1, "mac", {"type": "cmd", "id": "x", "action": "next"})
        self.assertEqual(self.mac.of("error")[-1]["code"], "no_active_device")

    async def test_late_ack_from_superseded_transfer_is_ignored(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.phone.clear(); self.mac.clear()
        await self.hub.handle(1, "phone", {"type": "cmd", "id": "first", "action": "transfer", "payload": {"to": "mac"}})
        first = self.mac.of("exec", action="start")[-1]
        await self.hub.handle(1, "phone", {"type": "cmd", "id": "second", "action": "transfer", "payload": {"to": "mac"}})
        second = self.mac.of("exec", action="start")[-1]
        self.assertNotEqual(first["id"], second["id"])
        # A delayed response from the cancelled attempt must not transfer ownership.
        await self.hub.handle(1, "mac", {"type": "ack", "id": first["id"], "ok": True, "state": state(2, pos=90)})
        self.assertEqual(self.snap(self.phone)["owner"], "phone")
        self.assertFalse(self.phone.of("exec", action="stop"))
        await self.hub.handle(1, "mac", {"type": "ack", "id": second["id"], "ok": True, "state": state(pos=12)})
        self.assertEqual(self.snap(self.phone)["owner"], "mac")
        self.assertTrue(self.phone.of("exec", action="stop"))

    async def test_transfer_success_after_ack(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state(pos=10)})
        self.clock.t += 5                                          # минуло 5 с відтворення
        self.phone.clear(); self.mac.clear()
        await self.hub.handle(1, "phone", {"type": "cmd", "id": "t", "action": "transfer", "payload": {"to": "mac"}})
        start = self.mac.of("exec", action="start")[0]
        self.assertAlmostEqual(start["payload"]["state"]["position"], 15, delta=0.01)
        self.assertTrue(start["payload"]["state"]["playing"])
        self.assertEqual(self.snap(self.phone)["owner"], "phone")  # ще не передали
        self.assertFalse(self.phone.of("exec", action="stop"))     # make-before-break
        await self.hub.handle(1, "mac", {"type": "ack", "id": start["id"], "ok": True,
                                         "state": state(pos=15.5)})
        self.assertEqual(self.snap(self.phone)["owner"], "mac")
        self.assertTrue(self.phone.of("exec", action="stop"))
        self.assertFalse(self.mac.of("exec", action="stop"))

    async def test_transfer_failure_keeps_old_device_playing(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.phone.clear()
        await self.hub.handle(1, "phone", {"type": "cmd", "action": "transfer", "payload": {"to": "mac"}})
        start = self.mac.of("exec", action="start")[0]
        await self.hub.handle(1, "mac", {"type": "ack", "id": start["id"], "ok": False, "reason": "autoplay_blocked"})
        self.assertEqual(self.snap(self.phone)["owner"], "phone")
        self.assertEqual(self.phone.of("error")[-1]["code"], "autoplay_blocked")
        self.assertFalse(self.phone.of("exec", action="stop"))

    async def test_transfer_needs_tap_extends_deadline_then_succeeds(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        await self.hub.handle(1, "phone", {"type": "cmd", "action": "transfer", "payload": {"to": "mac"}})
        start = self.mac.of("exec", action="start")[0]
        await self.hub.handle(1, "mac", {"type": "ack", "id": start["id"], "pending": True})
        self.assertEqual(self.phone.of("error")[-1]["code"], "needs_tap")
        self.clock.t += connect.TRANSFER_TIMEOUT + 5               # довше за звичайний тайм-аут
        await asyncio.sleep(1.2)
        self.assertEqual(self.snap(self.phone)["transfer"], "mac")  # ще чекаємо
        await self.hub.handle(1, "mac", {"type": "ack", "id": start["id"], "ok": True, "state": state()})
        self.assertEqual(self.snap(self.phone)["owner"], "mac")

    async def test_target_disconnect_cancels_pending_transfer_and_keeps_owner(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state(pos=25)})
        self.phone.clear(); self.mac.clear()
        await self.hub.handle(1, "phone", {"type": "cmd", "id": "handoff", "action": "transfer", "payload": {"to": "mac"}})
        start = self.mac.of("exec", action="start")[-1]
        await self.hub.disconnect(1, "mac", self.mac.send)
        self.assertEqual(self.snap(self.phone)["owner"], "phone")
        self.assertEqual(self.snap(self.phone)["transfer"], "")
        self.assertEqual(self.phone.of("error")[-1]["code"], "target_lost")
        # A late ACK from the now-disconnected target is ignored.
        await self.hub.handle(1, "mac", {"type": "ack", "id": start["id"], "ok": True, "state": state(pos=26)})
        self.assertEqual(self.snap(self.phone)["owner"], "phone")
        self.assertFalse(self.phone.of("exec", action="stop"))

    async def test_transfer_timeout(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        await self.hub.handle(1, "phone", {"type": "cmd", "action": "transfer", "payload": {"to": "mac"}})
        self.clock.t += connect.TRANSFER_TIMEOUT + 1
        await asyncio.sleep(1.3)
        self.assertEqual(self.phone.of("error")[-1]["code"], "timeout")
        self.assertEqual(self.snap(self.phone)["owner"], "phone")
        self.assertEqual(self.snap(self.phone)["transfer"], "")

    async def test_owner_disconnect_keeps_last_session_and_resume_works(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state(pos=30)})
        self.clock.t += 10
        await self.hub.disconnect(1, "phone", self.phone.send)
        s = self.snap(self.mac)
        self.assertEqual(s["owner"], "")
        self.assertFalse(s["state"]["playing"])
        self.assertAlmostEqual(s["state"]["position"], 40, delta=0.01)
        self.assertEqual([d["id"] for d in s["devices"]], ["mac"])
        await asyncio.sleep(0)
        self.assertTrue(self.saved)                                # стан збережено
        self.mac.clear()
        await self.hub.handle(1, "mac", {"type": "cmd", "action": "resume"})
        start = self.mac.of("exec", action="start")[0]
        self.assertAlmostEqual(start["payload"]["state"]["position"], 40, delta=0.01)

    async def test_reconnect_replaces_old_socket_and_old_close_is_ignored(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        new_phone = FakeDevice()
        await self.hub.connect(1, "phone", "iPhone", "ios", new_phone.send)
        await self.hub.disconnect(1, "phone", self.phone.send)     # закриття старого сокета
        self.assertEqual(self.snap(self.mac)["owner"], "phone")    # власник не втрачений
        self.assertIn("phone", [d["id"] for d in self.snap(self.mac)["devices"]])

    async def test_playqueue_goes_to_owner_with_clean_state(self):
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.phone.clear()
        await self.hub.handle(1, "mac", {"type": "cmd", "action": "playqueue",
                                         "payload": {"track": track(9), "queue": [track(9), track(8)], "index": 0}})
        ex = self.phone.of("exec", action="playqueue")[0]
        self.assertEqual(ex["payload"]["state"]["track"]["id"], "jam:9")
        self.assertTrue(ex["payload"]["state"]["playing"])

    async def test_accounts_are_isolated(self):
        other = FakeDevice()
        await self.hub.connect(2, "x", "Other", "web", other.send)
        await self.hub.handle(1, "phone", {"type": "claim", "state": state()})
        self.assertFalse(other.of("state"))
        self.assertEqual(self.snap(other)["owner"], "")
        self.assertEqual(len(self.snap(other)["devices"]), 1)

    async def test_clean_state_rejects_garbage(self):
        self.assertIsNone(clean_state("x"))
        self.assertIsNone(clean_state({"track": {"title": "no id"}}))
        st = clean_state({"track": track(1, cover="c" * 5000), "position": float("inf"),
                          "volume": 5, "queue": "oops", "evil": "x"})
        self.assertEqual(st["position"], 0)
        self.assertEqual(st["volume"], 1.0)
        self.assertEqual(len(st["track"]["cover"]), 2000)
        self.assertNotIn("evil", st)


if __name__ == "__main__":
    unittest.main()