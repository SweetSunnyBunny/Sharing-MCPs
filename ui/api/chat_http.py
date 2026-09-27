"""HTTP/SSE fallback for chat when WebSocket connections fail (e.g. mobile data).

POST /api/chat/send      — send a message, receive streamed response as SSE
POST /api/chat/identity   — switch identity
GET  /api/chat/history    — load conversation history
"""

# ANAM GUIDE: CHAT FALLBACK OVER PLAIN HTTP
# What: The backup chat lane — when the phone's WebSocket won't hold (mobile data), chat.js sends messages here and gets the reply streamed back as SSE.
# Called by: static/js/chat.js (it switches to this automatically when the WebSocket keeps dropping). Normal chat lives in api/chat.py.
# Edit here when: The fallback lane itself misbehaves (session cookies, SSE streaming, history loading). Shared turn logic belongs in services/chat_turn_prep.py / chat_turn_finalize.py so both lanes get it.

import asyncio
import json
import logging
import time
import uuid
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from config import (
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    CLAUDE_MODEL_INTERACTIVE,
)
from db.database import get_db, release_db
from services.connection_registry import set_active_identity
from services.provider_router import get_stream_source
from services.session_lifecycle import load_mode_rules
from services.session_manager import get_or_create_conversation, save_message
from services.chat_flow import StreamAccumulator, normalize_incoming_message
from services.chat_session_ops import (
    create_conversation,
    load_history_payload,
    switch_identity_conversation,
)
from services.chat_turn_prep import prepare_chat_turn
from services.chat_turn_finalize import finalize_assistant_turn, generate_voice_message
from services.task_manager import spawn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat-http"])

# Per-session state for HTTP mode (keyed by session cookie hash)
_http_sessions: dict[str, dict] = {}
_HTTP_SESSION_TTL_SECONDS = 12 * 3600
_HTTP_ANON_COOKIE = "anam_http_session"
_HTTP_ANON_COOKIE_MAX_AGE = 7 * 86400


def _new_http_session_state() -> dict:
    return {
        "identity": "Avery",
        "conversation_id": None,
        "session_type": "chat",
        "active_categories": {"core"},
    }


def _prune_http_sessions(now_monotonic: float | None = None) -> None:
    now_monotonic = now_monotonic or time.monotonic()
    cutoff = now_monotonic - _HTTP_SESSION_TTL_SECONDS
    stale_keys = [
        key for key, record in _http_sessions.items()
        if record.get("last_seen_at", 0) < cutoff
    ]
    for key in stale_keys:
        _http_sessions.pop(key, None)


def _resolve_http_session(request: Request) -> tuple[dict, str | None]:
    """Get or create session state and return an anon cookie if one was minted."""
    _prune_http_sessions()

    session_token = (request.cookies.get("anam_session") or "").strip()
    cookie_value = None
    if session_token:
        session_key = f"auth:{session_token}"
    else:
        anon_token = (request.cookies.get(_HTTP_ANON_COOKIE) or "").strip()
        if not anon_token:
            anon_token = uuid.uuid4().hex
            cookie_value = anon_token
        session_key = f"anon:{anon_token}"

    if session_key not in _http_sessions:
        _http_sessions[session_key] = {
            "state": _new_http_session_state(),
            "last_seen_at": time.monotonic(),
        }
    _http_sessions[session_key]["last_seen_at"] = time.monotonic()
    return _http_sessions[session_key]["state"], cookie_value


def _set_http_session_cookie(response, cookie_value: str | None) -> None:
    if not cookie_value:
        return
    response.set_cookie(
        _HTTP_ANON_COOKIE,
        cookie_value,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=_HTTP_ANON_COOKIE_MAX_AGE,
    )


def _get_session_lock(state: dict) -> asyncio.Lock:
    """Per-session turn lock so two concurrent POSTs for the same browser
    session can't interleave streams and clobber each other's conversation_id
    or active_categories. Created lazily inside the running loop."""
    lock = state.get("_lock")
    if lock is None:
        lock = asyncio.Lock()
        state["_lock"] = lock
    return lock


def _register_content_images(content: str, identity: str | None = None):
    """Scan markdown images in response, copy to images dir, rewrite URLs."""
    from api.images import informative_filename
    from api.chat import _register_content_images as _ws_register
    return _ws_register(content, identity)


def _register_content_documents(content: str, identity: str | None = None):
    from api.chat import _register_content_documents as _ws_register
    return _ws_register(content, identity)


class StreamSSEBridge:
    """Same interface as StreamWebSocketBridge but writes SSE events to an async queue."""

    def __init__(self, queue: asyncio.Queue, log_: logging.Logger):
        self._queue = queue
        self.log = log_
        self.ws_alive = True  # Always "alive" — HTTP doesn't disconnect mid-stream
        self.cancel_event = asyncio.Event()
        self.queued_messages: deque[dict] = deque()

    async def send(self, data: dict) -> None:
        await self._queue.put(data)

    async def listen_during_stream(self) -> None:
        """No-op — HTTP has no bidirectional listen."""
        try:
            while not self.cancel_event.is_set():
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def stop_listener(self, listener_task: asyncio.Task) -> bool:
        was_cancelled = self.cancel_event.is_set()
        if not was_cancelled:
            self.cancel_event.set()
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        return was_cancelled


async def _sse_event_generator(queue: asyncio.Queue):
    """Yield SSE-formatted events from the async queue."""
    while True:
        try:
            data = await asyncio.wait_for(queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            # Send keepalive comment to prevent connection timeout
            yield ": keepalive\n\n"
            continue

        if data is None:
            # Sentinel — stream is done
            break

        event_type = data.get("type", "message")
        payload = json.dumps(data, ensure_ascii=False)
        yield f"event: {event_type}\ndata: {payload}\n\n"


@router.post("/send")
async def chat_send(request: Request):
    """Accept a chat message and stream the response as SSE events."""
    state, anon_cookie = _resolve_http_session(request)

    body = await request.json()
    text, images_info, documents_info, audio_info = normalize_incoming_message(body)

    if not text and not images_info and not documents_info and not audio_info:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    identity = body.get("identity", state["identity"])
    state["identity"] = identity
    conversation_id = body.get("conversation_id", state["conversation_id"])
    is_approval_retry = bool(body.get("approval_retry"))

    queue: asyncio.Queue = asyncio.Queue()
    bridge = StreamSSEBridge(queue, log)

    async def _process():
        nonlocal conversation_id
        session_lock = _get_session_lock(state)
        await session_lock.acquire()
        try:
            await set_active_identity(identity)

            db = await get_db()
            try:
                conversation_id = await get_or_create_conversation(
                    db, identity, conversation_id
                )
                state["conversation_id"] = conversation_id

                prepared_turn = await prepare_chat_turn(
                    db=db,
                    conversation_id=conversation_id,
                    identity=identity,
                    text=text,
                    images_info=images_info,
                    documents_info=documents_info,
                    audio_info=audio_info,
                    active_categories=state["active_categories"],
                    log=log,
                    skip_user_save=is_approval_retry,
                )
            finally:
                await release_db(db)

            prompt = prepared_turn.prompt
            image_content_blocks = prepared_turn.image_content_blocks
            context_block = prepared_turn.context_block
            skill_context = prepared_turn.skill_context
            db_messages = prepared_turn.db_messages
            context_notice = prepared_turn.context_notice
            state["active_categories"] = prepared_turn.active_categories

            # Send stream_start
            await bridge.send({
                "type": "stream_start",
                "identity": identity,
                "context_notice": context_notice,
            })

            mode_rules = load_mode_rules(state["session_type"])
            acc = StreamAccumulator()
            finalized = False

            stream_source = await get_stream_source(
                message=prompt,
                identity=identity,
                conversation_id=conversation_id,
                orientation_context=context_block,
                db_messages=db_messages,
                model=CLAUDE_MODEL_INTERACTIVE,
                image_blocks=image_content_blocks or None,
                mode_rules=mode_rules,
                skill_context=skill_context,
                active_categories=state["active_categories"],
                cancel_event=bridge.cancel_event,
            )

            listener_task = asyncio.create_task(bridge.listen_during_stream())

            try:
                async for event in stream_source:
                    event_type = acc.observe(event)

                    if event_type == "meta":
                        pass
                    elif event_type == "keepalive":
                        await bridge.send({"type": "keepalive"})
                    elif event_type == "stream_end":
                        acc.flush_thinking()
                        streamed = acc.streamed_text()
                        content = streamed or event.get("full_content", "")
                        finalized_turn = await finalize_assistant_turn(
                            user_message_id=prepared_turn.user_message_id,
                            conversation_id=conversation_id,
                            identity=identity,
                            content=content,
                            session_id=event.get("session_id"),
                            response_images=acc.response_images,
                            response_documents=acc.response_documents,
                            thinking_blocks=acc.thinking_blocks,
                            tool_events=acc.tool_events,
                            tool_results_map=acc.tool_results_map,
                            context_notice=context_notice,
                            model_provenance=acc.model_provenance(),
                            ws_alive=True,
                            log=log,
                            register_content_images=_register_content_images,
                            register_content_documents=_register_content_documents,
                        )
                        finalized = True
                        content = finalized_turn.content
                        msg_id = finalized_turn.msg_id
                        event["full_content"] = content
                        event["model_provenance"] = acc.model_provenance()
                        if msg_id:
                            event["message_id"] = msg_id
                        await bridge.send(event)

                        if finalized_turn.response_images:
                            await bridge.send({
                                "type": "response_images",
                                "images": finalized_turn.response_images,
                            })
                        if finalized_turn.response_documents:
                            await bridge.send({
                                "type": "response_documents",
                                "documents": finalized_turn.response_documents,
                            })
                        if finalized_turn.voice_request and msg_id:
                            # HTTP/SSE fallback closes after stream_end, so generate
                            # in the background; history picks up the voice card on reload.
                            spawn(
                                generate_voice_message(
                                    msg_id=msg_id,
                                    identity=identity,
                                    voice_text=finalized_turn.voice_request["voice_text"],
                                    log=log,
                                    send=None,
                                ),
                                name=f"http_voice_message_{msg_id}",
                            )
                    else:
                        # stream_delta, tool_use_start, tool_input, tool_result,
                        # thinking_start/delta, content_block_stop, approval_required.
                        await bridge.send(event)
            finally:
                await bridge.stop_listener(listener_task)

            # Persist partial content if the stream ended without finalizing so
            # an interrupted turn isn't silently lost.
            if not finalized and acc.full_response:
                streamed = acc.streamed_text()
                if streamed:
                    content, _ = await asyncio.to_thread(
                        _register_content_images, streamed, identity,
                    )
                    db2 = await get_db()
                    try:
                        msg_id = await save_message(
                            db2, conversation_id, "assistant",
                            content + "\n\n*[interrupted]*",
                            identity=identity,
                        )
                    finally:
                        await release_db(db2)
                    await bridge.send({
                        "type": "stream_end",
                        "full_content": content,
                        "conversation_id": conversation_id,
                        "message_id": msg_id,
                        "stopped": False,
                    })

        except Exception as e:
            log.exception("HTTP chat error: %s", e)
            await bridge.send({"type": "error", "message": str(e)})
        finally:
            session_lock.release()
            await queue.put(None)  # Sentinel to end SSE stream

    # Start processing in background, return SSE stream immediately
    asyncio.create_task(_process())

    response = StreamingResponse(
        _sse_event_generator(queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx/proxy buffering
        },
    )
    _set_http_session_cookie(response, anon_cookie)
    return response


@router.post("/identity")
async def chat_switch_identity(request: Request):
    """Switch identity in HTTP mode."""
    state, anon_cookie = _resolve_http_session(request)

    body = await request.json()
    identity = body.get("identity", state["identity"])
    state["identity"] = identity
    await set_active_identity(identity)
    conversation_id = await switch_identity_conversation(identity)
    state["conversation_id"] = conversation_id

    response = JSONResponse({
        "type": "identity_switched",
        "identity": identity,
        "conversation_id": conversation_id,
    })
    _set_http_session_cookie(response, anon_cookie)
    return response


@router.get("/history")
async def chat_history(request: Request):
    """Load conversation history for current identity."""
    state, anon_cookie = _resolve_http_session(request)

    identity = request.query_params.get("identity", state["identity"])
    conversation_id = request.query_params.get("conversation_id", None)
    limit_raw = request.query_params.get("limit", "50")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 50
    state["identity"] = identity
    await set_active_identity(identity)

    payload, conv_id, session_type = await load_history_payload(
        identity,
        conversation_id,
        limit=limit,
    )
    state["conversation_id"] = conv_id
    state["session_type"] = session_type

    response = JSONResponse(payload)
    _set_http_session_cookie(response, anon_cookie)
    return response


@router.post("/new_conversation")
async def chat_new_conversation(request: Request):
    """Create a new conversation in HTTP mode."""
    state, anon_cookie = _resolve_http_session(request)

    body = await request.json()
    identity = body.get("identity", state["identity"])
    session_type = body.get("session_type", "chat")
    state["identity"] = identity
    await set_active_identity(identity)

    conversation_id, session_type = await create_conversation(identity, session_type)
    state["conversation_id"] = conversation_id
    state["session_type"] = session_type

    response = JSONResponse({
        "type": "new_conversation",
        "conversation_id": conversation_id,
        "identity": identity,
        "session_type": session_type,
    })
    _set_http_session_cookie(response, anon_cookie)
    return response
