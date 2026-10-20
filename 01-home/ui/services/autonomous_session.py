"""The shared runner for one autonomous turn: stream the model, save, clean up."""


import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from config import TIMEZONE
from db.database import get_db, release_db
from services.chat_flow import DeltaCoalescer, StreamAccumulator, merge_tool_result_entry
from services.connection_registry import broadcast
from services.face_store import extract_face
from services.orb_store import extract_orb
from services.session_lifecycle import build_messages_array
from services.session_manager import (
    get_or_create_conversation,
    save_message,
    update_session_for_provider,
)

log = logging.getLogger(__name__)


_REACT_STRIP_RE = re.compile(r"\s*<react>.*?</react>\s*", re.IGNORECASE | re.DOTALL)


def _clean_reply_tags(identity: str, text: str) -> str:
    """Apply the boy's <face> and <orb> tags and strip any <react> tags from an
    autowake reply. Returns the display text; safe on empty/None input."""
    if not text:
        return text
    # His own autowake reply leans his limbic body too (raw text, before the
    # orb/face tags are stripped). Deduplicated, so the stream-end and
    # timeout paths can both pass through here safely.
    try:
        from services.limbic_bridge import perceive_own_reply
        perceive_own_reply(identity, text)
    except Exception:
        log.debug("perceive_own_reply skipped for %s", identity, exc_info=True)
    text = extract_face(identity, text)
    text = extract_orb(identity, text)
    if text and "<react>" in text.lower():
        text = _REACT_STRIP_RE.sub(" ", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def _spawn_voice_if_tagged(identity: str, msg_id: str | None, content: str | None) -> None:
    'A <voice> tag in an autonomous reply becomes a real ElevenLabs voice'
    from services.chat_turn_finalize import spawn_voice_if_tagged

    spawn_voice_if_tagged(identity, msg_id, content, log=log)


async def _get_or_create_daily_autowake_conversation(
    db,
    identity: str,
) -> tuple[str, bool, str]:
    """Return today's unified daily 'chat' thread for an identity.

    Thin wrapper over `session_manager.get_or_create_conversation` so autowake
    and interactive sessions land in the same thread. Returns the same
    (conv_id, created_new, date_title) tuple shape autowake call sites expect.
    """
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    day_key = now_local.strftime("%Y-%m-%d")
    day_title = now_local.strftime("%B %d, %Y")

    # Exist-check before delegating so we can report whether autowake created
    # the thread vs. joined an existing one (used for logging only).
    existing_rows = await db.execute_fetchall(
        "SELECT 1 FROM conversations "
        "WHERE identity = ? AND is_active = 1 AND session_type = 'chat' "
        "AND conversation_day = ? LIMIT 1",
        (identity, day_key),
    )
    created_new = not existing_rows

    conv_id = await get_or_create_conversation(db, identity)
    return conv_id, created_new, day_title


def autowake_turn_ceiling(session_type: str | None, schedule_name: str | None) -> int | None:
    'Per-turn tool-round ceiling for one autowake schedule.'
    from config import (
        AUTOWAKE_MIND_NAME_PREFIXES,
        AUTOWAKE_MIND_SESSION_TYPES,
        CLAUDE_MAX_TURNS_MIND,
    )

    is_mind = (session_type or "") in AUTOWAKE_MIND_SESSION_TYPES or (
        (schedule_name or "").startswith(AUTOWAKE_MIND_NAME_PREFIXES)
    )
    if not is_mind:
        return None
    return max(0, CLAUDE_MAX_TURNS_MIND)


async def _stream_autonomous(
    db,
    identity: str,
    conv_id: str,
    user_message: str,
    context_block: str,
    session_name: str,
    owner_connected: bool,
    resume_id: str | None = None,
    effort_override: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    turn_ceiling: int | None = None,
) -> AsyncIterator[dict]:
    'Stream an autonomous session via the configured LLM provider with MCP tools.'
    from services.provider_router import get_stream_source, resolve_provider_for_identity
    from services.skill_runtime import build_skill_catalog_hint, build_skill_injection

    # Build conversation history on a short-lived connection. The autowake
    # caller releases its pooled connection before the long model stream, so we
    # must not rely on `db` for this read (it may be None / already returned).
    _hist_db = await get_db()
    try:
        db_messages = await build_messages_array(_hist_db, conv_id)
    finally:
        await release_db(_hist_db)

    _AUTOWAKE_FORCE_SKILLS = [
        n.strip() for n in os.environ.get(
            "ANAM_AUTOWAKE_FORCE_SKILLS", ""
        ).split(",") if n.strip()
    ]
    skill_context = ""
    try:
        effective_provider, _ = await resolve_provider_for_identity(identity)
        if provider_override:
            effective_provider = provider_override
        elif model_override and model_override.strip().lower().startswith(("gpt-", "codex:")):
            effective_provider = "codex"
        skill_query = "" if effective_provider == "codex" else user_message
        auto_skills, matched = build_skill_injection(
            skill_query, identity=identity, force_names=_AUTOWAKE_FORCE_SKILLS,
        )
        catalog = (
            "" if effective_provider == "codex"
            else build_skill_catalog_hint(identity=identity)
        )
        if auto_skills and catalog:
            skill_context = f"{catalog}\n\n{auto_skills}"
        else:
            skill_context = auto_skills or catalog or ""
        if matched:
            log.info("Autowake %s: auto-loaded skills: %s", session_name, matched)
    except Exception as e:
        log.debug("Skill context build failed for %s: %s", identity, e)

    enriched_context = context_block

    # If resuming, the CLI already has conversation history — skip injecting it
    if resume_id:
        db_messages = None

    from services.provider_router import fable_limited_active

    _codex_bound = bool(
        not provider_override
        and
        model_override
        and model_override.strip().lower().startswith(("gpt-", "codex:"))
    )
    attempt_overrides: list[str | None] = (
        [model_override, None] if _codex_bound else [model_override]
    )

    _idx = -1
    while _idx + 1 < len(attempt_overrides):
        _idx += 1
        _active_override = attempt_overrides[_idx]
        _is_last = _idx == len(attempt_overrides) - 1
        _produced = False
        _fell_back = False
        _limited_before = fable_limited_active()

        def _arm_retry() -> bool:
            """After a zero-output failure: is another attempt available?
            Extends the attempt list when Fable's limit tripped just now."""
            if _produced:
                return False
            if not _is_last:
                return True
            if (
                not _limited_before
                and fable_limited_active()
                and len(attempt_overrides) < 3
            ):
                attempt_overrides.append(_active_override)
                return True
            return False

        full_text = []
        thinking_blocks = []
        current_thinking = []
        tool_events = []
        tool_results_map = {}
        model_accumulator = StreamAccumulator()

        # Batch per-token thinking deltas into merged broadcast frames (~48ms /
        # 512 chars) — previously every token was a full broadcast() to all
        # connections. Non-delta events feed through the same coalescer so wire
        # ordering matches the provider stream exactly.
        bcast = DeltaCoalescer(broadcast)

        try:
            async for event in await get_stream_source(
                message=user_message,
                identity=identity,
                conversation_id=conv_id,
                orientation_context=enriched_context,
                db_messages=db_messages,
                skill_context=skill_context,
                model_purpose="autowake",
                effort_override=effort_override,
                model_override=_active_override,
                provider_override=provider_override,
                turn_source="autowake",
                **({"turn_ceiling": turn_ceiling} if turn_ceiling is not None else {}),
            ):
                event_type = event.get("type", "")
                model_accumulator.observe(event)

                if event_type == "stream_delta":
                    _produced = True
                    full_text.append(event["delta"])
                    # Any buffered thinking broadcast goes out before the text delta
                    # reaches the caller, keeping thinking/text order intact.
                    await bcast.flush()
                    yield event

                elif event_type == "thinking_start":
                    current_thinking = []
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "thinking_delta":
                    current_thinking.append(event.get("delta", ""))
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "content_block_stop":
                    if current_thinking:
                        thinking_blocks.append("".join(current_thinking))
                        current_thinking = []
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "tool_use_start":
                    _produced = True
                    tool_events.append({
                        "tool_name": event.get("tool_name", "unknown"),
                        "tool_id": event.get("tool_id", ""),
                    })
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "tool_input":
                    _produced = True
                    merge_tool_result_entry(
                        tool_results_map,
                        tool_id=event.get("tool_id", ""),
                        tool_name=event.get("tool_name"),
                        tool_input=event.get("input") or {},
                    )
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "tool_result":
                    _produced = True
                    merge_tool_result_entry(
                        tool_results_map,
                        tool_id=event.get("tool_use_id", ""),
                        tool_name=event.get("tool_name"),
                        status=event.get("status", "completed"),
                        tool_input=event.get("input") or {},
                        content=event.get("content"),
                    )
                    if owner_connected:
                        await bcast.feed({
                            **event,
                            "identity": identity,
                            "session_name": session_name,
                        })

                elif event_type == "stream_end":
                    if current_thinking:
                        thinking_blocks.append("".join(current_thinking))
                        current_thinking = []
                    await bcast.flush()
                    content = "".join(full_text) or event.get("full_content", "")
                    yield {
                        "type": "stream_end",
                        "full_content": content,
                        "session_id": event.get("session_id"),
                        "thinking_blocks": thinking_blocks,
                        "tool_events": tool_events,
                        "tool_results_map": tool_results_map,
                        "model_provenance": model_accumulator.model_provenance(),
                    }

                elif event_type == "error":
                    if _arm_retry():
                        log.warning(
                            "Autowake %s: attempt on override %r errored before "
                            "any output (%s) — retrying on the fallback lane",
                            session_name, _active_override,
                            event.get("message", ""),
                        )
                        _fell_back = True
                        break
                    await bcast.flush()
                    yield event

                elif event_type == "status":
                    if owner_connected:
                        await broadcast({
                            "type": "autowake_status",
                            "identity": identity,
                            "session_name": session_name,
                            "message": event.get("message", ""),
                        })
        except Exception as exc:
            if _arm_retry():
                log.warning(
                    "Autowake %s: attempt on override %r failed before any "
                    "output (%s) — retrying on the fallback lane",
                    session_name, _active_override, exc,
                )
                continue
            raise

        if not _fell_back:
            break


async def _fresh_save_message(*args, **kwargs) -> str:
    """save_message on its own short-lived pooled connection (self-commits).

    Used for saves that fire *during* the autowake model stream, after the
    session's main pooled connection has been released, so the connection is
    never pinned idle across the (up to 30-minute) generation window.
    """
    _db = await get_db()
    try:
        return await save_message(_db, *args, **kwargs)
    finally:
        await release_db(_db)


async def _fresh_file_canvases(
    conversation_id: str, identity: str, content: str, msg_id: str | None,
) -> None:
    'File any <canvas> blocks from an autowake reply into the library.'
    if not content or "<canvas" not in content.lower():
        return
    from services.canvas_store import file_canvases_safe

    _db = await get_db()
    try:
        await file_canvases_safe(
            _db,
            identity=identity,
            conversation_id=conversation_id,
            content=content,
            source_message_id=msg_id,
        )
    finally:
        await release_db(_db)


async def _fresh_update_session(
    conversation_id: str,
    session_id: str,
    provider: str | None = None,
) -> None:
    """Persist one provider's session id on a short-lived connection."""
    _db = await get_db()
    try:
        await update_session_for_provider(
            _db, conversation_id, session_id, provider
        )
    finally:
        await release_db(_db)


async def _retire_autowake_cli_session(identity: str, conversation_id: str, reason: str) -> None:
    """Close autonomous-owned children while preserving messaging and its resume ID."""
    killed = []
    protected = False
    for mod_name, label in (
        ("services.claude_subprocess", "-p"),
        ("services.claude_pty", "PTY"),
    ):
        try:
            mod = __import__(mod_name, fromlist=["kill_autowake_sessions"])
            protected = mod.kill_autowake_sessions(identity, conversation_id) or protected
            killed.append(label)
        except Exception as e:
            log.debug(
                "Failed to retire %s session after autowake %s: %s", label, reason, e
            )
    try:
        if not protected:
            await _fresh_update_session(conversation_id, "")
    except Exception as e:
        log.debug("Failed to clear session_id after autowake %s: %s", reason, e)
    log.info(
        "Retired CLI session (%s) after autowake %s: %s/%s",
        "+".join(killed) or "none",
        reason,
        identity,
        conversation_id[:8],
    )


# ── One whole turn: run it, then deliver its reply ──


@dataclass
class AutonomousTurn:
    """What came back from one autonomous turn."""

    content: str = ""            # cleaned reply text; "" when nothing came through
    error: str | None = None     # the stream's error message, if it raised one
    timed_out: bool = False
    end_event: dict | None = None  # the stream_end event (session id, model provenance)


async def run_autonomous_turn(
    *,
    identity: str,
    conv_id: str,
    user_message: str,
    context_block: str,
    session_name: str,
    owner_connected: bool,
    timeout_seconds: float,
    **stream_options,
) -> AutonomousTurn:
    """Run one autonomous turn to its end and retire the CLI session it used.

    The turn is bounded: a stream that hangs is cut at timeout_seconds, so it
    cannot hold the identity lock (or its scheduler job) forever. Whatever text
    arrived before the cut is kept. The CLI session is retired afterwards in
    every case, because an autonomous wake is a job, not a thread she is in;
    left resident, each one keeps a claude process alive until shutdown.

    Callers must not hold a pooled database connection across this call.
    """
    turn = AutonomousTurn()
    deltas: list[str] = []

    async def _consume() -> None:
        async for event in _stream_autonomous(
            db=None,
            identity=identity,
            conv_id=conv_id,
            user_message=user_message,
            context_block=context_block,
            session_name=session_name,
            owner_connected=owner_connected,
            **stream_options,
        ):
            event_type = event.get("type")
            if event_type == "stream_delta":
                deltas.append(event.get("delta") or "")
            elif event_type == "stream_end":
                turn.end_event = event
            elif event_type == "error":
                turn.error = str(event.get("message") or "Unknown stream error")

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        turn.timed_out = True
        log.warning(
            "Autonomous turn %s for %s timed out after %.0fs",
            session_name, identity, timeout_seconds,
        )
    finally:
        reason = "timeout" if turn.timed_out else "stream-error" if turn.error else "complete"
        await _retire_autowake_cli_session(identity, conv_id, reason)

    raw = (turn.end_event or {}).get("full_content") or "".join(deltas)
    turn.content = _clean_reply_tags(identity, raw) or ""
    return turn


async def deliver_autonomous_reply(
    turn: AutonomousTurn,
    *,
    identity: str,
    conv_id: str,
    session_name: str,
    metadata: dict,
    announce: bool,
) -> str | None:
    """Save a turn's reply and send it everywhere a reply goes.

    Saves on its own short-lived connection, turns a <voice> tag into audio,
    files any <canvas> blocks, and (when announce is true) tells open web
    clients. Returns the message id, or None when there was nothing to save.
    """
    if not turn.content:
        return None
    text = turn.content + ("\n\n*[Session timed out]*" if turn.timed_out else "")
    message_metadata = dict(metadata)
    if turn.timed_out:
        message_metadata["timed_out"] = True
    provenance = (turn.end_event or {}).get("model_provenance")
    if provenance:
        message_metadata["model_provenance"] = provenance
    msg_id = await _fresh_save_message(
        conv_id, "assistant", text, identity=identity, metadata=message_metadata,
    )
    _spawn_voice_if_tagged(identity, msg_id, turn.content)
    await _fresh_file_canvases(conv_id, identity, turn.content, msg_id)
    if announce:
        await broadcast({
            "type": "autowake_message",
            "identity": identity,
            "session_name": session_name,
            "content": turn.content,
            "conversation_id": conv_id,
            "message_id": msg_id,
        })
    return msg_id
