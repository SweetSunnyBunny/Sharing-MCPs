"""Prosody facade — one door for "how did she sound", whatever runs behind it.

ANAM GUIDE: PROSODY FACADE
What: The single import point for voice-tone analysis. Prefers the local
      on-device model (services/local_prosody.py); falls back to Hume only if
      local deps are missing AND a key is configured (Hume discontinued the
      Expression Measurement API in 2026, so that path is effectively
      historical). Contract everywhere: [{"emotion": str, "score": float}, ...]
      best-first, or None. Enrichment only — callers must never let this block
      or fail a transcript.
Loaded by: services/voice_call.py, api/voice.py (/transcribe).
Edit here when: changing provider preference or adding a new tone backend.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def is_available() -> bool:
    from services import local_prosody

    if local_prosody.is_available():
        return True
    from services import hume_prosody

    return hume_prosody.is_available()


async def analyze_prosody(
    audio_bytes: bytes, mime_type: str = "audio/webm"
) -> list[dict[str, Any]] | None:
    from services import local_prosody

    if local_prosody.is_available():
        return await local_prosody.analyze_prosody(audio_bytes, mime_type)
    from services import hume_prosody

    if hume_prosody.is_available():
        return await hume_prosody.analyze_prosody(audio_bytes, mime_type)
    return None
