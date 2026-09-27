"""Groq Whisper speech-to-text transcription service.

Uses Groq's OpenAI-compatible API for fast, cheap audio transcription.
$0.04/hour for whisper-large-v3-turbo.
"""

# ANAM GUIDE: GROQ VOICE-TO-TEXT
# What: Sends recorded audio (like your voice messages) to Groq's fast, cheap Whisper service and returns the words as text.
# Called by: api/voice.py and services/audio_transcription.py when a voice note arrives; scripts/index_voice_archive.py for bulk transcribing old recordings.
# Edit here when: You want to switch the Whisper model, handle a new audio format, or change how transcription errors are handled. The API key lives in .env (GROQ_API_KEY), not here.

import logging
import os

import httpx

from config import GROQ_API_KEY, GROQ_WHISPER_MODEL

log = logging.getLogger(__name__)

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def is_available() -> bool:
    """Check if Groq transcription is configured."""
    return bool(GROQ_API_KEY)


async def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Transcribe audio using Groq's Whisper API.

    Args:
        audio_bytes: Raw audio data
        mime_type: MIME type of the audio (audio/webm, audio/wav, etc.)

    Returns:
        Transcribed text string

    Raises:
        ValueError: If Groq API key is not configured
        httpx.HTTPStatusError: If the API returns an error
    """
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured")

    # Map mime type to file extension
    ext_map = {
        "audio/webm": "webm",
        "audio/wav": "wav",
        "audio/mp3": "mp3",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
    }
    ext = ext_map.get(mime_type, "webm")
    filename = f"audio.{ext}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GROQ_TRANSCRIPTION_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (filename, audio_bytes, mime_type)},
            data={
                "model": GROQ_WHISPER_MODEL,
                "response_format": "json",
                "language": "en",
                "prompt": os.environ.get("ANAM_TRANSCRIPTION_VOCABULARY", "Anam, Qualia."),
            },
        )
        response.raise_for_status()
        result = response.json()
        text = result.get("text", "").strip()
        # The supervised Windows stack may expose a cp1252 stderr stream.
        # Keep this message ASCII so a successful transcription does not emit
        # a misleading UnicodeEncodeError traceback after the request succeeds.
        log.info("Groq transcription: %d bytes -> %d chars", len(audio_bytes), len(text))
        return text
