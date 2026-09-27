"""Echo relay — routes voice commands from Echo Show into Owner's active chat.

Instead of going through the HTTP/SSE chat endpoint (which creates orphan sessions),
this endpoint finds the active WebSocket conversation and injects the message there,
so it appears live in Owner's chat alongside her normal messages.

Flow: Echo -> Alexa -> Pack Bond Worker -> this endpoint -> active WS conversation
"""


import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import CLAUDE_MODEL_INTERACTIVE
from db.database import get_db, release_db
from services.connection_registry import (
    broadcast,
    get_active_conversation,
    get_active_identity,
    is_anyone_connected,
    set_active_conversation,
)
from services.session_manager import get_or_create_conversation
from services.chat_flow import StreamAccumulator
from services.chat_turn_prep import prepare_chat_turn
from services.chat_turn_finalize import finalize_assistant_turn
from services.provider_router import get_stream_source
from services.session_lifecycle import load_mode_rules
from services.task_manager import spawn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/echo", tags=["echo-relay"])


def _strip_for_speech(text: str) -> str:
    """Strip formatting tags for Echo speech output."""
    import re
    text = re.sub(r"<\/?voice[^>]*>", "", text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\*[^*]+\*", "", text)  # *actions*
    text = re.sub(r"#{1,6}\s+", "", text)  # ## headers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # **bold**
    text = re.sub(r"- ", "", text)  # bullet points
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@router.post("/relay")
async def echo_relay(request: Request):
    """Receive a voice command from Echo and inject it into the active chat.

    Returns the AI response as JSON for the Alexa skill to speak back.
    Also broadcasts through WebSocket so Owner sees it in her chat UI.
    """
    body = await request.json()
    content = (body.get("content") or "").strip()
    requested_identity = body.get("identity", "Avery")

    if not content:
        return JSONResponse({"error": "No content"}, status_code=400)


    identity = get_active_identity() or requested_identity

    # Find the active conversation for this identity
    conversation_id = get_active_conversation(identity)

    db = await get_db()
    try:
        # If no tracked conversation, find/create the daily one
        if not conversation_id:
            conversation_id = await get_or_create_conversation(db, identity)
            set_active_conversation(identity, conversation_id)
    finally:
        await release_db(db)

    # NOTE: user message is saved by prepare_chat_turn() in _process_echo_turn,
    # so we do NOT save it here to avoid duplicates. We generate a placeholder
    # message_id for the broadcast below.
    import uuid
    user_msg_id = uuid.uuid4().hex[:12]


    if is_anyone_connected():
        await broadcast({
            "type": "echo_message",
            "role": "user",
            "message_id": user_msg_id,
            "content": content,
            "identity": identity,
            "conversation_id": conversation_id,
        })

    # Process the LLM response in the background, broadcast through WS,
    # and collect the response text for the Echo to speak
    response_text = await _process_echo_turn(
        conversation_id=conversation_id,
        identity=identity,
        content=content,
    )

    return JSONResponse({
        "status": "ok",
        "response": response_text,
        "speech": _strip_for_speech(response_text),
        "identity": identity,
        "conversation_id": conversation_id,
    })


async def _process_echo_turn(
    conversation_id: str,
    identity: str,
    content: str,
) -> str:
    """Run the LLM pipeline for an Echo message and broadcast results through WS."""
    db = await get_db()
    try:
        prepared_turn = await prepare_chat_turn(
            db=db,
            conversation_id=conversation_id,
            identity=identity,
            text=content,
            images_info=[],
            documents_info=[],
            active_categories={"core"},
            log=log,
        )
    finally:
        await release_db(db)

    prompt = prepared_turn.prompt
    context_block = prepared_turn.context_block
    skill_context = prepared_turn.skill_context
    db_messages = prepared_turn.db_messages
    context_notice = prepared_turn.context_notice

    mode_rules = load_mode_rules("chat")
    cancel_event = asyncio.Event()

    # Broadcast stream_start
    if is_anyone_connected():
        await broadcast({
            "type": "stream_start",
            "identity": identity,
            "conversation_id": conversation_id,
            "context_notice": context_notice,
        })

    stream_source = await get_stream_source(
        message=prompt,
        identity=identity,
        conversation_id=conversation_id,
        orientation_context=context_block,
        db_messages=db_messages,
        model=CLAUDE_MODEL_INTERACTIVE,
        image_blocks=None,
        mode_rules=mode_rules,
        skill_context=skill_context,
        active_categories={"core"},
        cancel_event=cancel_event,
    )

    acc = StreamAccumulator()
    connected = is_anyone_connected()

    async def _persist_and_finalize(end_event: dict | None):
        """Finalize the echo turn (save + broadcast) and return the saved text."""
        acc.flush_thinking()
        streamed = acc.streamed_text()
        content = streamed or (end_event.get("full_content", "") if end_event else "")
        if not content and not acc.tool_events:
            return ""
        from api.chat import _register_content_images, _register_content_documents
        finalized_turn = await finalize_assistant_turn(
            user_message_id=prepared_turn.user_message_id,
            conversation_id=conversation_id,
            identity=identity,
            content=content,
            session_id=end_event.get("session_id") if end_event else None,
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
        final_content = finalized_turn.content
        msg_id = finalized_turn.msg_id

        if connected:
            end = dict(end_event) if end_event else {"type": "stream_end"}
            end["full_content"] = final_content
            end["conversation_id"] = conversation_id
            end["model_provenance"] = acc.model_provenance()
            if msg_id:
                end["message_id"] = msg_id
            await broadcast(end)
            if finalized_turn.response_images:
                await broadcast({
                    "type": "response_images",
                    "images": finalized_turn.response_images,
                })
            if finalized_turn.response_documents:
                await broadcast({
                    "type": "response_documents",
                    "documents": finalized_turn.response_documents,
                })

        if finalized_turn.voice_request and msg_id:
            from services.chat_turn_finalize import generate_voice_message
            spawn(
                generate_voice_message(
                    msg_id=msg_id,
                    identity=identity,
                    voice_text=finalized_turn.voice_request["voice_text"],
                    log=log,
                ),
                name=f"echo_voice_{msg_id}",
            )
        return final_content

    async for event in stream_source:
        event_type = acc.observe(event)

        if event_type == "stream_end":
            return await _persist_and_finalize(event)
        elif event_type in ("meta", "keepalive"):
            pass  # not forwarded to the UI
        else:
            if connected:
                await broadcast(event)

    # Stream ended without a stream_end event. Persist whatever we have so the
    # saved user message isn't left unanswered, then return it for the Echo.
    recovered = await _persist_and_finalize(None)
    return recovered or "I'm here, but something went wrong with my response."
