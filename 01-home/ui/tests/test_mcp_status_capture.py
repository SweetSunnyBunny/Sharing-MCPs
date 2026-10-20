"""MCP status capture from the CLI system/init event (adoption item #10).

The Claude Code CLI's first `system`/`init` NDJSON event lists every MCP
server's connection status plus the flat tool roster — these tests pin down
that _capture_mcp_status stashes it on the session and sessions_snapshot()
surfaces it for the Settings Hub System panel.
"""

import unittest
from unittest.mock import patch

from services import claude_subprocess


class _FakeSession:
    """Just enough surface for _capture_mcp_status."""

    def __init__(self):
        self.mcp_servers: list[dict] = []


class CaptureMcpStatusTests(unittest.TestCase):
    def test_init_event_roster_and_tool_counts_are_captured(self):
        session = _FakeSession()
        event = {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [
                {"name": "qualia-backend", "status": "connected"},
                {"name": "discord-backend", "status": "connected"},
                {"name": "social-backend", "status": "failed"},
            ],
            "tools": [
                "Read",
                "Bash",
                "mcp__qualia-backend__mind_store",
                "mcp__qualia-backend__mind_search",
                "mcp__discord-backend__discord_send_message",
            ],
        }

        claude_subprocess._capture_mcp_status(event, session)

        self.assertEqual(
            session.mcp_servers,
            [
                {"name": "qualia-backend", "status": "connected", "tool_count": 2},
                {"name": "discord-backend", "status": "connected", "tool_count": 1},
                {"name": "social-backend", "status": "failed", "tool_count": 0},
            ],
        )

    def test_malformed_event_leaves_previous_roster_intact(self):
        session = _FakeSession()
        session.mcp_servers = [
            {"name": "qualia-backend", "status": "connected", "tool_count": 2},
        ]

        claude_subprocess._capture_mcp_status({"mcp_servers": "nope"}, session)
        claude_subprocess._capture_mcp_status({"mcp_servers": [{"status": "x"}]}, session)
        claude_subprocess._capture_mcp_status({}, session)

        self.assertEqual(len(session.mcp_servers), 1)
        self.assertEqual(session.mcp_servers[0]["name"], "qualia-backend")


class SessionsSnapshotMcpTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_snapshot_includes_per_session_mcp_servers(self):
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            session, _fresh = await claude_subprocess._get_or_spawn(
                identity="Claude",
                conversation_id="conv-mcp-snap",
                model="claude-opus-4-8",
                permission_mode="auto",
                effort="medium",
            )
        session.mcp_servers = [
            {"name": "qualia-backend", "status": "connected", "tool_count": 2},
        ]

        snap = claude_subprocess.sessions_snapshot()

        entry = next(s for s in snap if s["conversation_id"] == "conv-mcp-snap"[:8])
        self.assertEqual(
            entry["mcp_servers"],
            [{"name": "qualia-backend", "status": "connected", "tool_count": 2}],
        )
        # snapshot must hand out a copy, not the live list
        entry["mcp_servers"].append({"name": "tamper"})
        self.assertEqual(len(session.mcp_servers), 1)


if __name__ == "__main__":
    unittest.main()
