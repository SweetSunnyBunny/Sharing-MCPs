"""Inbound voice-note transcription for the platform bridges.

Telegram voice notes and Discord audio attachments used to arrive as the
contentless placeholder "[Owner sent a voice message]" even though the
audio file was already downloaded. This module turns the downloaded file
into real words via services.audio_transcription (Groq Whisper with a
cached .txt companion — no double-billing), formatted the same way Friend's
resonant reference does:

    [Voice message from Owner, 12s] "the actual transcript"

Every helper here is deliberately never-raise: a transcription failure
must degrade to the old placeholder, not kill the inbound path.
"""


from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AUDIO_ALLOWED_EXTENSIONS, AUDIO_DIR
from services.audio_transcription import transcribe_and_save

log = logging.getLogger(__name__)

# One line appended to the platform prompt when the inbound was spoken —
# the existing <voice> → ElevenLabs tag path does the rest. We only hint;
# we never auto-convert the reply.
VOICE_REPLY_HINT = (
    "Owner sent this by voice — a <voice> reply will reach her as a real voice note."
)

VOICE_PLACEHOLDER = "[Owner sent a voice message]"


def format_voice_line(
    transcript: str | None,
    duration_seconds: float | int | None = None,
) -> str:
    """Render the inbound-context line for a voice note.

    With a transcript: `[Voice message from Owner, 12s] "words"`.
    Without one (Groq missing/failed): the old placeholder, so the boys
    still know she spoke even when we couldn't hear the words.
    """
    if not transcript:
        return VOICE_PLACEHOLDER
    duration_note = ""
    try:
        if duration_seconds and float(duration_seconds) > 0:
            duration_note = f", {int(float(duration_seconds))}s"
    except (TypeError, ValueError):
        pass
    return f'[Voice message from Owner{duration_note}] "{transcript}"'


async def transcribe_local_audio(path: Path | str | None) -> str | None:
    """transcribe_and_save with a never-raise wrapper for inbound paths."""
    if not path:
        return None
    try:
        return await transcribe_and_save(Path(path))
    except Exception as exc:  # never let audio kill an inbound message
        log.warning("Inbound voice transcription failed for %s: %s", path, exc)
        return None


def is_audio_attachment(attachment: dict[str, Any]) -> bool:
    """True when a Discord attachment dict is an audio file (voice note or clip)."""
    content_type = str(attachment.get("content_type") or "").lower()
    if content_type.startswith("audio/"):
        return True
    # A declared video type is never audio — .webm video clips share the
    # extension with audio and must keep taking the document/video path.
    if content_type.startswith("video/"):
        return False
    ext = Path(str(attachment.get("filename") or "")).suffix.lower()
    return ext in AUDIO_ALLOWED_EXTENSIONS


async def transcribe_discord_attachment(
    attachment: dict[str, Any],
    identity: str,
) -> str | None:
    """Download a Discord audio attachment into AUDIO_DIR and transcribe it.

    For callers that don't already persist attachments (the mentions
    bridge). Returns the transcript text, or None on any failure.
    """
    cdn_url = attachment.get("url") or attachment.get("proxy_url")
    if not cdn_url:
        return None
    ext = Path(str(attachment.get("filename") or "voice.ogg")).suffix.lower() or ".ogg"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(cdn_url)
            if resp.status_code != 200:
                log.warning(
                    "Discord audio download failed (%d) for %s",
                    resp.status_code, attachment.get("filename"),
                )
                return None
            content = resp.content
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = AUDIO_DIR / f"{identity.lower()}_{ts}_{uuid.uuid4().hex[:12]}{ext}"
        await asyncio.to_thread(AUDIO_DIR.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, content)
    except Exception as exc:
        log.warning("Discord audio fetch error: %s", exc)
        return None
    return await transcribe_local_audio(dest)
