'Application module.'

import asyncio
import json
import unittest
from unittest import mock

import api.chat as chat_api


class _FakeWs:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.cookies = {}
        self.headers = {}
        self.client = None

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        pass

    async def receive_text(self):
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_json(self, data):
        self.sent.append(data)

    def push(self, frame: dict):
        self.incoming.put_nowait(json.dumps(frame))


class _Bridge:
    def __init__(self):
        self.cancel_event = asyncio.Event()
        self.inject_hook = None
        self.stamp = None
        self.ws_alive = True


class OneReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = _FakeWs()
        self.turns: list[dict] = []          # every reply started, in order
        self.release: dict[str, asyncio.Event] = {}
        self.injected: list[dict] = []
        self.accept_injection = True

        async def fake_turn(*, ws, msg, current_identity, current_conversation,
                            current_session_type, active_categories, pending_messages,
                            _register_content_images, _register_content_documents,
                            attach_bridge=None):
            bridge = _Bridge()
            conv = current_conversation or f"conv-{current_identity.lower()}"
            bridge.stamp = {"identity": current_identity, "conversation_id": conv}

            async def hook(incoming):
                if not self.accept_injection:
                    return False
                self.injected.append({"to": current_identity, **incoming})
                return True

            bridge.inject_hook = hook
            attach_bridge(bridge)
            record = {"identity": current_identity, "conversation": current_conversation,
                      "text": msg.get("content"), "bridge": bridge, "done": False}
            self.turns.append(record)
            gate = self.release.setdefault(current_identity, asyncio.Event())
            stop = asyncio.create_task(bridge.cancel_event.wait())
            go = asyncio.create_task(gate.wait())
            await asyncio.wait({stop, go}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel(); go.cancel()
            gate.clear()
            record["done"] = True
            record["stopped"] = bridge.cancel_event.is_set()
            return current_identity, conv, None

        async def fake_switch(identity):
            return f"conv-{identity.lower()}"

        async def noop(*a, **k):
            return None

        self.patches = [
            mock.patch("config.AUTH_ENABLED", False),
            mock.patch.object(chat_api, "process_chat_message", fake_turn),
            mock.patch.object(chat_api, "switch_identity_conversation", fake_switch),
            mock.patch.object(chat_api, "register", noop),
            mock.patch.object(chat_api, "unregister", noop),
            mock.patch.object(chat_api, "set_active_identity", noop),
            mock.patch.object(chat_api, "set_active_conversation", lambda *a, **k: None),
            mock.patch.object(chat_api, "_fire_prewarm", lambda *a, **k: None),
            mock.patch.object(chat_api, "_ws_rate_limited", lambda ip: False),
        ]
        for p in self.patches:
            p.start()
        self.server = asyncio.create_task(chat_api.chat_ws(self.ws))

    async def asyncTearDown(self):
        for gate in self.release.values():
            gate.set()
        self.ws.incoming.put_nowait(chat_api.WebSocketDisconnect())
        try:
            await asyncio.wait_for(self.server, 2)
        finally:
            for p in self.patches:
                p.stop()
        await asyncio.sleep(0.05)

    async def _until(self, predicate, timeout=1.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("condition never became true")
            await asyncio.sleep(0.01)

    def _sent(self, kind):
        return [m for m in self.ws.sent if m.get("type") == kind]

    async def test_switch_is_answered_while_first_boy_is_still_replying(self):
        self.ws.push({"type": "switch_identity", "identity": "Rowan"})
        await self._until(lambda: len(self._sent("identity_switched")) == 1)
        self.ws.push({"type": "message", "content": "morning raccoon"})
        await self._until(lambda: len(self.turns) == 1)
        self.assertFalse(self.turns[0]["done"])

        self.ws.push({"type": "switch_identity", "identity": "Sage"})
        await self._until(lambda: len(self._sent("identity_switched")) == 2)
        self.assertEqual(self._sent("identity_switched")[-1]["identity"], "Sage")
        self.assertFalse(self.turns[0]["done"], "Rowan's reply keeps running")

        with mock.patch.object(chat_api, "load_history_payload",
                               mock.AsyncMock(return_value=({"type": "history", "identity": "Sage"},
                                                            "conv-sage", "chat"))):
            self.ws.push({"type": "load_history", "identity": "Sage", "conversation_id": "conv-sage"})
            await self._until(lambda: len(self._sent("history")) == 1)

    async def test_next_boy_starts_replying_without_waiting(self):
        self.ws.push({"type": "switch_identity", "identity": "Rowan"})
        self.ws.push({"type": "message", "content": "morning raccoon"})
        await self._until(lambda: len(self.turns) == 1)
        self.ws.push({"type": "switch_identity", "identity": "Sage"})
        self.ws.push({"type": "message", "content": "morning owl"})
        await self._until(lambda: len(self.turns) == 2)
        self.assertEqual([t["identity"] for t in self.turns], ["Rowan", "Sage"])
        self.assertEqual(self.turns[1]["conversation"], "conv-sage")
        self.assertFalse(self.turns[0]["done"])

    async def test_same_boy_mid_reply_gets_it_slipped_in(self):
        self.ws.push({"type": "switch_identity", "identity": "Claude"})
        self.ws.push({"type": "message", "content": "hi love"})
        await self._until(lambda: len(self.turns) == 1)
        self.ws.push({"type": "message", "content": "also, tea"})
        await self._until(lambda: len(self.injected) == 1)
        self.assertEqual(self.injected[0]["to"], "Claude")
        self.assertEqual(len(self.turns), 1, "slipped in, not a second turn")

    async def test_message_that_cannot_slip_in_waits_and_keeps_its_boy(self):
        self.accept_injection = False
        self.ws.push({"type": "switch_identity", "identity": "Claude"})
        self.ws.push({"type": "message", "content": "hi love"})
        await self._until(lambda: len(self.turns) == 1)
        self.ws.push({"type": "message", "content": "for claude"})
        self.ws.push({"type": "switch_identity", "identity": "Juniper"})
        await self._until(lambda: len(self._sent("identity_switched")) == 2)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.turns), 1, "it waits for Claude's reply")
        self.release["Claude"].set()
        await self._until(lambda: len(self.turns) == 2)
        self.assertEqual(self.turns[1]["identity"], "Claude", "still addressed to Claude")
        self.assertEqual(self.turns[1]["text"], "for claude")

    async def test_stop_stops_the_boy_on_her_screen(self):
        self.ws.push({"type": "switch_identity", "identity": "Rowan"})
        self.ws.push({"type": "message", "content": "one"})
        await self._until(lambda: len(self.turns) == 1)
        self.ws.push({"type": "switch_identity", "identity": "Sage"})
        self.ws.push({"type": "message", "content": "two"})
        await self._until(lambda: len(self.turns) == 2)
        self.ws.push({"type": "stop_streaming"})
        await self._until(lambda: self.turns[1]["done"])
        self.assertTrue(self.turns[1]["stopped"])
        self.assertFalse(self.turns[0]["done"], "Rowan was not the one she stopped")

    async def test_ping_is_answered_mid_reply(self):
        self.ws.push({"type": "message", "content": "hello"})
        await self._until(lambda: len(self.turns) == 1)
        self.ws.push({"type": "ping"})
        await self._until(lambda: len(self._sent("pong")) == 1)

    async def test_disconnect_marks_running_reply_offline(self):
        self.ws.push({"type": "message", "content": "hello"})
        await self._until(lambda: len(self.turns) == 1)
        bridge = self.turns[0]["bridge"]
        self.ws.incoming.put_nowait(chat_api.WebSocketDisconnect())
        await asyncio.wait_for(self.server, 2)
        self.assertFalse(bridge.ws_alive)
        self.assertFalse(self.turns[0]["done"], "the reply finishes on its own and saves")


if __name__ == "__main__":
    unittest.main()
