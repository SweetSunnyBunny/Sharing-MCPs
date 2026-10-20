import json
import unittest
from unittest.mock import AsyncMock, patch

from services import chat_session_ops


class _FakeDb:
    def __init__(self, query_results=None):
        self.query_results = query_results or {}
        self.executed = []
        self.committed = False

    async def execute_fetchall(self, query, params=()):
        self.executed.append(("fetchall", query, params))
        key = (query, params)
        return list(self.query_results.get(key, []))

    async def execute(self, query, params=()):
        self.executed.append(("execute", query, params))
        return None

    async def commit(self):
        self.committed = True


class ChatSessionOpsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_conversation_uses_typed_path_when_needed(self):
        fake_db = _FakeDb()

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ), patch.object(
            chat_session_ops, "new_typed_conversation", new=AsyncMock(return_value="conv-1")
        ) as typed_mock:
            conversation_id, session_type = await chat_session_ops.create_conversation("Claude", "roleplay")

        self.assertEqual((conversation_id, session_type), ("conv-1", "roleplay"))
        typed_mock.assert_awaited_once_with(fake_db, "Claude", "roleplay")

    async def test_load_history_payload_builds_expected_response(self):
        fake_db = _FakeDb({
            (
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                ("conv-2",),
            ): [(75,)],
            (
                "SELECT session_type FROM conversations WHERE id = ?",
                ("conv-2",),
            ): [("chat",)],
        })

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ), patch.object(
            chat_session_ops, "get_or_create_conversation", new=AsyncMock(return_value="conv-2")
        ), patch.object(
            chat_session_ops, "get_messages", new=AsyncMock(return_value=[{"id": "m1"}])
        ):
            payload, conversation_id, session_type = await chat_session_ops.load_history_payload("Avery", None)

        self.assertEqual(conversation_id, "conv-2")
        self.assertEqual(session_type, "chat")
        self.assertEqual(payload["type"], "history")
        self.assertEqual(payload["messages"], [{"id": "m1"}])
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["total_count"], 75)

    async def test_load_history_payload_honors_custom_limit(self):
        fake_db = _FakeDb({
            (
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                ("conv-5",),
            ): [(75,)],
            (
                "SELECT session_type FROM conversations WHERE id = ?",
                ("conv-5",),
            ): [("chat",)],
        })

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ), patch.object(
            chat_session_ops, "get_or_create_conversation", new=AsyncMock(return_value="conv-5")
        ), patch.object(
            chat_session_ops, "get_messages", new=AsyncMock(return_value=[{"id": "m1"}])
        ) as get_messages_mock:
            payload, conversation_id, session_type = await chat_session_ops.load_history_payload(
                "Avery",
                None,
                limit=80,
            )

        self.assertEqual(conversation_id, "conv-5")
        self.assertEqual(session_type, "chat")
        self.assertFalse(payload["has_more"])
        get_messages_mock.assert_awaited_once_with(fake_db, "conv-5", limit=80)

    async def test_load_history_payload_compacts_internal_documents_and_tool_output(self):
        fake_db = _FakeDb({
            (
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                ("conv-6",),
            ): [(1,)],
            (
                "SELECT session_type FROM conversations WHERE id = ?",
                ("conv-6",),
            ): [("chat",)],
        })
        huge_content = "x" * 5000
        huge_input = {"query": "y" * 3000}
        message = {
            "id": "m1",
            "metadata": {
                "documents": [
                    {
                        "original_name": "toolu_abc.json",
                        "path": r"C:/Users/YOU\.claude\projects\x\tool-results\toolu_abc.json",
                    },
                    {
                        "original_name": "notes.pdf",
                        "url": "/api/documents/file/notes.pdf",
                    },
                ],
                "tools": [
                    {
                        "tool_name": "Read",
                        "tool_id": "tool-1",
                        "status": "completed",
                        "input": huge_input,
                        "content": huge_content,
                    },
                ],
            },
        }

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ), patch.object(
            chat_session_ops, "get_or_create_conversation", new=AsyncMock(return_value="conv-6")
        ), patch.object(
            chat_session_ops, "get_messages", new=AsyncMock(return_value=[message])
        ):
            payload, _, _ = await chat_session_ops.load_history_payload("Avery", None)

        meta = payload["messages"][0]["metadata"]
        self.assertEqual(meta["documents"], [{"original_name": "notes.pdf", "url": "/api/documents/file/notes.pdf"}])
        self.assertLess(len(meta["tools"][0]["content"]), len(huge_content))
        self.assertIn("truncated", meta["tools"][0]["content"])
        self.assertIsInstance(meta["tools"][0]["input"], str)

    async def test_prepare_regeneration_returns_last_user_text_and_invalidates_cache(self):
        fake_db = _FakeDb({
            (
                "SELECT id, role, content FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at_epoch DESC LIMIT 5",
                ("conv-3",),
            ): [
                ("a1", "assistant", "reply"),
                ("u1", "user", "original prompt"),
            ],
        })

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ), patch(
            "services.session_lifecycle.invalidate_conversation_cache"
        ) as invalidate_mock:
            text = await chat_session_ops.prepare_regeneration("conv-3")

        self.assertEqual(text, "original prompt")
        self.assertTrue(fake_db.committed)
        invalidate_mock.assert_called_once_with("conv-3")

    async def test_update_conversation_pin_merges_metadata(self):
        fake_db = _FakeDb({
            (
                "SELECT metadata FROM conversations WHERE id = ?",
                ("conv-4",),
            ): [(json.dumps({"foo": "bar"}),)],
        })

        with patch.object(chat_session_ops, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_session_ops, "release_db", new=AsyncMock()
        ):
            payload = await chat_session_ops.update_conversation_pin("conv-4", True)

        self.assertEqual(payload, {
            "type": "conversation_pinned",
            "conversation_id": "conv-4",
            "pinned": True,
        })
        execute_calls = [call for call in fake_db.executed if call[0] == "execute"]
        self.assertTrue(execute_calls)
        saved_meta = json.loads(execute_calls[0][2][0])
        self.assertEqual(saved_meta, {"foo": "bar", "pinned": True})
