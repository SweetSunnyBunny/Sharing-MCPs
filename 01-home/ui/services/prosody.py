'Prosody facade — one door for "how did she sound", whatever runs behind it.'

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
