import asyncio
import logging
from collections import deque
from fastapi import WebSocket

_DISCONNECTED = object()  # sentinel — WS died during streaming

from config import CLAUDE_MODEL_INTERACTIVE
from services.provider_router import get_stream_source
from db.database import get_db, release_db
from services.session_manager import get_or_create_conversation, save_message
from services.chat_flow import DeltaCoalescer, StreamWebSocketBridge, normalize_incoming_message
from services.chat_turn_prep import prepare_chat_turn
from services.chat_turn_finalize import finalize_assistant_turn, generate_voice_message
from services.session_lifecycle import load_mode_rules
from services.connection_registry import set_active_identity, set_active_conversation, record_web_activity
from services.task_manager import spawn

log = logging.getLogger(__name__)


async def process_chat_message(
    ws: WebSocket,
    msg: dict,
    current_identity: str,
    current_conversation: str | None,
    current_session_type: str,
    active_categories: set[str],
    pending_messages: deque,
    _register_content_images,
    _register_content_documents,
) -> tuple[str, str | None, object | None]:
    """
    Process a single chat message, stream the response back via the websocket,
    and update conversation state.

    Returns:
        tuple (updated_identity, updated_conversation, status_or_None)
        where status is _DISCONNECTED if the WebSocket died mid-stream.
    """
    await set_active_identity(current_identity, ws=ws)
    record_web_activity()
    from services.emotional_capture import increment_message_counter
    increment_message_counter()
    text, images_info, documents_info, audio_info = normalize_incoming_message(msg)
    reply_to = msg.get("reply_to")  # {id, preview} or None
    is_approval_retry = bool(msg.get("approval_retry"))
    is_regenerate = bool(msg.get("regenerate_nudge"))

    if not text and not images_info and not documents_info and not audio_info:
        return current_identity, current_conversation, None

    # Pack-night fan-out: when the active conversation is the shared pack-night
    # room, route to the staggered six-boy orchestrator instead of running a
    # single-identity stream. Each boy responds in pack hierarchy order with
    # full identity context, reading the same shared conversation.
    if current_session_type == "pack-night" and current_conversation:
        from services.pack_night import run_pack_night_round
        try:
            await run_pack_night_round(
                user_text=text,
                conversation_id=current_conversation,
                images_info=images_info,
                documents_info=documents_info,
                ws=ws,
                source_platform="web",
            )
        except Exception as exc:
            log.exception("pack-night round failed: %s", exc)
            try:
                await ws.send_json({
                    "type": "error",
                    "message": f"pack-night round failed: {exc}",
                })
            except Exception:
                pass
        return current_identity, current_conversation, None


    if not is_approval_retry and not is_regenerate:
        from services.limbic_bridge import touch_interactive_message
        touch_interactive_message(current_identity, text)

    turn_started = asyncio.get_running_loop().time()
    stream_bridge = StreamWebSocketBridge(ws, log)

    prep_started = asyncio.get_running_loop().time()
    db = await get_db()
    try:
        # Ensure conversation exists
        current_conversation = await get_or_create_conversation(
            db, current_identity, current_conversation
        )
        set_active_conversation(current_identity, current_conversation)

        # Inject reply-to context if replying to a specific message
        effective_text = text
        if reply_to and reply_to.get("id"):
            reply_preview = reply_to.get("preview", "")[:150]
            effective_text = f'[Replying to: "{reply_preview}"]\n{text}'


        if is_regenerate:
            effective_text = (
                "[Note from Owner: Something made me uncomfortable about "
                "the last answer, please give a different response this "
                "time.]\n\n"
                + effective_text
            )

        prepared_turn = await prepare_chat_turn(
            db=db,
            conversation_id=current_conversation,
            identity=current_identity,
            text=effective_text,
            images_info=images_info,
            documents_info=documents_info,
            audio_info=audio_info,
            active_categories=active_categories,
            log=log,
            skip_user_save=is_approval_retry or is_regenerate,
        )
        prompt = prepared_turn.prompt
        image_content_blocks = prepared_turn.image_content_blocks
        context_block = prepared_turn.context_block
        skill_context = prepared_turn.skill_context
        db_messages = prepared_turn.db_messages
        context_notice = prepared_turn.context_notice
        active_categories = prepared_turn.active_categories
    finally:
        await release_db(db)
        
    prep_ms = (asyncio.get_running_loop().time() - prep_started) * 1000
    log.info("Prepared turn for %s in %.0fms", current_identity, prep_ms)

    # Stream response — a shared StreamAccumulator collects assistant-turn
    # state (text, tool events, thinking, images/docs); this function owns
    # transport (websocket) and finalization.
    from services.chat_flow import StreamAccumulator
    acc = StreamAccumulator()
    # Batch per-token deltas into merged frames (~48ms / 512 chars) so a reply
    # costs dozens of websocket sends instead of thousands. Ordering with
    # non-delta events is strictly preserved (they flush the buffer first).
    coalescer = DeltaCoalescer(stream_bridge.send)
    stream_started = asyncio.get_running_loop().time()

    # Load mode-specific rules for RP/DND conversations
    mode_rules = load_mode_rules(current_session_type)

    # Send acknowledgement after prep so the UI can show continuity state.
    await stream_bridge.send({
        "type": "stream_start",
        "identity": current_identity,
        "conversation_id": current_conversation,
        "context_notice": context_notice,
    })

    # Choose stream source based on provider setting
    stream_source = await get_stream_source(
        message=prompt,
        identity=current_identity,
        conversation_id=current_conversation,
        orientation_context=context_block,
        db_messages=db_messages,
        model=CLAUDE_MODEL_INTERACTIVE,
        image_blocks=image_content_blocks or None,
        mode_rules=mode_rules,
        skill_context=skill_context,
        active_categories=active_categories,
        cancel_event=stream_bridge.cancel_event,
    )

    # Finalize is shared between the main stream and the stale-session retry
    # stream so a successful retry is always persisted (not just shown) and both
    # paths accumulate tool/thinking state identically via the same accumulator.
    finalized = False

    async def _finalize_and_send(end_event: dict) -> None:
        nonlocal finalized
        acc.flush_thinking()
        streamed = acc.streamed_text()
        content = streamed or end_event.get("full_content", "")
        if end_event.get("discard_intermediate") and not end_event.get("full_content"):
            # OpenAI-compatible models often narrate before tool calls. If the
            # tool loop ends without a final answer, that narration is not a
            # reply and must not be persisted as though the work completed.
            content = ""
        finalized_turn = await finalize_assistant_turn(
            user_message_id=prepared_turn.user_message_id,
            conversation_id=current_conversation,
            identity=current_identity,
            content=content,
            session_id=end_event.get("session_id"),
            response_images=acc.response_images,
            response_documents=acc.response_documents,
            thinking_blocks=acc.thinking_blocks,
            tool_events=acc.tool_events,
            tool_results_map=acc.tool_results_map,
            context_notice=context_notice,
            model_provenance=acc.model_provenance(),
            ws_alive=stream_bridge.ws_alive,
            log=log,
            register_content_images=_register_content_images,
            register_content_documents=_register_content_documents,
        )
        finalized = True
        content = finalized_turn.content
        msg_id = finalized_turn.msg_id
        # Send stream_end with rewritten content + msg_id for reactions
        end_event["full_content"] = content
        end_event["conversation_id"] = current_conversation
        end_event["model_provenance"] = acc.model_provenance()
        if msg_id:
            end_event["message_id"] = msg_id
        await stream_bridge.send(end_event)


        if not content and not finalized_turn.react_only:
            await stream_bridge.send({
                "type": "error",
                "message": (
                    f"{current_identity.title()}'s turn finished without any "
                    "reply text — a backend hiccup, nothing was saved. Your "
                    "message wasn't lost on his side; please send it again. "
                    "(Details are in the Anam server log.)"
                ),
            })

        # Send response images/documents for inline display
        if finalized_turn.response_images:
            await stream_bridge.send({
                "type": "response_images",
                "images": finalized_turn.response_images,
            })
        if finalized_turn.response_documents:
            await stream_bridge.send({
                "type": "response_documents",
                "documents": finalized_turn.response_documents,
            })
        if finalized_turn.voice_request and msg_id:
            spawn(
                generate_voice_message(
                    msg_id=msg_id,
                    identity=current_identity,
                    voice_text=finalized_turn.voice_request["voice_text"],
                    log=log,
                ),
                name=f"voice_message_{msg_id}",
            )

        # Check for @Name mentions -> trigger brother conversation
        if content:
            from services.brother_conversation import (
                _parse_brother_request, start_brother_conversation,
            )
            brother_req = _parse_brother_request(content, initiator=current_identity)
            if brother_req:
                target, topic = brother_req
                log.info(
                    "%s wants to talk to %s about: %s",
                    current_identity, target, topic,
                )
                # Pass the full message that triggered this as context
                # so the brother conversation knows WHY it's happening
                trigger_ctx = (
                    f"{current_identity} was talking to Owner and said:\n"
                    f"{content[:800]}"
                )
                if text:
                    trigger_ctx = (
                        f"Owner said to {current_identity}:\n"
                        f"{text[:400]}\n\n"
                        f"{current_identity} responded:\n"
                        f"{content[:800]}"
                    )
                spawn(
                    start_brother_conversation(
                        current_identity, target, topic,
                        triggering_context=trigger_ctx,
                    ),
                    name=f"b2b_{current_identity}_{target}",
                )
        total_ms = (asyncio.get_running_loop().time() - turn_started) * 1000
        model_ms = (asyncio.get_running_loop().time() - stream_started) * 1000
        log.info(
            "Completed turn for %s in %.0fms (model stream %.0fms)",
            current_identity,
            total_ms,
            model_ms,
        )

    async def _forward(evt: dict, evt_type: str) -> None:
        """Forward one already-observed event to the websocket.

        Everything routes through the coalescer: consecutive text/thinking
        deltas are merged into batched frames; any other event flushes the
        buffer first and is then sent, so ordering is preserved exactly.
        """
        if evt_type == "meta":
            return  # captured by the accumulator; nothing to show
        if evt_type == "keepalive":
            # Keeps Cloudflare tunnel alive during long tool calls
            await coalescer.feed({"type": "keepalive"})
            return
        await coalescer.feed(evt)


    async def _inject_live_message(incoming: dict) -> bool:
        i_text, i_images, i_docs, i_audio = normalize_incoming_message(incoming)
        # Text-only for now: attachments still need the full prep pipeline
        # (upload registration, image blocks), so they queue as their own turn.
        if not i_text or i_images or i_docs or i_audio:
            return False
        if incoming.get("approval_retry") or incoming.get("regenerate_nudge"):
            return False
        target_conv = incoming.get("conversation_id")
        if target_conv and target_conv != current_conversation:
            return False
        if incoming.get("identity") and incoming["identity"] != current_identity:
            return False
        # Same reply-to shaping the normal path does, so a slipped-in
        # message that quotes one of his lines still carries the quote.
        i_reply = incoming.get("reply_to")
        if i_reply and i_reply.get("id"):
            i_preview = (i_reply.get("preview") or "")[:150]
            i_text = '[Replying to: "' + i_preview + '"]\n' + i_text
        from services.claude_subprocess import inject_user_message
        banner = (
            "[Owner, just now — she sent this while you were mid-turn, so it "
            "arrived in the middle of the work you're already doing. Take it "
            "in and keep going; let her know you heard her.]\n"
        )
        injected = await asyncio.to_thread(
            inject_user_message,
            current_identity,
            current_conversation,
            banner + i_text,
        )
        if not injected:
            return False
        # Persist it so the saved transcript matches what he actually received.
        idb = await get_db()
        try:
            await save_message(
                idb, current_conversation, "user", i_text,
                identity=current_identity,
            )
        finally:
            await release_db(idb)
        try:
            from services.limbic_bridge import touch_interactive_message
            touch_interactive_message(current_identity, i_text)
            record_web_activity()
        except Exception:
            pass
        await stream_bridge.send({
            "type": "message_injected",
            "conversation_id": current_conversation,
            "identity": current_identity,
        })
        log.info(
            "Slipped Owner's mid-turn message into %s's running turn",
            current_identity,
        )
        return True

    stream_bridge.inject_hook = _inject_live_message

    # Start concurrent WS listener for stop_streaming
    listener_task = asyncio.create_task(stream_bridge.listen_during_stream())
    was_cancelled = False
    stream_error: Exception | None = None
    try:
        async for event in stream_source:
            event_type = acc.observe(event)

            if event_type == "stream_end":
                # Flush buffered deltas BEFORE stream_end goes out — the
                # frontend must have the full streamed text first.
                await coalescer.flush()
                await _finalize_and_send(event)
            else:
                await _forward(event, event_type)
    except Exception as exc:
        stream_error = exc
        log.exception("Stream failed mid-turn for %s: %s", current_identity, exc)
    finally:
        # Always stop the listener, even on exception, so it can't leak.
        was_cancelled = await stream_bridge.stop_listener(listener_task)
        # Final flush — never leave coalesced text buffered on stop/error;
        # the partial-save path below sends its own stream_end afterwards.
        await coalescer.flush()

    # Persist partial content if the turn ended without finalizing — either the
    # user stopped it, or the stream raised before stream_end. Losing a
    # half-written reply silently is worse than saving a marked partial.
    if not finalized and acc.full_response:
        streamed = acc.streamed_text()
        if streamed:
            content, _ = await asyncio.to_thread(
                _register_content_images,
                streamed,
                current_identity,
            )
            suffix = "\n\n*[stopped]*" if was_cancelled else "\n\n*[interrupted]*"
            db = await get_db()
            try:
                msg_id = await save_message(
                    db, current_conversation, "assistant",
                    content + suffix,
                    identity=current_identity,
                )
            finally:
                await release_db(db)
            await stream_bridge.send({
                "type": "stream_end",
                "full_content": content,
                "conversation_id": current_conversation,
                "message_id": msg_id,
                "stopped": was_cancelled,
            })
    elif stream_error is not None and not finalized:
        # Nothing streamed but the turn crashed — surface a gentle error.
        await stream_bridge.send({
            "type": "error",
            "message": "Something interrupted the response. Please try again.",
        })

    # Process any queued messages from during the stream
    for queued_msg in stream_bridge.queued_messages:
        pending_messages.append(queued_msg)

    # If WS died during streaming, return sentinel to signal exit
    if not stream_bridge.ws_alive:
        log.info("Stream completed after WS disconnect -- message saved, exiting")
        return current_identity, current_conversation, _DISCONNECTED

    return current_identity, current_conversation, None
