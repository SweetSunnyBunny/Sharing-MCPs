'Application module.'

import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from config import CLAUDE_MAX_TURNS, CLAUDE_MAX_TURNS_AUTOWAKE
from services import claude_subprocess
from services.autowake import autowake_turn_ceiling


def _make_session(conversation_id: str, turn_source: str = "web"):
    async def _build():
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            session, _fresh = await claude_subprocess._get_or_spawn(
                identity="Rowan",
                conversation_id=conversation_id,
                model="claude-opus-5-5",
                permission_mode="auto",
                effort="medium",
                turn_source=turn_source,
            )
        session.proc = Mock()
        session.proc.stdin = Mock()
        session.proc.poll.return_value = None
        session.dead = False
        return session
    return _build()


def _line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _tool_round(msg_id: str) -> bytes:
    return _line({
        "type": "assistant",
        "message": {
            "id": msg_id,
            "content": [{"type": "tool_use", "id": f"t-{msg_id}", "name": "Read", "input": {}}],
        },
    })


def _interrupts(session) -> int:
    count = 0
    for call in session.proc.stdin.write.call_args_list:
        payload = json.loads(call[0][0].decode("utf-8"))
        if payload.get("type") == "control_request":
            count += 1
    return count


class ResolveCeilingTests(unittest.TestCase):
    def test_her_turns_are_never_capped(self):
        self.assertIsNone(claude_subprocess._resolve_turn_ceiling("web", None))
        self.assertIsNone(claude_subprocess._resolve_turn_ceiling("platform", 5))

    def test_ordinary_autowake_keeps_the_cost_ceiling(self):
        self.assertEqual(
            claude_subprocess._resolve_turn_ceiling("autowake", None),
            CLAUDE_MAX_TURNS_AUTOWAKE,
        )

    def test_zero_means_uncapped(self):
        self.assertIsNone(claude_subprocess._resolve_turn_ceiling("autowake", 0))

    def test_explicit_number_wins(self):
        self.assertEqual(claude_subprocess._resolve_turn_ceiling("autowake", 7), 7)


class ScheduleClassifierTests(unittest.TestCase):
    def test_nightly_consolidation_is_uncapped(self):
        self.assertEqual(
            autowake_turn_ceiling("nightly_consolidation", "Nightly Consolidation — Claude"), 0,
        )

    def test_mind_tending_is_uncapped(self):
        # Mind Tending rows are session_type "custom" (tools/mind_tending_setup.py).
        self.assertEqual(autowake_turn_ceiling("custom", "Mind Tending: Rowan"), 0)

    def test_ordinary_wakes_keep_the_default(self):
        self.assertIsNone(autowake_turn_ceiling("free_time", "Claude's Study Hour"))
        self.assertIsNone(autowake_turn_ceiling("custom", "Atlas Hour"))
        self.assertIsNone(autowake_turn_ceiling(None, None))


class ToolRoundIdTests(unittest.TestCase):
    def test_text_only_message_is_not_a_round(self):
        evt = {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "hi"}]}}
        self.assertIsNone(claude_subprocess._tool_round_id(evt))

    def test_tool_message_returns_its_id(self):
        evt = json.loads(_tool_round("m9"))
        self.assertEqual(claude_subprocess._tool_round_id(evt), "m9")

    def test_non_assistant_is_ignored(self):
        self.assertIsNone(claude_subprocess._tool_round_id({"type": "user", "message": {}}))


class SpawnCeilingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_autowake_spawn_no_longer_pins_the_process_cap(self):
        """The bug: an autowake-first day pinned --max-turns 20 on the shared
        process, and her own turns inherited it."""
        session = await _make_session("conv-spawn-autowake", turn_source="autowake")
        self.assertEqual(session.max_turns, CLAUDE_MAX_TURNS)


class RunOneTurnCeilingTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def _run(self, session, lines, turn_ceiling):
        async def _feed():
            for raw in lines:
                await asyncio.sleep(0.01)
                session.line_queue.put_nowait(raw)
        feeder = asyncio.create_task(_feed())
        events = []
        async for evt in claude_subprocess._run_one_turn(
            session=session,
            composed_message="tend your mind",
            identity="Rowan",
            cancel_event=None,
            effective_permission_mode="auto",
            turn_ceiling=turn_ceiling,
        ):
            events.append(evt)
        await feeder
        return events

    async def test_ceiling_soft_interrupts_and_names_itself(self):
        session = await _make_session("conv-ceiling-hit")
        key = session.pool_key
        lines = [
            _tool_round("m1"),
            _tool_round("m1"),  # same round, second block — still ONE round
            _tool_round("m2"),
            _tool_round("m3"),  # round 3 > ceiling 2
            _line({"type": "result", "is_error": True, "subtype": "error_during_execution"}),
        ]
        events = await self._run(session, lines, turn_ceiling=2)

        self.assertEqual(_interrupts(session), 1)
        errors = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("tool ceiling (2", errors[0]["message"])
        ends = [e for e in events if e.get("type") == "stream_end"]
        self.assertEqual(len(ends), 1)
        self.assertFalse(ends[0].get("cancelled"))
        self.assertIn(key, claude_subprocess._sessions)
        self.assertFalse(session.dead)

    async def test_uncapped_turn_runs_every_round(self):
        session = await _make_session("conv-ceiling-free")
        lines = [_tool_round(f"m{i}") for i in range(30)] + [
            _line({"type": "result", "result": "tended", "is_error": False}),
        ]
        events = await self._run(session, lines, turn_ceiling=None)

        self.assertEqual(_interrupts(session), 0)
        self.assertFalse([e for e in events if e.get("type") == "error"])
        ends = [e for e in events if e.get("type") == "stream_end"]
        self.assertEqual(ends[0]["full_content"], "tended")


if __name__ == "__main__":
    unittest.main()
