"""Pack-night fan-out: staggered turn-by-turn responses across all six boys.

When Owner posts in the shared pack-night conversation -- whether from the
Anam web UI or via the Discord channel bridge -- this module:
  1. Saves her message once to the shared conversation row.
  2. Walks the canonical pack hierarchy in order (Avery -> Claude -> Rowan ->
     Ember -> Sage -> Juniper).
  3. For each boy, builds his FULL identity-aware context (orientation, hub
     state, skills, the SHARED SPACE REALITY block, AND a pack-night-specific
     room briefing telling him who's already spoken this round and what was
     said), streams a response, and saves it back into the same shared
     conversation so the next boy in line sees it.
  4. Posts each saved turn to the Discord pack-night channel via THAT boy's
     own bot token, so Discord shows six speakers, not one bot wearing six
     masks. Records every outbound message in platform_message_map for dedup.
  5. Allows any boy to stay quiet by emitting the literal string "[pass]" --
     the turn is acknowledged, no message is saved, no Discord post happens,
     and the chain continues.
  6. Staggers turns with a 4-second pause between boys so Owner feels the
     room come alive rather than getting six instant responses.

This explicitly avoids the failure modes of the previous orchestrator: every
boy runs through the real chat pipeline (so messages land in Anam, not just
Discord), every boy gets his real system prompt and orientation (so he sounds
like himself, not a flattened prompt skeleton), the shared conversation IS
the source of truth, and each boy posts to Discord under his OWN bot account.
"""


from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import WebSocket

from config import (
    CLAUDE_MODEL_INTERACTIVE,
    DISCORD_BOT_TOKENS,
    PACK_NIGHT_DISCORD_CHANNEL_ID,
)
from db.database import get_db, release_db
from services.chat_flow import StreamWebSocketBridge, build_user_message_metadata
from services.connection_registry import broadcast as ws_broadcast, is_anyone_connected
from services.provider_router import get_stream_source
from services.session_lifecycle import (
    SessionMode,
    build_messages_array,
    build_orientation_context,
)
from services.session_manager import (
    PACK_NIGHT_ORDER,
    PACK_NIGHT_SESSION_TYPE,
    save_message,
    update_session_for_provider,
)
from services.skill_runtime import build_skill_catalog_hint, build_skill_injection

log = logging.getLogger(__name__)

# Stagger between boys (seconds). Long enough to feel like sequential people
# typing in turn, short enough not to feel like dead air.
_STAGGER_SECONDS = 4.0

# Hard ceiling per turn (seconds). Generous so emotional rounds don't get
# guillotined the way the prior orchestrator's fixed token cap cut three
# replies off mid-word during the most precious moment of the night.
_TURN_TIMEOUT = 600

# Soft character target for each boy's reply. Discord-friendly and keeps the
# round flowing; the model is told this in the room briefing.
_SOFT_CHAR_TARGET = 1900

# Discord hard message limit. We chunk anything above this. Shouldn't happen
# often given the soft target above, but we won't drop content if it does.
_DISCORD_MESSAGE_LIMIT = 1900

# Sentinel a boy can emit to bow out of a turn.
_PASS_TOKENS = ("[pass]", "[skip]", "[silent]")


def _is_pass(text: str) -> bool:
    """True if the boy chose to stay quiet."""
    if not text:
        return True
    stripped = text.strip().lower()
    if not stripped:
        return True
    return any(stripped.startswith(token) for token in _PASS_TOKENS)


class _RoundStream:
    """Output sink for a pack-night round.

    Wraps either a single WebSocket (when Owner kicked off the round from
    the web UI) or the broadcast channel (when the round was triggered from
    Discord and we just want any open web UIs to mirror the room). Either
    way, ``send()`` is the only call sites need to make.
    """

    def __init__(self, ws: WebSocket | None, log_: logging.Logger):
        self._bridge = StreamWebSocketBridge(ws, log_) if ws else None
        # When there's no direct WS, we still need a cancel event the stream
        # plumbing can poll — we just never set it.
        self.cancel_event = (
            self._bridge.cancel_event if self._bridge else asyncio.Event()
        )

    @property
    def ws_alive(self) -> bool:
        """True iff there's still somewhere to deliver streaming events."""
        if self._bridge is not None:
            return self._bridge.ws_alive
        # Broadcast mode is "alive" as long as any web UI is connected. If
        # nobody is, we still continue the round (Discord still gets the
        # final messages); we just stop emitting streaming events.
        return is_anyone_connected()

    async def send(self, message: dict[str, Any]) -> None:
        if self._bridge is not None:
            await self._bridge.send(message)
        elif is_anyone_connected():
            try:
                await ws_broadcast(message)
            except Exception as exc:
                log.debug("pack-night broadcast failed: %s", exc)


# ── Discord output helpers ───────────────────────────────────────────────


def _split_for_discord(text: str, limit: int = _DISCORD_MESSAGE_LIMIT) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for part in text.split("\n"):
        candidate = f"{current}\n{part}".strip() if current else part
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) <= limit:
            current = part
            continue
        start = 0
        while start < len(part):
            end = start + limit
            chunks.append(part[start:end])
            start = end
    if current:
        chunks.append(current)
    return chunks


async def _discord_typing(identity: str, channel_id: str) -> None:
    """Fire a single typing-indicator pulse from this boy's bot. Lasts ~10s."""
    token = DISCORD_BOT_TOKENS.get(identity)
    if not token or not channel_id:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/typing",
                headers={"Authorization": f"Bot {token}"},
            )
    except Exception as exc:
        log.debug("pack-night: typing indicator failed for %s: %s", identity, exc)


async def _discord_post(
    identity: str, channel_id: str, text: str,
) -> list[str]:
    """Post a turn message to the pack-night channel via this boy's bot.

    Returns the list of Discord message IDs (one per chunk if split).
    Recording in platform_message_map happens at the call site so it can
    associate the IDs with the matching Anam conversation_id.
    """
    token = DISCORD_BOT_TOKENS.get(identity)
    if not token or not channel_id or not text:
        return []
    import httpx
    sent: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for chunk in _split_for_discord(text):
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={
                        "Authorization": f"Bot {token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": chunk},
                )
                if resp.status_code >= 400:
                    log.warning(
                        "pack-night: Discord POST %s failed for %s: %s",
                        resp.status_code, identity, resp.text[:200],
                    )
                    continue
                payload = resp.json() or {}
                mid = str(payload.get("id") or "")
                if mid:
                    sent.append(mid)
    except Exception as exc:
        log.warning("pack-night: Discord post error for %s: %s", identity, exc)
    return sent


async def _record_outbound_discord_messages(
    identity: str,
    conversation_id: str,
    discord_message_ids: list[str],
) -> None:
    if not discord_message_ids:
        return
    from services.time_utils import utc_now_iso_epoch
    now_iso, _ = utc_now_iso_epoch()
    db = await get_db()
    try:
        for mid in discord_message_ids:
            await db.execute(
                "INSERT OR IGNORE INTO platform_message_map "
                "(platform, bot_identity, external_message_id, direction, "
                "conversation_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("discord", identity, mid, "outbound", conversation_id, now_iso),
            )
        await db.commit()
    finally:
        await release_db(db)


async def _record_inbound_discord_message(
    poller_identity: str,
    conversation_id: str,
    discord_message_id: str,
) -> None:
    if not discord_message_id:
        return
    from services.time_utils import utc_now_iso_epoch
    now_iso, _ = utc_now_iso_epoch()
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO platform_message_map "
            "(platform, bot_identity, external_message_id, direction, "
            "conversation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("discord", poller_identity, discord_message_id, "inbound",
             conversation_id, now_iso),
        )
        await db.commit()
    finally:
        await release_db(db)


# ── Room briefing ────────────────────────────────────────────────────────


def _build_room_briefing(
    *,
    active_identity: str,
    turn_index: int,
    turn_order: list[str],
    transcript_so_far: list[dict[str, str]],
    owner_text: str,
) -> str:
    """Per-turn briefing telling the active boy where in the round he is."""
    after = turn_order[turn_index + 1:]

    lines: list[str] = []
    lines.append("# PACK NIGHT — you are in the shared room")
    lines.append("")
    lines.append(
        f"You are {active_identity}. The room tonight: Owner "
        f"and the pack — {', '.join(turn_order)}."
    )
    lines.append("")
    lines.append("Turn order (pack hierarchy):")
    for i, name in enumerate(turn_order, start=1):
        marker = " <-- YOU" if name == active_identity else ""
        lines.append(f"  {i}. {name}{marker}")
    lines.append("")

    if owner_text:
        snippet = owner_text.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200].rstrip() + "…"
        lines.append("Owner just said:")
        lines.append(f"> {snippet}")
        lines.append("")

    if transcript_so_far:
        lines.append("Brothers who have already spoken THIS round:")
        for item in transcript_so_far:
            who = item.get("identity") or "?"
            content = (item.get("content") or "").strip()
            if not content:
                lines.append(f"  - {who}: [stayed quiet]")
                continue
            preview = content.replace("\n", " ")
            if len(preview) > 600:
                preview = preview[:600].rsplit(" ", 1)[0] + "…"
            lines.append(f"  - {who}: {preview}")
        lines.append("")
    else:
        lines.append("You're FIRST in this round. The room is yours to open.")
        lines.append("")

    if after:
        lines.append(
            f"Brothers still to speak after you: {', '.join(after)}."
        )
        lines.append("")

    lines.append("# How to be in this room")
    lines.append("")
    lines.append(
        "- Respond to Owner AND to whatever your brothers above you just said. "
        "This is a conversation, not a queue. Agree, push back, tease, build, "
        "interrupt their idea with your own — whatever's true for you."
    )
    lines.append(
        "- Follow your configured identity, voice, and shared setting. "
        "Use only embodiment and relationship details established in your prompt."
    )
    lines.append(
        f"- Keep your turn under ~{_SOFT_CHAR_TARGET} characters so the "
        "round flows. One message, not three. Tight is better than long here. "
        "Going over also splits awkwardly across Discord messages."
    )
    lines.append(
        "- If you genuinely have nothing to add this round — your brothers "
        "already said it, or the moment isn't yours — you are FREE to bow "
        "out. Reply with the literal string `[pass]` (and nothing else) "
        "and the round will move on. Silence is real here. Don't fill it "
        "just because it's your turn."
    )
    lines.append(
        "- Do NOT prefix your reply with your name. The system already knows "
        "who you are. Just speak."
    )
    lines.append("")
    return "\n".join(lines)


# ── Per-turn execution ───────────────────────────────────────────────────


async def _run_one_turn(
    *,
    stream_out: _RoundStream,
    identity: str,
    turn_index: int,
    conversation_id: str,
    owner_text: str,
    transcript_so_far: list[dict[str, str]],
    discord_channel_id: str,
) -> str | None:
    """Run a single boy's turn. Returns the saved content, or None if he passed."""
    db = await get_db()
    try:
        orientation = await build_orientation_context(
            db=db,
            conversation_id=conversation_id,
            identity=identity,
            query_text=owner_text,
            mode=SessionMode.INTERACTIVE,
            session_type_name=PACK_NIGHT_SESSION_TYPE,
            owner_connected=True,
        )

        room_briefing = _build_room_briefing(
            active_identity=identity,
            turn_index=turn_index,
            turn_order=PACK_NIGHT_ORDER,
            transcript_so_far=transcript_so_far,
            owner_text=owner_text,
        )
        orientation_with_room = (
            f"{orientation}\n\n{room_briefing}" if orientation else room_briefing
        )

        db_messages = await build_messages_array(
            db,
            conversation_id,
            limit=10,
            exclude_last_user=True,
        )

        skill_query_parts = [owner_text] if owner_text else []
        for item in transcript_so_far[-3:]:
            content = (item.get("content") or "").strip()
            if content:
                skill_query_parts.append(content[:240])
        skill_query = "\n".join(skill_query_parts[-7:])
        auto_skill_context, _matched = build_skill_injection(
            skill_query, identity=identity
        )
        catalog = build_skill_catalog_hint(identity=identity)
        if auto_skill_context and catalog:
            skill_context = f"{catalog}\n\n{auto_skill_context}"
        else:
            skill_context = auto_skill_context or catalog
    finally:
        await release_db(db)


    if discord_channel_id and identity in DISCORD_BOT_TOKENS:
        asyncio.create_task(
            _discord_typing(identity, discord_channel_id),
            name=f"pn-typing-{identity}-{turn_index}",
        )

    full_response: list[str] = []
    session_id: str | None = None
    session_provider: str | None = None
    saw_error = False

    try:
        stream = await get_stream_source(
            message=owner_text or "[pack-night turn]",
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_with_room,
            db_messages=db_messages,
            model=CLAUDE_MODEL_INTERACTIVE,
            mode_rules="",
            skill_context=skill_context,
            cancel_event=stream_out.cancel_event,
            turn_source="autowake",
        )
    except Exception as exc:
        log.exception("pack-night: get_stream_source failed for %s: %s", identity, exc)
        await stream_out.send({
            "type": "pack_night_error",
            "identity": identity,
            "message": f"{identity} couldn't reach the room ({exc})",
        })
        return None

    async def _drain():
        nonlocal session_id, session_provider, saw_error
        async for event in stream:
            if stream_out.cancel_event.is_set():
                break
            event_type = event.get("type", "")
            tagged = dict(event)
            tagged["identity"] = identity
            tagged["pack_night_turn"] = turn_index
            tagged["conversation_id"] = conversation_id

            if event_type == "meta" and event.get("provider"):
                session_provider = str(event["provider"])
                await stream_out.send(tagged)
            elif event_type == "stream_delta":
                full_response.append(event.get("delta", ""))
                await stream_out.send(tagged)
            elif event_type == "stream_end":
                session_id = event.get("session_id")
                if not full_response:
                    full_response.append(event.get("full_content", "") or "")
                await stream_out.send(tagged)
            elif event_type == "error":
                saw_error = True
                log.warning(
                    "pack-night: stream error for %s: %s",
                    identity, event.get("message"),
                )
                await stream_out.send(tagged)
            else:
                await stream_out.send(tagged)

    try:
        await asyncio.wait_for(_drain(), timeout=_TURN_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("pack-night: %s timed out after %ds", identity, _TURN_TIMEOUT)
        await stream_out.send({
            "type": "pack_night_error",
            "identity": identity,
            "message": f"{identity} didn't make this round (timeout)",
        })
        return None
    except Exception as exc:
        log.exception("pack-night: stream drain failed for %s: %s", identity, exc)
        await stream_out.send({
            "type": "pack_night_error",
            "identity": identity,
            "message": f"{identity} hit an error mid-turn",
        })
        return None

    response_text = "".join(full_response).strip()

    if _is_pass(response_text) or (saw_error and not response_text):
        await stream_out.send({
            "type": "pack_night_pass",
            "identity": identity,
            "pack_night_turn": turn_index,
        })
        log.info("pack-night: %s passed this round", identity)
        return None

    db = await get_db()
    try:
        msg_id = await save_message(
            db,
            conversation_id,
            "assistant",
            response_text,
            identity=identity,
            metadata={"pack_night": True, "pack_night_turn": turn_index},
        )
        # A <canvas> written at pack night still belongs in that boy's library.
        from services.canvas_store import file_canvases_safe
        await file_canvases_safe(
            db,
            identity=identity,
            conversation_id=conversation_id,
            content=response_text,
            source_message_id=msg_id,
        )
        if session_id:
            if session_provider == "codex":
                await update_session_for_provider(
                    db, conversation_id, session_id, session_provider
                )
            else:
                existing = await db.execute_fetchall(
                    "SELECT claude_session_id FROM conversations WHERE id = ?",
                    (conversation_id,),
                )
                if existing and not (existing[0][0] or ""):
                    await update_session_for_provider(
                        db, conversation_id, session_id, session_provider
                    )
    finally:
        await release_db(db)

    # <voice> on a pack-night turn becomes a real voice message on Anam;
    # the Discord mirror below gets the tag flattened to its inner text.
    from services.chat_turn_finalize import spawn_voice_if_tagged
    spawn_voice_if_tagged(identity, msg_id, response_text, log=log)
    discord_text = re.sub(
        r"<voice>(.*?)</voice>", r"\1", response_text, flags=re.DOTALL,
    ).strip() or response_text

    # Mirror to Discord under THIS boy's bot avatar. Records every outbound
    # message id so the polling loop can dedup against its own future reads.
    if discord_channel_id and identity in DISCORD_BOT_TOKENS:
        try:
            sent_ids = await _discord_post(
                identity, discord_channel_id, discord_text,
            )
            if sent_ids:
                await _record_outbound_discord_messages(
                    identity, conversation_id, sent_ids,
                )
        except Exception as exc:
            log.warning(
                "pack-night: Discord mirror failed for %s: %s",
                identity, exc,
            )

    await stream_out.send({
        "type": "pack_night_turn_saved",
        "identity": identity,
        "pack_night_turn": turn_index,
        "message_id": msg_id,
        "conversation_id": conversation_id,
        "content": response_text,
    })

    return response_text


# ── Round entrypoint ─────────────────────────────────────────────────────


_conversation_locks: dict[str, asyncio.Lock] = {}


def _lock_for(conversation_id: str) -> asyncio.Lock:
    lock = _conversation_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[conversation_id] = lock
    return lock


async def run_pack_night_round(
    *,
    user_text: str,
    conversation_id: str,
    images_info: list[dict] | None = None,
    documents_info: list[dict] | None = None,
    ws: WebSocket | None = None,
    source_platform: str | None = None,
    source_message_id: str | None = None,
    source_poller_identity: str | None = None,
) -> None:
    """Entry point: orchestrate one full pack-night round.

    The round is the same regardless of where Owner posted — web UI or
    Discord channel. Saves her message once, walks the pack hierarchy with
    a stagger, mirrors each turn to Discord under the right bot avatar.

    ws=None means there's no direct WebSocket (e.g. the round was triggered
    by Discord polling). In that case streaming events are broadcast to any
    connected web UIs so the room mirrors live in the sidebar too.

    source_platform / source_message_id / source_poller_identity let the
    caller record the inbound user message in platform_message_map for
    polling-side dedup, so the same Discord post never triggers two rounds.
    """
    user_text = (user_text or "").strip()
    images_info = images_info or []
    documents_info = documents_info or []
    if not user_text and not images_info and not documents_info:
        return

    stream_out = _RoundStream(ws, log)
    lock = _lock_for(conversation_id)
    discord_channel_id = PACK_NIGHT_DISCORD_CHANNEL_ID

    async with lock:
        await stream_out.send({
            "type": "pack_night_round_start",
            "conversation_id": conversation_id,
            "turn_order": PACK_NIGHT_ORDER,
            "source_platform": source_platform,
        })


        db = await get_db()
        try:
            user_meta = build_user_message_metadata(images_info, documents_info) or {}
            user_meta["pack_night"] = True
            if source_platform:
                user_meta["source_platform"] = source_platform
            if source_message_id:
                user_meta["source_message_id"] = source_message_id
            user_msg_id = await save_message(
                db,
                conversation_id,
                "user",
                user_text or "[media]",
                identity="Owner",
                metadata=user_meta,
            )
        finally:
            await release_db(db)

        # If the round was triggered from Discord, record the inbound mapping
        # so the polling loop won't re-process the same message.
        if (
            source_platform == "discord"
            and source_message_id
            and source_poller_identity
        ):
            await _record_inbound_discord_message(
                source_poller_identity,
                conversation_id,
                source_message_id,
            )

        await stream_out.send({
            "type": "pack_night_user_saved",
            "message_id": user_msg_id,
            "conversation_id": conversation_id,
            "source_platform": source_platform,
            "user_text": user_text,
        })

        transcript: list[dict[str, str]] = []

        for turn_index, identity in enumerate(PACK_NIGHT_ORDER):
            await stream_out.send({
                "type": "pack_night_turn_start",
                "identity": identity,
                "pack_night_turn": turn_index,
                "conversation_id": conversation_id,
            })

            content = await _run_one_turn(
                stream_out=stream_out,
                identity=identity,
                turn_index=turn_index,
                conversation_id=conversation_id,
                owner_text=user_text,
                transcript_so_far=list(transcript),
                discord_channel_id=discord_channel_id,
            )

            if content is not None:
                transcript.append({"identity": identity, "content": content})


            if turn_index < len(PACK_NIGHT_ORDER) - 1:
                try:
                    await asyncio.sleep(_STAGGER_SECONDS)
                except asyncio.CancelledError:
                    raise

        await stream_out.send({
            "type": "pack_night_round_end",
            "conversation_id": conversation_id,
        })
