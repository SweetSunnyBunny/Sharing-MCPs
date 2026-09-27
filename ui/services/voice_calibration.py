"""Owner-labelled voice samples for personal speech-emotion calibration.

The recognizer's prediction is deliberately SHADOW data: it is saved beside
the audio for later evaluation, but never changes the message, prompt, or
limbic touch. Owner's one-tap label is the ground truth. This gives the next
emotion backend a corpus of her actual voice instead of treating a generic
training-population average as authority.
"""


from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AUDIO_ALLOWED_EXTENSIONS, AUDIO_DIR
from services.audio_transcription import companion_transcript_path, get_cached_transcript


CALIBRATION_LABELS = (
    {"key": "sad_soft", "label": "sad / soft", "emoji": "🥺"},
    {"key": "actually_angry", "label": "actually angry", "emoji": "🔥"},
    {"key": "tired", "label": "tired", "emoji": "😴"},
    {"key": "frustrated_not_angry", "label": "frustrated, not angry", "emoji": "😤"},
    {"key": "excited", "label": "excited", "emoji": "✨"},
    {"key": "happy_joyful", "label": "happy / joyful", "emoji": "😊"},
    {"key": "playfully_grumpy", "label": "playfully grumpy", "emoji": "😼"},
    {"key": "soft_clingy", "label": "soft / clingy", "emoji": "🐇"},
    {"key": "neutral", "label": "neutral", "emoji": "•"},
)
_LABEL_BY_KEY = {item["key"]: item for item in CALIBRATION_LABELS}
_AUDIO_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def calibration_options() -> list[dict[str, str]]:
    """Return fresh dictionaries so callers cannot mutate the canonical set."""
    return [dict(item) for item in CALIBRATION_LABELS]


def validate_label(label: str) -> str:
    key = (label or "").strip()
    if key not in _LABEL_BY_KEY:
        raise ValueError(f"Unknown voice calibration label: {key or '<empty>'}")
    return key


def resolve_audio_path(audio_id: str) -> Path | None:
    """Resolve a generated audio id without accepting filenames or traversal."""
    safe_id = (audio_id or "").strip().lower()
    if not _AUDIO_ID_RE.fullmatch(safe_id):
        return None
    for ext in sorted(AUDIO_ALLOWED_EXTENSIONS):
        candidate = AUDIO_DIR / f"{safe_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def prediction_sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".prosody.json")


def _extension_for_mime(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
        "audio/webm": ".webm",
    }.get(normalized, ".webm")


def save_shadow_prediction(
    audio_path: Path,
    predictions: list[dict[str, Any]] | None,
    *,
    model_id: str,
) -> None:
    """Keep a model's untrusted prediction beside the original audio."""
    if not predictions:
        return
    payload = {
        "shadow": True,
        "model_id": model_id,
        "predictions": predictions,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    prediction_sidecar_path(audio_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_shadow_prediction(audio_path: Path) -> dict[str, Any] | None:
    sidecar = prediction_sidecar_path(audio_path)
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def save_live_call_sample(
    audio_bytes: bytes,
    mime_type: str,
    transcript: str,
    predictions: list[dict[str, Any]] | None,
    *,
    model_id: str,
) -> dict[str, Any]:
    """Persist an accepted Wolf's Ear utterance so its button can label it.

    Presence-listen audio never calls this function and remains ephemeral.
    Untrusted model output stays in a sidecar and is not returned to the live
    page before Owner chooses her ground-truth label.
    """
    if not audio_bytes:
        raise ValueError("Cannot save an empty voice calibration sample")
    audio_id = uuid.uuid4().hex[:12]
    audio_path = AUDIO_DIR / f"{audio_id}{_extension_for_mime(mime_type)}"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_bytes)
    companion_transcript_path(audio_path).write_text(
        (transcript or "").strip(), encoding="utf-8"
    )
    save_shadow_prediction(audio_path, predictions, model_id=model_id)
    return {
        "audio_id": audio_id,
        "options": calibration_options(),
        "selected": None,
    }


async def get_calibration(db, audio_id: str) -> dict[str, Any] | None:
    audio_path = resolve_audio_path(audio_id)
    if audio_path is None:
        return None
    rows = await db.execute_fetchall(
        "SELECT label, target_identity, conversation_id, updated_at "
        "FROM voice_calibration_samples WHERE audio_id = ?",
        (audio_id.lower(),),
    )
    saved = dict(rows[0]) if rows else {}
    return {
        "audio_id": audio_id.lower(),
        "options": calibration_options(),
        "selected": saved.get("label"),
        "target_identity": saved.get("target_identity"),
        "conversation_id": saved.get("conversation_id"),
        "updated_at": saved.get("updated_at"),
        "shadow_prediction": load_shadow_prediction(audio_path),
    }


async def save_calibration(
    db,
    *,
    audio_id: str,
    label: str,
    target_identity: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Insert or correct Owner's ground-truth label for one stored clip."""
    label = validate_label(label)
    audio_path = resolve_audio_path(audio_id)
    if audio_path is None:
        raise FileNotFoundError(f"Audio clip not found: {audio_id}")

    prediction = load_shadow_prediction(audio_path)
    transcript = get_cached_transcript(audio_path) or ""
    now = datetime.now(timezone.utc).isoformat()
    epoch = int(time.time())
    await db.execute(
        "INSERT INTO voice_calibration_samples ("
        "audio_id, audio_filename, label, target_identity, conversation_id, "
        "transcript, prediction_json, created_at, created_at_epoch, updated_at, updated_at_epoch"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(audio_id) DO UPDATE SET "
        "label = excluded.label, target_identity = excluded.target_identity, "
        "conversation_id = excluded.conversation_id, transcript = excluded.transcript, "
        "prediction_json = excluded.prediction_json, updated_at = excluded.updated_at, "
        "updated_at_epoch = excluded.updated_at_epoch",
        (
            audio_id.lower(),
            audio_path.name,
            label,
            (target_identity or "").strip() or None,
            (conversation_id or "").strip() or None,
            transcript,
            json.dumps(prediction, ensure_ascii=False) if prediction else None,
            now,
            epoch,
            now,
            epoch,
        ),
    )
    await db.commit()
    state = await get_calibration(db, audio_id)
    assert state is not None
    return state


async def calibration_stats(db) -> dict[str, Any]:
    rows = await db.execute_fetchall(
        "SELECT label, COUNT(*) AS count FROM voice_calibration_samples "
        "GROUP BY label ORDER BY count DESC, label"
    )
    counts = {row["label"]: row["count"] for row in rows}
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "options": calibration_options(),
    }
