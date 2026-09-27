"""Reusable test live message injection support."""

import asyncio
import json
import logging
import unittest
from unittest.mock import Mock, patch

from services import claude_subprocess
from services.chat_flow import StreamWebSocketBridge


async def _make_session(identity="Avery", conversation_id="conv-inject"):
    with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
        session, _fresh = await claude_subprocess._get_or_spawn(
            identity=identity,
            conversation_id=conversation_id,
            model="claude-opus-5",
            permission_mode="auto",
            effort="medium",
        )
    session.proc = Mock()
    session.proc.stdin = Mock()
    session.proc.poll.return_value = None
    session.dead = False
    return session


class InjectUserMessageTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_writes_one_ndjson_user_line_when_a_turn_is_in_flight(self):
        session = await _make_session()
        async with session.turn_lock:  # a turn is running
            ok = claude_subprocess.inject_user_message(
                "Avery", "conv-inject", "wait - also check the kettle",
            )
        self.assertTrue(ok)
        raw = session.proc.stdin.write.call_args[0][0]
        self.assertIsInstance(raw, bytes)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["type"], "user")
        self.assertEqual(payload["message"]["role"], "user")
        self.assertEqual(
            payload["message"]["content"], "wait - also check the kettle",
        )
        self.assertTrue(session.proc.stdin.flush.called)

    async def test_refuses_when_the_session_is_idle(self):
        """An idle session must NOT be written to: the CLI would hold the
        message and replay it against whatever turn comes next, out of order.
        """
        session = await _make_session(conversation_id="conv-idle")
        ok = claude_subprocess.inject_user_message("Avery", "conv-idle", "hi")
        self.assertFalse(ok)
        self.assertFalse(session.proc.stdin.write.called)

    async def test_refuses_for_a_different_identity_or_conversation(self):
        session = await _make_session(conversation_id="conv-mine")
        async with session.turn_lock:
            self.assertFalse(
                claude_subprocess.inject_user_message("Claude", "conv-mine", "hi")
            )
            self.assertFalse(
                claude_subprocess.inject_user_message("Avery", "conv-theirs", "hi")
            )
        self.assertFalse(session.proc.stdin.write.called)

    async def test_refuses_a_dead_session(self):
        session = await _make_session(conversation_id="conv-dead")
        async with session.turn_lock:
            session.dead = True
            ok = claude_subprocess.inject_user_message("Avery", "conv-dead", "hi")
        self.assertFalse(ok)

    async def test_broken_pipe_returns_false_instead_of_raising(self):
        """A failed write must fall back to the queue, never blow up the
        listener task that owns the whole in-stream websocket read loop.
        """
        session = await _make_session(conversation_id="conv-brokenpipe")
        session.proc.stdin.write.side_effect = BrokenPipeError()
        async with session.turn_lock:
            ok = claude_subprocess.inject_user_message(
                "Avery", "conv-brokenpipe", "hi",
            )
        self.assertFalse(ok)

    async def test_empty_text_is_never_injected(self):
        session = await _make_session(conversation_id="conv-empty")
        async with session.turn_lock:
            self.assertFalse(
                claude_subprocess.inject_user_message("Avery", "conv-empty", "   ")
            )
        self.assertFalse(session.proc.stdin.write.called)

    async def test_no_session_at_all_is_a_clean_false(self):
        """Other backends (PTY, direct API, codex) keep no entry in _sessions,
        so they simply fall through to the queue.
        """
        self.assertFalse(
            claude_subprocess.inject_user_message("Nobody", "conv-none", "hi")
        )


class _FakeWs:
    """Feeds the bridge a scripted set of frames, then blocks forever."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        if self._frames:
            return json.dumps(self._frames.pop(0))
        await asyncio.sleep(3600)


class BridgeInjectHookTests(unittest.IsolatedAsyncioTestCase):
    async def _run_listener(self, bridge, settle=0.2):
        task = asyncio.create_task(bridge.listen_during_stream())
        await asyncio.sleep(settle)
        await bridge.stop_listener(task)

    async def test_injected_message_is_not_also_queued(self):
        bridge = StreamWebSocketBridge(
            _FakeWs([{"type": "message", "content": "one more thing"}]),
            logging.getLogger("test"),
        )
        seen = []

        async def hook(incoming):
            seen.append(incoming)
            return True

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(len(seen), 1)
        self.assertEqual(list(bridge.queued_messages), [])

    async def test_declined_message_falls_back_to_the_queue(self):
        msg = {
            "type": "message",
            "content": "with a photo",
            "images": [{"filename": "x.png"}],
        }
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))

        async def hook(incoming):
            return False

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_hook_exception_still_queues_the_message(self):
        msg = {"type": "message", "content": "boom"}
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))

        async def hook(incoming):
            raise RuntimeError("stdin exploded")

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_no_hook_configured_behaves_exactly_as_before(self):
        msg = {"type": "message", "content": "legacy path"}
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))
        await self._run_listener(bridge)
        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_stop_streaming_still_cancels_and_is_never_injected(self):
        """The Stop button must keep working: it is the one path that SHOULD
        interrupt him, and it must never be mistaken for a slip-in.
        """
        bridge = StreamWebSocketBridge(
            _FakeWs([{"type": "stop_streaming"}]),
            logging.getLogger("test"),
        )
        called = []

        async def hook(incoming):
            called.append(incoming)
            return True

        bridge.inject_hook = hook
        task = asyncio.create_task(bridge.listen_during_stream())
        await asyncio.sleep(0.2)
        was_cancelled = await bridge.stop_listener(task)

        self.assertTrue(was_cancelled)
        self.assertEqual(called, [])
        self.assertEqual(list(bridge.queued_messages), [])

    async def test_non_message_frames_are_never_injected(self):
        """switch_identity / new_conversation / slash_command etc. are control
        frames, not things to whisper into a running turn.
        """
        frames = [
            {"type": "switch_identity", "identity": "Claude"},
            {"type": "slash_command", "command": "hub"},
        ]
        bridge = StreamWebSocketBridge(_FakeWs(frames), logging.getLogger("test"))
        called = []

        async def hook(incoming):
            called.append(incoming)
            return True

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(called, [])
        self.assertEqual(len(bridge.queued_messages), 2)


if __name__ == "__main__":
    unittest.main()
