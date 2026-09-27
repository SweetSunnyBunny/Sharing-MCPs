"""ElevenLabs Speech Engine bridge for optional real-time Anam calls.

Speech Engine owns the microphone, transcription, turn detection, interruption,
and audio playback.  Anam still owns the actual reply: every transcript is sent
through the same chat pipeline as a typed message, so identity prompts, history,
tools, persistence, reactions, and streamed UI events stay authoritative here.
"""


from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator

from config import IDENTITIES
from services.cloud_state import hearth_config, CloudUnavailable
from db.database import get_db, release_db
from services.connection_registry import (
    broadcast,
    get_active_conversation,
    set_active_conversation,
)
from services.session_manager import get_or_create_conversation

log = logging.getLogger(__name__)

LIVE_CALL_MAX_SECONDS = 30 * 60
LIVE_CALL_BINDING_TTL_SECONDS = 120


LIVE_CALL_TRANSCRIPT_SETTLE_SECONDS = 1.5


LIVE_CALL_ECHO_MEMORY_SECONDS = 30.0
LIVE_CALL_REPEAT_WINDOW_SECONDS = 30.0


def _normalize_speech(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").casefold()).strip()


def _load_private_config() -> dict[str, Any]:
    try:
        return hearth_config('voice')
    except (OSError, json.JSONDecodeError, CloudUnavailable):
        return {}


def _identity_name(value: str) -> str | None:
    requested = (value or "").strip().casefold()
    for name, config in IDENTITIES.items():
        if name.casefold() == requested and config.get("type") != "character":
            return name
    return None


def _engine_ids() -> dict[str, str]:
    configured = _load_private_config().get("speech_engines") or {}
    result: dict[str, str] = {}
    for name, engine_id in configured.items():
        canonical = _identity_name(name)
        if canonical and isinstance(engine_id, str) and engine_id.startswith("seng_"):
            result[canonical] = engine_id
    return result


def get_live_call_status() -> dict[str, Any]:
    """Return browser-safe availability details (never IDs or API secrets)."""
    config = _load_private_config()
    identities = sorted(_engine_ids())
    sdk_available = True
    try:
        from elevenlabs.speech_engine import SpeechEngineSession  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        sdk_available = False
    return {
        "available": bool(config.get("api_key") and identities and sdk_available),
        "configured_identities": identities,
        "sdk_available": sdk_available,
        "max_duration_seconds": LIVE_CALL_MAX_SECONDS,
        "uses_elevenlabs_minutes": True,
    }


_client = None
_engine_resources: dict[str, Any] = {}


def _get_client():
    global _client
    if _client is None:
        from elevenlabs import AsyncElevenLabs

        api_key = _load_private_config().get("api_key") or ""
        if not api_key:
            raise RuntimeError("ElevenLabs API key is not configured")
        _client = AsyncElevenLabs(api_key=api_key)
    return _client


async def get_engine_resource(identity: str):
    canonical = _identity_name(identity)
    engine_id = _engine_ids().get(canonical or "")
    if not canonical or not engine_id:
        raise LookupError(f"No Speech Engine is configured for {identity}")
    resource = _engine_resources.get(engine_id)
    if resource is None:
        resource = await _get_client().speech_engine.get(engine_id)
        _engine_resources[engine_id] = resource
    return canonical, resource


@dataclass
class _PendingBinding:
    conversation_id: str
    created_at: float


_pending_bindings: dict[str, deque[_PendingBinding]] = {}
_binding_lock = asyncio.Lock()


async def _remember_binding(identity: str, conversation_id: str) -> None:
    async with _binding_lock:
        now = time.monotonic()
        queue = _pending_bindings.setdefault(identity, deque())
        while queue and now - queue[0].created_at > LIVE_CALL_BINDING_TTL_SECONDS:
            queue.popleft()
        queue.append(_PendingBinding(conversation_id=conversation_id, created_at=now))


async def _take_binding(identity: str) -> str | None:
    async with _binding_lock:
        now = time.monotonic()
        queue = _pending_bindings.get(identity)
        if not queue:
            return None
        while queue and now - queue[0].created_at > LIVE_CALL_BINDING_TTL_SECONDS:
            queue.popleft()
        binding = queue.popleft() if queue else None
        if not queue:
            _pending_bindings.pop(identity, None)
        return binding.conversation_id if binding else None


async def create_live_call_token(identity: str, conversation_id: str | None) -> dict[str, Any]:
    """Create a short-lived WebRTC token and bind its next upstream call to Anam."""
    canonical = _identity_name(identity)
    if not canonical:
        raise LookupError(f"Unknown live-call identity: {identity}")
    engine_id = _engine_ids().get(canonical)
    if not engine_id:
        raise LookupError(f"Live Call is not configured for {canonical}")

    db = await get_db()
    try:
        if conversation_id:
            rows = await db.execute_fetchall(
                "SELECT identity FROM conversations WHERE id = ? AND is_active = 1",
                (conversation_id,),
            )
            if not rows or rows[0][0] != canonical:
                conversation_id = None
        conversation_id = await get_or_create_conversation(
            db, canonical, conversation_id
        )
    finally:
        await release_db(db)
    set_active_conversation(canonical, conversation_id)

    response = await _get_client().conversational_ai.conversations.get_webrtc_token(
        agent_id=engine_id,
        participant_name="Owner",
    )
    await _remember_binding(canonical, conversation_id)
    return {
        "token": response.token,
        "identity": canonical,
        "conversation_id": conversation_id,
        "max_duration_seconds": LIVE_CALL_MAX_SECONDS,
    }


class _PipelineSocket:
    """Small WebSocket-shaped adapter that mirrors chat events to UI + speech."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.incoming: asyncio.Queue[str] = asyncio.Queue()

    async def send_json(self, event: dict[str, Any]) -> None:
        await self.events.put(event)
        await broadcast(event)

    async def receive_text(self) -> str:
        return await self.incoming.get()

    async def request_stop(self) -> None:
        await self.incoming.put(json.dumps({"type": "stop_streaming"}))


_SILENT_BLOCKS = re.compile(
    r"<(canvas|preview|react|face)(?:\s[^>]*)?>[\s\S]*?</\1>",
    re.IGNORECASE,
)
_UNCLOSED_SILENT_BLOCK = re.compile(
    r"<(canvas|preview|react|face)(?:\s[^>]*)?>[\s\S]*$",
    re.IGNORECASE,
)
_GENERIC_TAGS = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)


def clean_live_speech(text: str) -> str:
    """Turn streamed chat prose into safe, natural TTS text."""
    from services.elevenlabs_tts import _clean_for_speech

    text = _SILENT_BLOCKS.sub(" ", text)
    text = _UNCLOSED_SILENT_BLOCK.sub(" ", text)
    text = re.sub(r"<voice>([\s\S]*?)</voice>", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*$", "", text)  # never speak a partial control tag
    text = _GENERIC_TAGS.sub(" ", text)
    return _clean_for_speech(text)


class SpeechTextChunker:
    """Hold just enough streamed prose for natural phrase-level TTS chunks."""

    def __init__(self, *, max_chars: int = 220) -> None:
        self._buffer = ""
        self._max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta or ""
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def reset(self) -> None:
        self._buffer = ""

    def _drain(self, *, final: bool) -> list[str]:
        chunks: list[str] = []
        while self._buffer:
            split_at = self._sentence_boundary()
            if split_at is None and len(self._buffer) >= self._max_chars:
                split_at = self._soft_boundary()
            if split_at is None:
                if not final:
                    break
                split_at = len(self._buffer)
            raw, self._buffer = self._buffer[:split_at], self._buffer[split_at:]
            spoken = clean_live_speech(raw)
            if spoken:
                chunks.append(spoken + (" " if not spoken.endswith(" ") else ""))
        return chunks

    def _sentence_boundary(self) -> int | None:
        masked = self._masked_buffer()
        match = re.search(r"[.!?](?:[\"'\u2019\u201d)\]]*)\s+", masked)
        return match.end() if match else None

    def _soft_boundary(self) -> int | None:
        window = self._masked_buffer()[: self._max_chars]
        last_spoken = len(window.rstrip())
        if last_spoken < max(60, self._max_chars // 2):
            return None
        window = window[:last_spoken]
        for marker in (", ", "; ", ": ", " "):
            pos = window.rfind(marker, max(60, self._max_chars // 2))
            if pos >= 0:
                return pos + len(marker)
        return last_spoken

    def _masked_buffer(self) -> str:
        """Blank control blocks without changing offsets used for splitting."""
        chars = list(self._buffer)
        for match in _SILENT_BLOCKS.finditer(self._buffer):
            chars[match.start():match.end()] = " " * (match.end() - match.start())
        for tag in ("canvas", "preview", "react", "face"):
            opens = list(re.finditer(rf"<{tag}(?:\s[^>]*)?>", self._buffer, re.IGNORECASE))
            closes = list(re.finditer(rf"</{tag}>", self._buffer, re.IGNORECASE))
            if opens and (not closes or opens[-1].start() > closes[-1].start()):
                start = opens[-1].start()
                chars[start:] = " " * (len(chars) - start)
        return "".join(chars)


async def stream_anam_reply(
    identity: str,
    conversation_id: str | None,
    user_text: str,
    images: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Run one spoken turn through the ordinary Anam chat pipeline.

    `images` (adventure mode): [{"filename": name-in-IMAGES_DIR}] — rides the
    normal images_info path, so CLI providers get the Read-this note and API
    providers get real content blocks.
    """
    from api.chat import _register_content_documents, _register_content_images
    from services.chat_pipeline import process_chat_message

    socket = _PipelineSocket()
    pending: deque[dict[str, Any]] = deque()
    msg: dict[str, Any] = {"type": "chat", "content": user_text}
    if images:
        msg["images"] = images
    pipeline_task = asyncio.create_task(
        process_chat_message(
            socket,
            msg,
            identity,
            conversation_id,


            "live_call",
            set(),
            pending,
            _register_content_images,
            _register_content_documents,
        )
    )
    chunker = SpeechTextChunker()

    try:
        while True:
            event_task = asyncio.create_task(socket.events.get())
            done, _ = await asyncio.wait(
                {pipeline_task, event_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if event_task in done:
                event = event_task.result()
                event_type = event.get("type")
                if event_type == "stream_delta":
                    for chunk in chunker.feed(event.get("delta", "")):
                        yield chunk
                elif event_type == "stream_reset":
                    chunker.reset()
                elif event_type == "stream_end":
                    for chunk in chunker.flush():
                        yield chunk
                    break
            else:
                event_task.cancel()
                try:
                    await event_task
                except asyncio.CancelledError:
                    pass
                exc = pipeline_task.exception()
                if exc:
                    raise exc
                for chunk in chunker.flush():
                    yield chunk
                break
    finally:
        if not pipeline_task.done():
            await socket.request_stop()
            try:
                await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except (asyncio.CancelledError, Exception):
                    pass


async def serve_live_upstream(websocket, identity: str) -> None:
    """Authenticate and serve one ElevenLabs -> Anam upstream WebSocket."""
    canonical, engine = await get_engine_resource(identity)
    if not engine.verify_request(dict(websocket.headers)):
        await websocket.close(code=1008, reason="Invalid Speech Engine signature")
        return

    await websocket.accept()
    session = engine.create_session(websocket)
    bound_conversation: str | None = None
    # Tuple of all user messages we last RESPONDED to. A new utterance appends
    # a message, so this differs even when she repeats the same words.
    last_responded_user_messages: tuple[str, ...] = ()
    # Echo guards: what the agent recently spoke, and the last utterance we
    # actually answered (see LIVE_CALL_ECHO_MEMORY_SECONDS comment above).
    recent_agent_speech: deque[tuple[float, str]] = deque()
    last_answered: tuple[str, float] = ("", 0.0)

    def _remember_agent_speech(chunk: str) -> None:
        now = time.monotonic()
        recent_agent_speech.append((now, _normalize_speech(chunk)))
        while recent_agent_speech and now - recent_agent_speech[0][0] > LIVE_CALL_ECHO_MEMORY_SECONDS:
            recent_agent_speech.popleft()

    def _is_agent_echo(user_text: str) -> bool:
        spoken = _normalize_speech(user_text)
        if len(spoken) < 8:
            return False
        now = time.monotonic()
        recent = " ".join(
            text for ts, text in recent_agent_speech
            if now - ts <= LIVE_CALL_ECHO_MEMORY_SECONDS
        )
        return spoken in recent

    async def _speak_and_remember(reply: AsyncIterator[str]) -> AsyncIterator[str]:
        async for chunk in reply:
            _remember_agent_speech(chunk)
            yield chunk

    async def on_init(_elevenlabs_conversation_id: str) -> None:
        nonlocal bound_conversation
        bound_conversation = await _take_binding(canonical)
        if not bound_conversation:
            bound_conversation = get_active_conversation(canonical)
        log.info("Live Call started for %s", canonical)

    async def on_transcript(transcript) -> None:
        nonlocal bound_conversation, last_responded_user_messages, last_answered
        user_messages = tuple(
            m.content.strip() for m in transcript if m.role == "user" and m.content.strip()
        )
        if not user_messages:
            return
        if user_messages == last_responded_user_messages:
            # Transcript update carried no new user speech (e.g. the agent's
            # own reply got appended) — nothing new to answer.
            return
        # Let the transcript settle. If she's still talking, the next partial
        # cancels this handler task (SDK barge-in) before the sleep finishes,
        # so only the final utterance actually reaches the pipeline.
        await asyncio.sleep(LIVE_CALL_TRANSCRIPT_SETTLE_SECONDS)
        user_text = user_messages[-1]
        # Echo guard 1: the mic picked up the agent's own TTS playback.
        if _is_agent_echo(user_text):
            last_responded_user_messages = user_messages
            log.info("Live Call ignored agent-echo transcript for %s", canonical)
            return
        # Echo guard 2: the same utterance replayed moments after we answered.
        now = time.monotonic()
        answered_text, answered_at = last_answered
        if (
            _normalize_speech(user_text) == answered_text
            and answered_text
            and now - answered_at <= LIVE_CALL_REPEAT_WINDOW_SECONDS
        ):
            last_responded_user_messages = user_messages
            log.info("Live Call ignored repeated-utterance echo for %s", canonical)
            return
        last_responded_user_messages = user_messages
        last_answered = (_normalize_speech(user_text), now)
        if not bound_conversation:
            bound_conversation = await _take_binding(canonical)
        await broadcast({
            "type": "live_call_user",
            "identity": canonical,
            "conversation_id": bound_conversation,
            "content": user_text,
        })
        await session.send_response(
            _speak_and_remember(stream_anam_reply(canonical, bound_conversation, user_text))
        )

    def on_error(exc: Exception) -> None:
        log.warning("Live Call error for %s: %s", canonical, exc)

    def on_close() -> None:
        log.info("Live Call ended for %s", canonical)

    session.on("init", on_init)
    session.on("user_transcript", on_transcript)
    session.on("error", on_error)
    session.on("close", on_close)
    session.on("disconnected", on_close)
    await session.run()
