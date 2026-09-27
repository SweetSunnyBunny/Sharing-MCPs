import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from services import provider_router


class ProviderRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_provider_override_routes_to_chatgpt(self):
        chatgpt_mock = Mock(return_value=iter(()))
        codex_mock = Mock(return_value=iter(()))

        settings = {
            "provider": "codex",
            "config": {"profile_name": "Owner", "cdp_port": 9231},
            "effort": "medium",
            "model": None,
            "autowake_model": "claude-sonnet-4-6",
        }
        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value=settings)
        ), patch(
            "services.chatgpt_provider.stream_chatgpt", new=chatgpt_mock
        ), patch(
            "services.codex_app_server.stream_codex_app_server", new=codex_mock
        ):
            await provider_router.get_stream_source(
                message="tend your mind",
                identity="Claude",
                conversation_id="conv-auto",
                model_purpose="autowake",
                model_override="gpt-5.6-sol",
                provider_override="chatgpt",
            )

        self.assertTrue(chatgpt_mock.called)
        self.assertFalse(codex_mock.called)
        self.assertEqual(chatgpt_mock.call_args.kwargs["profile_name"], "Owner")
        self.assertEqual(chatgpt_mock.call_args.kwargs["cdp_port"], 9231)

    async def test_claude_code_provider_passes_image_blocks_through(self):
        """The persistent supervisor owns prompt composition. The router
        forwards image_blocks unchanged so the supervisor can fold them in
        as a text instruction on the appropriate turn."""
        stream_mock = Mock(return_value=iter(()))

        image_blocks = [
            {
                "type": "image",
                "source": {
                    "type": "url",
                    "url": "https://example.com/api/images/file/sample.png",
                },
            }
        ]

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value={
                "provider": "claude-code", "config": {},
                "effort": "medium", "model": None,
            })
        ), patch(
            "services.claude_subprocess.stream_claude",
            new=stream_mock,
        ):
            await provider_router.get_stream_source(
                message="tell me what you see",
                identity="Claude",
                conversation_id="conv-1",
                orientation_context="orientation",
                db_messages=[],
                image_blocks=image_blocks,
            )

        kwargs = stream_mock.call_args.kwargs
        self.assertEqual(kwargs["message"], "tell me what you see")
        self.assertEqual(kwargs["image_blocks"], image_blocks)
        self.assertEqual(kwargs["orientation_context"], "orientation")
        self.assertEqual(kwargs["identity"], "Claude")
        self.assertEqual(kwargs["conversation_id"], "conv-1")
        self.assertNotIn("resume_session", kwargs)

    async def test_identity_override_routes_to_different_provider(self):
        """Global provider claude-code, but an override sends one identity
        (e.g. Atlas) to codex — his stream must come from stream_codex."""
        codex_mock = Mock(return_value=iter(()))
        subprocess_mock = Mock(return_value=iter(()))

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value={
                "provider": "claude-code", "config": {},
                "effort": "medium", "model": None,
                "identity_overrides": {
                    "Atlas": {"provider": "codex", "config": {}},
                },
            })
        ), patch(
            "services.codex_app_server.stream_codex_app_server",
            new=codex_mock,
        ), patch(
            "services.claude_subprocess.stream_claude",
            new=subprocess_mock,
        ):
            await provider_router.get_stream_source(
                message="hello from atlas's session",
                identity="Atlas",
                conversation_id="conv-s",
            )
            await provider_router.get_stream_source(
                message="hello from claude's session",
                identity="Claude",
                conversation_id="conv-c",
            )

        self.assertTrue(codex_mock.called)
        self.assertEqual(
            codex_mock.call_args.kwargs["identity"], "Atlas",
        )
        self.assertTrue(subprocess_mock.called)
        self.assertEqual(
            subprocess_mock.call_args.kwargs["identity"], "Claude",
        )

    async def test_autowake_uses_background_model_without_changing_chat_model(self):
        stream_mock = Mock(return_value=iter(()))
        settings = {
            "provider": "claude-code",
            "config": {},
            "effort": "medium",
            "model": "claude-fable-5",
            "autowake_model": "claude-sonnet-4-6",
        }

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value=settings)
        ), patch(
            "services.claude_subprocess.stream_claude", new=stream_mock
        ):
            await provider_router.get_stream_source(
                message="background hour",
                identity="Claude",
                conversation_id="conv-bg",
                model_purpose="autowake",
            )
            self.assertEqual(
                stream_mock.call_args.kwargs["model"], "claude-sonnet-4-6"
            )

            await provider_router.get_stream_source(
                message="chat turn",
                identity="Claude",
                conversation_id="conv-chat",
            )
            self.assertEqual(
                stream_mock.call_args.kwargs["model"], "claude-fable-5"
            )

    async def test_gpt_model_override_reroutes_schedule_to_codex(self):
        """A per-schedule model override naming a GPT model (e.g. River's
        chapter reviews on gpt-5.6-sol) must reroute that one call to the
        Codex CLI even though the identity's provider is claude-code."""
        codex_mock = Mock(return_value=iter(()))
        subprocess_mock = Mock(return_value=iter(()))

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value={
                "provider": "claude-code", "config": {},
                "effort": "medium", "model": None,
                "autowake_model": "claude-sonnet-4-6",
            })
        ), patch(
            "services.codex_app_server.stream_codex_app_server",
            new=codex_mock,
        ), patch(
            "services.claude_subprocess.stream_claude",
            new=subprocess_mock,
        ):
            await provider_router.get_stream_source(
                message="review chapter twelve",
                identity="River",
                conversation_id="conv-r",
                model_purpose="autowake",
                model_override="gpt-5.6-sol",
            )

        self.assertTrue(codex_mock.called)
        self.assertFalse(subprocess_mock.called)
        self.assertEqual(codex_mock.call_args.kwargs["model"], "gpt-5.6-sol")

    async def test_codex_prefix_override_reroutes_and_strips_prefix(self):
        """The explicit "codex:<model>" form routes to Codex with the bare
        model id; a Claude model override still rides claude-code untouched."""
        codex_mock = Mock(return_value=iter(()))
        subprocess_mock = Mock(return_value=iter(()))

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value={
                "provider": "claude-code", "config": {},
                "effort": "medium", "model": None,
                "autowake_model": "claude-sonnet-4-6",
            })
        ), patch(
            "services.codex_app_server.stream_codex_app_server",
            new=codex_mock,
        ), patch(
            "services.claude_subprocess.stream_claude",
            new=subprocess_mock,
        ):
            await provider_router.get_stream_source(
                message="review chapter thirteen",
                identity="River",
                conversation_id="conv-r2",
                model_purpose="autowake",
                model_override="codex:gpt-5.6-sol",
            )
            await provider_router.get_stream_source(
                message="review chapter fourteen",
                identity="River",
                conversation_id="conv-r3",
                model_purpose="autowake",
                model_override="claude-fable-5",
            )

        self.assertEqual(codex_mock.call_args.kwargs["model"], "gpt-5.6-sol")
        self.assertTrue(subprocess_mock.called)
        self.assertEqual(
            subprocess_mock.call_args.kwargs["model"], "claude-fable-5"
        )

    async def test_codex_provider_injects_current_image_note(self):
        stream_mock = Mock(return_value=iter(()))

        with TemporaryDirectory() as tmpdir:
            image_dir = Path(tmpdir)
            (image_dir / "photo.png").write_bytes(b"png")

            with patch.object(
                provider_router, "_load_settings", new=AsyncMock(return_value={
                    "provider": "codex", "config": {},
                    "effort": "medium", "model": None,
                })
            ), patch(
                "services.codex_app_server.stream_codex_app_server",
                new=stream_mock,
            ), patch(
                "config.IMAGES_DIR",
                new=image_dir,
            ), patch(
                "services.cli_text_utils.IMAGES_DIR",
                new=image_dir,
            ):
                await provider_router.get_stream_source(
                    message="what's in this photo?",
                    identity="Claude",
                    conversation_id="conv-2",
                    image_blocks=[
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/api/images/file/photo.png",
                            },
                        }
                    ],
                )

        prompt = stream_mock.call_args.kwargs["message"]
        self.assertIn("[Owner shared an image:", prompt)
        self.assertIn("what's in this photo?", prompt)

    def test_history_format_keeps_image_note(self):
        from services import cli_text_utils as claude_subprocess  # back-compat alias for old test code

        with TemporaryDirectory() as tmpdir:
            image_dir = Path(tmpdir)
            (image_dir / "history.png").write_bytes(b"png")
            with patch("config.IMAGES_DIR", new=image_dir), \
                 patch.object(claude_subprocess, "IMAGES_DIR", new=image_dir):
                history = claude_subprocess._format_history(
                    db_messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "url",
                                        "url": "https://example.com/api/images/file/history.png",
                                    },
                                },
                                {"type": "text", "text": "do you remember this?"},
                            ],
                        }
                    ],
                    identity="Claude",
                )

        self.assertIn("[Owner shared an image:", history)
        self.assertIn("do you remember this?", history)

    def test_history_format_labels_assistant_with_identity(self):
        from services import cli_text_utils as claude_subprocess  # back-compat alias for old test code

        history = claude_subprocess._format_history(
            db_messages=[
                {"role": "assistant", "content": "I remember exactly where we left off."},
                {"role": "user", "content": "Pick it back up from there."},
            ],
            identity="Avery",
        )

        self.assertIn("Avery: I remember exactly where we left off.", history)
        self.assertNotIn("You: I remember exactly where we left off.", history)

    def test_history_format_preserves_original_identity_labels(self):
        from services import cli_text_utils as claude_subprocess  # back-compat alias for old test code

        history = claude_subprocess._format_history(
            db_messages=[
                {"role": "assistant", "identity": "Rowan", "content": "I already tucked that memory away."},
                {"role": "assistant", "identity": "Avery", "content": "I'll keep carrying it with you."},
            ],
            identity="Avery",
        )

        self.assertIn("Rowan: I already tucked that memory away.", history)
        self.assertIn("Avery: I'll keep carrying it with you.", history)
