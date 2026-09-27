"""REST: Audio endpoints — upload + serve.

Pairs with services.audio_transcription so every uploaded clip gets a
companion transcript that the boys actually read. The original audio
file stays playable in the chat UI.
"""

# ANAM GUIDE: AUDIO UPLOAD AND PLAYBACK ROUTES
# What: /api/audio/upload (save a voice memo + transcribe it right away) and /api/audio/file/{name} (stream it back for the chat player).
# Called by: static/js/chat.js (the mic/attach flow on the chat page) and services/platform_bridge.py; transcription itself lives in services/audio_transcription.py.
# Edit here when: Changing allowed audio types, the size limit, upload safety checks, or how audio files are served — not for transcription logic.

import asyncio
import logging
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import (
    AUDIO_DIR,
    AUDIO_ALLOWED_EXTENSIONS,
    AUDIO_MAX_SIZE_MB,
    AUDIO_MIME_BY_EXT,
    TIMEZONE,
)
from services.audio_transcription import transcribe_and_save
from services.rate_limit import limiter
from services.voice_calibration import (
    calibration_options,
    calibration_stats,
    get_calibration,
    save_calibration,
    save_shadow_prediction,
)

# Same executable-magic reject as documents.py — somebody renaming an EXE
# to .mp3 should still bounce.
_EXECUTABLE_SIGNATURES = [
    b"MZ",        # Windows PE executable
    b"\x7fELF",   # Linux ELF executable
]


def _is_executable(data: bytes) -> bool:
    for sig in _EXECUTABLE_SIGNATURES:
        if data[:len(sig)] == sig:
            return True
    return False


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio")


def human_size(size_bytes: int) -> str:
    if size_bytes > 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    return f"{round(size_bytes / 1024)} KB"


def informative_filename(identity: str | None, ext: str) -> str:
    """Generate {identity}_{YYYYMMDD}_{HHMMSS}_{4hex}.{ext}."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    ts = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:4]
    name = identity.lower() if identity else "unknown"
    return f"{name}_{ts}_{short_id}{ext}"


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_audio(request: Request, file: UploadFile = File(...)):
    """Upload an audio file. Returns {audio_id, filename, original_name,
    size_display, duration_seconds, url, transcript, transcribed}.

    The transcription is performed inline before returning so the caller
    sees the text immediately — typical voice memos transcribe in <2s on
    Groq's whisper-large-v3-turbo. Larger files (15+ minutes) may take
    longer; the upload still completes either way.
    """
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No filename"})

    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"Audio type {ext} not allowed. Supported: "
                    + ", ".join(sorted(AUDIO_ALLOWED_EXTENSIONS))
                )
            },
        )

    try:
        data = await file.read()
    finally:
        await file.close()
    if len(data) > AUDIO_MAX_SIZE_MB * 1024 * 1024:
        return JSONResponse(
            status_code=400,
            content={"error": f"Audio file too large (max {AUDIO_MAX_SIZE_MB}MB)"},
        )

    if _is_executable(data):
        return JSONResponse(
            status_code=400,
            content={"error": "Executable files are not allowed"},
        )

    audio_id = uuid.uuid4().hex[:12]
    filename = f"{audio_id}{ext}"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    filepath = AUDIO_DIR / filename
    filepath.write_bytes(data)

    log.info(
        "Audio uploaded: %s -> %s (%d bytes)",
        file.filename, filename, len(data),
    )


    from services.local_prosody import MODEL_ID as SHADOW_MODEL_ID
    from services.prosody import analyze_prosody

    mime_type = AUDIO_MIME_BY_EXT.get(ext, "audio/mpeg")

    async def _bounded_shadow_prediction():
        return await asyncio.wait_for(
            analyze_prosody(data, mime_type), timeout=3.0
        )

    transcript_result, prediction_result = await asyncio.gather(
        transcribe_and_save(filepath),
        _bounded_shadow_prediction(),
        return_exceptions=True,
    )
    transcript = None if isinstance(transcript_result, Exception) else transcript_result
    prediction = None if isinstance(prediction_result, Exception) else prediction_result
    if isinstance(prediction_result, Exception):
        log.debug("Shadow voice prediction skipped for %s: %s", filename, prediction_result)
    if prediction:
        try:
            save_shadow_prediction(filepath, prediction, model_id=SHADOW_MODEL_ID)
        except OSError as exc:
            # The voice note and transcript are still valid. Losing a shadow
            # guess must never strand her attachment.
            log.warning("Could not cache shadow prediction for %s: %s", filename, exc)
    transcribed = transcript is not None

    return JSONResponse(
        content={
            "audio_id": audio_id,
            "filename": filename,
            "original_name": file.filename,
            "size_display": human_size(len(data)),
            "url": f"/api/audio/file/{filename}",
            "transcribed": transcribed,
            "transcript": transcript or "",
            "calibration": {
                "options": calibration_options(),
                "selected": None,
                "shadow_prediction": {
                    "shadow": True,
                    "model_id": SHADOW_MODEL_ID,
                    "predictions": prediction,
                } if prediction else None,
            },
        }
    )


@router.get("/calibration/{audio_id}")
async def get_voice_calibration(audio_id: str):
    """Return buttons, saved ground truth, and the powerless shadow guess."""
    from db.database import get_db, release_db

    db = await get_db()
    try:
        state = await get_calibration(db, audio_id)
    finally:
        await release_db(db)
    if state is None:
        return JSONResponse(status_code=404, content={"error": "Audio clip not found"})
    return JSONResponse(content=state)


@router.post("/calibration")
async def label_voice_calibration(data: dict):
    """Save or correct Owner's one-tap ground-truth label for a voice clip."""
    from db.database import get_db, release_db

    audio_id = str(data.get("audio_id") or "").strip().lower()
    label = str(data.get("label") or "").strip()
    if not audio_id or not label:
        return JSONResponse(
            status_code=400,
            content={"error": "audio_id and label are required"},
        )

    db = await get_db()
    try:
        try:
            state = await save_calibration(
                db,
                audio_id=audio_id,
                label=label,
                target_identity=data.get("target_identity"),
                conversation_id=data.get("conversation_id"),
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except FileNotFoundError:
            return JSONResponse(status_code=404, content={"error": "Audio clip not found"})
    finally:
        await release_db(db)
    return JSONResponse(content=state)


@router.get("/calibration-stats")
async def voice_calibration_stats():
    """Small corpus counter for the later calibration/evaluation screen."""
    from db.database import get_db, release_db

    db = await get_db()
    try:
        stats = await calibration_stats(db)
    finally:
        await release_db(db)
    return JSONResponse(content=stats)


@router.get("/file/{filename}")
async def serve_audio(filename: str):
    """Serve a stored audio file for inline playback."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    filepath = AUDIO_DIR / safe_name
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "Audio not found"})

    suffix = Path(safe_name).suffix.lower()
    media_type = (
        AUDIO_MIME_BY_EXT.get(suffix)
        or mimetypes.guess_type(safe_name)[0]
        or "application/octet-stream"
    )
    # No Content-Disposition: we want browsers to stream/play in-place.
    return FileResponse(
        filepath,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Accept-Ranges": "bytes",
        },
    )
