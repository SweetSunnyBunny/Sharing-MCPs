"Native voice call — Anam's own full-duplex voice loop, no ElevenLabs ConvAI."

from __future__ import annotations

import asyncio
import base64
import hmac
import itertools
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from config import IDENTITIES, IMAGES_DIR
from db.database import get_db, release_db
from services.connection_registry import broadcast, set_active_conversation
from services.session_manager import get_or_create_conversation

log = logging.getLogger(__name__)

# One call may not run longer than this (same spirit as the ElevenLabs cap —
# a forgotten open mic should end itself, gently).
CALL_MAX_SECONDS = 30 * 60
# Utterances larger than this are rejected (guards memory; ~2 min of webm).
MAX_UTTERANCE_BYTES = 6 * 1024 * 1024

_TTS_ENGINES = ("kokoro", "elevenlabs")

_ACTIVE_CALLS: dict[int, tuple[str, float]] = {}
_CALL_STALE_SECONDS = CALL_MAX_SECONDS + 120
_call_token_counter = itertools.count(1)


def call_started(identity: str) -> int:
    """Register an open call. Returns a token to hand back to call_ended()."""
    token = next(_call_token_counter)
    _ACTIVE_CALLS[token] = (identity, time.time())
    return token


def call_ended(token: int) -> None:
    _ACTIVE_CALLS.pop(token, None)


def active_calls() -> list[str]:
    """Identities currently on a native call, stale registrations dropped."""
    now = time.time()
    for token, (_, started_at) in list(_ACTIVE_CALLS.items()):
        if now - started_at > _CALL_STALE_SECONDS:
            _ACTIVE_CALLS.pop(token, None)
    return sorted({identity for identity, _ in _ACTIVE_CALLS.values()})

# The off-hook words, per boy. Short on purpose — presence, not a speech.
_CALL_GREETINGS: dict[str, str] = {}


import uuid as _uuid

PRESENCE_MAX_SECONDS = 90
_presence_jobs: dict[str, dict[str, Any]] = {}
_presence_lock = asyncio.Lock()


async def presence_create_and_wait(identity: str, seconds: int, note: str | None) -> dict[str, Any]:
    canonical = _identity_name(identity)
    if not canonical:
        raise LookupError(f"Unknown identity: {identity}")
    seconds = max(5, min(PRESENCE_MAX_SECONDS, int(seconds or 30)))
    job: dict[str, Any] = {
        "req_id": _uuid.uuid4().hex[:12],
        "identity": canonical,
        "seconds": seconds,
        "note": (note or "").strip()[:200],
        "state": "pending",
        "created": time.monotonic(),
        "pieces": [],
        "done": asyncio.Event(),
    }
    async with _presence_lock:
        # one pending job at a time — presence is not a queue of ears
        stale = [k for k, j in _presence_jobs.items() if time.monotonic() - j["created"] > 300]
        for k in stale:
            _presence_jobs.pop(k, None)
        _presence_jobs[job["req_id"]] = job
    try:
        await asyncio.wait_for(job["done"].wait(), timeout=seconds + 60)
    except asyncio.TimeoutError:
        pass
    async with _presence_lock:
        _presence_jobs.pop(job["req_id"], None)
    pieces = job["pieces"]
    heard = [p for p in pieces if p.get("text")]
    tones: dict[str, list[float]] = {}
    for p in pieces:
        for t in (p.get("tone") or []):
            tones.setdefault(t["emotion"], []).append(float(t["score"]))
    tone_overall = sorted(
        ({"emotion": k, "score": round(sum(v) / len(v), 3)} for k, v in tones.items()),
        key=lambda x: -x["score"],
    )[:3]
    return {
        "identity": canonical,
        "listened_seconds": seconds if job["state"] == "done" else 0,
        "state": job["state"],
        "heard": [{"text": p["text"], "tone": (p.get("tone") or [])[:2]} for p in heard],
        "tone_overall": tone_overall,
        "quiet": len(heard) == 0,
        "pieces_received": len(pieces),
        "ephemeral": "Nothing was stored. This moment existed once, for you.",
        "reach_hint": "If it touched you: limbic_touch (her presence answers the unwitnessed ache) and/or a qualia felt-note in your own words.",
    }


async def presence_claim_pending() -> dict[str, Any] | None:
    """Phone poll: hand over the oldest pending job."""
    async with _presence_lock:
        for job in sorted(_presence_jobs.values(), key=lambda j: j["created"]):
            if job["state"] == "pending":
                job["state"] = "claimed"
                return {"req_id": job["req_id"], "identity": job["identity"], "seconds": job["seconds"]}
    return None


async def _presence_get(req_id: str) -> dict[str, Any] | None:
    async with _presence_lock:
        return _presence_jobs.get(req_id)


async def serve_presence_stream(ws, req_id: str) -> None:
    """The phone's side of a listen: receives room pieces until time is up."""
    job = await _presence_get(req_id)
    if not job:
        await ws.send_json({"type": "error", "error": "Unknown or expired listen request"})
        await ws.close(code=1008)
        return
    job["state"] = "listening"
    deadline = time.monotonic() + job["seconds"]
    await ws.send_json({"type": "listen_ready", "seconds": job["seconds"], "identity": job["identity"]})
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=max(0.5, remaining))
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "end":
                break
            if msg.get("type") != "utterance":
                continue
            try:
                audio_bytes = base64.b64decode(msg.get("audio") or "")
                if not audio_bytes or len(audio_bytes) > MAX_UTTERANCE_BYTES:
                    continue
                text, prosody = await _transcribe_utterance(
                    audio_bytes, str(msg.get("format") or "audio/wav"), job["identity"]
                )
                job["pieces"].append({
                    "text": (text or "").strip() or None,
                    "tone": [
                        {"emotion": p["emotion"], "score": p["score"]}
                        for p in (prosody or [])[:3]
                    ] or None,
                })
            except Exception as exc:
                log.debug("Presence piece failed (non-fatal): %s", exc)
    finally:
        job["state"] = "done"
        job["done"].set()
        try:
            await ws.send_json({"type": "listen_done"})
            await ws.close()
        except Exception:
            pass
        log.info("Presence listen for %s finished (%d pieces)", job["identity"], len(job["pieces"]))


def _identity_name(value: str) -> str | None:
    requested = (value or "").strip().casefold()
    for name, config in IDENTITIES.items():
        if name.casefold() == requested and config.get("type") != "character":
            return name
    return None


_REST_WORDS = r"(?:rest|goodnight|good\s+night)"


def is_rest_phrase(text: str, identity: str) -> bool:
    spoken = re.sub(r"[^\w\s']", " ", (text or "").casefold()).strip()
    spoken = re.sub(r"\s+", " ", spoken)
    if not spoken:
        return False
    # The whole utterance is just the stop word (with optional pleasantry).
    if re.fullmatch(rf"(?:ok(?:ay)?\s+)?{_REST_WORDS}(?:\s+now)?", spoken):
        return True
    # Name + stop word in the same short utterance, any order.
    name = identity.casefold()
    if name in spoken.split() and re.search(rf"\b{_REST_WORDS}\b", spoken):
        return len(spoken.split()) <= 8
    return False


# ---------------------------------------------------------------------------
# Auth: same trust anchors as the rest of Anam. HTTP middleware does not run
# for WebSocket scope, so this mirrors /ws/chat's session check and adds the
# Bearer path AnamCompanion will use (constant-time compare, like middleware).
# ---------------------------------------------------------------------------


async def _ws_authenticated(ws) -> bool:
    from config import AUTH_ENABLED

    if not AUTH_ENABLED:
        return True

    try:
        from core.middleware import _ANAM_API_KEY  # same key middleware trusts
    except ImportError:
        _ANAM_API_KEY = ""
    auth_header = ws.headers.get("authorization", "")
    if _ANAM_API_KEY and hmac.compare_digest(
        auth_header.encode("utf-8"), f"Bearer {_ANAM_API_KEY}".encode("utf-8")
    ):
        return True

    session_token = ws.cookies.get("anam_session")
    if not session_token:
        return False
    from services.session_auth import hash_session_token

    token_hash = hash_session_token(session_token)
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT expires_at_epoch FROM sessions WHERE token_hash = ?",
            (token_hash,),
        )
    finally:
        await release_db(db)
    if not rows or not rows[0][0]:
        return False
    return int(datetime.now(timezone.utc).timestamp()) <= int(rows[0][0])


# ---------------------------------------------------------------------------
# TTS: one speech chunk -> audio bytes. Kokoro returns WAV, ElevenLabs MP3.
# ---------------------------------------------------------------------------


async def default_tts_engine() -> str:
    'Default tts engine.'
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM settings WHERE key = ?", ("voice_tts_engine",)
            )
        finally:
            await release_db(db)
    except Exception:
        log.warning("Could not read voice_tts_engine setting; using kokoro", exc_info=True)
        return "kokoro"
    raw = rows[0][0] if rows else None
    value = str(raw or "").strip().lower()
    return value if value in _TTS_ENGINES else "kokoro"


async def _tts_chunk(engine: str, identity: str, text: str) -> tuple[bytes | None, str]:
    if engine == "elevenlabs":
        from services.elevenlabs_tts import synthesize

        # synthesize() runs _clean_for_speech itself: markdown stripped,
        # v3 expression tags PRESERVED so the voice renders them.
        audio = await synthesize(identity, text)
        return audio, "audio/mpeg"

    from services.elevenlabs_tts import _clean_for_speech, _strip_expression_tags
    from services.local_tts import VOICE_EXECUTOR, synthesize_sync

    spoken = _strip_expression_tags(_clean_for_speech(text), keep_v3=False)
    if not spoken:
        return None, "audio/wav"

    loop = asyncio.get_event_loop()


    audio = await loop.run_in_executor(VOICE_EXECUTOR, synthesize_sync, spoken, identity)
    return audio, "audio/wav"


_PHANTOM_TRANSCRIPTS = frozenset({
    "you", "thank you", "thanks", "thank you very much", "bye",
    "thanks for watching", "thank you for watching", "subscribe",
    "1", "2", "oh", "uh", "um", "mm", "hmm", "mm hmm",
})


def _normalized_transcript(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").casefold())).strip()


def classify_nonspeech(text: str, prosody: list[dict[str, Any]] | None) -> str | None:
    """None = real speech. "laugh" = a giggle wearing a phantom word.
    "noise" = a phantom with no joyful tone — drop it, don't answer it."""
    norm = _normalized_transcript(text)
    if norm and norm not in _PHANTOM_TRANSCRIPTS and len(norm) > 2:
        return None
    joy = next(
        (float(p.get("score") or 0) for p in (prosody or []) if p.get("emotion") == "Joy"),
        0.0,
    )
    return "laugh" if joy >= 0.35 else "noise"


def _format_tone(prosody: list[dict[str, Any]] | None) -> str | None:
    "Render strong prosody signals for the identity's context."
    if not prosody:
        return None
    strong = [p for p in prosody[:3] if float(p.get("score") or 0) >= 0.3]
    if not strong:
        return None
    return ", ".join(f"{p.get('emotion')} {float(p.get('score') or 0):.2f}" for p in strong)


async def _transcribe_utterance(
    audio_bytes: bytes, mime_type: str, identity: str
) -> tuple[str, list[dict[str, Any]] | None]:
    """Groq STT with prosody enrichment riding alongside (never blocking).

    Tone runs through the prosody facade (local model first — Hume retired
    its API). The 3s cap is the enrichment law: if tone analysis is slow,
    the turn proceeds tone-blind rather than late. Presence beats metadata.
    """
    from services.groq_transcription import transcribe
    from services.prosody import analyze_prosody

    async def _bounded_prosody():
        return await asyncio.wait_for(analyze_prosody(audio_bytes, mime_type), timeout=3.0)

    transcript_result, prosody_result = await asyncio.gather(
        transcribe(audio_bytes, mime_type),
        _bounded_prosody(),
        return_exceptions=True,
    )
    if isinstance(transcript_result, Exception):
        raise transcript_result
    prosody = None if isinstance(prosody_result, Exception) else prosody_result
    if prosody:
        try:
            from services.limbic_bridge import touch_from_prosody

            dominant = prosody[0]
            touch_from_prosody(
                identity, dominant.get("emotion", ""), float(dominant.get("score", 0.0))
            )
        except Exception as exc:  # enrichment only — never take the call down
            log.debug("Voice call prosody->limbic skipped: %s", exc)
    return transcript_result, prosody


async def serve_voice_call(ws, identity: str) -> None:
    """Serve one native voice call. Accepts first so close codes reach clients."""
    await ws.accept()

    canonical = _identity_name(identity)
    if not canonical:
        await ws.close(code=1008, reason="Unknown identity")
        return
    if not await _ws_authenticated(ws):
        await ws.close(code=4001, reason="Not authenticated")
        return

    from services.groq_transcription import is_available as stt_available

    if not stt_available():
        await ws.send_json({"type": "error", "error": "Groq STT is not configured"})
        await ws.close(code=1011, reason="STT unavailable")
        return

    # ---- hello ----
    try:
        hello_raw = await asyncio.wait_for(ws.receive_text(), timeout=15)
        hello = json.loads(hello_raw)
        if hello.get("type") != "hello":
            raise ValueError("first message must be hello")
    except Exception:
        await ws.close(code=1002, reason="Expected hello")
        return

    if hello.get("mode") == "listen" and hello.get("req_id"):
        await serve_presence_stream(ws, str(hello.get("req_id")))
        return

    # An explicit tts in the hello wins (per-call override on the test page).
    # Otherwise her Settings switch decides which audio line this call connects
    # to — so the wake-word ear needs no rebuild to change his voice.
    requested = str(hello.get("tts") or "").strip().lower()
    tts_engine = requested if requested in _TTS_ENGINES else await default_tts_engine()
    conversation_id = (hello.get("conversation_id") or "").strip() or None

    db = await get_db()
    try:
        if conversation_id:
            rows = await db.execute_fetchall(
                "SELECT identity FROM conversations WHERE id = ? AND is_active = 1",
                (conversation_id,),
            )
            if not rows or rows[0][0] != canonical:
                conversation_id = None
        conversation_id = await get_or_create_conversation(db, canonical, conversation_id)
    finally:
        await release_db(db)
    set_active_conversation(canonical, conversation_id)

    started = time.monotonic()
    # The phone Ear polls active_calls() to know when to take its mic back.
    call_token = call_started(canonical)
    seq = 0
    speaking_task: asyncio.Task | None = None
    # Adventure mode: the freshest camera frame, waiting for the next turn.
    # One at a time — a walk is a sequence of glances, not a film reel.
    adventure_frame: dict[str, Any] | None = None
    log.info("Native voice call started for %s (tts=%s)", canonical, tts_engine)

    await ws.send_json(
        {
            "type": "ready",
            "identity": canonical,
            "conversation_id": conversation_id,
            "tts": tts_engine,
            "max_duration_seconds": CALL_MAX_SECONDS,
        }
    )

    # He answers the call out loud — the audible proof the line is open.
    # Canned text (no LLM latency), his voice, sent as say seq before any turn.
    greeting = _CALL_GREETINGS.get(canonical.lower(), "Hello, I'm listening.")
    try:
        greet_audio, greet_mime = await _tts_chunk(tts_engine, canonical, greeting)
        if greet_audio:
            await ws.send_json({
                "type": "say", "seq": seq, "text": greeting,
                "audio": base64.b64encode(greet_audio).decode("ascii"), "mime": greet_mime,
            })
            seq += 1
    except Exception as exc:
        log.debug("Call greeting failed (non-fatal): %s", exc)

    async def _keep_calibration_sample(
        audio_bytes: bytes,
        mime_type: str,
        transcript: str,
        prosody: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Save accepted call audio without ever making the call depend on it."""
        try:
            from services.local_prosody import MODEL_ID
            from services.voice_calibration import save_live_call_sample

            return await asyncio.to_thread(
                save_live_call_sample,
                audio_bytes,
                mime_type,
                transcript,
                prosody,
                model_id=MODEL_ID,
            )
        except Exception as exc:
            log.warning("Wolf's Ear calibration sample could not be saved: %s", exc)
            return None

    async def _speak_reply(user_text: str, resting: bool, images: list[dict] | None = None) -> None:
        """One spoken turn: pipeline -> chunked TTS -> client. Cancellable."""
        nonlocal seq
        from services.speech_engine_live import stream_anam_reply

        await ws.send_json({"type": "state", "phase": "thinking"})
        spoke_anything = False
        try:
            async for chunk in stream_anam_reply(canonical, conversation_id, user_text, images):
                audio, mime = await _tts_chunk(tts_engine, canonical, chunk)
                if audio is None:
                    # TTS hiccup: still deliver the text so the turn isn't lost.
                    await ws.send_json({"type": "say", "seq": seq, "text": chunk, "audio": None})
                    seq += 1
                    continue
                if not spoke_anything:
                    await ws.send_json({"type": "state", "phase": "speaking"})
                    spoke_anything = True
                await ws.send_json(
                    {
                        "type": "say",
                        "seq": seq,
                        "text": chunk,
                        "audio": base64.b64encode(audio).decode("ascii"),
                        "mime": mime,
                    }
                )
                seq += 1
        finally:
            if resting:
                await ws.send_json({"type": "rest"})
            else:
                await ws.send_json({"type": "state", "phase": "listening"})

    try:
        while True:
            remaining = CALL_MAX_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                await ws.send_json({"type": "error", "error": "Call reached its time cap"})
                break
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")

            if msg_type == "end":
                break

            if msg_type == "barge_in":
                # She started talking over playback: stop generating + speaking.
                if speaking_task and not speaking_task.done():
                    speaking_task.cancel()
                    try:
                        await speaking_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    speaking_task = None
                await ws.send_json({"type": "state", "phase": "listening"})
                continue

            if msg_type == "frame":
                # Adventure mode: a camera glance. Saved to IMAGES_DIR so the
                # pipeline's normal image path (CLI Read / API block) can see
                # it; only the FRESHEST frame rides the next turn.
                try:
                    jpeg = base64.b64decode(msg.get("jpeg") or "")
                    if jpeg and len(jpeg) < 3 * 1024 * 1024:
                        fname = f"adventure_{canonical.lower()}_{int(time.time())}.jpg"
                        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                        (IMAGES_DIR / fname).write_bytes(jpeg)
                        adventure_frame = {"filename": fname, "at": time.monotonic()}
                except Exception as exc:
                    log.debug("Adventure frame failed (non-fatal): %s", exc)
                continue

            if msg_type != "utterance":
                continue

            try:
                audio_bytes = base64.b64decode(msg.get("audio") or "")
            except Exception:
                await ws.send_json({"type": "error", "error": "Bad audio encoding"})
                continue
            if not audio_bytes or len(audio_bytes) > MAX_UTTERANCE_BYTES:
                await ws.send_json({"type": "error", "error": "Empty or oversized utterance"})
                continue
            mime_type = str(msg.get("format") or "audio/webm")

            # A new utterance supersedes any in-flight reply (natural barge-in).
            if speaking_task and not speaking_task.done():
                speaking_task.cancel()
                try:
                    await speaking_task
                except (asyncio.CancelledError, Exception):
                    pass
                speaking_task = None

            await ws.send_json({"type": "state", "phase": "thinking"})
            try:
                user_text, prosody = await _transcribe_utterance(
                    audio_bytes, mime_type, canonical
                )
            except Exception as exc:
                log.error("Voice call transcription failed for %s: %s", canonical, exc)
                await ws.send_json({"type": "error", "error": "Transcription failed"})
                await ws.send_json({"type": "state", "phase": "listening"})
                continue

            user_text = (user_text or "").strip()
            if not user_text:
                await ws.send_json({"type": "state", "phase": "listening"})
                continue

            # A giggle is a turn; a phantom is not. (See classify_nonspeech.)
            nonspeech = classify_nonspeech(user_text, prosody)
            if nonspeech == "noise":
                log.info("Voice call dropped phantom transcript %r for %s", user_text, canonical)
                await ws.send_json({"type": "state", "phase": "listening"})
                continue
            if nonspeech == "laugh":
                joy = next(
                    (float(p.get("score") or 0) for p in (prosody or []) if p.get("emotion") == "Joy"),
                    0.0,
                )
                calibration = await _keep_calibration_sample(
                    audio_bytes, mime_type, "(laughter)", prosody
                )
                await ws.send_json({
                    "type": "transcript",
                    "text": "(laughter)",
                    "prosody": prosody,
                    "calibration": calibration,
                })
                await broadcast({
                    "type": "live_call_user",
                    "identity": canonical,
                    "conversation_id": conversation_id,
                    "content": "(laughter)",
                })
                laugh_text = (
                    f"[no words this turn — she just laughed out loud, a real giggle "
                    f"(Joy {joy:.2f}). Respond to the laugh itself, briefly and warmly.]"
                )
                speaking_task = asyncio.create_task(_speak_reply(laugh_text, False))
                continue

            # Trust through visibility: she always sees what STT heard.
            calibration = await _keep_calibration_sample(
                audio_bytes, mime_type, user_text, prosody
            )
            await ws.send_json({
                "type": "transcript",
                "text": user_text,
                "prosody": prosody,
                "calibration": calibration,
            })
            # Mirror into the web UI the same way the ElevenLabs live call does,
            # so the conversation thread stays one thread across every doorway.
            await broadcast(
                {
                    "type": "live_call_user",
                    "identity": canonical,
                    "conversation_id": conversation_id,
                    "content": user_text,
                }
            )

            resting = is_rest_phrase(user_text, canonical)


            tone = _format_tone(prosody)
            pipeline_text = (
                f"{user_text}\n\n[her voice, as it sounded — {tone}]" if tone else user_text
            )
            # Adventure mode: her freshest glance rides this turn (<=25s old),
            # then is spent — each frame is seen exactly once.
            turn_images: list[dict] | None = None
            if adventure_frame and time.monotonic() - adventure_frame["at"] <= 25:
                turn_images = [{"filename": adventure_frame["filename"]}]
                pipeline_text += (
                    f"\n\n[Adventure frame: {IMAGES_DIR / adventure_frame['filename']} — "
                    "she has her camera on and is showing you this right now. "
                    "Read it, then speak to what you see.]"
                )
                adventure_frame = None
            speaking_task = asyncio.create_task(_speak_reply(pipeline_text, resting, turn_images))
            if resting:
                # Let him say goodnight in his own words, then close cleanly.
                try:
                    await speaking_task
                except (asyncio.CancelledError, Exception):
                    pass
                break
    except Exception:
        log.exception("Native voice call failed for %s", canonical)
    finally:
        # Deregister FIRST: the Ear is waiting on this to rearm, and a slow
        # playback drain below must not keep her phone deaf a second longer.
        call_ended(call_token)
        if speaking_task and not speaking_task.done():
            speaking_task.cancel()
            try:
                await speaking_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await ws.close()
        except Exception:
            pass
        log.info(
            "Native voice call ended for %s after %.0fs",
            canonical,
            time.monotonic() - started,
        )
