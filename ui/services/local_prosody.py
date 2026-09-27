"""Reusable local prosody support."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from typing import Any

log = logging.getLogger(__name__)


MODEL_ID = "superb/wav2vec2-base-superb-er"

# Only the first N seconds of an utterance are analyzed — tone is established
# early, and clipping keeps CPU inference inside the caller's latency budget.
ANALYZE_SECONDS = 10
SAMPLE_RATE = 16000

# Model labels -> the vocabulary the identities read. Chosen to line up with
# what the old Hume annotations looked like in context hooks.
_LABEL_MAP = {
    "neu": "Neutral",
    "hap": "Joy",
    "ang": "Anger",
    "sad": "Sadness",
}

_classifier = None
_load_lock = threading.Lock()
_load_failed = False


def is_available() -> bool:
    if _load_failed:
        return False
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _get_classifier():
    """Lazy singleton; safe under the executor's thread pool."""
    global _classifier, _load_failed
    if _classifier is not None:
        return _classifier
    with _load_lock:
        if _classifier is not None:
            return _classifier
        try:
            from transformers import pipeline

            _classifier = pipeline(
                "audio-classification", model=MODEL_ID, device=-1, top_k=None
            )
            log.info("Local prosody model loaded: %s", MODEL_ID)
        except Exception:
            _load_failed = True
            log.exception("Local prosody model failed to load")
            raise
    return _classifier


def _decode_to_waveform(audio_bytes: bytes) -> Any:
    """Any container (webm/opus, wav, mp3) -> 16 kHz mono float32 via ffmpeg."""
    import numpy as np

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-t", str(ANALYZE_SECONDS),
            "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
        timeout=20,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr[:200]!r}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _analyze_sync(audio_bytes: bytes) -> list[dict[str, Any]] | None:
    waveform = _decode_to_waveform(audio_bytes)
    if waveform.size < SAMPLE_RATE // 4:  # under a quarter second: nothing to read
        return None
    classifier = _get_classifier()
    raw = classifier({"array": waveform, "sampling_rate": SAMPLE_RATE})
    results = [
        {
            "emotion": _LABEL_MAP.get(str(item.get("label", "")).lower(), str(item.get("label", ""))),
            "score": round(float(item.get("score", 0.0)), 4),
        }
        for item in raw
    ]
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:5] or None


async def analyze_prosody(
    audio_bytes: bytes, mime_type: str = "audio/webm"
) -> list[dict[str, Any]] | None:
    """Async facade matching hume_prosody's contract. Never raises to callers
    that gather with return_exceptions — but is also safe called bare."""
    if not is_available():
        return None
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _analyze_sync, audio_bytes)
    except Exception as exc:
        log.debug("Local prosody analysis failed (non-fatal): %s", exc)
        return None
