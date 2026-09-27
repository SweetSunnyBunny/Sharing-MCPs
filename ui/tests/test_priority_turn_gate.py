"""Priority turn gate (#30): web > platform > autowake for the shared
per-session turn_lock in claude_subprocess.py.

One ClaudeSession (and its single turn_lock) is shared per (identity,
conversation_id, model) -- so a live web chat and an in-progress autowake
turn for the same identity can land on the exact same lock. Without a
priority gate, a higher-priority arrival just waits in plain FIFO order
behind whatever got there first. These tests pin down
_maybe_preempt_lower_priority_turn: it fires the existing write_interrupt()
soft-stop when (and only when) the lock is held by a strictly
lower-priority turn, and never touches anything when the lock is free, held
by an equal/higher-priority turn, or when the interrupt write itself fails.
"""

import unittest
from unittest.mock import Mock, patch

from services import claude_subprocess


def _make_session(**overrides):
    """A ClaudeSession with a mocked subprocess, built via _get_or_spawn
    (with _spawn patched out) -- mirrors the helper in
    test_claude_subprocess_interrupt.py.
    """
    async def _build():
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            session, _fresh = await claude_subprocess._get_or_spawn(
                identity=overrides.get("identity", "Claude"),
                conversation_id=overrides.get("conversation_id", "conv-priority"),
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


class PriorityConstantsTests(unittest.TestCase):
    def test_web_outranks_platform_outranks_autowake(self):
        self.assertLess(
            claude_subprocess._TURN_SOURCE_PRIORITY["web"],
            claude_subprocess._TURN_SOURCE_PRIORITY["platform"],
        )
        self.assertLess(
            claude_subprocess._TURN_SOURCE_PRIORITY["platform"],
            claude_subprocess._TURN_SOURCE_PRIORITY["autowake"],
        )

    def test_default_turn_source_is_web(self):
        self.assertEqual(claude_subprocess._DEFAULT_TURN_SOURCE, "web")


class MaybePreemptTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_noop_when_lock_is_free(self):
        session = await _make_session(conversation_id="conv-free")
        # Lock never acquired -- nothing to preempt.
        claude_subprocess._maybe_preempt_lower_priority_turn(session, "web")
        session.proc.stdin.write.assert_not_called()

    async def test_web_interrupts_in_progress_autowake_turn(self):
        session = await _make_session(conversation_id="conv-web-over-autowake")
        session.active_turn_source = "autowake"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "web")

        session.proc.stdin.write.assert_called_once()
        session.proc.stdin.flush.assert_called_once()

    async def test_platform_interrupts_in_progress_autowake_turn(self):
        session = await _make_session(conversation_id="conv-platform-over-autowake")
        session.active_turn_source = "autowake"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "platform")

        session.proc.stdin.write.assert_called_once()

    async def test_web_interrupts_in_progress_platform_turn(self):
        session = await _make_session(conversation_id="conv-web-over-platform")
        session.active_turn_source = "platform"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "web")

        session.proc.stdin.write.assert_called_once()

    async def test_autowake_never_interrupts_in_progress_web_turn(self):
        session = await _make_session(conversation_id="conv-autowake-under-web")
        session.active_turn_source = "web"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "autowake")

        session.proc.stdin.write.assert_not_called()

    async def test_platform_never_interrupts_in_progress_web_turn(self):
        session = await _make_session(conversation_id="conv-platform-under-web")
        session.active_turn_source = "web"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "platform")

        session.proc.stdin.write.assert_not_called()

    async def test_equal_priority_never_interrupts(self):
        session = await _make_session(conversation_id="conv-equal")
        session.active_turn_source = "autowake"
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "autowake")

        session.proc.stdin.write.assert_not_called()

    async def test_missing_active_turn_source_treated_as_lowest_priority(self):
        # A held lock with no recorded holder (shouldn't normally happen,
        # but must fail safe) is treated as autowake-tier -- a web arrival
        # still preempts it.
        session = await _make_session(conversation_id="conv-missing-holder")
        session.active_turn_source = None
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "web")

        session.proc.stdin.write.assert_called_once()

    async def test_interrupt_failure_is_best_effort(self):
        # write_interrupt() raising (e.g. broken pipe) must never propagate
        # out of the preempt check -- the incoming turn just waits the
        # normal FIFO amount instead of jumping the queue.
        session = await _make_session(conversation_id="conv-interrupt-fails")
        session.active_turn_source = "autowake"
        session.proc.stdin.write.side_effect = BrokenPipeError()
        async with session.turn_lock:
            claude_subprocess._maybe_preempt_lower_priority_turn(session, "web")
        # No exception raised -- test reaching here is the assertion.


class ActiveTurnSourceTrackingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_new_session_has_no_active_turn_source(self):
        session = await _make_session(conversation_id="conv-fresh")
        self.assertIsNone(session.active_turn_source)


if __name__ == "__main__":
    unittest.main()
