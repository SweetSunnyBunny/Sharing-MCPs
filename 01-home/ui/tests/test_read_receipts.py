'Read receipts: Sent -> Delivered -> Read (services/read_receipts.py).'

import json
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite

from services import chat_turn_prep, read_receipts


async def _memory_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, "
        "metadata TEXT, created_at TEXT)"
    )
    return db


class ReadSignalTests(unittest.TestCase):
    def test_model_work_counts_as_read(self):
        for t in ("stream_delta", "thinking_start", "thinking_delta", "tool_use_start"):
            self.assertTrue(read_receipts.is_read_signal(t, {}), t)

    def test_transport_noise_is_not_read(self):
        for t in ("meta", "keepalive", "stream_reset", "error", "tool_result"):
            self.assertFalse(read_receipts.is_read_signal(t, {}), t)

    def test_stream_end_reads_only_with_a_real_reply(self):
        self.assertTrue(read_receipts.is_read_signal("stream_end", {"full_content": "hi Bunny"}))
        self.assertFalse(read_receipts.is_read_signal("stream_end", {"full_content": "  "}))
        self.assertFalse(read_receipts.is_read_signal("stream_end", {}))

    def test_delivered_event_carries_client_nonce(self):
        evt = read_receipts.delivered_event("m1", "c1", "2026-09-30T16:54:00+00:00", "cabc")
        self.assertEqual(evt["type"], "message_delivered")
        self.assertEqual(evt["client_msg_id"], "cabc")
        self.assertNotIn("client_msg_id", read_receipts.delivered_event("m1", "c1", "t"))


class MarkReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await _memory_db()

    async def asyncTearDown(self):
        await self.db.close()

    async def _meta(self, mid):
        rows = await self.db.execute_fetchall("SELECT metadata FROM messages WHERE id=?", (mid,))
        return json.loads(rows[0][0])

    async def test_marks_read_and_keeps_existing_metadata(self):
        await self.db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)",
            ("m1", "c1", "user",
             json.dumps({"images": [{"url": "/x.png"}], "receipt": {"delivered_at": "D"}}), "C"),
        )
        evt = await read_receipts.mark_read(self.db, "m1", "Claude")
        self.assertEqual(evt["type"], "message_read")
        self.assertEqual(evt["read_by"], "Claude")
        self.assertEqual(evt["delivered_at"], "D")
        meta = await self._meta("m1")
        self.assertEqual(meta["images"], [{"url": "/x.png"}])
        self.assertEqual(meta["receipt"]["read_at"], evt["read_at"])

    async def test_first_read_wins(self):
        await self.db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)", ("m2", "c1", "user", None, "C"),
        )
        with patch.object(read_receipts, "now_iso", return_value="FIRST"):
            await read_receipts.mark_read(self.db, "m2", "Claude")
        with patch.object(read_receipts, "now_iso", return_value="SECOND"):
            evt = await read_receipts.mark_read(self.db, "m2", "Avery")
        self.assertEqual(evt["read_at"], "FIRST")
        self.assertEqual(evt["read_by"], "Claude")
        meta = await self._meta("m2")
        # Old messages with no receipt fall back to their saved time.
        self.assertEqual(meta["receipt"]["delivered_at"], "C")

    async def test_ignores_his_messages_and_missing_rows(self):
        await self.db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)", ("a1", "c1", "assistant", None, "C"),
        )
        self.assertIsNone(await read_receipts.mark_read(self.db, "a1", "Claude"))
        self.assertIsNone(await read_receipts.mark_read(self.db, "nope", "Claude"))
        self.assertIsNone(await read_receipts.mark_read(self.db, "", "Claude"))

    async def test_mark_read_and_send_never_raises(self):
        send = AsyncMock()
        with patch("db.database.get_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            await read_receipts.mark_read_and_send("m1", "Claude", send)
        send.assert_not_awaited()


class _FakeDb:
    async def execute_fetchall(self, query, params=()):
        return []


class _Log:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass


class PrepDeliveredTests(unittest.IsolatedAsyncioTestCase):
    async def test_prep_stamps_delivered_and_fires_callback(self):
        save = AsyncMock(return_value="user-1")
        on_saved = AsyncMock()
        with patch.object(chat_turn_prep, "USE_DIRECT_API", True), patch.object(
            chat_turn_prep, "save_message", new=save
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=AsyncMock(return_value="o")
        ), patch.object(
            chat_turn_prep, "build_skill_injection", return_value=("", [])
        ), patch.object(
            chat_turn_prep, "build_skill_catalog_hint", return_value=""
        ), patch.object(
            chat_turn_prep, "build_messages_array", new=AsyncMock(return_value=[])
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ), patch.object(read_receipts, "now_iso", return_value="T-DELIVERED"):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=_FakeDb(), conversation_id="c1", identity="Claude",
                text="can we add read receipts?", images_info=[], documents_info=[],
                active_categories={"core"}, log=_Log(), on_user_saved=on_saved,
            )
        self.assertEqual(prepared.user_message_id, "user-1")
        self.assertEqual(
            save.await_args.kwargs["metadata"], {"receipt": {"delivered_at": "T-DELIVERED"}},
        )
        on_saved.assert_awaited_once_with("user-1", "T-DELIVERED")

    async def test_callback_failure_does_not_cost_the_turn(self):
        with patch.object(chat_turn_prep, "USE_DIRECT_API", True), patch.object(
            chat_turn_prep, "save_message", new=AsyncMock(return_value="user-2")
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=AsyncMock(return_value="o")
        ), patch.object(
            chat_turn_prep, "build_skill_injection", return_value=("", [])
        ), patch.object(
            chat_turn_prep, "build_skill_catalog_hint", return_value=""
        ), patch.object(
            chat_turn_prep, "build_messages_array", new=AsyncMock(return_value=[])
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=_FakeDb(), conversation_id="c1", identity="Claude",
                text="hi", images_info=[], documents_info=[],
                active_categories={"core"}, log=_Log(),
                on_user_saved=AsyncMock(side_effect=RuntimeError("socket gone")),
            )
        self.assertEqual(prepared.user_message_id, "user-2")


if __name__ == "__main__":
    unittest.main()
