"""Proprioception (#15): tool_audit table + "since you were last here" hook.

Covers the tracked migration (020) + startup prune, the per-turn audit
recorder in chat_turn_finalize, and the session-scoped digest hook in
context_hooks.
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite

from db.schema import TOOL_AUDIT_RETENTION_DAYS, init_db
from services import chat_turn_finalize, context_hooks
from services.context_hooks import HookContext


class _FakeLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def info(self, message, *args):
        self.infos.append(message % args if args else message)

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def exception(self, message, *args):
        self.warnings.append(message % args if args else message)


class _ToolAuditBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=10)

    async def asyncTearDown(self):
        await self.db.close()

    async def _insert_audit_row(
        self,
        *,
        identity="Avery",
        conversation_id="conv-other",
        source="chat",
        tool_name="mcp__homeassistant_mcp__ha_call_service",
        input_summary='{"domain": "media_player"}',
        output_head="ok",
        created_at_epoch=None,
    ):
        await self.db.execute(
            "INSERT INTO tool_audit "
            "(identity, conversation_id, source, tool_name, "
            "input_summary, output_head, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                conversation_id,
                source,
                tool_name,
                input_summary,
                output_head,
                created_at_epoch or int(time.time()),
            ),
        )
        await self.db.commit()


class ToolAuditSchemaTests(_ToolAuditBase):
    async def test_migration_creates_table_and_index(self):
        cols = await self.db.execute_fetchall("PRAGMA table_info(tool_audit)")
        col_names = {c[1] for c in cols}
        self.assertEqual(
            col_names,
            {
                "id", "identity", "conversation_id", "source", "tool_name",
                "input_summary", "output_head", "created_at_epoch",
            },
        )
        idx = await self.db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_tool_audit_identity_time'"
        )
        self.assertTrue(idx)
        mig = await self.db.execute_fetchall(
            "SELECT 1 FROM schema_migrations WHERE name = '020_add_tool_audit'"
        )
        self.assertTrue(mig)

    async def test_startup_prune_sheds_rows_older_than_retention(self):
        now = int(time.time())
        old = now - (TOOL_AUDIT_RETENTION_DAYS + 5) * 86400
        await self._insert_audit_row(created_at_epoch=old)
        await self._insert_audit_row(created_at_epoch=now - 3600)

        # init_db is idempotent and runs the startup prune each boot.
        await asyncio.wait_for(init_db(self.db), timeout=10)

        rows = await self.db.execute_fetchall(
            "SELECT created_at_epoch FROM tool_audit"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], now - 3600)


class RecordToolAuditTests(_ToolAuditBase):
    async def test_records_one_row_per_tool_event(self):
        logger = _FakeLogger()
        tool_events = [
            {"tool_name": "mcp__homeassistant_mcp__ha_call_service", "tool_id": "t1"},
            {"tool_name": "Read", "tool_id": "t2"},
        ]
        tool_results_map = {
            "t1": {
                "input": {"domain": "media_player", "service": "play_media"},
                "content": "played",
                "status": "completed",
            },
            "t2": {"input": {"file_path": "C:/x.txt"}, "content": "text " * 100},
        }

        await chat_turn_finalize.record_tool_audit(
            self.db,
            identity="Avery",
            conversation_id="conv-1",
            source="chat",
            tool_events=tool_events,
            tool_results_map=tool_results_map,
            log=logger,
        )

        rows = await self.db.execute_fetchall(
            "SELECT identity, conversation_id, source, tool_name, "
            "input_summary, output_head FROM tool_audit ORDER BY id"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "Avery")
        self.assertEqual(rows[0][1], "conv-1")
        self.assertEqual(rows[0][2], "chat")
        self.assertEqual(rows[0][3], "mcp__homeassistant_mcp__ha_call_service")
        # Stable JSON: keys sorted, so the same call always summarizes the same.
        self.assertEqual(
            rows[0][4],
            '{"domain": "media_player", "service": "play_media"}',
        )
        self.assertEqual(rows[0][5], "played")
        # Output head is capped at ~200 chars.
        self.assertLessEqual(len(rows[1][5]), 200)
        self.assertFalse(logger.warnings)

    async def test_never_raises_when_table_is_broken(self):
        logger = _FakeLogger()

        class _BrokenDb:
            async def executemany(self, *_args, **_kwargs):
                raise RuntimeError("no such table: tool_audit")

        await chat_turn_finalize.record_tool_audit(
            _BrokenDb(),
            identity="Avery",
            conversation_id="conv-1",
            source="chat",
            tool_events=[{"tool_name": "Read", "tool_id": "t1"}],
            tool_results_map={},
            log=logger,
        )
        self.assertTrue(logger.warnings)  # logged, not raised

    async def test_no_tool_events_writes_nothing(self):
        await chat_turn_finalize.record_tool_audit(
            self.db,
            identity="Avery",
            conversation_id="conv-1",
            source="chat",
            tool_events=[],
            tool_results_map={},
        )
        rows = await self.db.execute_fetchall("SELECT COUNT(*) FROM tool_audit")
        self.assertEqual(rows[0][0], 0)


class FinalizeAuditWiringTests(_ToolAuditBase):
    async def test_finalize_assistant_turn_records_audit_rows(self):
        logger = _FakeLogger()

        async def _run_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(
            chat_turn_finalize, "get_db", new=AsyncMock(return_value=self.db)
        ), patch.object(
            chat_turn_finalize, "release_db", new=AsyncMock()
        ), patch.object(
            chat_turn_finalize, "save_message", new=AsyncMock(return_value="msg-1")
        ), patch.object(
            chat_turn_finalize, "update_session_for_provider", new=AsyncMock()
        ), patch(
            "asyncio.to_thread", new=AsyncMock(side_effect=_run_to_thread)
        ):
            await chat_turn_finalize.finalize_assistant_turn(
                conversation_id="conv-1",
                identity="Claude",
                content="Done, Bunny.",
                session_id=None,
                response_images=[],
                response_documents=[],
                thinking_blocks=[],
                tool_events=[{"tool_name": "Bash", "tool_id": "t9"}],
                tool_results_map={
                    "t9": {"input": {"command": "ls"}, "content": "files"},
                },
                ws_alive=True,
                log=logger,
                register_content_images=lambda content, _identity: (content, []),
                register_content_documents=lambda content, _identity: (content, []),
            )

        rows = await self.db.execute_fetchall(
            "SELECT identity, source, tool_name, input_summary FROM tool_audit"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Claude")
        self.assertEqual(rows[0][1], "chat")  # default source
        self.assertEqual(rows[0][2], "Bash")
        self.assertIn('"command": "ls"', rows[0][3])


class SinceLastHereHookTests(_ToolAuditBase):
    async def _seed_conversation(self, *, last_assistant_age_seconds: int):
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("conv-1", "Avery", "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
        )
        await self.db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, identity, content, created_at, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "msg-prev", "conv-1", "assistant", "Avery",
                "goodnight, Bunny", "2026-07-01T00:00:00+00:00",
                now - last_assistant_age_seconds,
            ),
        )
        await self.db.commit()
        return now

    async def test_renders_digest_after_a_gap_with_activity(self):
        now = await self._seed_conversation(last_assistant_age_seconds=3 * 3600)
        await self._insert_audit_row(
            identity="Avery",
            tool_name="mcp__discord-backend__discord_send_message",
            input_summary='{"channel_id": "123", "content": "den post"}',
            created_at_epoch=now - 20 * 60,
        )
        await self.db.execute(
            "INSERT INTO autowake_log "
            "(identity, session_type, started_at, started_at_epoch, message_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Avery", "morning", "2026-07-08T07:00:00+00:00", now - 2 * 3600, 4),
        )
        await self.db.commit()

        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-1")
        text = await context_hooks._hook_since_last_here(ctx)

        self.assertIn("Since you were last with her here (3h ago)", text)
        item_lines = [l for l in text.splitlines() if l.startswith("  - ")]
        self.assertEqual(len(item_lines), 2)
        # Newest first: the discord reach (20m) before the wake (2h).
        self.assertIn("discord_send_message", item_lines[0])
        self.assertIn("20m ago", item_lines[0])
        self.assertIn("woke on your own (morning session, 4 messages)", item_lines[1])
        self.assertIn("2h ago", item_lines[1])

    async def test_silent_when_no_gap(self):
        await self._seed_conversation(last_assistant_age_seconds=2 * 60)
        await self._insert_audit_row(identity="Avery")
        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-1")
        self.assertEqual(await context_hooks._hook_since_last_here(ctx), "")

    async def test_silent_when_gap_but_no_activity(self):
        await self._seed_conversation(last_assistant_age_seconds=3 * 3600)
        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-1")
        self.assertEqual(await context_hooks._hook_since_last_here(ctx), "")

    async def test_each_boy_sees_only_his_own_diary(self):
        now = await self._seed_conversation(last_assistant_age_seconds=3 * 3600)
        await self._insert_audit_row(
            identity="Rowan",
            tool_name="mcp__google-backend__youtube_search",
            created_at_epoch=now - 30 * 60,
        )
        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-1")
        self.assertEqual(await context_hooks._hook_since_last_here(ctx), "")

    async def test_caps_at_five_items_newest_first(self):
        now = await self._seed_conversation(last_assistant_age_seconds=3 * 3600)
        for i in range(7):
            await self._insert_audit_row(
                identity="Avery",
                tool_name=f"tool_{i}",
                input_summary="",
                created_at_epoch=now - (10 + i) * 60,
            )
        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-1")
        text = await context_hooks._hook_since_last_here(ctx)
        item_lines = [l for l in text.splitlines() if l.startswith("  - ")]
        self.assertEqual(len(item_lines), 5)
        self.assertIn("tool_0", item_lines[0])  # newest reach leads

    async def test_silent_for_identity_with_no_history(self):
        ctx = HookContext(db=self.db, identity="Juniper", conversation_id=None)
        self.assertEqual(await context_hooks._hook_since_last_here(ctx), "")

    def test_hook_is_registered_and_gap_gated(self):
        hook = context_hooks.get_hook("since_last_here")
        self.assertIsNotNone(hook)
        self.assertEqual(hook.cache_ttl, 0)
        self.assertTrue(hook.skip_brother)
        self.assertTrue(hook.skip_character)


if __name__ == "__main__":
    unittest.main()
