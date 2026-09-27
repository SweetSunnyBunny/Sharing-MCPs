import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse, Response

from api import voice


class _FakeAudioFile:
    def __init__(self, data=b"audio-bytes", content_type="audio/webm"):
        self._data = data
        self.content_type = content_type

    async def read(self):
        return self._data


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only .form() is used by /transcribe."""

    def __init__(self, form_data: dict):
        self._form_data = form_data

    async def form(self):
        return self._form_data


class VoiceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_local_returns_wav_when_kokoro_succeeds(self):
        with patch.object(voice, "kokoro_available", return_value=True), patch.object(
            voice, "synthesize_sync", return_value=b"wav-bytes"
        ):
            response = await voice.tts_local({"identity": "Rowan", "text": "hello"})

        self.assertIsInstance(response, Response)
        self.assertEqual(response.media_type, "audio/wav")
        self.assertEqual(response.body, b"wav-bytes")

    async def test_tts_stream_returns_ndjson_chunks(self):
        with patch.object(voice, "kokoro_available", return_value=True), patch.object(
            voice, "synthesize_chunks_sync", return_value=iter([b"a", b"b"])
        ):
            response = await voice.tts_stream({"identity": "Rowan", "text": "hello there"})
            body = b""
            async for chunk in response.body_iterator:
                body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk

        lines = [line for line in body.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        payloads = [json.loads(line) for line in lines]
        self.assertEqual(payloads[0]["audio"], "YQ==")
        self.assertEqual(payloads[1]["audio"], "Yg==")

    async def test_voice_file_returns_404_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(voice, "VOICE_DIR", Path(tmp)):
                response = await voice.serve_voice_file("missing")

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 404)


class TranscribeProsodyTests(unittest.IsolatedAsyncioTestCase):
    """Item #16: /transcribe runs Groq transcription + Hume prosody concurrently.

    Prosody is enrichment only — it must never block, fail, or change the
    transcript, and it should feed a limbic touch only when it actually
    succeeds and an identity was supplied.
    """

    async def test_returns_prosody_alongside_transcript_when_hume_succeeds(self):
        request = _FakeRequest({"audio": _FakeAudioFile(), "identity": "Claude"})
        with patch(
            "services.groq_transcription.is_available", return_value=True
        ), patch(
            "services.groq_transcription.transcribe",
            new=AsyncMock(return_value="hello love"),
        ), patch(
            "services.prosody.analyze_prosody",
            new=AsyncMock(return_value=[{"emotion": "Joy", "score": 0.72}]),
        ), patch(
            "services.limbic_bridge.touch_from_prosody"
        ) as touch:
            response = await voice.voice_transcribe(request)

        self.assertIsInstance(response, JSONResponse)
        body = json.loads(response.body)
        self.assertEqual(body["text"], "hello love")
        self.assertEqual(body["prosody"], [{"emotion": "Joy", "score": 0.72}])
        touch.assert_called_once_with("Claude", "Joy", 0.72)

    async def test_prosody_is_null_when_hume_unconfigured(self):
        request = _FakeRequest({"audio": _FakeAudioFile()})
        with patch(
            "services.groq_transcription.is_available", return_value=True
        ), patch(
            "services.groq_transcription.transcribe",
            new=AsyncMock(return_value="hello"),
        ), patch(
            "services.prosody.analyze_prosody", new=AsyncMock(return_value=None)
        ):
            response = await voice.voice_transcribe(request)

        body = json.loads(response.body)
        self.assertEqual(body["text"], "hello")
        self.assertIsNone(body["prosody"])

    async def test_prosody_exception_never_breaks_transcription(self):
        """asyncio.gather(..., return_exceptions=True): a Hume-side blowup
        must surface as prosody=null, not as a failed transcription."""
        request = _FakeRequest({"audio": _FakeAudioFile()})
        with patch(
            "services.groq_transcription.is_available", return_value=True
        ), patch(
            "services.groq_transcription.transcribe",
            new=AsyncMock(return_value="still transcribed"),
        ), patch(
            "services.prosody.analyze_prosody",
            new=AsyncMock(side_effect=RuntimeError("hume exploded")),
        ):
            response = await voice.voice_transcribe(request)

        self.assertIsInstance(response, JSONResponse)
        body = json.loads(response.body)
        self.assertEqual(body["text"], "still transcribed")
        self.assertIsNone(body["prosody"])

    async def test_no_limbic_touch_without_identity(self):
        request = _FakeRequest({"audio": _FakeAudioFile()})  # no identity field
        with patch(
            "services.groq_transcription.is_available", return_value=True
        ), patch(
            "services.groq_transcription.transcribe",
            new=AsyncMock(return_value="hi"),
        ), patch(
            "services.prosody.analyze_prosody",
            new=AsyncMock(return_value=[{"emotion": "Joy", "score": 0.9}]),
        ), patch(
            "services.limbic_bridge.touch_from_prosody"
        ) as touch:
            await voice.voice_transcribe(request)

        touch.assert_not_called()

    async def test_transcription_failure_still_returns_error(self):
        request = _FakeRequest({"audio": _FakeAudioFile()})
        with patch(
            "services.groq_transcription.is_available", return_value=True
        ), patch(
            "services.groq_transcription.transcribe",
            new=AsyncMock(side_effect=RuntimeError("groq down")),
        ), patch(
            "services.prosody.analyze_prosody", new=AsyncMock(return_value=None)
        ):
            response = await voice.voice_transcribe(request)

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
