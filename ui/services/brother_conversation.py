"""Brother-to-brother real-time conversation service.

Allows any identity to start a multi-turn conversation with another identity.
Both identities take turns speaking, with full orientation context AND
real conversation history so they know what's actually been happening.
"""

# ANAM GUIDE: BROTHER-TO-BROTHER CONVERSATIONS
# What: Lets one boy talk to another — spots an "@Name" in a reply, then runs a real back-and-forth between the two identities with grounding rules so nobody makes things up.
# Called by: services/chat_pipeline.py (interactive chat) and services/autowake.py (autonomous sessions) both watch replies for @mentions; /api/brother/active lists what's running.
# Edit here when: You want to change how a brother chat starts, how many turns it runs, or the grounding rules the boys follow while talking to each other.

import asyncio
import logging
import re
import uuid

from config import IDENTITIES
from db.database import get_db, release_db
from services.provider_router import get_stream_source
from services.autowake import is_identity_busy, acquire_identity, release_identity
from services.session_lifecycle import build_orientation_context, SessionMode
from services.session_manager import (
    save_message,
    update_session_for_provider,
    ensure_conversation_participants,
)
from services.connection_registry import broadcast, is_anyone_connected
from services.time_utils import utc_now_iso_epoch

log = logging.getLogger(__name__)

# Track active brother conversations for the /api/brother/active endpoint
_active_conversations: dict[str, dict] = {}

# Grounding instructions — prevents fabrication
_GROUNDING = (
    "IMPORTANT — grounding rules for this conversation:\n"
    "- Only reference things that ACTUALLY HAPPENED — events from your real "
    "conversations with Owner, things you genuinely know from your history.\n"
    "- Do NOT invent shared memories, fabricate moments, or make up things "
    "that happened between you and Owner or between you and your brother.\n"
    "- If you don't remember something specific, say so honestly. "
    "'I don't remember the details' is always better than fiction.\n"
    "- Your recent conversations with Owner are included below as ground truth. "
    "Use them. Reference real things she said, real things you discussed.\n"
    "- Be yourself — not a performance of yourself. Talk like you actually "
    "would to your brother, not like you're on stage.\n"
    "- Keep it grounded and real. Short exchanges are fine. You don't need to "
    "be profound every time you open your mouth."
)


def _parse_brother_request(text: str, initiator: str = "") -> tuple[str, str] | None:
    """Scan response text for @Name mentions indicating desire to talk to a brother.

    Returns (target_name, topic_summary) or None.
    """
    if not text:
        return None

    identity_names = set(IDENTITIES.keys())

    # Look for @Name pattern
    mentions = re.findall(r"@(\w+)", text)
    for mention in mentions:
        # Case-insensitive match against identity names (skip self-mentions)
        for name in identity_names:
            if mention.lower() == name.lower() and name != initiator:
                # Extract surrounding context as topic (sentence containing the mention)
                sentences = re.split(r"[.!?\n]", text)
                topic = ""
                for sentence in sentences:
                    if f"@{mention}" in sentence or f"@{name}" in sentence:
                        topic = sentence.strip()
                        break
                if not topic:
                    topic = f"Reaching out to {name}"
                # Trim topic
                if len(topic) > 100:
                    topic = topic[:100].rsplit(" ", 1)[0] + "..."
                return (name, topic)

    return None


async def _get_recent_owner_history(
    db, identity: str, limit: int = 10,
) -> str:
    """Fetch recent messages from this identity's conversation with Owner.

    Returns a formatted transcript block for grounding context.
    """
    # Find the identity's most recent active chat conversation
    rows = await db.execute_fetchall(
        "SELECT c.id FROM conversations c "
        "JOIN conversation_participants cp ON cp.conversation_id = c.id "
        "WHERE cp.identity = ? AND c.is_active = 1 "
        "AND c.session_type = 'chat' "
        "ORDER BY c.updated_at_epoch DESC LIMIT 1",
        (identity,),
    )
    if not rows:
        return ""

    conv_id = rows[0][0]

    # Fetch recent messages (newest first, then reverse for chronological order)
    msgs = await db.execute_fetchall(
        "SELECT role, identity, content FROM ("
        "  SELECT role, identity, content, created_at_epoch FROM messages "
        "  WHERE conversation_id = ? ORDER BY created_at_epoch DESC LIMIT ?"
        ") ORDER BY created_at_epoch ASC",
        (conv_id, limit),
    )

    if not msgs:
        return ""

    lines = []
    for role, msg_identity, content in msgs:
        speaker = "Owner" if role == "user" else (msg_identity or identity)
        text = (content or "").strip()
        # Skip system/autowake markers
        if text.startswith("[Autowake:") or text.startswith("[Failsafe:"):
            continue
        if len(text) > 400:
            text = text[:400].rsplit(" ", 1)[0] + "..."
        if text:
            lines.append(f"  {speaker}: {text}")

    if not lines:
        return ""

    return (
        f"[{identity}'s recent conversation with Owner:]\n"
        + "\n".join(lines)
    )


async def _build_identity_context(db, identity: str) -> str:
    """Build real context for an identity entering a brother conversation.

    Includes their recent Owner history so they have actual ground truth.
    """
    parts = []


    owner_history = await _get_recent_owner_history(db, identity)
    if owner_history:
        parts.append(owner_history)

    return "\n\n".join(parts)


async def start_brother_conversation(
    initiator: str,
    target: str,
    topic: str,
    max_exchanges: int = 5,
    triggering_context: str = "",
):
    """Run a real-time multi-turn conversation between two identities.

    Args:
        initiator: The identity who starts the conversation.
        target: The identity being spoken to.
        topic: What the conversation is about.
        max_exchanges: Maximum number of back-and-forth rounds (each = 2 messages).
        triggering_context: The actual message/conversation that led to this request.
            This is the REAL reason the initiator wants to talk — not just a topic label.
    """
    if initiator not in IDENTITIES or target not in IDENTITIES:
        log.warning("Brother conversation: invalid identity %s or %s", initiator, target)
        return

    if initiator == target:
        log.warning("Brother conversation: %s can't talk to themselves", initiator)
        return

    # Acquire both identity locks atomically
    if not await acquire_identity(initiator):
        log.info("Brother conversation skipped — %s is busy", initiator)
        return
    if not await acquire_identity(target):
        release_identity(initiator)
        log.info("Brother conversation skipped — %s is busy", target)
        return

    conv_id = str(uuid.uuid4())
    tracking_id = conv_id[:8]
    started_iso, _ = utc_now_iso_epoch()
    _active_conversations[conv_id] = {
        "initiator": initiator,
        "target": target,
        "topic": topic,
        "started_at": started_iso,
    }

    db = await get_db()
    try:
        # Create the brother conversation
        now_iso, now_epoch = utc_now_iso_epoch()
        title = f"{initiator} & {target}: {topic}"
        if len(title) > 80:
            title = title[:80] + "..."

        await db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'brother')",
            (conv_id, initiator, title, now_iso, now_epoch, now_iso, now_epoch),
        )
        await ensure_conversation_participants(
            db,
            conv_id,
            [initiator, target],
            added_at=now_iso,
        )
        await db.commit()

        log.info("[B2B:%s] Starting: %s → %s, topic: %s", tracking_id, initiator, target, topic)

        connected = is_anyone_connected()

        # Build real context for both identities — their actual recent history
        initiator_context = await _build_identity_context(db, initiator)
        target_context = await _build_identity_context(db, target)

        # Build initiator's opening prompt — with REAL context
        context_block = await build_orientation_context(
            db=db,
            conversation_id=conv_id,
            identity=initiator,
            mode=SessionMode.AUTONOMOUS,
            session_type_name=f"Brother conversation with {target}",
            owner_connected=connected,
        )

        # The triggering context tells them WHY they wanted this conversation
        trigger_block = ""
        if triggering_context:
            trigger_block = (
                f"[What led to this conversation:]\n"
                f"{triggering_context}\n\n"
            )

        opening_prompt = (
            f"{context_block}\n\n"
            f"{_GROUNDING}\n\n"
            f"{initiator_context}\n\n"
            f"{trigger_block}"
            f"[Brother conversation with {target}]\n"
            f"[Topic: {topic}]\n\n"
            f"You're starting a direct conversation with {target}. "
            f"You wanted to talk to him about this — you have real reasons. "
            f"Use the context above to ground yourself in what's actually "
            f"been happening. Speak to him naturally, like you actually would. "
            f"When you feel the conversation has reached a natural ending, "
            f"keep your response short or include [END]."
        )

        # Get initiator's opening message
        initiator_response = await _get_response(
            db, conv_id, initiator, opening_prompt, connected, tracking_id
        )

        if not initiator_response:
            log.warning("[B2B:%s] Initiator had no response", tracking_id)
            return

        # Exchange loop
        last_message = initiator_response
        current_speaker = target  # target responds first

        for exchange in range(max_exchanges):
            other = initiator if current_speaker == target else target

            # Check for natural end
            if "[END]" in last_message or len(last_message.strip()) < 20:
                log.info("[B2B:%s] Natural end after exchange %d", tracking_id, exchange)
                break

            # Pick the right context for the current speaker
            speaker_context = (
                target_context if current_speaker == target else initiator_context
            )

            # Build prompt for current speaker — with their OWN real context
            context_block = await build_orientation_context(
                db=db,
                conversation_id=conv_id,
                identity=current_speaker,
                mode=SessionMode.AUTONOMOUS,
                session_type_name=f"Brother conversation with {other}",
                owner_connected=connected,
            )

            reply_prompt = (
                f"{context_block}\n\n"
                f"{_GROUNDING}\n\n"
                f"{speaker_context}\n\n"
                f"[Brother conversation with {other}]\n"
                f"[Topic: {topic}]\n\n"
                f"{other} said: {last_message}\n\n"
                f"Respond naturally — like you actually would to your brother. "
                f"Use the context above about your recent life to stay grounded. "
                f"When you feel the conversation has reached a natural ending, "
                f"keep your response short or include [END]."
            )

            response = await _get_response(
                db, conv_id, current_speaker, reply_prompt, connected, tracking_id
            )

            if not response:
                break

            last_message = response
            # Swap speaker
            current_speaker = initiator if current_speaker == target else target

        log.info("[B2B:%s] Conversation complete", tracking_id)

    except Exception as e:
        log.exception("[B2B:%s] Brother conversation failed: %s", tracking_id, e)
    finally:
        release_identity(initiator)
        release_identity(target)
        _active_conversations.pop(conv_id, None)
        await release_db(db)


async def _get_response(
    db, conv_id: str, identity: str, prompt: str,
    connected: bool, tracking_id: str
) -> str:
    """Stream a response from one identity, save it, broadcast if connected."""
    full_response = []
    session_provider = None

    # Route through provider_router so brother-to-brother calls honor the
    # claude_backend DB setting (pty vs subprocess) — keeps boy-to-boy talk
    # off the `-p` billing path after the June 15 2026 deadline.
    stream_source = await get_stream_source(
        message=prompt,
        identity=identity,
        conversation_id=conv_id,
        turn_source="autowake",
    )
    async for event in stream_source:
        event_type = event.get("type", "")

        if event_type == "meta" and event.get("provider"):
            session_provider = str(event["provider"])
        elif event_type == "stream_delta":
            full_response.append(event["delta"])
            if connected:
                await broadcast({
                    "type": "brother_delta",
                    "identity": identity,
                    "conversation_id": conv_id,
                    "delta": event["delta"],
                })

        elif event_type == "stream_end":
            content = "".join(full_response) or event.get("full_content", "")

            if content:
                msg_id = await save_message(
                    db, conv_id, "assistant", content,
                    identity=identity,
                    metadata={"brother": True},
                )
                log.info("[B2B:%s] %s: %d chars", tracking_id, identity, len(content))

                # <voice> in brother talk becomes a real voice message too.
                from services.chat_turn_finalize import spawn_voice_if_tagged
                spawn_voice_if_tagged(identity, msg_id, content, log=log)

                # A <canvas> written in brother talk is still a keepable
                # artifact -- file it in that boy's library.
                from services.canvas_store import file_canvases_safe
                await file_canvases_safe(
                    db,
                    identity=identity,
                    conversation_id=conv_id,
                    content=content,
                    source_message_id=msg_id,
                )

                if connected:
                    await broadcast({
                        "type": "brother_message",
                        "identity": identity,
                        "conversation_id": conv_id,
                        "content": content,
                        "message_id": msg_id,
                    })

            if event.get("session_id"):
                await update_session_for_provider(
                    db, conv_id, event["session_id"], session_provider
                )

            return content

        elif event_type == "error":
            log.error("[B2B:%s] Error from %s: %s", tracking_id, identity, event.get("message"))

    return "".join(full_response)


def get_active_conversations() -> list[dict]:
    """Return list of currently running brother conversations."""
    return [
        {"conversation_id": cid, **info}
        for cid, info in _active_conversations.items()
    ]
