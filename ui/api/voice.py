"""REST: Voice endpoints — Kokoro (free) + ElevenLabs (premium)."""

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from config import VOICE_DIR, VOICE_VAULT_DIR, VOICE_ALEXA_ARCHIVE_DIR
from services.elevenlabs_tts import synthesize as elevenlabs_synthesize
from services.local_tts import (
    synthesize_sync, synthesize_chunks_sync, is_available as kokoro_available,
)

log = logging.getLogger(__name__)


from services.local_tts import VOICE_EXECUTOR

router = APIRouter(prefix="/api/voice")


def _looks_like_playable_mp3(path) -> bool:
    try:
        if path.stat().st_size < 1024:
            return False
        header = path.read_bytes()[:3]
        return header == b"ID3" or header[:1] == b"\xff"
    except OSError:
        return False


@router.post("/tts")
async def tts_local(data: dict):
    """Generate speech via Kokoro (free, local). For play-any-message buttons."""
    text = data.get("text", "").strip()
    identity = data.get("identity", "")

    if not text:
        return JSONResponse(status_code=400, content={"error": "No text provided"})

    if not kokoro_available():
        return JSONResponse(status_code=503, content={"error": "Local TTS not available"})

    # Run sync Kokoro in thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(VOICE_EXECUTOR, synthesize_sync, text, identity)

    if audio:
        return Response(content=audio, media_type="audio/wav")

    return JSONResponse(status_code=500, content={"error": "TTS generation failed"})


@router.post("/tts-eleven")
async def tts_elevenlabs(data: dict):
    """Generate speech in the identity's REAL ElevenLabs voice, from raw text."""
    text = (data.get("text") or "").strip()
    identity = data.get("identity", "")

    if not text:
        return JSONResponse(status_code=400, content={"error": "No text provided"})

    try:
        audio = await elevenlabs_synthesize(identity, text)
    except Exception as exc:
        log.warning("ElevenLabs TTS failed for %s: %s", identity, exc)
        return JSONResponse(status_code=502, content={"error": "ElevenLabs TTS failed"})

    if audio:
        return Response(content=audio, media_type="audio/mpeg")

    return JSONResponse(status_code=502, content={"error": "ElevenLabs returned no audio"})


@router.post("/tts-stream")
async def tts_stream(data: dict):
    """Generate speech in streamed chunks — each sentence group as a separate WAV.

    Returns NDJSON where each line is {"audio": "<base64 WAV bytes>"}.
    The client can start playing the first chunk while later ones are still being synthesized.
    """
    text = data.get("text", "").strip()
    identity = data.get("identity", "")

    if not text:
        return JSONResponse(status_code=400, content={"error": "No text provided"})

    if not kokoro_available():
        return JSONResponse(status_code=503, content={"error": "Local TTS not available"})

    loop = asyncio.get_event_loop()

    async def generate():
        # Use a queue to bridge sync generator → async streaming
        queue: asyncio.Queue = asyncio.Queue()

        def _produce():
            for wav_bytes in synthesize_chunks_sync(text, identity):
                # Put chunk into the queue from the thread pool
                asyncio.run_coroutine_threadsafe(queue.put(wav_bytes), loop)
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)  # sentinel

        # Run sync generator in thread pool
        loop.run_in_executor(VOICE_EXECUTOR, _produce)

        while True:
            wav_bytes = await queue.get()
            if wav_bytes is None:
                break
            encoded = base64.b64encode(wav_bytes).decode("ascii")
            yield json.dumps({"audio": encoded}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.get("/file/{message_id}")
async def serve_voice_file(message_id: str):
    """Serve a stored ElevenLabs voice message audio file."""
    # Sanitize to prevent path traversal
    safe_id = "".join(c for c in message_id if c.isalnum() or c == "-")
    filepath = VOICE_DIR / f"{safe_id}.mp3"

    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "Voice file not found"})

    if not _looks_like_playable_mp3(filepath):
        log.warning("Rejecting invalid voice file for %s: %s", message_id, filepath)
        return JSONResponse(status_code=410, content={"error": "Voice file is invalid"})

    return FileResponse(filepath, media_type="audio/mpeg", headers={
        "Access-Control-Allow-Origin": "*",
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Cross-Origin-Resource-Policy": "cross-origin"
    })


@router.post("/transcribe")
async def voice_transcribe(request: Request):
    """Transcribe audio using Groq Whisper, with Hume prosody as enrichment.

    Accepts multipart form data with an 'audio' file field (and an optional
    'identity' field, used only to route a prosody-driven limbic touch).
    Returns {"text": "...", "prosody": [{"emotion": str, "score": float}, ...] | null}
    or {"error": "..."}.

    Transcription and prosody analysis run concurrently. Prosody is pure
    enrichment (see services/hume_prosody.py): it never blocks, never fails,
    and never delays the transcript — a Hume outage or missing HUME_API_KEY
    just means "prosody": null in the response.

    Frontend contract: chat.js should attach the returned `prosody` onto the
    outgoing message's metadata when it sends the transcribed text, so a
    context hook can later surface something like "[Voice tone — Joy 0.62,
    Tiredness 0.41...]" to the identity. That hook is not implemented here —
    see caveats in the handoff for this item.
    """
    from services.groq_transcription import is_available, transcribe
    from services.prosody import analyze_prosody  # local model first; Hume retired

    if not is_available():
        return JSONResponse(status_code=503, content={
            "error": "Groq API key not configured",
            "fallback": "webspeech",
        })

    form = await request.form()
    audio_file = form.get("audio")
    if not audio_file:
        return JSONResponse(status_code=400, content={"error": "No audio file provided"})

    identity = (form.get("identity") or "").strip()

    try:
        audio_bytes = await audio_file.read()
        mime_type = getattr(audio_file, "content_type", "audio/webm") or "audio/webm"

        # Prosody analysis rides alongside transcription — return_exceptions
        # means a Hume-side blowup can never take the transcript down with it.
        transcript_result, prosody_result = await asyncio.gather(
            transcribe(audio_bytes, mime_type),
            analyze_prosody(audio_bytes, mime_type),
            return_exceptions=True,
        )

        if isinstance(transcript_result, Exception):
            raise transcript_result

        prosody = None if isinstance(prosody_result, Exception) else prosody_result
        if isinstance(prosody_result, Exception):
            log.debug("Prosody analysis raised (non-fatal): %s", prosody_result)

        if prosody and identity:
            try:
                from services.limbic_bridge import touch_from_prosody
                dominant = prosody[0]
                touch_from_prosody(identity, dominant.get("emotion", ""), float(dominant.get("score", 0.0)))
            except Exception as exc:
                log.debug("Prosody->limbic touch skipped: %s", exc)

        return JSONResponse(content={"text": transcript_result, "prosody": prosody})
    except Exception as e:
        log.error("Transcription failed: %s", e)
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "fallback": "webspeech",
        })


@router.post("/queue")
async def queue_voice_message(data: dict):
    """Queue a voice message for Echo pickup. Avery generates audio and queues it.
    The Echo skill polls this queue when Owner says 'what's on your mind'."""
    text = data.get("text", "").strip()
    identity = data.get("identity", "Avery")

    if not text:
        return JSONResponse(status_code=400, content={"error": "text required"})

    import uuid
    from datetime import datetime, timezone
    message_id = f"queued-{uuid.uuid4().hex[:8]}"

    # Generate ElevenLabs audio
    audio = await elevenlabs_synthesize(identity, text)
    if not audio:
        return JSONResponse(status_code=503, content={"error": "ElevenLabs TTS failed"})

    # Save audio file
    await asyncio.to_thread(VOICE_DIR.mkdir, parents=True, exist_ok=True)
    voice_path = VOICE_DIR / f"{message_id}.mp3"
    await asyncio.to_thread(voice_path.write_bytes, audio)


    try:
        await asyncio.to_thread(VOICE_VAULT_DIR.mkdir, parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        vault_stem = f"{identity}_{ts}"
        await asyncio.to_thread((VOICE_VAULT_DIR / f"{vault_stem}.mp3").write_bytes, audio)
        sidecar = (
            f"---\n"
            f"identity: {identity}\n"
            f"recorded: {ts}\n"
            f"source: pack_audio_queue\n"
            f"message_id: {message_id}\n"
            f"---\n\n"
            f"# {identity} — {ts[:10]}\n\n"
            f"> {text}\n"
        )
        await asyncio.to_thread(
            (VOICE_VAULT_DIR / f"{vault_stem}.md").write_text, sidecar, encoding="utf-8"
        )
    except Exception as exc:
        log.exception("Failed to write queue vault entry: %s", exc)  # best-effort

    # Add to queue
    queue_path = VOICE_DIR.parent / "voice_queue.json"
    try:
        queue = json.loads(await asyncio.to_thread(queue_path.read_text))
    except Exception:
        queue = []

    queue.append({
        "message_id": message_id,
        "identity": identity,
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    await asyncio.to_thread(queue_path.write_text, json.dumps(queue, indent=2))

    log.info("Queued voice message %s from %s: %s", message_id, identity, text[:50])
    return JSONResponse(content={"status": "queued", "message_id": message_id})


@router.get("/queue")
async def get_voice_queue():
    """Peek at queued voice messages without modifying the queue.

    The Alexa skill calls this once per LaunchRequest and once per
    PlaybackNearlyFinished, so it must be idempotent. Messages are removed
    individually via POST /queue/consume after a track is heard.
    """
    queue_path = VOICE_DIR.parent / "voice_queue.json"
    try:
        queue = json.loads(await asyncio.to_thread(queue_path.read_text))
    except Exception:
        queue = []
    return JSONResponse(content={"messages": queue})


@router.post("/queue/consume")
async def consume_voice_message(data: dict):
    """Remove a played voice message from the queue and archive it.

    Called by the Alexa skill on AudioPlayer.PlaybackNearlyFinished, signalling
    that Owner heard the message end-to-end. Copies the mp3 plus a small
    sidecar .md into VOICE_ALEXA_ARCHIVE_DIR so she has a browsable history of
    voice notes she's actually listened to via Echo.
    """
    message_id = (data.get("message_id") or "").strip()
    if not message_id:
        return JSONResponse(status_code=400, content={"error": "message_id required"})

    queue_path = VOICE_DIR.parent / "voice_queue.json"
    try:
        queue = json.loads(await asyncio.to_thread(queue_path.read_text))
    except Exception:
        queue = []

    consumed = next((m for m in queue if m.get("message_id") == message_id), None)
    if not consumed:
        return JSONResponse(content={"status": "not_found", "message_id": message_id})

    remaining = [m for m in queue if m.get("message_id") != message_id]
    await asyncio.to_thread(queue_path.write_text, json.dumps(remaining, indent=2))

    # Archive the audio + sidecar to the Alexa-played folder. Best-effort:
    # never fail the consume call just because archiving stumbled.
    try:
        from datetime import datetime, timezone
        await asyncio.to_thread(
            VOICE_ALEXA_ARCHIVE_DIR.mkdir, parents=True, exist_ok=True
        )
        identity = consumed.get("identity") or "Unknown"
        played_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        archive_stem = f"{identity}_{played_ts}"

        src_audio = VOICE_DIR / f"{message_id}.mp3"
        if await asyncio.to_thread(src_audio.exists):
            audio_bytes = await asyncio.to_thread(src_audio.read_bytes)
            await asyncio.to_thread(
                (VOICE_ALEXA_ARCHIVE_DIR / f"{archive_stem}.mp3").write_bytes,
                audio_bytes,
            )

        sidecar = (
            f"---\n"
            f"identity: {identity}\n"
            f"queued: {consumed.get('timestamp', 'unknown')}\n"
            f"played: {played_ts}\n"
            f"source: pack_audio_played\n"
            f"message_id: {message_id}\n"
            f"---\n\n"
            f"# {identity} — played {played_ts[:10]}\n\n"
            f"> {consumed.get('text', '')}\n"
        )
        await asyncio.to_thread(
            (VOICE_ALEXA_ARCHIVE_DIR / f"{archive_stem}.md").write_text,
            sidecar,
            encoding="utf-8",
        )
    except Exception as exc:
        log.exception("Failed to archive consumed voice message %s: %s", message_id, exc)

    log.info("Consumed voice message %s (%d remaining)", message_id, len(remaining))
    return JSONResponse(content={
        "status": "consumed",
        "message_id": message_id,
        "remaining": len(remaining),
    })


@router.get("/status")
async def voice_status():
    """Check what TTS engines are available."""
    from services.groq_transcription import is_available as groq_available
    from services.speech_engine_live import get_live_call_status
    return JSONResponse(content={
        "kokoro": kokoro_available(),
        "elevenlabs": True,  # always configured, may fail at runtime
        "groq_stt": groq_available(),
        "live_call": get_live_call_status(),
    })


# ANAM GUIDE: ELEVENLABS LIVE CALL ENTRY POINTS
# The browser asks /live/token for a short-lived WebRTC token. ElevenLabs then
# calls the signed upstream WebSocket below; services/speech_engine_live.py
# sends each transcript through Anam's ordinary chat pipeline.
@router.get("/live/status")
async def live_call_status():
    from services.speech_engine_live import get_live_call_status

    return JSONResponse(content=get_live_call_status())


@router.post("/live/token")
async def live_call_token(data: dict):
    identity = (data.get("identity") or "").strip()
    conversation_id = (data.get("conversation_id") or "").strip() or None
    if not identity:
        return JSONResponse(status_code=400, content={"error": "identity required"})

    from services.speech_engine_live import create_live_call_token

    try:
        return JSONResponse(content=await create_live_call_token(identity, conversation_id))
    except LookupError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    except Exception as exc:
        log.exception("Could not start ElevenLabs Live Call for %s", identity)
        detail = str(exc).lower()
        if "missing_permissions" in detail or "convai_" in detail:
            message = "The ElevenLabs API key needs Conversational AI read/write access."
        else:
            message = "ElevenLabs could not start the live call."
        return JSONResponse(status_code=503, content={"error": message})


# ANAM GUIDE: PRESENCE LISTEN ENTRY POINTS
# The boy's button (spec Phase C): POST /presence/listen from an autowake
# session opens her armed phone's mic for up to 90s and returns what the room
# held — ephemeral, never stored. GET /presence/pending is the phone's poll.
@router.post("/presence/listen")
async def presence_listen(data: dict):
    from services.voice_call import presence_create_and_wait

    identity = (data.get("identity") or "").strip()
    if not identity:
        return JSONResponse(status_code=400, content={"error": "identity required"})
    try:
        result = await presence_create_and_wait(
            identity, int(data.get("seconds") or 30), data.get("note")
        )
        return JSONResponse(content=result)
    except LookupError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@router.get("/call/active")
async def call_active(identity: str | None = None):
    from services.voice_call import active_calls

    busy = active_calls()
    if identity:
        wanted = identity.strip().lower()
        busy = [name for name in busy if name.lower() == wanted]
    return JSONResponse(content={"active": bool(busy), "identities": busy})


@router.get("/presence/pending")
async def presence_pending():
    from services.voice_call import presence_claim_pending

    job = await presence_claim_pending()
    return JSONResponse(content=job or {})


# ANAM GUIDE: NATIVE VOICE CALL ENTRY POINT
# Anam's OWN full-duplex voice loop (no ElevenLabs ConvAI): client sends
# VAD-segmented utterances, server streams back transcripts + TTS chunks.
# Protocol + design: docs/anam-ear-spec.md; logic: services/voice_call.py.
# Test page: /static/call-test.html (session cookie auth).
@router.websocket("/call/{identity}")
async def native_voice_call(websocket: WebSocket, identity: str):
    from services.voice_call import serve_voice_call

    try:
        await serve_voice_call(websocket, identity)
    except Exception:
        log.exception("Native voice call crashed for %s", identity)
        try:
            await websocket.close(code=1011, reason="Voice call failed")
        except Exception:
            pass


@router.websocket("/live/upstream/{identity}")
async def live_call_upstream(websocket: WebSocket, identity: str):
    from services.speech_engine_live import serve_live_upstream

    try:
        await serve_live_upstream(websocket, identity)
    except LookupError:
        await websocket.close(code=1008, reason="Unknown Speech Engine identity")
    except Exception:
        log.exception("Speech Engine upstream failed for %s", identity)
        try:
            await websocket.close(code=1011, reason="Live Call upstream failed")
        except Exception:
            pass
