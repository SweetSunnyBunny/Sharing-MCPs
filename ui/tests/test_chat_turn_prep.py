import unittest
from unittest.mock import AsyncMock, patch

from services import chat_turn_prep


class _FakeDb:
    def __init__(self, fetch_results=None):
        self.fetch_results = fetch_results or {}

    async def execute_fetchall(self, query, params=()):
        return list(self.fetch_results.get((query, params), []))


class _FakeMcpBridge:
    pass


class _FakeLogger:
    def info(self, *_args, **_kwargs):
        return None

    def debug(self, *_args, **_kwargs):
        return None


class ChatTurnPrepTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_chat_turn_direct_api_path(self):
        fake_db = _FakeDb()
        orientation_mock = AsyncMock(return_value="orientation")

        with patch.object(chat_turn_prep, "USE_DIRECT_API", True), patch.object(
            chat_turn_prep, "save_message", new=AsyncMock(return_value="user-source-1")
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=orientation_mock
        ), patch.object(
            chat_turn_prep, "get_messages",
            new=AsyncMock(return_value=[{"identity": "Claude", "content": "recent context"}]),
        ), patch.object(
            chat_turn_prep, "build_skill_injection",
            return_value=("auto skill", ["testing-skill"]),
        ), patch.object(
            chat_turn_prep, "build_skill_catalog_hint",
            return_value="catalog",
        ), patch.object(
            chat_turn_prep, "build_messages_array",
            new=AsyncMock(return_value=[{"role": "user", "content": "old"}]),
        ), patch.object(
            chat_turn_prep, "build_image_content_block",
            return_value={"type": "image"},
        ), patch.object(
            chat_turn_prep, "build_document_note",
            return_value="document note",
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ), patch(
            "services.mcp_bridge.mcp_bridge",
            new=_FakeMcpBridge(),
        ):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=fake_db,
                conversation_id="conv-1",
                identity="Claude",
                text="hello",
                images_info=[{"filename": "sample.png"}],
                documents_info=[{"filename": "doc.txt"}],
                active_categories={"core"},
                log=_FakeLogger(),
            )

        self.assertEqual(prepared.context_block, "orientation")
        self.assertEqual(prepared.image_content_blocks, [{"type": "image"}])
        self.assertEqual(prepared.user_message_id, "user-source-1")
        self.assertEqual(prepared.db_messages, [{"role": "user", "content": "old"}])
        self.assertEqual(prepared.active_categories, {"core"})
        self.assertEqual(prepared.skill_context, "catalog\n\nauto skill")
        self.assertEqual(orientation_mock.await_args.kwargs["query_text"], "hello")
        self.assertIsNone(prepared.context_notice)

    async def test_prepare_chat_turn_api_provider_gets_history_without_env_flag(self):
        """A DB-routed API provider must get history even with USE_DIRECT_API off."""
        fake_db = _FakeDb()

        with patch.object(chat_turn_prep, "USE_DIRECT_API", False), patch.object(
            chat_turn_prep, "save_message", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=AsyncMock(return_value="orientation")
        ), patch.object(
            chat_turn_prep, "get_messages",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            chat_turn_prep, "build_skill_injection",
            return_value=("", []),
        ), patch.object(
            chat_turn_prep, "build_skill_catalog_hint",
            return_value="catalog",
        ), patch.object(
            chat_turn_prep, "build_messages_array",
            new=AsyncMock(return_value=[{"role": "user", "content": "old"}]),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("openrouter", {})),
        ):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=fake_db,
                conversation_id="conv-2",
                identity="Claude",
                text="hello",
                images_info=[],
                documents_info=[],
                active_categories={"core"},
                log=_FakeLogger(),
            )

        self.assertEqual(prepared.db_messages, [{"role": "user", "content": "old"}])

    async def test_prepare_chat_turn_cli_path_no_history_no_notice(self):
        """CLI providers own history injection — prep hands them no transcript."""
        fake_db = _FakeDb()

        with patch.object(chat_turn_prep, "USE_DIRECT_API", False), patch.object(
            chat_turn_prep, "save_message", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=AsyncMock(return_value="orientation")
        ), patch.object(
            chat_turn_prep, "get_messages",
            new=AsyncMock(return_value=[
                {"role": "assistant", "identity": "Claude", "content": "assistant reply"},
                {"role": "user", "identity": None, "content": "last user line"},
            ]),
        ), patch.object(
            chat_turn_prep, "build_skill_injection",
            return_value=("", []),
        ), patch.object(
            chat_turn_prep, "build_skill_catalog_hint",
            return_value="catalog",
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=fake_db,
                conversation_id="conv-3",
                identity="Claude",
                text="hello again",
                images_info=[],
                documents_info=[],
                active_categories={"core"},
                log=_FakeLogger(),
            )

        self.assertIsNone(prepared.db_messages)
        # Orientation is NOT prepended here — provider_router injects it for CLI providers
        self.assertIn("hello again", prepared.prompt)
        self.assertEqual(prepared.context_block, "orientation")
        self.assertIsNone(prepared.context_notice)

    async def test_prepare_chat_turn_codex_omits_duplicate_catalog_and_keeps_bootstrap_history(self):
        fake_db = _FakeDb()
        history = [{"role": "user", "content": "real prior turn"}]

        with patch.object(chat_turn_prep, "USE_DIRECT_API", False), patch.object(
            chat_turn_prep, "save_message", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "auto_title_conversation", new=AsyncMock()
        ), patch.object(
            chat_turn_prep, "build_orientation_context", new=AsyncMock(return_value="orientation")
        ), patch.object(
            chat_turn_prep, "build_skill_injection",
            return_value=("matched body", ["identity-fixation-protocols"]),
        ) as injection_mock, patch.object(
            chat_turn_prep, "build_skill_catalog_hint",
            return_value="duplicate catalog",
        ) as catalog_mock, patch.object(
            chat_turn_prep, "build_messages_array",
            new=AsyncMock(return_value=history),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("codex", {})),
        ):
            prepared = await chat_turn_prep.prepare_chat_turn(
                db=fake_db,
                conversation_id="conv-sol",
                identity="Avery",
                text="Does this feel true?",
                images_info=[],
                documents_info=[],
                active_categories={"core"},
                log=_FakeLogger(),
            )

        self.assertEqual(prepared.skill_context, "matched body")
        self.assertEqual(prepared.db_messages, history)
        injection_mock.assert_called_once_with(
            "Does this feel true?", identity="Avery"
        )
        catalog_mock.assert_not_called()
