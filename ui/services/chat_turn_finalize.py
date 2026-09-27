"""Post-stream assistant turn finalization helpers."""

# ANAM GUIDE: FINISH AND SAVE A REPLY
# What: Everything that happens AFTER a boy finishes talking — saves the reply to the database, applies <react> emoji tags, turns <voice> tags into real ElevenLabs voice messages, files <canvas> blocks, and logs tool use.
# Called by: services/chat_pipeline.py at the end of every interactive turn; also services/autowake.py, discord_mentions_bridge.py, platform_bridge.py, pack_night.py, brother_conversation.py, api/chat_http.py, api/echo_relay.py.
# Edit here when: You want to change how <react>/<voice> tags behave, what gets saved with a finished reply, or how voice messages are generated from a tag.

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from config import VOICE_DIR, VOICE_VAULT_DIR
from db.database import get_db, release_db
from services.chat_flow import build_assistant_message_metadata
from services.connection_registry import broadcast
from services.document_visibility import visible_chat_documents
from services.elevenlabs_tts import synthesize
from services.session_manager import (
    save_message,
    update_session_for_provider,
    update_message_metadata,
)


@dataclass
class FinalizedAssistantTurn:
    content: str
    msg_id: str | None
    response_images: list[dict]
    response_documents: list[dict]
    voice_request: dict[str, Any] | None
    # True when the reply was ONLY <react> tags — content legitimately empty.
    # Lets the pipeline distinguish "boy just reacted" from "backend produced
    # nothing" so the empty-turn banner never fires on a real reaction.
    react_only: bool = False


_REACT_TAG_RE = re.compile(
    r"<react>\s*"
    r"(?:"
    r":([a-z0-9][a-z0-9_-]{0,62}):(?::?\s*([0-9a-fA-F\-]{8,}))?"  # :shortcode: [+id]
    r"|([^:<]+?)(?:\s*:\s*([0-9a-fA-F\-]{8,}))?"                  # unicode emoji [+ :id]
    r")"
    r"\s*</react>",
    re.IGNORECASE,
)
# Permissive strip for display — catches even a malformed tag the strict parser
# above would skip, so nothing tag-shaped ever leaks into the visible reply.
_REACT_STRIP_RE = re.compile(r"\s*<react>.*?</react>\s*", re.IGNORECASE | re.DOTALL)


def _parse_react_tags(content: str) -> tuple[str, list[tuple[str, str | None]]]:
    """Pull <react> tags out of an assistant reply.

    Returns (content_with_tags_removed, [(emoji, target_message_id_or_None), ...]).
    """
    tags: list[tuple[str, str | None]] = []
    for m in _REACT_TAG_RE.finditer(content):
        shortcode, sc_target, unicode_emoji, uni_target = m.groups()
        if shortcode:
            # Custom emoji — normalize to the manifest's lowercase `:name:` key.
            emoji = f":{shortcode.strip().lower()}:"
            target = (sc_target or "").strip() or None
        else:
            emoji = (unicode_emoji or "").strip()
            target = (uni_target or "").strip() or None
        if emoji:
            tags.append((emoji, target))
    cleaned = _REACT_STRIP_RE.sub(" ", content)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, tags


async def _apply_reaction_tags(
    db, conversation_id: str, identity: str,
    tags: list[tuple[str, str | None]], log,
) -> None:
    """Persist each parsed <react> tag onto its target message and broadcast a
    live update, so the emoji shows up on Owner's message in real time.

    An emoji with no explicit id targets Owner's most recent message in this
    conversation (looked up once, lazily). Reacting is idempotent per identity.
    """
    if not tags:
        return

    resolved_default: str | None = None
    looked_up = False
    touched_any = False

    for emoji, target in tags:
        mid = target
        if not mid:
            if not looked_up:
                rows = await db.execute_fetchall(
                    "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' "
                    "ORDER BY created_at_epoch DESC LIMIT 1",
                    (conversation_id,),
                )
                resolved_default = rows[0][0] if rows else None
                looked_up = True
            mid = resolved_default
        if not mid:
            continue

        rows = await db.execute_fetchall(
            "SELECT metadata FROM messages WHERE id = ?", (mid,)
        )
        if not rows:
            continue
        meta = json.loads(rows[0][0]) if rows[0][0] else {}
        reactions = meta.get("reactions", {})
        reactors = reactions.get(emoji, [])
        if identity not in reactors:
            reactors.append(identity)
        reactions[emoji] = reactors
        meta["reactions"] = reactions

        await db.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta), mid),
        )
        await db.commit()
        touched_any = True
        log.info("%s reacted %s to message %s", identity, emoji, mid)
        await broadcast({
            "type": "ai_reaction",
            "message_id": mid,
            "reactions": reactions,
        })

    if touched_any:
        # Bust this conversation's hook cache so the boy sees the just-applied
        # reactions in his next turn's context instead of the 120s-stale copy.
        try:
            from services.context_hooks import invalidate_hook_cache_for_conversation
            invalidate_hook_cache_for_conversation(conversation_id)
        except Exception:
            pass


_TOOL_AUDIT_SUMMARY_CHARS = 200


def _audit_summary(value: Any) -> str:
    """First ~200 chars of a stable text rendering of a tool input/output.

    Dicts serialize with sorted keys so the same call always summarizes the
    same way; whitespace collapses so a row is one honest line. Never raises.
    """
    if value in (None, "", {}, []):
        return ""
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    text = " ".join(text.split())
    return text[:_TOOL_AUDIT_SUMMARY_CHARS]


async def record_tool_audit(
    db,
    *,
    identity: str,
    conversation_id: str | None,
    source: str,
    tool_events: list[dict],
    tool_results_map: dict[str, dict[str, Any]],
    log=None,
) -> None:
    """Batch-insert one tool_audit row per tool call of this turn.

    ``source`` names the turn origin: chat | autowake | platform | mentions.
    Contract: never raises — proprioception is a gift, not a dependency, so a
    broken audit table can't take a turn save down with it.
    """
    if not tool_events:
        return
    logger = log or logging.getLogger(__name__)
    try:
        epoch = int(datetime.now(timezone.utc).timestamp())
        rows = []
        for event in tool_events:
            tool_id = event.get("tool_id", "")
            result = tool_results_map.get(tool_id, {}) if tool_id else {}
            tool_name = (
                event.get("tool_name") or result.get("tool_name") or "unknown"
            )
            rows.append((
                identity,
                conversation_id,
                source,
                tool_name,
                _audit_summary(result.get("input")),
                _audit_summary(result.get("content")),
                epoch,
            ))
        await db.executemany(
            "INSERT INTO tool_audit "
            "(identity, conversation_id, source, tool_name, "
            "input_summary, output_head, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()
    except Exception as exc:
        logger.warning("tool_audit insert failed (never turn-fatal): %s", exc)


def spawn_voice_if_tagged(
    identity: str,
    msg_id: str | None,
    content: str | None,
    log=None,
) -> None:
    """A <voice> tag in a saved assistant message becomes a real ElevenLabs."""
    if not msg_id or not content or "<voice>" not in content.lower():
        return
    voice_text = extract_voice_text(content)
    if not voice_text:
        return
    from services.task_manager import spawn

    spawn(
        generate_voice_message(
            msg_id=msg_id,
            identity=identity,
            voice_text=voice_text,
            log=log or logging.getLogger(__name__),
        ),
        name=f"voice_message_{msg_id}",
    )


# Deliberately NOT re.IGNORECASE. The guard above lowercases, but chat.js's
# display-strip regex is case-sensitive -- so accepting <VOICE> here would
# spawn audio AND leak raw markup into her bubble. Case-sensitive on both
# surfaces means a mis-cased tag simply does nothing, which is the safe miss.
_VOICE_RE = re.compile(r"<voice>(.*?)</voice>", re.DOTALL)


def extract_voice_text(content: str) -> str | None:
    """Pull the real <voice> tags out of a reply -- ignoring ones in backticks."""
    from services.tag_masking import find_tag_spans

    spans = find_tag_spans(content, _VOICE_RE)
    if not spans:
        return None
    voice_text = " ".join((s[2] or "").strip() for s in spans).strip()
    return voice_text or None


_PREVIEW_RE = re.compile(r"<preview>[\s\S]*?</preview>\s*", re.IGNORECASE)
_PREVIEW_UNCLOSED_RE = re.compile(r"<preview>[\s\S]*$", re.IGNORECASE)
_CANVAS_EXTERNAL_RE = re.compile(
    r'<canvas(?:\s+title="([^"]*)")?>([\s\S]*?)</canvas>', re.IGNORECASE,
)


def flatten_control_tags_for_external(text: str) -> str:
    """Make a reply safe to post OUTSIDE Anam (Discord, Telegram)."""
    if not text:
        return text
    cleaned = _PREVIEW_RE.sub("", text)
    cleaned = _PREVIEW_UNCLOSED_RE.sub("", cleaned)

    def _canvas(match: re.Match) -> str:
        title = (match.group(1) or "").strip()
        body = (match.group(2) or "").strip()
        return f"**{title}**\n\n{body}" if title else body

    cleaned = _CANVAS_EXTERNAL_RE.sub(_canvas, cleaned)
    return cleaned.strip() or text


async def generate_voice_message(
    *,
    msg_id: str,
    identity: str,
    voice_text: str,
    log,
    send=None,
) -> dict[str, Any] | None:
    """Generate and persist a <voice> message without blocking the main reply.

    Uses ``broadcast()`` to notify connected clients so that the WebSocket
    is never written to concurrently from a background task and the main
    chat loop (which would corrupt the connection).
    """
    try:
        audio = await synthesize(
            identity,
            voice_text,
            model_override="eleven_v3",
        )
        if not audio:
            return None

        await asyncio.to_thread(VOICE_DIR.mkdir, parents=True, exist_ok=True)
        voice_path = VOICE_DIR / f"{msg_id}.mp3"
        await asyncio.to_thread(voice_path.write_bytes, audio)

        try:
            await asyncio.to_thread(
                VOICE_VAULT_DIR.mkdir,
                parents=True,
                exist_ok=True,
            )
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            vault_name = f"{identity}_{ts}.mp3"
            await asyncio.to_thread(
                (VOICE_VAULT_DIR / vault_name).write_bytes,
                audio,
            )
            log.info("Voice saved to vault: %s", vault_name)
        except Exception as exc:
            log.exception("Failed to save voice to vault: %s", exc)

        db = await get_db()
        try:
            await update_message_metadata(db, msg_id, {"has_voice": True})
        finally:
            await release_db(db)

        payload = {
            "type": "voice_message",
            "identity": identity,
            "message_id": msg_id,
            "audio_url": f"/api/voice/file/{msg_id}",
            "voice_text": voice_text,
        }
        if callable(send):
            await send(payload)
        else:
            await broadcast(payload)
        log.info("Voice message ready for %s", identity)
        return payload
    except Exception as exc:
        log.exception("Voice TTS failed: %s", exc)
        return None


async def finalize_assistant_turn(
    *,
    conversation_id: str,
    identity: str,
    content: str,
    session_id: str | None,
    response_images: list[dict],
    response_documents: list[dict],
    thinking_blocks: list[str],
    tool_events: list[dict],
    tool_results_map: dict[str, dict[str, Any]],
    context_notice: dict[str, Any] | None = None,
    model_provenance: dict[str, Any] | None = None,
    user_message_id: str | None = None,
    # Turn origin for the tool_audit trail: chat | autowake | platform |
    # mentions. Every current caller (chat_pipeline WS, chat_http SSE,
    # echo_relay) is an interactive chat surface, so "chat" is the honest
    # default; other paths pass their own when they adopt this finalizer.
    source: str = "chat",
    ws_alive: bool,
    log,
    register_content_images: Callable[[str, str | None], tuple[str, list[dict]]],
    register_content_documents: Callable[[str, str | None], tuple[str, list[dict]]],
) -> FinalizedAssistantTurn:
    """Rewrite/save an assistant response and perform voice side effects."""
    voice_request = None

    if content:
        content, content_images = await asyncio.to_thread(
            register_content_images,
            content,
            identity,
        )
        response_images.extend(content_images)
        content, content_documents = await asyncio.to_thread(
            register_content_documents,
            content,
            identity,
        )
        response_documents.extend(content_documents)

    react_tags: list[tuple[str, str | None]] = []
    if content and "<react>" in content:
        content, react_tags = _parse_react_tags(content)

    # <face> — set the boy's little Hearth face. Stripped from the visible reply;
    # persisted (best-effort) so the hub corner reflects where he's at right now.
    if content and "<face>" in content.lower():
        from services.face_store import extract_face
        content = extract_face(identity, content)


    if content:
        from services.orb_store import extract_orb
        content = extract_orb(identity, content)

    response_documents = visible_chat_documents(response_documents)

    msg_id = None
    assistant_metadata = build_assistant_message_metadata(
        response_images,
        response_documents,
        thinking_blocks,
        tool_events,
        tool_results_map,
        context_notice,
        model_provenance,
    )

    if thinking_blocks or tool_events:
        log.info(
            "Metadata for %s: %d thinking blocks, %d tool events, metadata=%s",
            identity,
            len(thinking_blocks),
            len(tool_events),
            "present" if assistant_metadata else "NONE",
        )

    db = await get_db()
    try:


        if react_tags:
            await _apply_reaction_tags(db, conversation_id, identity, react_tags, log)

        if content:
            msg_id = await save_message(
                db,
                conversation_id,
                "assistant",
                content,
                identity=identity,
                metadata=assistant_metadata,
            )
            log.info(
                "Saved assistant message (%d chars) for %s%s",
                len(content),
                identity,
                " [ws disconnected]" if not ws_alive else "",
            )

            # A completed roleplay turn may become a private source excerpt for
            # the matching World Feed character. Best-effort and deliberately
            # conservative: it never blocks chat or tells uninvolved profiles.
            from services.world_feed_story_bridge import capture_roleplay_turn_safely
            await capture_roleplay_turn_safely(
                db,
                conversation_id=conversation_id,
                identity=identity,
                assistant_message_id=msg_id,
                assistant_content=content,
                user_message_id=user_message_id,
            )

            # #32: persist any <canvas> blocks into the boy's canvas library.
            # Never strips the tag from `content` -- chat.js re-extracts on
            # every render (live or from history), same as before this
            # existed. Best-effort: never let a persistence bug break the turn.
            if "<canvas" in content.lower():
                try:
                    from services.canvas_store import persist_canvas_blocks
                    await persist_canvas_blocks(
                        db,
                        identity=identity,
                        conversation_id=conversation_id,
                        content=content,
                        source_message_id=msg_id,
                    )
                except Exception:
                    log.exception("Canvas persistence failed for %s (turn unaffected)", identity)
        else:
            log.warning("Empty assistant content -- nothing saved for %s", identity)

        if session_id:
            provider = (model_provenance or {}).get("provider")
            await update_session_for_provider(
                db,
                conversation_id,
                session_id,
                provider,
            )

        if content and msg_id:
            voice_text = extract_voice_text(content)
            if voice_text:
                voice_request = {
                    "message_id": msg_id,
                    "identity": identity,
                    "voice_text": voice_text,
                }

        # Proprioception: log this turn's tool calls to tool_audit — even on
        # an empty reply (the reaching still happened). record_tool_audit is
        # try/except-sealed; it can never break the turn save above.
        if tool_events:
            await record_tool_audit(
                db,
                identity=identity,
                conversation_id=conversation_id,
                source=source,
                tool_events=tool_events,
                tool_results_map=tool_results_map,
                log=log,
            )
    finally:
        await release_db(db)

    # Browsing is an intention, not two chores. A Playwright leaf lazily opens
    # this identity's Chrome in the gateway; completing the turn releases its
    # lease and closes the profile when no concurrent turn still owns it. The
    # gateway also has a short idle reaper for interrupted/non-chat paths.
    try:
        from services.anam_tool_gateway import close_browsers_for_turn
        await close_browsers_for_turn(identity, conversation_id)
    except Exception:
        log.exception("Automatic browser close failed for %s (turn unaffected)", identity)

    return FinalizedAssistantTurn(
        content=content,
        msg_id=msg_id,
        response_images=response_images,
        response_documents=response_documents,
        voice_request=voice_request,
        react_only=bool(react_tags) and not content,
    )
