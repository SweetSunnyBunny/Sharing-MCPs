import unittest
from unittest.mock import AsyncMock, Mock, patch

import config
from api import settings as settings_api
from config import cli_cold_history_limits
from services import claude_subprocess
from services.cli_text_utils import _format_history


class ClaudeModelLaneTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_same_conversation_keeps_separate_model_processes(self):
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            fable, fable_fresh = await claude_subprocess._get_or_spawn(
                identity="Claude",
                conversation_id="conv-shared",
                model="claude-fable-5",
                permission_mode="auto",
                effort="medium",
            )
            sonnet, sonnet_fresh = await claude_subprocess._get_or_spawn(
                identity="Claude",
                conversation_id="conv-shared",
                model="claude-sonnet-4-6",
                permission_mode="auto",
                effort="medium",
            )
            fable_again, fable_again_fresh = await claude_subprocess._get_or_spawn(
                identity="Claude",
                conversation_id="conv-shared",
                model="claude-fable-5",
                permission_mode="auto",
                effort="medium",
            )

        self.assertTrue(fable_fresh)
        self.assertTrue(sonnet_fresh)
        self.assertFalse(fable_again_fresh)
        self.assertIs(fable_again, fable)
        self.assertIsNot(fable, sonnet)
        self.assertEqual(len(claude_subprocess._sessions), 2)

    async def test_settings_save_chat_and_autowake_models_separately(self):
        class Request:
            async def json(self):
                return {
                    "interactive_model": "claude-fable-5",
                    "autowake_model": "claude-sonnet-4-6",
                }

        set_setting = AsyncMock()
        retire = Mock()
        with patch.object(settings_api, "_set_setting", new=set_setting), \
             patch.object(settings_api, "_retire_claude_code_sessions", new=retire), \
             patch.object(config, "CLAUDE_MODEL", "claude-opus-4-8"), \
             patch.object(config, "CLAUDE_MODEL_INTERACTIVE", "claude-opus-4-8"):
            result = await settings_api.set_model(Request())

        self.assertTrue(result["ok"])
        self.assertEqual(result["interactive_model"], "claude-fable-5")
        self.assertEqual(result["autowake_model"], "claude-sonnet-4-6")
        set_setting.assert_any_await("claude_model", "claude-fable-5")
        set_setting.assert_any_await(
            "claude_autowake_model", "claude-sonnet-4-6"
        )
        retire.assert_called_once_with("model-change")


class FableColdReplayTests(unittest.TestCase):
    def test_fable_replay_is_deeper_than_standard_cli_replay(self):
        standard_messages, standard_chars = cli_cold_history_limits(
            "claude-opus-4-8"
        )
        fable_messages, fable_chars = cli_cold_history_limits("claude-fable-5")

        self.assertGreater(fable_messages, standard_messages)
        self.assertGreater(fable_chars, standard_chars)

    def test_history_formatter_honors_message_and_character_limits(self):
        history = _format_history(
            [
                {"role": "user", "content": "oldest"},
                {"role": "assistant", "content": "x" * 20},
                {"role": "user", "content": "newest"},
            ],
            "Claude",
            message_limit=2,
            per_message_chars=8,
        )

        self.assertNotIn("oldest", history)
        self.assertIn("xxxxxxxx", history)
        self.assertIn("[...truncated...]", history)
        self.assertIn("newest", history)
