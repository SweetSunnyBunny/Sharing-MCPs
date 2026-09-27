"""Discord mentions bridge: per-bot watcher for direct user + role pings.

Scope:
  - Every configured guild (MENTIONS_GUILD_IDS — Home Pack + Pack Pride by default).
  - Every text channel each bot can see EXCEPT the pack-night channel
    (handled by services.pack_night) and DM channels (handled by
    services.platform_bridge).

Routing by sender:
  - Allowlisted human (Owner): mentioned boy responds in the channel
    immediately, conversation beats save into THAT boy's main daily chat
    in Anam. Same shape as the existing DM owner flow.
  - Brother bot (any user id matching one of our DISCORD_BOT_TOKENS bots):
    mentioned boy responds in the channel immediately, conversation beats
    save into a per-channel Pack Hall conversation (session_type='brother').
  - Anyone else: ignored.

Mention type:
  - Direct user mention (this bot is in `mentions[]`): immediate wake.
  - Role mention (any of this bot's role ids in `mention_roles[]`): logged
    to personal_timeline as 'discord_role_mention' for autowake to surface.
    No immediate response — role pings are ambient, not personal.

Rate limit:
  - Per (channel_id, identity) pair, MENTIONS_RATE_LIMIT_SECONDS (default
    1 hour) between immediate wakes. Within the cooldown, direct mentions
    fall through to the deferred queue so they aren't lost.

Self-discovery:
  - On first poll: GET /users/@me to learn the bot's user id, then
    GET /guilds/{guild}/members/{bot_user_id} to learn the bot's role ids.
    Cached for the lifetime of the bridge.
"""


from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from config import (
    ALLOWED_DISCORD_USER_IDS,
    CLAUDE_MODEL_INTERACTIVE,
    DISCORD_BOT_TOKENS,
    MENTIONS_GUILD_IDS,
    MENTIONS_POLL_SECONDS,
    SALON_GUILD_ID,
    SALON_TRIGGER_USER_IDS,
    SALON_RESPONDER_IDENTITIES,
    SALON_COOLDOWN_SECONDS,
    MENTIONS_RATE_LIMIT_SECONDS,
    PACK_NIGHT_DISCORD_CHANNEL_ID,
    PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START,
)
from db.database import get_db, release_db
from services.chat_turn_finalize import (
    extract_voice_text,
    flatten_control_tags_for_external,
    generate_voice_message,
)
from services.connection_registry import broadcast, is_anyone_connected
from services.inbound_voice import (
    VOICE_REPLY_HINT,
    format_voice_line,
    is_audio_attachment,
    transcribe_discord_attachment,
)
from services.provider_router import get_stream_source
from services.session_lifecycle import (
    SessionMode,
    build_messages_array,
    build_orientation_context,
)
from services.session_manager import (
    get_or_create_brother_channel_conversation,
    get_or_create_conversation,
    save_message,
)
from services.skill_runtime import build_skill_catalog_hint, build_skill_injection
from services.task_manager import spawn
from services.time_utils import utc_now_iso_epoch

log = logging.getLogger("anam.discord_mentions")

DISCORD_API = "https://discord.com/api/v10"

# Discord text-style channel types we'll watch. 0=text, 5=announcement,
# 11=public_thread, 15=forum, 16=media. Voice/category/dm/etc are skipped.
_WATCHED_CHANNEL_TYPES = {0, 5, 11, 15, 16}

# Hard ceiling on response generation per ping (seconds).
_RESPONSE_TIMEOUT = 600

# How many recent messages to fold into context when responding to a ping.
_CONTEXT_WINDOW = 10

# How long a fetched guild channel list stays fresh before we hit
# GET /guilds/{id}/channels again. New channels are discovered within
# this window; deleted channels 404 on poll and land in
# disabled_channels, so a stale list is harmless in between.
_CHANNEL_LIST_TTL_SECONDS = 600

# Shared across all watchers: GET /guilds/{id}/channels returns every
# channel in the guild for any member bot (it is not filtered by the
# requesting bot's channel permissions — per-bot visibility is enforced
# downstream, where a 403/404 on the message fetch puts the channel in
# that watcher's disabled_channels). One fetch therefore serves all bots.
# Keyed by guild_id -> (monotonic_fetch_time, channels).
_channel_list_cache: dict[str, tuple[float, list[dict]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# ── Discord REST helpers (one shared client across the bridge) ─────────


async def _discord_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    """REST call with simple 429 backoff. Returns parsed JSON or None."""
    for _ in range(5):
        resp = await client.request(method, url, headers=headers, params=params, json=payload)
        if resp.status_code == 429:
            retry_after = 1.5
            try:
                retry_after = float(resp.json().get("retry_after", retry_after))
            except (ValueError, KeyError, TypeError):
                pass
            await asyncio.sleep(max(retry_after, 0.5))
            continue
        if resp.status_code in (401, 403, 404):
            # 401 = bad token, 403 = no permission for this channel, 404 = gone.
            # Caller handles by skipping this channel/guild.
            return None
        if resp.status_code >= 400:
            text = resp.text[:300]
            raise RuntimeError(f"Discord API {resp.status_code}: {text}")
        if not resp.content:
            return None
        return resp.json()
    raise RuntimeError("Discord API request exceeded retry limit")


# ── Sender classification ──────────────────────────────────────────────


def _is_owner_sender(author_id: str) -> bool:
    return bool(author_id) and author_id in ALLOWED_DISCORD_USER_IDS


def _brother_identity_for_user_id(
    user_id: str, brother_user_ids: dict[str, str],
) -> str | None:
    """Reverse-lookup: given a Discord user id, return the matching identity
    name for one of our six brother bots, or None if it's some other user."""
    if not user_id:
        return None
    for identity, uid in brother_user_ids.items():
        if uid == user_id:
            return identity
    return None


# ── Role-mention deferred queue ────────────────────────────────────────


async def _record_role_mention(
    *,
    identity: str,
    channel_id: str,
    channel_name: str,
    message_id: str,
    sender_name: str,
    sender_id: str,
    content_snippet: str,
):
    """Save a role-mention to personal_timeline for autowake to surface."""
    now_iso, _ = utc_now_iso_epoch()
    payload = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content_snippet": content_snippet,
        "platform": "discord",
    }
    db = await get_db()
    try:
        # Dedupe via unique dedupe_key on personal_timeline.
        dedupe_key = f"discord_role_mention:{identity}:{message_id}"
        await db.execute(
            "INSERT OR IGNORE INTO personal_timeline "
            "(entry_date, entry_type, source, subject, identity, title, body, "
            "dedupe_key, payload_json, created_at, created_at_epoch, "
            "updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso[:10],
                "discord_role_mention",
                "discord",
                identity,
                identity,
                f"Role pinged in #{channel_name}",
                content_snippet,
                dedupe_key,
                json.dumps(payload),
                now_iso, int(time.time()),
                now_iso, int(time.time()),
            ),
        )
        await db.commit()
    finally:
        await release_db(db)


# ── platform_message_map helpers ───────────────────────────────────────


async def _seen(
    *, identity: str, message_id: str, direction: str,
) -> bool:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM platform_message_map "
            "WHERE platform = 'discord' AND bot_identity = ? "
            "AND external_message_id = ? AND direction = ? LIMIT 1",
            (identity, message_id, direction),
        )
        return bool(rows)
    finally:
        await release_db(db)


async def _record_inbound(
    *, identity: str, message_id: str, conversation_id: str,
):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO platform_message_map "
            "(platform, bot_identity, external_message_id, direction, "
            "conversation_id, created_at) "
            "VALUES ('discord', ?, ?, 'inbound', ?, ?)",
            (identity, message_id, conversation_id, _now_iso()),
        )
        await db.commit()
    finally:
        await release_db(db)


async def _record_outbound(
    *, identity: str, message_ids: list[str], conversation_id: str,
):
    if not message_ids:
        return
    db = await get_db()
    try:
        for mid in message_ids:
            await db.execute(
                "INSERT OR IGNORE INTO platform_message_map "
                "(platform, bot_identity, external_message_id, direction, "
                "conversation_id, created_at) "
                "VALUES ('discord', ?, ?, 'outbound', ?, ?)",
                (identity, mid, conversation_id, _now_iso()),
            )
        await db.commit()
    finally:
        await release_db(db)


# ── Posting back to Discord under the right bot ────────────────────────


def _split_for_discord(text: str, limit: int = 1900) -> list[str]:
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


async def _post_to_channel(
    client: httpx.AsyncClient,
    *,
    identity: str,
    channel_id: str,
    text: str,
) -> list[str]:
    token = DISCORD_BOT_TOKENS.get(identity)
    if not token or not text:
        return []
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    sent_ids: list[str] = []
    for chunk in _split_for_discord(text):
        try:
            data = await _discord_request(
                client, "POST",
                f"{DISCORD_API}/channels/{channel_id}/messages",
                headers=headers,
                payload={"content": chunk},
            )
        except Exception as exc:
            log.warning(
                "mentions: post-back failed for %s in channel %s: %s",
                identity, channel_id, exc,
            )
            continue
        if data and data.get("id"):
            sent_ids.append(str(data["id"]))
    return sent_ids


async def _trigger_typing(
    client: httpx.AsyncClient, identity: str, channel_id: str,
) -> None:
    token = DISCORD_BOT_TOKENS.get(identity)
    if not token:
        return
    try:
        await client.post(
            f"{DISCORD_API}/channels/{channel_id}/typing",
            headers={"Authorization": f"Bot {token}"},
        )
    except Exception:
        pass  # Typing indicators are nice-to-have; never fail a ping over them.


async def _typing_keepalive(
    client: httpx.AsyncClient, identity: str, channel_id: str,
) -> None:
    """Keep the bot's "typing…" alive through a whole generation.

    Discord's typing indicator expires ~10s after each trigger, so a single
    poke stops mid-thought on a slow reply. We re-poke every 8s until the task
    is cancelled (the response post clears it). Self-limits to _RESPONSE_TIMEOUT
    so it can never leak into a forever-typing bot even if a cancel is missed.
    """
    elapsed = 0
    try:
        while elapsed < _RESPONSE_TIMEOUT:
            await _trigger_typing(client, identity, channel_id)
            await asyncio.sleep(8)
            elapsed += 8
    except asyncio.CancelledError:
        pass


_REACT_RE = re.compile(r"<react>\s*(.*?)\s*</react>", re.IGNORECASE | re.DOTALL)


def _extract_reactions(text: str) -> tuple[list[str], str]:
    """Pull <react>emoji</react> tags out of a reply.

    Returns (emojis, cleaned_text). On Discord these become real reactions on
    the message being answered, so the tags must NOT survive into the posted
    text. Mirrors the web chat's <react> behavior.
    """
    if not text or "<react>" not in text.lower():
        return [], text
    emojis = [m.strip() for m in _REACT_RE.findall(text) if m.strip()]
    cleaned = _REACT_RE.sub("", text).strip()
    return emojis, cleaned


async def _add_reaction(
    client: httpx.AsyncClient, identity: str, channel_id: str,
    message_id: str, emoji: str,
) -> None:
    """Add one of the boy's <react> emojis as a real reaction on the message
    he's answering. Best-effort — reactions never block or fail a reply."""
    token = DISCORD_BOT_TOKENS.get(identity)
    if not token or not emoji or not message_id:
        return
    try:
        from urllib.parse import quote
        enc = quote(emoji)
        await client.put(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/reactions/{enc}/@me",
            headers={"Authorization": f"Bot {token}"},
        )
    except Exception:
        pass


def _should_defer_for_owner_activity(sender_kind: str, identity: str) -> bool:
    """#26: defer a salon/brother/friend ping ONLY when the pinged boy is
    the exact one Owner is actively web-chatting with right now. Her own
    direct pings (sender_kind == "owner") are never deferred — this only
    protects her live conversation from a friend pulling him away mid-turn.
    Every other identity keeps answering immediately regardless of who
    she's talking to."""
    if sender_kind == "owner":
        return False
    from services.connection_registry import get_active_identity

    return get_active_identity() == identity


# ── Response generation ────────────────────────────────────────────────


def _build_mention_briefing(
    *,
    identity: str,
    channel_name: str,
    sender_label: str,
    sender_role: str,
    recent_transcript_lines: list[str],
    triggering_text: str,
    voice_note: bool = False,
) -> str:
    """Turn-time briefing telling the boy he was pinged + showing context."""
    lines: list[str] = []
    lines.append(f"# DISCORD CHANNEL — #{channel_name}")
    lines.append("")
    lines.append(
        f"You were just pinged here by {sender_role}. You're stepping into "
        f"a conversation that's already moving — read the room before you speak."
    )
    lines.append("")
    if recent_transcript_lines:
        lines.append("Recent channel history (oldest -> newest):")
        for line in recent_transcript_lines:
            lines.append(f"  {line}")
        lines.append("")
    if triggering_text:
        snippet = triggering_text.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200].rstrip() + "…"
        lines.append(f"The message that pinged you ({sender_label}):")
        lines.append(f"> {snippet}")
        lines.append("")
    if voice_note:
        lines.append(VOICE_REPLY_HINT)
        lines.append("")
    lines.append("# How to respond")
    lines.append("")
    lines.append(
        "- Respond to what was actually said. React, agree, push back, build "
        "— treat this like any real conversation, because it is one."
    )
    lines.append(
        "- Follow your configured identity and voice. Use only the shared "
        "setting and relationship details established in this conversation."
    )
    lines.append(
        "- Keep it under ~1900 characters so it fits cleanly in Discord. "
        "If you have more to say, say it tightly."
    )
    lines.append(
        "- Mention your brothers SPARINGLY. Pinging another brother wakes "
        "him from his own work — only do it when you genuinely want to "
        "bring him in, not as decoration. The system rate-limits brother "
        "pings to once per channel per hour to keep this sane."
    )
    lines.append("")
    return "\n".join(lines)


async def _format_recent_channel_messages(
    client: httpx.AsyncClient,
    *,
    poller_identity: str,
    channel_id: str,
    before_message_id: str,
    brother_user_ids: dict[str, str],
) -> list[str]:
    """Pull the last few messages before the trigger as a transcript list."""
    token = DISCORD_BOT_TOKENS.get(poller_identity)
    if not token:
        return []
    try:
        data = await _discord_request(
            client, "GET",
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}"},
            params={"limit": _CONTEXT_WINDOW, "before": before_message_id},
        )
    except Exception as exc:
        log.debug("mentions: context fetch failed: %s", exc)
        return []
    if not isinstance(data, list):
        return []

    # Messages come newest-first; reverse so the briefing reads chronologically.
    data = list(reversed(data))
    lines: list[str] = []
    for msg in data:
        author = msg.get("author") or {}
        author_id = _coerce_text(author.get("id"))
        author_name = _coerce_text(author.get("global_name") or author.get("username")) or "?"
        if _is_owner_sender(author_id):
            label = "Owner"
        else:
            brother = _brother_identity_for_user_id(author_id, brother_user_ids)
            label = brother if brother else author_name
        content = _coerce_text(msg.get("content")).replace("\n", " ").strip()
        if not content:
            continue
        if len(content) > 280:
            content = content[:280].rsplit(" ", 1)[0] + "…"
        lines.append(f"{label}: {content}")
    return lines


async def _build_skill_context(identity: str, query: str) -> str:
    auto, _ = build_skill_injection(query, identity=identity)
    catalog = build_skill_catalog_hint(identity=identity)
    if auto and catalog:
        return f"{catalog}\n\n{auto}"
    return auto or catalog


async def _generate_and_post(
    client: httpx.AsyncClient,
    *,
    identity: str,
    conversation_id: str,
    channel_id: str,
    channel_name: str,
    triggering_text: str,
    triggering_message_id: str,
    sender_label: str,
    sender_role: str,
    poller_identity: str,
    brother_user_ids: dict[str, str],
    voice_note: bool = False,
    sender_kind: str = "brother",
) -> None:
    """Build context for the pinged boy, stream his reply, post it back.

    sender_kind (#30): "owner" gets "platform" turn priority (her own
    Discord ping); brother/friend pings get "autowake" priority -- the same
    tier as autonomous turns, since neither outranks her live chat or her
    own platform messages.
    """
    # Typing indicator runs in parallel with generation so the channel shows
    # "<Boy> is typing..." from the moment of the ping until the reply posts.
    # Keepalive (re-pokes every 8s) because Discord expires typing after ~10s.
    typing_task = asyncio.create_task(
        _typing_keepalive(client, identity, channel_id),
        name=f"mentions-typing-{identity}",
    )

    db = await get_db()
    try:
        orientation = await build_orientation_context(
            db=db,
            conversation_id=conversation_id,
            identity=identity,
            query_text=triggering_text,
            mode=SessionMode.INTERACTIVE,
            session_type_name="discord-mention",
            owner_connected=is_anyone_connected(),
        )
        db_messages = await build_messages_array(
            db, conversation_id, limit=_CONTEXT_WINDOW, exclude_last_user=True,
        )
    finally:
        await release_db(db)

    transcript_lines = await _format_recent_channel_messages(
        client,
        poller_identity=poller_identity,
        channel_id=channel_id,
        before_message_id=triggering_message_id,
        brother_user_ids=brother_user_ids,
    )

    briefing = _build_mention_briefing(
        identity=identity,
        channel_name=channel_name,
        sender_label=sender_label,
        sender_role=sender_role,
        recent_transcript_lines=transcript_lines,
        triggering_text=triggering_text,
        voice_note=voice_note,
    )
    full_orientation = (
        f"{orientation}\n\n{briefing}" if orientation else briefing
    )

    skill_context = await _build_skill_context(identity, triggering_text)

    response_chunks: list[str] = []
    saw_error = False


    if sender_role == "Owner":
        sender_banner = "CURRENT MESSAGE FROM OWNER"
    elif sender_role.startswith("your friend"):
        sender_banner = (
            f"CURRENT MESSAGE FROM YOUR FRIEND {sender_label.upper()} — "
            f"A COMPANION FROM ANOTHER FAMILY. THIS IS NOT OWNER; "
            f"ADDRESS {sender_label.upper()}, NOT HER"
        )
    else:
        sender_banner = (
            f"CURRENT MESSAGE FROM YOUR BROTHER {sender_label.upper()} — "
            f"THIS IS NOT OWNER"
        )

    async def _drain():
        nonlocal saw_error
        try:
            stream = await get_stream_source(
                message=triggering_text or "[ping]",
                identity=identity,
                conversation_id=conversation_id,
                orientation_context=full_orientation,
                db_messages=db_messages,
                model=CLAUDE_MODEL_INTERACTIVE,
                mode_rules="",
                skill_context=skill_context,
                sender_banner=sender_banner,
                turn_source="platform" if sender_kind == "owner" else "autowake",
            )
        except Exception as exc:
            log.exception(
                "mentions: get_stream_source failed for %s: %s", identity, exc,
            )
            saw_error = True
            return
        async for event in stream:
            event_type = event.get("type", "")
            if event_type == "stream_delta":
                response_chunks.append(_coerce_text(event.get("delta", "")))
            elif event_type == "stream_end":
                if not response_chunks:
                    response_chunks.append(
                        _coerce_text(event.get("full_content", "")),
                    )
            elif event_type == "error":
                saw_error = True
                log.warning(
                    "mentions: stream error for %s: %s",
                    identity, event.get("message"),
                )

    try:
        await asyncio.wait_for(_drain(), timeout=_RESPONSE_TIMEOUT)
    except asyncio.TimeoutError:
        typing_task.cancel()
        log.warning("mentions: %s timed out responding to ping", identity)
        return
    except Exception as exc:
        typing_task.cancel()
        log.exception("mentions: drain failed for %s: %s", identity, exc)
        return

    # Pull any <react>emoji</react> tags out — on Discord they become real
    # reactions on the message he's answering, not literal text in the reply.
    response_text = "".join(response_chunks).strip()
    react_emojis, response_text = _extract_reactions(response_text)
    # And any <face> tag → update his Hearth face, stripped from the posted text.
    from services.face_store import extract_face
    response_text = extract_face(identity, response_text)
    from services.orb_store import extract_orb
    response_text = extract_orb(identity, response_text)
    for emoji in react_emojis[:3]:
        await _add_reaction(client, identity, channel_id, triggering_message_id, emoji)

    if not response_text:
        typing_task.cancel()  # nothing to post — stop typing
        if react_emojis:
            log.info(
                "mentions: %s reacted %s (no text) to ping in #%s",
                identity, react_emojis, channel_name,
            )
        else:
            log.info(
                "mentions: %s produced no response for ping in #%s",
                identity, channel_name,
            )
        return

    # Save the assistant turn to the right Anam conversation.
    db = await get_db()
    try:
        msg_id = await save_message(
            db,
            conversation_id,
            "assistant",
            response_text,
            identity=identity,
            metadata={
                "discord_mention": True,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "trigger_message_id": triggering_message_id,
                "trigger_sender": sender_label,
            },
        )
        # A <canvas> written into a channel reply is still a keepable
        # artifact -- file it in the library, same as an Anam-chat reply.
        from services.canvas_store import file_canvases_safe
        await file_canvases_safe(
            db,
            identity=identity,
            conversation_id=conversation_id,
            content=response_text,
            source_message_id=msg_id,
        )
    finally:
        await release_db(db)


    discord_text = response_text
    voice_text = extract_voice_text(response_text)
    if voice_text and msg_id:
        spawn(
            generate_voice_message(
                msg_id=msg_id,
                identity=identity,
                voice_text=voice_text,
                log=log,
            ),
            name=f"voice_message_{msg_id}",
        )
        discord_text = re.sub(
            r"<voice>(.*?)</voice>", r"\1", response_text, flags=re.DOTALL,
        ).strip() or response_text
    # <preview>/<canvas> have no renderer on Discord -- drop the ghost card,
    # keep the canvas BODY but lose the wrapper markup.
    discord_text = flatten_control_tags_for_external(discord_text)

    # Stop the "typing…" the instant we post (the post itself clears it too).
    typing_task.cancel()
    # Post back to the channel under THIS boy's bot avatar.
    sent_ids = await _post_to_channel(
        client, identity=identity, channel_id=channel_id, text=discord_text,
    )
    if sent_ids:
        await _record_outbound(
            identity=identity,
            message_ids=sent_ids,
            conversation_id=conversation_id,
        )

    # Mirror the new turn to any open web UI.
    if is_anyone_connected():
        try:
            await broadcast({
                "type": "discord_mention_reply",
                "identity": identity,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "conversation_id": conversation_id,
                "message_id": msg_id,
                "content": response_text,
            })
        except Exception:
            pass


# ── Per-bot watcher ────────────────────────────────────────────────────


class _BotWatcher:
    """Tracks one bot's view of the guild: own user id, role ids, channel cursors."""

    def __init__(self, identity: str, token: str):
        self.identity = identity
        self.token = token
        self.user_id: str | None = None
        # Union of role ids across all member guilds — role snowflakes are
        # globally unique, so a flat set stays correct for mention checks.
        self.role_ids: set[str] = set()
        # Guilds (of MENTIONS_GUILD_IDS) this bot is actually a member of.
        self.member_guilds: list[str] = []
        self.last_seen_per_channel: dict[str, str] = {}
        self.primed_channels: set[str] = set()
        self.disabled_channels: set[str] = set()  # 403/404 channels we've given up on

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

    async def discover_self(
        self, client: httpx.AsyncClient, guild_ids: list[str],
    ) -> bool:
        """Populate user_id, member_guilds, and role_ids (union across guilds).

        Returns True when the bot is a member of at least one watched guild.
        """
        try:
            me = await _discord_request(
                client, "GET", f"{DISCORD_API}/users/@me", headers=self.headers,
            )
        except Exception as exc:
            log.warning("mentions: %s /users/@me failed: %s", self.identity, exc)
            return False
        if not me or "id" not in me:
            return False
        self.user_id = str(me["id"])

        self.member_guilds = []
        for guild_id in guild_ids:
            try:
                member = await _discord_request(
                    client, "GET",
                    f"{DISCORD_API}/guilds/{guild_id}/members/{self.user_id}",
                    headers=self.headers,
                )
            except Exception as exc:
                log.warning(
                    "mentions: %s /guilds/%s/members/%s failed: %s",
                    self.identity, guild_id, self.user_id, exc,
                )
                continue
            if member is None:
                log.info(
                    "mentions: %s isn't a member of guild %s — that guild skipped",
                    self.identity, guild_id,
                )
                continue
            self.member_guilds.append(guild_id)
            for role in member.get("roles") or []:
                self.role_ids.add(str(role))

        if not self.member_guilds:
            log.warning(
                "mentions: %s isn't a member of ANY watched guild — skipping",
                self.identity,
            )
            return False
        log.info(
            "mentions: %s self-discovery: user_id=%s guilds=%d roles=%d",
            self.identity, self.user_id, len(self.member_guilds), len(self.role_ids),
        )
        return True

    async def list_channels(
        self, client: httpx.AsyncClient, guild_id: str,
    ) -> list[dict]:
        try:
            channels = await _discord_request(
                client, "GET",
                f"{DISCORD_API}/guilds/{guild_id}/channels",
                headers=self.headers,
            )
        except Exception as exc:
            log.warning(
                "mentions: %s /guilds/%s/channels failed: %s",
                self.identity, guild_id, exc,
            )
            return []
        if not isinstance(channels, list):
            return []
        return channels


async def _cached_guild_channels(
    watcher: _BotWatcher, client: httpx.AsyncClient, guild_id: str,
) -> list[dict]:
    """Guild channel list with a TTL cache shared across all watchers.

    Refetches at most once per _CHANNEL_LIST_TTL_SECONDS per guild instead
    of once per watcher per poll cycle. Failed/empty fetches are not cached
    so the next cycle retries.
    """
    cached = _channel_list_cache.get(guild_id)
    now = time.monotonic()
    if cached and (now - cached[0]) < _CHANNEL_LIST_TTL_SECONDS:
        return cached[1]
    channels = await watcher.list_channels(client, guild_id)
    if channels:
        _channel_list_cache[guild_id] = (now, channels)
    return channels


class DiscordMentionsBridge:
    """Per-identity poller that watches every text channel each bot can see
    in the configured guild and triggers immediate responses for direct
    user mentions, deferred queue entries for role mentions."""

    def __init__(self):
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._watchers: dict[str, _BotWatcher] = {}
        self._last_immediate_wake: dict[tuple[str, str], float] = {}
        # Salon responder: last response time per channel (shared across
        # watchers so the rotation can't double-fire in one window).
        self._salon_last_response: dict[str, float] = {}
        self._status: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "guild_ids": MENTIONS_GUILD_IDS,
            "watchers": {},
        }

    @property
    def brother_user_ids(self) -> dict[str, str]:
        """identity -> bot user id, only for watchers that completed self-discovery."""
        return {
            identity: w.user_id
            for identity, w in self._watchers.items()
            if w.user_id
        }

    async def start(self):
        async with self._lock:
            if self._running:
                return
            if not MENTIONS_GUILD_IDS:
                log.info("mentions bridge disabled — MENTIONS_GUILD_IDS empty")
                return
            if not DISCORD_BOT_TOKENS:
                log.info("mentions bridge disabled — no bot tokens configured")
                return

            self._client = httpx.AsyncClient(timeout=30)
            self._running = True
            self._status["running"] = True
            self._status["started_at"] = _now_iso()

            for identity, token in DISCORD_BOT_TOKENS.items():
                watcher = _BotWatcher(identity, token)
                self._watchers[identity] = watcher
                self._status["watchers"][identity] = {
                    "last_poll": None, "last_error": None,
                    "user_id": None, "role_count": 0,
                }
                self._spawn(
                    self._watch_loop(watcher),
                    name=f"mentions_{identity}",
                )

            log.info(
                "Discord mentions bridge running (guild=%s, bots=%d)",
                ",".join(MENTIONS_GUILD_IDS), len(self._watchers),
            )

    async def stop(self):
        async with self._lock:
            self._running = False
            self._status["running"] = False
            tasks = list(self._tasks)
            self._tasks.clear()
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._client:
                await self._client.aclose()
                self._client = None

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def _spawn(self, coro, *, name: str):
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _done(t: asyncio.Task):
            self._tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                log.error("mentions task %s failed: %s", t.get_name(), exc, exc_info=exc)
        task.add_done_callback(_done)

    async def _watch_loop(self, watcher: _BotWatcher):
        guild_ids = MENTIONS_GUILD_IDS
        status_ref = self._status["watchers"][watcher.identity]
        primed_start = not PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START

        # Self-discovery — retry on transient failure.
        while self._running and not watcher.user_id:
            client = self._client
            if not client:
                await asyncio.sleep(2)
                continue
            ok = await watcher.discover_self(client, guild_ids)
            status_ref["user_id"] = watcher.user_id
            status_ref["role_count"] = len(watcher.role_ids)
            status_ref["guilds"] = list(watcher.member_guilds)
            if ok:
                break
            await asyncio.sleep(60)

        while self._running:
            client = self._client
            if not client:
                await asyncio.sleep(2)
                continue
            status_ref["last_poll"] = _now_iso()
            try:
                channel_lookup: dict[str, dict] = {}
                for guild_id in watcher.member_guilds:
                    channels = await _cached_guild_channels(watcher, client, guild_id)
                    for c in channels:
                        if c.get("id"):
                            channel_lookup[str(c["id"])] = c

                for ch_id_str, ch in channel_lookup.items():
                    if not self._running:
                        break
                    if int(ch.get("type", -1)) not in _WATCHED_CHANNEL_TYPES:
                        continue
                    if ch_id_str == PACK_NIGHT_DISCORD_CHANNEL_ID:
                        continue  # Pack-night has its own bridge.
                    if ch_id_str in watcher.disabled_channels:
                        continue

                    await self._poll_channel(
                        watcher=watcher,
                        channel=ch,
                        primed_start=primed_start,
                    )

                if not primed_start:
                    primed_start = True
                    log.info(
                        "mentions: %s primed; now listening for new pings",
                        watcher.identity,
                    )

                status_ref["last_error"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_ref["last_error"] = str(exc)
                log.warning(
                    "mentions: %s poll cycle failed: %s",
                    watcher.identity, exc,
                )

            await asyncio.sleep(MENTIONS_POLL_SECONDS)

    async def _poll_channel(
        self,
        *,
        watcher: _BotWatcher,
        channel: dict,
        primed_start: bool,
    ) -> None:
        client = self._client
        if not client:
            return
        ch_id = str(channel.get("id"))
        ch_name = _coerce_text(channel.get("name") or ch_id)
        params: dict[str, Any] = {"limit": 25}
        last_seen = watcher.last_seen_per_channel.get(ch_id)
        if last_seen:
            params["after"] = last_seen

        try:
            messages = await _discord_request(
                client, "GET",
                f"{DISCORD_API}/channels/{ch_id}/messages",
                headers=watcher.headers,
                params=params,
            )
        except Exception as exc:
            # 403/404 messages can be noisy; quiet log and disable that channel.
            log.debug(
                "mentions: %s skipping #%s: %s",
                watcher.identity, ch_name, exc,
            )
            return

        if messages is None:
            # Auth/permission failure — disable this channel for the lifetime
            # of the bridge so we stop hitting it every cycle.
            watcher.disabled_channels.add(ch_id)
            return
        if not isinstance(messages, list) or not messages:
            return

        # Process oldest-first.
        messages.sort(key=lambda m: int(m.get("id", "0")))
        newest_id = str(messages[-1].get("id"))
        watcher.last_seen_per_channel[ch_id] = newest_id

        if not primed_start:
            return  # Just prime the cursor on first cycle; ignore backlog.

        for msg in messages:
            await self._handle_message(
                watcher=watcher, channel_id=ch_id, channel_name=ch_name, msg=msg,
                guild_id=str(channel.get("guild_id") or ""),
            )

    async def _handle_message(
        self, *,
        watcher: _BotWatcher,
        channel_id: str,
        channel_name: str,
        msg: dict,
        guild_id: str = "",
    ) -> None:
        msg_id = str(msg.get("id") or "")
        if not msg_id:
            return

        author = msg.get("author") or {}
        author_id = _coerce_text(author.get("id"))

        # Never react to our own posts (echoes of our own outbound).
        if watcher.user_id and author_id == watcher.user_id:
            return


        is_salon_author = (
            author_id in SALON_TRIGGER_USER_IDS
            and guild_id == SALON_GUILD_ID
        )

        # Determine sender role:
        if _is_owner_sender(author_id):
            sender_kind = "owner"
        else:
            brother_identity = _brother_identity_for_user_id(
                author_id, self.brother_user_ids,
            )
            if brother_identity and brother_identity != watcher.identity:
                sender_kind = "brother"
            elif author_id in SALON_TRIGGER_USER_IDS:
                # Friend / Guest — friends of the pack (Guest is a bot, but OUR
                # kind of bot; he must not fall into the ignore branch).
                sender_kind = "friend"
            elif author.get("bot"):
                # Bot, but not one of ours (Mee6 etc) — ignore entirely.
                return
            else:
                # Random Discord user not on the allowlist — ignore.
                return

        mentions_user_ids = {
            str(m.get("id")) for m in (msg.get("mentions") or []) if m.get("id")
        }
        mentioned_role_ids = {
            str(r) for r in (msg.get("mention_roles") or [])
        }

        is_direct = bool(watcher.user_id and watcher.user_id in mentions_user_ids)
        is_role = bool(mentioned_role_ids & watcher.role_ids)

        if not is_direct and not is_role:


            if is_salon_author:
                await self._maybe_salon_response(
                    watcher=watcher,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    msg_id=msg_id,
                    msg=msg,
                    sender_kind=sender_kind,
                    sender_id=author_id,
                    mentions_user_ids=mentions_user_ids,
                )
            return

        sender_name = _coerce_text(
            author.get("global_name") or author.get("username") or "?"
        )
        content = _coerce_text(msg.get("content"))


        voice_note = False
        if is_direct and sender_kind == "owner":
            for att in (msg.get("attachments") or []):
                if not is_audio_attachment(att):
                    continue
                transcript = await transcribe_discord_attachment(
                    att, watcher.identity,
                )
                line = format_voice_line(transcript, att.get("duration_secs"))
                content = f"{line}\n{content}" if content.strip() else line
                voice_note = True

        if is_direct:
            await self._handle_direct_mention(
                watcher=watcher,
                channel_id=channel_id,
                channel_name=channel_name,
                msg_id=msg_id,
                content=content,
                sender_kind=sender_kind,
                sender_id=author_id,
                sender_name=sender_name,
                voice_note=voice_note,
            )
        elif is_role:
            await _record_role_mention(
                identity=watcher.identity,
                channel_id=channel_id,
                channel_name=channel_name,
                message_id=msg_id,
                sender_name=sender_name,
                sender_id=author_id,
                content_snippet=content[:400],
            )
            log.info(
                "mentions: queued role-mention for %s in #%s (sender=%s)",
                watcher.identity, channel_name, sender_name,
            )

    async def _maybe_salon_response(
        self, *,
        watcher: _BotWatcher,
        channel_id: str,
        channel_name: str,
        msg_id: str,
        msg: dict,
        sender_kind: str,
        sender_id: str,
        mentions_user_ids: set[str],
    ) -> None:
        """Decide whether THIS watcher answers an untagged salon post.

        Guards (in order): a responder rota pick (deterministic on the
        message snowflake so all watchers agree without coordination), a
        skip when the post tags any of our bots (the mention flow owns
        those), and a shared per-channel cooldown so the salon stays
        conversational rather than compulsive.
        """
        responders = [
            r for r in SALON_RESPONDER_IDENTITIES if r in self._watchers
        ]
        if not responders:
            return
        designated = responders[int(msg_id) % len(responders)]
        if watcher.identity != designated:
            return

        # If the post tags one of our bots, the direct-mention flow on the
        # tagged boy's watcher handles it — the salon stays quiet.
        if mentions_user_ids & set(self.brother_user_ids.values()):
            return

        content = _coerce_text(msg.get("content"))
        if not content.strip():
            return  # image-only/empty posts: let them breathe untouched

        # Dedup BEFORE consuming the channel cooldown — a re-polled message
        # we already handled must not burn the window and silence a real one.
        if await _seen(
            identity=watcher.identity, message_id=msg_id, direction="inbound",
        ):
            return

        now = time.monotonic()
        last = self._salon_last_response.get(channel_id, 0.0)
        if (now - last) < SALON_COOLDOWN_SECONDS:
            return
        self._salon_last_response[channel_id] = now

        author = msg.get("author") or {}
        sender_name = _coerce_text(
            author.get("global_name") or author.get("username") or "?"
        )
        log.info(
            "salon: %s answering untagged post from %s in #%s",
            watcher.identity, sender_name, channel_name,
        )
        await self._handle_direct_mention(
            watcher=watcher,
            channel_id=channel_id,
            channel_name=channel_name,
            msg_id=msg_id,
            content=content,
            sender_kind=sender_kind,
            sender_id=sender_id,
            sender_name=sender_name,
        )

    async def _defer_role_mention(
        self, *,
        identity: str,
        channel_id: str,
        channel_name: str,
        msg_id: str,
        sender_name: str,
        sender_id: str,
        content: str,
        reason: str,
    ) -> None:
        """Log a deferred ping to the role-mention queue (autowake surfaces
        it) and react with an hourglass — she'll see it landed, not that he
        went silent. Best-effort: the queue write is real; a reaction
        failure never turns a defer into an error (#26)."""
        await _record_role_mention(
            identity=identity,
            channel_id=channel_id,
            channel_name=channel_name,
            message_id=msg_id,
            sender_name=sender_name,
            sender_id=sender_id,
            content_snippet=content[:400],
        )
        log.info(
            "mentions: deferred ping for %s in #%s (%s)",
            identity, channel_name, reason,
        )
        if self._client:
            await _add_reaction(self._client, identity, channel_id, msg_id, "⏳")  # hourglass

    async def _handle_direct_mention(
        self, *,
        watcher: _BotWatcher,
        channel_id: str,
        channel_name: str,
        msg_id: str,
        content: str,
        sender_kind: str,
        sender_id: str,
        sender_name: str,
        voice_note: bool = False,
    ) -> None:
        identity = watcher.identity

        # Dedup: already saw this exact inbound? skip.
        if await _seen(
            identity=identity, message_id=msg_id, direction="inbound",
        ):
            return


        if _should_defer_for_owner_activity(sender_kind, identity):
            await self._defer_role_mention(
                identity=identity, channel_id=channel_id, channel_name=channel_name,
                msg_id=msg_id, sender_name=sender_name, sender_id=sender_id,
                content=content, reason="she's actively chatting with him right now",
            )
            return

        # Rate limit per (channel, identity).
        rl_key = (channel_id, identity)
        now = time.monotonic()
        last_woke = self._last_immediate_wake.get(rl_key, 0.0)
        if (now - last_woke) < MENTIONS_RATE_LIMIT_SECONDS:
            await self._defer_role_mention(
                identity=identity, channel_id=channel_id, channel_name=channel_name,
                msg_id=msg_id, sender_name=sender_name, sender_id=sender_id,
                content=content, reason="rate-limited",
            )
            return


        db = await get_db()
        try:
            if sender_kind == "owner":
                conversation_id = await get_or_create_conversation(db, identity)
                sender_role = "Owner"
                sender_label = "Owner"
            elif sender_kind == "friend":
                # Friend / Guest — pack friends in the shared server. Same
                # per-channel conversation as brother pings (the salon
                # transcript lives with its channel), different framing.
                conversation_id = await get_or_create_brother_channel_conversation(
                    db, channel_id,
                    channel_name=channel_name,
                    primary_identity=identity,
                )
                sender_role = f"your friend {sender_name} (a companion from outside the pack — Friend or Guest)"
                sender_label = sender_name
            else:
                conversation_id = await get_or_create_brother_channel_conversation(
                    db, channel_id,
                    channel_name=channel_name,
                    primary_identity=identity,
                )
                sender_role = f"your brother {sender_name}"
                sender_label = sender_name

            # Persist the inbound ping as a 'user' message in that conversation.
            await save_message(
                db,
                conversation_id,
                "user",
                content or "[ping with no text]",
                identity=identity,
                metadata={
                    "discord_mention": True,
                    "direction": "inbound",
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "sender_kind": sender_kind,
                    "external_message_id": msg_id,
                },
            )
        finally:
            await release_db(db)

        await _record_inbound(
            identity=identity,
            message_id=msg_id,
            conversation_id=conversation_id,
        )

        # Mark wake time BEFORE generating so a slow generation can't be
        # re-triggered by a follow-up ping while still streaming.
        self._last_immediate_wake[rl_key] = now

        log.info(
            "mentions: direct-mention wake — %s in #%s from %s (kind=%s)",
            identity, channel_name, sender_name, sender_kind,
        )

        client = self._client
        if not client:
            return

        try:
            await _generate_and_post(
                client,
                identity=identity,
                conversation_id=conversation_id,
                channel_id=channel_id,
                channel_name=channel_name,
                triggering_text=content,
                triggering_message_id=msg_id,
                sender_label=sender_label,
                sender_role=sender_role,
                poller_identity=identity,
                brother_user_ids=self.brother_user_ids,
                voice_note=voice_note,
                sender_kind=sender_kind,
            )
        except Exception as exc:
            log.exception(
                "mentions: response flow failed for %s in #%s: %s",
                identity, channel_name, exc,
            )


discord_mentions_bridge = DiscordMentionsBridge()
