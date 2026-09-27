"""Audio file → Groq Whisper transcription, with on-disk companion.

The pattern matches `services/docling_convert.py` for documents: when an
audio file is uploaded, we transcribe it once via Groq's whisper-large-v3-turbo
and cache the result in a sibling `.txt` file. Subsequent reads (e.g. when
the user re-attaches the same audio to a follow-up turn) reuse the cached
companion — no second API call.

The companion file is the source of truth the boys actually read. The
original audio file stays on disk for playback in the chat UI.
"""

# ANAM GUIDE: AUDIO TRANSCRIPT CACHE
# What: Transcribes an uploaded audio file once (via Groq Whisper) and saves the words in a .txt file right next to the audio, so it never has to be transcribed twice.
# Called by: api/audio.py (on upload), services/attachment_context.py (building prompts), services/inbound_voice.py and platform_bridge.py (voice notes from Discord/Telegram).
# Edit here when: changing where transcripts are cached or how a failed/missing transcription is handled. The actual Groq API call lives in services/groq_transcription.py.

from __future__ import annotations

import logging
from pathlib import Path

from config import AUDIO_MIME_BY_EXT
from services.groq_transcription import transcribe, is_available

log = logging.getLogger(__name__)


def companion_transcript_path(audio_path: Path) -> Path:
    """Return the expected path of the cached transcript next to the audio."""
    return audio_path.with_suffix(audio_path.suffix + ".txt")


def get_cached_transcript(audio_path: Path) -> str | None:
    """Read an already-transcribed companion, if one exists."""
    companion = companion_transcript_path(audio_path)
    if not companion.exists():
        return None
    try:
        return companion.read_text(encoding="utf-8").strip()
    except OSError:
        return None


async def transcribe_and_save(audio_path: Path) -> str | None:
    """Transcribe an audio file, save the transcript next to it, return the text.

    If a companion transcript already exists, returns that without re-transcribing.
    If Groq isn't configured or transcription fails, returns None and logs the
    reason — the caller is expected to fall back to a "no transcript available"
    note rather than dying.
    """
    if not audio_path.exists():
        log.warning("Audio transcription: file missing: %s", audio_path)
        return None

    cached = get_cached_transcript(audio_path)
    if cached is not None:
        return cached

    if not is_available():
        log.info(
            "Audio transcription skipped (no GROQ_API_KEY); %s saved without transcript",
            audio_path.name,
        )
        return None

    suffix = audio_path.suffix.lower()
    mime = AUDIO_MIME_BY_EXT.get(suffix, "audio/mpeg")
    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as e:
        log.warning("Audio transcription: failed to read %s: %s", audio_path, e)
        return None

    try:
        text = await transcribe(audio_bytes, mime_type=mime)
    except Exception as e:
        log.warning("Audio transcription failed for %s: %s", audio_path.name, e)
        return None

    text = (text or "").strip()
    companion = companion_transcript_path(audio_path)
    try:
        companion.write_text(text, encoding="utf-8")
        log.info(
            "Audio transcript cached: %s (%d audio bytes -> %d transcript chars)",
            audio_path.name, len(audio_bytes), len(text),
        )
    except OSError as e:
        log.warning("Audio transcription: failed to cache companion at %s: %s", companion, e)

    return text or None
