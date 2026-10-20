import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.inbound_voice import (
    VOICE_PLACEHOLDER,
    format_voice_line,
    is_audio_attachment,
    transcribe_local_audio,
)
from services.platform_bridge import _persist_downloaded_media


class FormatVoiceLineTests(unittest.TestCase):
    def test_transcript_with_duration(self):
        self.assertEqual(
            format_voice_line("hi love", 12),
            '[Voice message from Owner, 12s] "hi love"',
        )

    def test_transcript_without_duration(self):
        self.assertEqual(
            format_voice_line("hi love"),
            '[Voice message from Owner] "hi love"',
        )

    def test_float_duration_is_truncated(self):
        self.assertEqual(
            format_voice_line("hey", 4.71),
            '[Voice message from Owner, 4s] "hey"',
        )

    def test_no_transcript_falls_back_to_placeholder(self):
        self.assertEqual(format_voice_line(None, 9), VOICE_PLACEHOLDER)
        self.assertEqual(format_voice_line(""), VOICE_PLACEHOLDER)


class IsAudioAttachmentTests(unittest.TestCase):
    def test_detects_by_content_type(self):
        self.assertTrue(
            is_audio_attachment(
                {"filename": "voice-message.bin", "content_type": "audio/ogg"}
            )
        )

    def test_detects_by_extension(self):
        self.assertTrue(is_audio_attachment({"filename": "memo.m4a"}))

    def test_rejects_non_audio(self):
        self.assertFalse(
            is_audio_attachment(
                {"filename": "photo.png", "content_type": "image/png"}
            )
        )


class TranscribeLocalAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_when_transcriber_raises(self):
        with patch(
            "services.inbound_voice.transcribe_and_save",
            side_effect=RuntimeError("groq down"),
        ):
            self.assertIsNone(await transcribe_local_audio("C:/nope/voice.ogg"))

    async def test_returns_none_for_missing_path(self):
        self.assertIsNone(await transcribe_local_audio(None))
        self.assertIsNone(await transcribe_local_audio(""))


class PersistAudioMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_routes_to_audio_dir_with_local_path(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            images_dir = root / "images"
            docs_dir = root / "documents"
            audio_dir = root / "audio"
            with patch("services.platform_bridge.IMAGES_DIR", images_dir), patch(
                "services.platform_bridge.DOCUMENTS_DIR", docs_dir
            ), patch("services.platform_bridge.AUDIO_DIR", audio_dir):
                result = await _persist_downloaded_media("Avery", ".oga", b"ogg")
                audio_exists = (audio_dir / result["filename"]).exists()

            self.assertEqual(result["type"], "audio")
            self.assertTrue(audio_exists)
            self.assertTrue(result["url"].startswith("/api/audio/file/"))
            self.assertEqual(result["path"], str(audio_dir / result["filename"]))

    async def test_webm_still_routes_to_documents_for_video_watch(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch(
                "services.platform_bridge.IMAGES_DIR", root / "images"
            ), patch(
                "services.platform_bridge.DOCUMENTS_DIR", root / "documents"
            ), patch("services.platform_bridge.AUDIO_DIR", root / "audio"):
                result = await _persist_downloaded_media("Avery", ".webm", b"vid")

            self.assertEqual(result["type"], "document")
