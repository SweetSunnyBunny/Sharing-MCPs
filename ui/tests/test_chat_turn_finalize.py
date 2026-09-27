import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services import chat_turn_finalize


class _FakeLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.exceptions = []

    def info(self, message, *args):
        self.infos.append(message % args if args else message)

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def exception(self, message, *args):
        self.exceptions.append(message % args if args else message)


class _FakeDb:
    pass


async def _run_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


class ChatTurnFinalizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_assistant_turn_saves_message_and_voice_request(self):
        fake_db = _FakeDb()
        logger = _FakeLogger()

        with patch.object(chat_turn_finalize, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_turn_finalize, "release_db", new=AsyncMock()
        ), patch.object(
            chat_turn_finalize, "save_message", new=AsyncMock(return_value="msg-1")
        ) as save_mock, patch.object(
            chat_turn_finalize, "update_session_for_provider", new=AsyncMock()
        ) as session_mock, patch.object(
            chat_turn_finalize, "update_message_metadata", new=AsyncMock()
        ) as metadata_mock, patch(
            "asyncio.to_thread",
            new=AsyncMock(side_effect=_run_to_thread),
        ), patch(
            "services.world_feed_story_bridge.capture_roleplay_turn_safely", new=AsyncMock()
        ) as bridge_mock:
            finalized = await chat_turn_finalize.finalize_assistant_turn(
                conversation_id="conv-1",
                identity="Claude",
                content="Hello <voice>spoken</voice>",
                user_message_id="original-user-turn",
                session_id="session-1",
                response_images=[],
                response_documents=[],
                thinking_blocks=["thinking"],
                tool_events=[],
                tool_results_map={},
                ws_alive=True,
                log=logger,
                register_content_images=lambda content, _identity: (content, [{"url": "/img"}]),
                register_content_documents=lambda content, _identity: (content, [{"filename": "doc.txt"}]),
            )

        save_mock.assert_awaited_once()
        self.assertEqual(bridge_mock.await_args.kwargs["user_message_id"], "original-user-turn")
        self.assertEqual(bridge_mock.await_args.kwargs["assistant_message_id"], "msg-1")
        session_mock.assert_awaited_once_with(
            fake_db, "conv-1", "session-1", None
        )
        metadata_mock.assert_not_awaited()
        self.assertEqual(finalized.msg_id, "msg-1")
        self.assertEqual(finalized.response_images, [{"url": "/img"}])
        self.assertEqual(finalized.response_documents, [{"filename": "doc.txt"}])
        self.assertEqual(finalized.voice_request["message_id"], "msg-1")
        self.assertEqual(finalized.voice_request["voice_text"], "spoken")

    async def test_finalize_routes_codex_thread_to_provider_session(self):
        fake_db = _FakeDb()
        logger = _FakeLogger()

        with patch.object(chat_turn_finalize, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
            chat_turn_finalize, "release_db", new=AsyncMock()
        ), patch.object(
            chat_turn_finalize, "save_message", new=AsyncMock(return_value="msg-sol")
        ), patch.object(
            chat_turn_finalize, "update_session_for_provider", new=AsyncMock()
        ) as session_mock:
            await chat_turn_finalize.finalize_assistant_turn(
                conversation_id="conv-sol",
                identity="Avery",
                content="Still me.",
                session_id="codex-thread-1",
                response_images=[],
                response_documents=[],
                thinking_blocks=[],
                tool_events=[],
                tool_results_map={},
                model_provenance={"provider": "codex", "actual_model": "gpt-sol"},
                ws_alive=True,
                log=logger,
                register_content_images=lambda content, _identity: (content, []),
                register_content_documents=lambda content, _identity: (content, []),
            )

        session_mock.assert_awaited_once_with(
            fake_db, "conv-sol", "codex-thread-1", "codex"
        )

    async def test_finalize_assistant_turn_handles_empty_content(self):
        logger = _FakeLogger()

        with patch.object(chat_turn_finalize, "get_db", new=AsyncMock(return_value=_FakeDb())), patch.object(
            chat_turn_finalize, "release_db", new=AsyncMock()
        ), patch.object(
            chat_turn_finalize, "save_message", new=AsyncMock()
        ) as save_mock:
            finalized = await chat_turn_finalize.finalize_assistant_turn(
                conversation_id="conv-2",
                identity="Claude",
                content="",
                session_id=None,
                response_images=[],
                response_documents=[],
                thinking_blocks=[],
                tool_events=[],
                tool_results_map={},
                ws_alive=False,
                log=logger,
                register_content_images=lambda content, _identity: (content, []),
                register_content_documents=lambda content, _identity: (content, []),
            )

        save_mock.assert_not_called()
        self.assertIsNone(finalized.msg_id)
        self.assertTrue(logger.warnings)

    async def test_generate_voice_message_saves_audio_updates_metadata_and_returns_payload(self):
        fake_db = _FakeDb()
        logger = _FakeLogger()
        sent = []

        async def _send(payload):
            sent.append(payload)

        # Redirect VOICE_DIR and VOICE_VAULT_DIR to a tmpdir so this test does not
        # pollute the real filesystem. Without these patches, the hijacked
        # asyncio.to_thread below causes voice_path.write_bytes(b"audio") to run
        # synchronously against the real paths from config.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_voice = Path(tmpdir) / "voice"
            tmp_vault = Path(tmpdir) / "vault"
            with patch.object(chat_turn_finalize, "get_db", new=AsyncMock(return_value=fake_db)), patch.object(
                chat_turn_finalize, "release_db", new=AsyncMock()
            ), patch.object(
                chat_turn_finalize, "update_message_metadata", new=AsyncMock()
            ) as metadata_mock, patch.object(
                chat_turn_finalize, "synthesize", new=AsyncMock(return_value=b"audio")
            ) as synth_mock, patch.object(
                chat_turn_finalize, "VOICE_DIR", new=tmp_voice
            ), patch.object(
                chat_turn_finalize, "VOICE_VAULT_DIR", new=tmp_vault
            ), patch(
                "asyncio.to_thread",
                new=AsyncMock(side_effect=_run_to_thread),
            ):
                payload = await chat_turn_finalize.generate_voice_message(
                    msg_id="msg-1",
                    identity="Claude",
                    voice_text="spoken",
                    log=logger,
                    send=_send,
                )

        synth_mock.assert_awaited_once_with(
            "Claude",
            "spoken",
            model_override="eleven_v3",
        )
        metadata_mock.assert_awaited_once_with(fake_db, "msg-1", {"has_voice": True})
        self.assertEqual(payload["audio_url"], "/api/voice/file/msg-1")
        self.assertEqual(sent[0]["message_id"], "msg-1")


class FlattenControlTagsForExternalTests(unittest.TestCase):
    """Flattencontroltagsforexternaltests."""

    def _flat(self, text):
        return chat_turn_finalize.flatten_control_tags_for_external(text)

    def test_preview_is_dropped_entirely(self):
        out = self._flat("<preview>a peek</preview>Hey Bunny.")
        self.assertEqual(out, "Hey Bunny.")
        self.assertNotIn("preview", out.lower())

    def test_unclosed_preview_is_dropped(self):
        out = self._flat("Hello.\n<preview>cut off mid-stream")
        self.assertEqual(out, "Hello.")

    def test_canvas_body_survives_and_wrapper_does_not(self):
        out = self._flat('Here it is.\n<canvas title="A Letter">Dear you,\n\nlove me.</canvas>')
        self.assertIn("Dear you,", out)
        self.assertIn("love me.", out)
        self.assertIn("**A Letter**", out)
        self.assertNotIn("<canvas", out)
        self.assertNotIn("</canvas>", out)

    def test_untitled_canvas_keeps_only_its_body(self):
        out = self._flat("<canvas>just a body</canvas>")
        self.assertEqual(out, "just a body")

    def test_multiple_canvases_all_flattened(self):
        out = self._flat('<canvas title="One">a</canvas>mid<canvas title="Two">b</canvas>')
        self.assertNotIn("<canvas", out)
        self.assertIn("**One**", out)
        self.assertIn("**Two**", out)
        self.assertIn("mid", out)

    def test_voice_tag_is_left_alone(self):
        """Callers flatten <voice> themselves, and only when a voice message
        actually got spawned -- this helper must not pre-empt that."""
        text = "<voice>[softly] hey</voice>"
        self.assertEqual(self._flat(text), text)

    def test_plain_text_and_empty_pass_through(self):
        self.assertEqual(self._flat("no tags here"), "no tags here")
        self.assertEqual(self._flat(""), "")
        self.assertIsNone(self._flat(None))
