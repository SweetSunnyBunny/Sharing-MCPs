"""Session-preserving stop/interrupt for the persistent -p supervisor (#11).

A stop click used to map straight to `_retire_session()` -> `kill()` -> the
next turn paying a cold respawn + lossy DB replay. These tests pin down the
new soft path: `ClaudeSession.write_interrupt()` sends a
`control_request`/`interrupt` NDJSON line over the same stdin pipe
`write_user_message` already uses, and `_run_one_turn` tries that FIRST when
it sees `cancel_event` set — draining stdout for the CLI's own `result` event
instead of tearing the process down immediately. Only if that grace window
(`_INTERRUPT_TIMEOUT`) elapses without a result does it fall back to the
original hard-kill-and-respawn behavior, which must never be removed.
"""

import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from services import claude_subprocess


def _make_session(**overrides):
    """A ClaudeSession with a mocked subprocess, built via _get_or_spawn
    (with _spawn patched out) so it carries a real asyncio loop/queue exactly
    like a live session does.
    """
    async def _build():
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            session, _fresh = await claude_subprocess._get_or_spawn(
                identity=overrides.get("identity", "Claude"),
                conversation_id=overrides.get("conversation_id", "conv-interrupt"),
                model=overrides.get("model", "claude-opus-4-8"),
                permission_mode="auto",
                effort="medium",
            )
        session.proc = Mock()
        session.proc.stdin = Mock()
        session.proc.poll.return_value = None  # "alive"
        session.dead = False
        return session
    return _build()


class WriteInterruptShapeTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_control_request_shape_and_newline_termination(self):
        session = await _make_session()

        session.write_interrupt()

        self.assertTrue(session.proc.stdin.write.called)
        raw = session.proc.stdin.write.call_args[0][0]
        self.assertIsInstance(raw, bytes)
        self.assertTrue(raw.endswith(b"\n"))
        # Exactly one line was written -- no extra newlines mid-payload.
        self.assertEqual(raw.count(b"\n"), 1)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(
            payload,
            {"type": "control_request", "request": {"subtype": "interrupt"}},
        )
        self.assertTrue(session.proc.stdin.flush.called)

    async def test_raises_when_session_already_dead(self):
        session = await _make_session()
        session.dead = True

        with self.assertRaises(RuntimeError):
            session.write_interrupt()

    async def test_raises_when_no_process(self):
        session = await _make_session()
        session.proc = None

        with self.assertRaises(RuntimeError):
            session.write_interrupt()

    async def test_stdin_write_failure_marks_session_dead(self):
        session = await _make_session()
        session.proc.stdin.write.side_effect = BrokenPipeError()

        with self.assertRaises(RuntimeError):
            session.write_interrupt()
        self.assertTrue(session.dead)


class RunOneTurnSoftInterruptTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_soft_interrupt_success_keeps_session_alive(self):
        """cancel_event set -> write_interrupt() fires once, then the CLI's
        own `result` event ends the turn normally. The session must NOT be
        retired/killed, and the resulting stream_end must carry
        cancelled=True so the frontend knows Owner stopped it.
        """
        session = await _make_session(conversation_id="conv-soft-ok")
        key = session.pool_key
        self.assertIn(key, claude_subprocess._sessions)

        cancel_event = asyncio.Event()
        cancel_event.set()

        result_line = (
            json.dumps({"type": "result", "result": "partial", "is_error": False})
            + "\n"
        ).encode("utf-8")

        async def _feed_result_shortly():
            await asyncio.sleep(0.05)
            session.line_queue.put_nowait(result_line)

        feed_task = asyncio.create_task(_feed_result_shortly())

        events = []
        async for evt in claude_subprocess._run_one_turn(
            session=session,
            composed_message="hi",
            identity="Claude",
            cancel_event=cancel_event,
            effective_permission_mode="auto",
        ):
            events.append(evt)
        await feed_task

        # write_interrupt was sent exactly once (user message + interrupt).
        write_calls = session.proc.stdin.write.call_args_list
        self.assertEqual(len(write_calls), 2)
        interrupt_payload = json.loads(write_calls[1][0][0].decode("utf-8"))
        self.assertEqual(interrupt_payload["type"], "control_request")
        self.assertEqual(interrupt_payload["request"]["subtype"], "interrupt")

        stream_ends = [e for e in events if e.get("type") == "stream_end"]
        self.assertEqual(len(stream_ends), 1)
        self.assertTrue(stream_ends[0].get("cancelled"))
        self.assertEqual(stream_ends[0]["full_content"], "partial")

        # Session-preserving: the process was never retired/killed.
        self.assertIn(key, claude_subprocess._sessions)
        self.assertFalse(session.dead)

    async def test_timeout_falls_back_to_hard_kill(self):
        """cancel_event set, but the CLI never answers the interrupt with a
        result event. Once _INTERRUPT_TIMEOUT elapses, _run_one_turn must
        fall back to the original hard-cancel path: retire the session and
        emit a cancelled stream_end even without a CLI result.
        """
        session = await _make_session(conversation_id="conv-soft-timeout")
        key = session.pool_key
        session.kill = Mock()  # avoid spawning a real taskkill in the test

        cancel_event = asyncio.Event()
        cancel_event.set()

        with patch.object(claude_subprocess, "_INTERRUPT_TIMEOUT", 0.05):
            events = []
            async for evt in claude_subprocess._run_one_turn(
                session=session,
                composed_message="hi",
                identity="Claude",
                cancel_event=cancel_event,
                effective_permission_mode="auto",
            ):
                events.append(evt)

        # write_interrupt fired once even though nothing ever answered it.
        write_calls = session.proc.stdin.write.call_args_list
        self.assertEqual(len(write_calls), 2)

        stream_ends = [e for e in events if e.get("type") == "stream_end"]
        self.assertEqual(len(stream_ends), 1)
        self.assertTrue(stream_ends[0].get("cancelled"))

        # Hard-kill fallback path must still be there: session retired.
        self.assertNotIn(key, claude_subprocess._sessions)
        session.kill.assert_called_once()

    async def test_interrupt_write_failure_falls_back_immediately(self):
        """If write_interrupt() itself raises (e.g. broken pipe), don't loop
        waiting for a result that can never come -- fall back to hard cancel
        right away.
        """
        session = await _make_session(conversation_id="conv-soft-writefail")
        key = session.pool_key
        session.kill = Mock()
        # First write (the user message, sent before the loop even starts)
        # must succeed so we actually reach the interrupt attempt; only the
        # SECOND stdin write (the interrupt itself) fails.
        session.proc.stdin.write.side_effect = [None, BrokenPipeError()]

        cancel_event = asyncio.Event()
        cancel_event.set()

        events = []
        async for evt in claude_subprocess._run_one_turn(
            session=session,
            composed_message="hi",
            identity="Claude",
            cancel_event=cancel_event,
            effective_permission_mode="auto",
        ):
            events.append(evt)

        stream_ends = [e for e in events if e.get("type") == "stream_end"]
        self.assertEqual(len(stream_ends), 1)
        self.assertTrue(stream_ends[0].get("cancelled"))
        self.assertNotIn(key, claude_subprocess._sessions)


if __name__ == "__main__":
    unittest.main()
