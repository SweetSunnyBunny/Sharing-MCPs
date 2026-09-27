"""Compaction hygiene (#18).

The Claude Code CLI emits a `system`/`compact_boundary` event when it
auto-compacts a session's context. `_parse_event` must: clear the turn's
`full_text` dedup accumulator (stale against what the model now holds),
emit `stream_reset` (same event openai_provider.py already uses to reset
the pipeline's StreamAccumulator), and emit a `compaction_notice` the
frontend can show as a quiet toast — never treating compaction as an error
or hard interruption.
"""

import unittest

from services import claude_subprocess
from services.claude_subprocess import ClaudeSession, _parse_event


def _make_session() -> ClaudeSession:
    return ClaudeSession(
        identity="Claude",
        conversation_id="conv-compact",
        model="claude-opus-4-8",
        permission_mode="auto",
        effort="medium",
    )


class CompactBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_boundary_clears_full_text_and_emits_reset_and_notice(self):
        session = _make_session()
        full_text = ["some", "earlier", "text"]
        event = {"type": "system", "subtype": "compact_boundary"}

        events = [e async for e in _parse_event(event, session, "Claude", full_text)]

        self.assertEqual(full_text, [])
        types = [e["type"] for e in events]
        self.assertEqual(types, ["stream_reset", "compaction_notice"])
        self.assertIn("compacted", events[1]["message"].lower())

    async def test_init_subtype_still_captures_mcp_status_and_yields_nothing(self):
        session = _make_session()
        full_text = ["untouched"]
        event = {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "test-server", "status": "connected"}],
        }

        events = [e async for e in _parse_event(event, session, "Claude", full_text)]

        self.assertEqual(events, [])
        self.assertEqual(full_text, ["untouched"])  # compact_boundary-only reset

    async def test_unrelated_system_subtype_is_silently_swallowed(self):
        session = _make_session()
        full_text = ["untouched"]
        event = {"type": "system", "subtype": "something_else"}

        events = [e async for e in _parse_event(event, session, "Claude", full_text)]

        self.assertEqual(events, [])
        self.assertEqual(full_text, ["untouched"])

    async def test_session_id_still_captured_on_compact_boundary(self):
        session = _make_session()
        event = {"type": "system", "subtype": "compact_boundary", "session_id": "sess-123"}

        _ = [e async for e in _parse_event(event, session, "Claude", [])]

        self.assertEqual(session.session_id, "sess-123")


if __name__ == "__main__":
    unittest.main()
