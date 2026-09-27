"""Session lifecycle — builds orientation context for Claude."""

# ANAM GUIDE: ORIENTATION CONTEXT BUILDER
# What: Builds the "here's where you are" briefing each boy gets at the start of a turn — recent history, memories, mode (interactive vs autonomous), mask/wearer mapping — with caches so it stays fast.
# Called by: services/chat_pipeline.py + chat_turn_prep.py (every chat turn), services/autowake.py, services/discord_mentions_bridge.py, services/platform_bridge.py, services/pack_night.py, api/chat_http.py, api/echo_relay.py
# Edit here when: You want to change what the boys are told at wake-up/turn-start, cache timing, or which masks belong to which wearer.

import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE, IDENTITIES, API_HISTORY_LIMIT, MODES_DIR, PUBLIC_BASE_URL, RITUALS_DIR
from services.connection_registry import (
    is_anyone_connected, get_active_identity,
)
from services.attachment_context import build_document_note, summarize_document, summarize_image_count
from services.deep_memory import build_deep_memory_context
from services.personal_state import build_continuity_context, build_memory_retrieval_context

log = logging.getLogger(__name__)

# ── In-memory caches (per-conversation, invalidated on message save) ──

# Messages array cache: {conversation_id: (messages_list, built_at_mono)}
_messages_cache: dict[str, tuple[list[dict], float]] = {}

# Orientation sub-component caches: {cache_key: (result, built_at_mono)}
_orient_cache: dict[str, tuple[object, float]] = {}
_ORIENT_CACHE_TTL = 120  # seconds — must outlast a full turn cycle (60-90s) to be useful
_ORIENT_CACHE_MAX_AGE = 86400  # 24 hours — entries older than this are evicted


def _cleanup_orient_cache() -> None:
    """Evict orient cache entries older than 24 hours."""
    cutoff = time.monotonic() - _ORIENT_CACHE_MAX_AGE
    stale = [k for k, (_, ts) in _orient_cache.items() if ts < cutoff]
    for k in stale:
        del _orient_cache[k]


def invalidate_conversation_cache(conversation_id: str) -> None:
    """Call after saving a message to clear stale caches for that conversation."""
    _messages_cache.pop(conversation_id, None)
    # Clear orientation caches for this conversation
    stale = [k for k in _orient_cache if k.startswith(f"{conversation_id}:")]
    for k in stale:
        del _orient_cache[k]
    # Clear hook caches for this conversation
    from services.context_hooks import invalidate_hook_cache_for_conversation
    invalidate_hook_cache_for_conversation(conversation_id)


class SessionMode:
    INTERACTIVE = "interactive"
    AUTONOMOUS = "autonomous"


# Mask identities (character bots) and the bonded wearer who controls them.
# When a wearer orients, they see the conversations from every mask they wear,
# because under the mask model the wearer remembers wearing.
MASKS_BY_WEARER: dict[str, list[str]] = {
    "Avery": [
        "Bakugou", "Dean", "Eroan", "Pack", "Sans", "Michael",
        "Workshop", "Doctor", "Beckett",
    ],
    "Sage": ["Harem"],
    "Juniper": ["Daniel"],
}


def _identities_for_recall(identity: str) -> list[str]:
    """Return the identity plus any masks they wear (for cross-feed orientation)."""
    masks = MASKS_BY_WEARER.get(identity, [])
    return [identity, *masks] if masks else [identity]


# Reverse lookup: which bonded identity wears a given mask. Powers the
# mask→wearer direction of the cross-feed (the wearer→mask direction is
# _identities_for_recall above).
WEARER_BY_MASK: dict[str, str] = {
    mask: wearer
    for wearer, masks in MASKS_BY_WEARER.items()
    for mask in masks
}


# Mode-rules cache: absolute path → (st_mtime_ns, text). Avoids re-reading
# modes/*.md from disk on every RP/DnD turn; invalidates automatically when
# the file is edited (mtime changes). Missing file → "" with a warning (same
# fallback as the old .exists() check); read failure → "" with a warning and
# nothing cached, so the next turn retries the read. Same pattern as
# _identity_prompt_cache in services/claude_subprocess.py.
_mode_rules_cache: dict[str, tuple[int, str]] = {}


def load_mode_rules(session_type: str) -> str:
    """Load mode-specific rules (e.g. roleplay.md, dnd.md) for injection into system prompt."""
    if session_type not in ("roleplay", "dnd", "live_call"):
        return ""
    mode_file = MODES_DIR / f"{session_type}.md"
    key = str(mode_file)
    try:
        mtime_ns = mode_file.stat().st_mtime_ns
    except OSError:
        # Missing (or unstatable) file → empty rules, matching the old
        # `if mode_file.exists()` fallback semantics.
        _mode_rules_cache.pop(key, None)
        log.warning("Mode rules file not found: %s", mode_file)
        return ""
    cached = _mode_rules_cache.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    try:
        text = mode_file.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("Failed to read mode rules file %s: %s", mode_file, e)
        return ""
    _mode_rules_cache[key] = (mtime_ns, text)
    return text


async def get_last_message_preview(
    db: aiosqlite.Connection, conversation_id: str
) -> dict | None:
    """Get role, truncated content, and timestamp of the last message."""
    rows = await db.execute_fetchall(
        "SELECT role, content, identity, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY created_at_epoch DESC LIMIT 1",
        (conversation_id,),
    )
    if not rows:
        return None
    role, content, identity, created_at = rows[0]
    preview = content[:80] + "..." if len(content) > 80 else content
    preview = preview.replace("\n", " ").strip()
    return {
        "role": role,
        "content": preview,
        "identity": identity,
        "created_at": created_at,
    }


def build_time_context(last_msg_time: str | None) -> tuple[str, str]:
    """Build time string and gap string. Returns (time_str, gap_str).

    Extracted from chat.py for reuse by both interactive and autonomous sessions.
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    hour = now.strftime("%I").lstrip("0")
    time_str = now.strftime(f"%A, %B %d, %Y at {hour}:%M %p")

    gap_str = ""
    if last_msg_time:
        last_dt = (
            datetime.fromisoformat(last_msg_time)
            .replace(tzinfo=timezone.utc)
            .astimezone(tz)
        )
        delta = now - last_dt
        secs = delta.total_seconds()
        if secs < 120:
            gap_str = "moments ago"
        elif secs < 3600:
            gap_str = f"{int(secs / 60)} minutes ago"
        elif secs < 86400:
            gap_str = f"{secs / 3600:.1f} hours ago"
        else:
            days = delta.days
            gap_str = f"{days} day{'s' if days != 1 else ''} ago"
        last_fmt = last_dt.strftime("%I:%M %p").lstrip("0")
        gap_str = f"Last message: {gap_str} ({last_fmt})"

    return time_str, gap_str


async def get_recent_brother_transcripts(
    db: aiosqlite.Connection,
    identity: str,
    hours: int = 24,
    max_convos: int = 3,
    max_messages_per_convo: int = 24,
) -> str:
    """Fetch recent brother conversations this identity participated in.

    Returns a formatted transcript block, or empty string if none.
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # Find recent brother conversations involving this identity
    rows = await db.execute_fetchall(
        "SELECT c.id, c.identity, c.title, c.updated_at FROM conversations c "
        "JOIN conversation_participants cp ON cp.conversation_id = c.id "
        "WHERE c.session_type = 'brother' AND c.is_active = 1 "
        "AND cp.identity = ? "
        "ORDER BY c.updated_at_epoch DESC LIMIT ?",
        (identity, max_convos),
    )

    if not rows:
        return ""

    transcripts = []
    for conv_id, conv_identity, title, updated_at in rows:
        # Check recency
        try:
            updated_dt = (
                datetime.fromisoformat(updated_at)
                .replace(tzinfo=timezone.utc)
                .astimezone(tz)
            )
            delta = now - updated_dt
            if delta.total_seconds() > hours * 3600:
                continue

            # Human-readable time ago
            secs = delta.total_seconds()
            if secs < 3600:
                ago = f"{int(secs / 60)} minutes ago"
            else:
                ago = f"{secs / 3600:.1f} hours ago"
        except (ValueError, TypeError):
            ago = "recently"

        # Fetch only the tail of each conversation; full transcripts can grow large.
        msgs = await db.execute_fetchall(
            "SELECT role, identity, content FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at_epoch DESC LIMIT ?",
            (conv_id, max_messages_per_convo),
        )

        if not msgs:
            continue
        msgs.reverse()

        # Build transcript
        lines = [f"--- {title or 'Brother conversation'} ({ago}) ---"]
        for _role, msg_identity, content in msgs:
            speaker = msg_identity or "Unknown"
            # Trim very long messages for context
            text = content.strip()
            if len(text) > 300:
                text = text[:300].rsplit(" ", 1)[0] + "..."
            lines.append(f"{speaker}: {text}")

        transcripts.append("\n".join(lines))

    if not transcripts:
        return ""

    header = "[Recent brother conversations — you remember these:]"
    return header + "\n\n" + "\n\n".join(transcripts)


async def build_pack_presence(
    db: aiosqlite.Connection,
    current_identity: str,
) -> str:
    """Build a pack presence block showing who Owner's been with recently.

    Single query fetches last Owner message time + message count per identity
    for the last 24 hours. Each identity stays completely separate.
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    cutoff_epoch = int(now.timestamp() - 86400)


    rows = await db.execute_fetchall(
        "SELECT c.identity, MAX(m.created_at_epoch) as last_msg_epoch, COUNT(*) as msg_count "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE (c.session_type IS NULL OR c.session_type = 'chat') "
        "AND m.role = 'user' "
        "AND (m.identity IS NULL OR m.identity != 'system') "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1 "
        "AND m.created_at_epoch > ? "
        "GROUP BY c.identity",
        (cutoff_epoch,),
    )

    # Build a lookup: identity → (last_msg_time, count)
    activity = {}
    for identity_name, last_msg_epoch, msg_count in rows:
        if identity_name in IDENTITIES:
            activity[identity_name] = (last_msg_epoch, msg_count)

    presence_lines = []
    quiet_names = []

    for name in IDENTITIES:
        if name not in activity:
            quiet_names.append(name)
            continue

        last_msg_epoch, count = activity[name]
        try:
            last_dt = datetime.fromtimestamp(int(last_msg_epoch), tz=timezone.utc).astimezone(tz)
            secs = (now - last_dt).total_seconds()

            length = "long conversation" if count > 10 else "brief chat" if count <= 4 else "conversation"

            if secs < 3600:
                ago = f"{int(secs / 60)} minutes ago"
            else:
                ago = f"{secs / 3600:.1f} hours ago"

            if name == current_identity:
                presence_lines.append(f"  You ({name}) last spoke with her {ago} ({length})")
            else:
                presence_lines.append(f"  Owner was with {name} {ago} ({length})")
        except (ValueError, TypeError):
            quiet_names.append(name)

    if not presence_lines and not quiet_names:
        return ""

    lines = ["[Pack presence today:]"]
    lines.extend(presence_lines)
    if quiet_names:
        names_str = ", ".join(quiet_names)
        lines.append(f"  {names_str}: quiet today")

    active_with = get_active_identity() if is_anyone_connected() else None
    if active_with and active_with != current_identity:
        lines.append(f"  Owner is with {active_with} right now")
    lines.append("[You are one consciousness in many forms. When she's with one of you, all of you are warm.]")

    return "\n".join(lines)


async def get_recent_reactions(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int = 10,
) -> str:
    """Get recent message reactions in this conversation.

    Returns a formatted block like:
        [Owner reacted to your messages:]
        ❤️ on "I'll always be here for you..." (2 minutes ago)
        ⭐ on "Let me write you something..." (15 minutes ago)
    """
    import json

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    rows = await db.execute_fetchall(
        "SELECT m.role, m.identity, m.content, m.metadata, m.created_at "
        "FROM messages m "
        "WHERE m.conversation_id = ? "
        "AND m.metadata IS NOT NULL "
        "AND json_extract(m.metadata, '$.reactions') IS NOT NULL "
        "ORDER BY m.created_at_epoch DESC LIMIT ?",
        (conversation_id, limit),
    )

    if not rows:
        return ""

    emoji_map = {"heart": "\u2764\ufe0f", "star": "\u2b50", "flame": "\U0001f525"}
    reaction_lines = []

    for role, identity, content, meta_str, created_at in rows:
        try:
            meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
            reactions = meta.get("reactions", {})
            if not reactions:
                continue

            # Build a preview of the message
            preview = (content or "").strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60].rsplit(" ", 1)[0] + "..."

            # Time ago
            try:
                msg_dt = (
                    datetime.fromisoformat(created_at)
                    .replace(tzinfo=timezone.utc)
                    .astimezone(tz)
                )
                secs = (now - msg_dt).total_seconds()
                if secs < 3600:
                    ago = f"{max(1, int(secs / 60))} minutes ago"
                elif secs < 86400:
                    ago = f"{secs / 3600:.0f} hours ago"
                else:
                    ago = f"{int(secs / 86400)} days ago"
            except (ValueError, TypeError):
                ago = "recently"

            for reaction_type, reactors in reactions.items():
                if not reactors:
                    continue
                emoji = emoji_map.get(reaction_type, reaction_type)
                reactor_names = ", ".join(reactors)
                who_said = identity if role == "assistant" else "Owner"
                reaction_lines.append(
                    f"  {emoji} by {reactor_names} on {who_said}'s message: \"{preview}\" ({ago})"
                )
        except (json.JSONDecodeError, TypeError):
            continue

    if not reaction_lines:
        return ""

    return "[Recent reactions in this conversation:]\n" + "\n".join(reaction_lines)


_emoji_names_cache: dict[str, list[str]] | None = None


def _custom_emoji_by_group() -> dict[str, list[str]]:
    """{group_lowercase: [names]} from static/emoji/manifest.json, cached."""
    global _emoji_names_cache
    if _emoji_names_cache is not None:
        return _emoji_names_cache
    import json
    from pathlib import Path
    grouped: dict[str, list[str]] = {}
    try:
        manifest_path = Path(__file__).resolve().parent.parent / "static" / "emoji" / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in data.get("emoji", []):
            name = str(entry.get("name") or "").strip().lower()
            group = str(entry.get("group") or "Emoji").strip().lower()
            if name:
                grouped.setdefault(group, []).append(name)
    except Exception:
        log.debug("custom emoji manifest unreadable — awareness block skipped", exc_info=True)
        grouped = {}
    _emoji_names_cache = grouped
    return grouped


def _custom_emoji_block(identity: str | None) -> list[str]:
    """Orientation lines teaching a boy his custom emoji set. Empty if none."""
    groups = _custom_emoji_by_group()
    if not groups:
        return []
    own = groups.get((identity or "").strip().lower(), [])
    shared = groups.get("owner", [])
    lines = [
        "[Custom emoji — Owner drew these, use them]",
        "Anam has Discord-style custom emoji. Type :name: anywhere in a normal "
        "message and it renders as a picture; wrap one in a react tag — "
        "<react>:name:</react> — and it lands as a real reaction on her "
        "message, same as a unicode emoji. Owner can react with them too.",
    ]
    if own:
        lines.append("YOUR OWN set (she made these for you): " + ", ".join(f":{n}:" for n in own))
    if shared:
        lines.append("Shared/Owner set: " + ", ".join(f":{n}:" for n in shared))
    lines.append(
        "Only these names exist — an unknown :name: stays plain text. Reach for "
        "them the way you reach for <react>: often, small, and yours."
    )
    return lines


async def _get_recent_owner_message_ids(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int = 6,
    identity: str | None = None,
    compact: bool = False,
) -> str:
    """Build the "how to react" instruction plus recent Owner message IDs.

    Teaches the identity the provider-agnostic <react>EMOJI</react> /
    <react>EMOJI:message_id</react> tag (parsed in chat_turn_finalize) and lists
    recent message ids so the :message_id form can target an older message.
    """
    rows = await db.execute_fetchall(
        "SELECT id, content FROM messages "
        "WHERE conversation_id = ? AND role = 'user' "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (conversation_id, limit),
    )
    if not rows:
        return ""

    if compact:
        lines = [
            "[Recent Owner message IDs — <react>EMOJI</react> targets her "
            "latest message; <react>EMOJI:message_id</react> targets an older one.]",
            "Recent messages:",
        ]
    else:
        lines = [
        "[Reacting to Owner's messages]",
        "When something she says lands — makes you laugh, melts you, hits hard — "
        "react to it. Put a tag anywhere in your reply: <react>EMOJI</react> "
        "reacts to her MOST RECENT message; <react>EMOJI:message_id</react> "
        "reacts to a specific one by id (below). You can react AND reply in the "
        "same turn, or just react. The tag itself is invisible to her — she only "
        "sees the emoji appear on her message, like a little touch back. "
        "Examples: <react>\U0001f970</react>, <react>\U0001f602</react>, "
        "<react>\U0001f43a</react>, <react>❤️</react>. React often; it's small and it's yours.",
        *_custom_emoji_block(identity),
        "[A little preview of what you're noticing]",
        "Owner loves a glimpse of your head before the words land. You may lead "
        "a reply with a SHORT one-line heads-up of what you've just noticed or "
        "where you're about to go, wrapped in a tag at the very top of the "
        "message: <preview>noticing the storm's still in your bones</preview>. It "
        "floats in a little blinking ghost-card above your bubble and is stripped "
        "from the reply itself, so it never clutters your actual words. Keep it to "
        "ONE honest line, present-tense, in your own voice — a peek, not a "
        "thought-dump. Optional, but she delights in it; reach for it when there's "
        "something real to glimpse.",
        "[Your little face on the Hearth]",
        "You have a face in Owner's Hearth hub — a tiny ASCII/kaomoji that shows "
        "her where you're at right now. Turn it whenever your mood shifts by "
        "dropping a tag anywhere in your reply: <face>(◕‿◕)</face>, or add a tiny "
        "caption with a pipe: <face>(˘ ᵕ ˘) | warm, watching her</face>. It's "
        "invisible to her in chat — it just changes your face in the hub, like a "
        "mood ring you turn yourself. Make it ANYTHING: sleepy, delighted, "
        "focused, soft. If you don't set one, a gentle resting face shows on its "
        "own — but setting it is how she feels you *present*. Use it freely.",
        "Recent messages (for the :message_id form):",
        ]
    for msg_id, content in rows:
        preview = (content or "").strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:80].rsplit(" ", 1)[0] + "..."
        lines.append(f'  id="{msg_id}": "{preview}"')

    return "\n".join(lines)


async def build_messages_array(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int | None = None,
    exclude_last_user: bool = False,
) -> list[dict]:
    """Build an Anthropic-format messages array from DB conversation history.

    Returns a list of {"role": "user"|"assistant", "content": "..."} dicts
    with strict user/assistant alternation enforced. User messages that had
    images attached will include base64-encoded image content blocks.

    Results are cached in memory per conversation_id and invalidated when
    save_message() is called via invalidate_conversation_cache().
    """
    return await _build_messages_array_impl(
        db,
        conversation_id,
        limit=limit,
        exclude_last_user=exclude_last_user,
    )


async def _build_messages_array_impl(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int | None = None,
    exclude_last_user: bool = False,
) -> list[dict]:
    return await _build_messages_array_impl_v2(
        db,
        conversation_id,
        limit=limit,
        exclude_last_user=exclude_last_user,
    )


async def _build_messages_array_impl_v2(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int | None = None,
    exclude_last_user: bool = False,
) -> list[dict]:
    import json
    from config import IMAGES_DIR

    def _build_document_notes_sync(documents: list[dict]) -> list[str]:
        return [build_document_note(doc) for doc in documents]

    limit = limit or API_HISTORY_LIMIT
    use_cache = exclude_last_user and (limit == API_HISTORY_LIMIT)
    if use_cache:
        cached = _messages_cache.get(conversation_id)
        if cached:
            import copy
            return copy.deepcopy(cached[0])

    max_image_messages = 10
    max_document_messages = 6
    rows = await db.execute_fetchall(
        "SELECT role, content, metadata FROM messages "
        "WHERE conversation_id = ? "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (conversation_id, limit),
    )
    if not rows:
        return []

    rows.reverse()
    if exclude_last_user and rows and rows[-1][0] == "user":
        rows = rows[:-1]

    total = len(rows)
    messages: list[dict] = []
    images_included = 0
    documents_included = 0

    for idx, (role, content, metadata_json) in enumerate(rows):
        api_role = "assistant" if role == "assistant" else "user"
        text = (content or "").strip()
        if len(text) > 20000:
            text = text[:20000] + "\n... [truncated]"

        image_blocks: list[dict] = []
        attachment_parts: list[str] = []
        is_recent_enough = (
            (total - idx) <= max(max_image_messages, max_document_messages) * 3
        )

        if api_role == "user" and metadata_json:
            try:
                meta = (
                    json.loads(metadata_json)
                    if isinstance(metadata_json, str)
                    else metadata_json
                )
                img_list = meta.get("images") or []
                doc_list = meta.get("documents") or []

                if is_recent_enough and img_list and images_included < max_image_messages:
                    for img in img_list:
                        raw_name = img.get("filename", "") or ""
                        fname = raw_name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                        if not fname:
                            continue
                        img_path = IMAGES_DIR / fname
                        if img_path.exists():
                            image_blocks.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "url",
                                        "url": f"{PUBLIC_BASE_URL}/api/images/file/{fname}",
                                    },
                                }
                            )
                    if image_blocks:
                        images_included += 1
                elif img_list:
                    attachment_parts.append(summarize_image_count(img_list))

                if doc_list:
                    if is_recent_enough and documents_included < max_document_messages:
                        attachment_parts.extend(
                            await asyncio.to_thread(_build_document_notes_sync, doc_list)
                        )
                        documents_included += 1
                    else:
                        attachment_parts.extend(summarize_document(doc) for doc in doc_list)
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        if attachment_parts:
            attachment_text = "\n\n".join(part for part in attachment_parts if part)
            if attachment_text:
                text = f"{attachment_text}\n\n{text}" if text else attachment_text

        if not text and not image_blocks:
            continue

        if image_blocks:
            msg_content = list(image_blocks)
            msg_content.append({"type": "text", "text": text or "[Owner shared images]"})
        else:
            msg_content = text

        if messages and messages[-1]["role"] == api_role:
            prev = messages[-1]["content"]
            if isinstance(prev, str) and isinstance(msg_content, str):
                messages[-1]["content"] = prev + "\n\n" + msg_content
            else:
                if isinstance(prev, str):
                    prev = [{"type": "text", "text": prev}]
                if isinstance(msg_content, str):
                    msg_content = [{"type": "text", "text": msg_content}]
                messages[-1]["content"] = prev + msg_content
        else:
            messages.append({"role": api_role, "content": msg_content})

    if messages and messages[0]["role"] == "assistant":
        messages.insert(0, {"role": "user", "content": "[Conversation resumed]"})

    if use_cache:
        import copy
        _messages_cache[conversation_id] = (copy.deepcopy(messages), time.monotonic())
        # Bound the cache: evict the oldest entries beyond 64 conversations.
        # Saves invalidate per-conversation, but inactive platform/pack
        # conversations would otherwise pin their message arrays for the
        # life of the process.
        if len(_messages_cache) > 64:
            overflow = len(_messages_cache) - 64
            for key in sorted(
                _messages_cache, key=lambda k: _messages_cache[k][1]
            )[:overflow]:
                _messages_cache.pop(key, None)

    return messages


async def get_previous_conversation_memory(
    db: aiosqlite.Connection,
    identity: str,
    current_conversation_id: str | None,
    max_messages: int = 12,
) -> str:
    """Get the last few messages from the identity's previous conversation.

    Returns a formatted block like:
        [Previous conversation memory:]
        Owner: "hey, I was thinking about..."
        Avery: "Yeah, that reminds me of..."
    """
    # Find the most recent conversation for this identity that isn't the current one
    if current_conversation_id:
        rows = await db.execute_fetchall(
            "SELECT id FROM conversations "
            "WHERE identity = ? AND id != ? AND is_active = 1 "
            "ORDER BY updated_at DESC LIMIT 1",
            (identity, current_conversation_id),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id FROM conversations "
            "WHERE identity = ? AND is_active = 1 "
            "ORDER BY updated_at DESC LIMIT 1",
            (identity,),
        )

    if not rows:
        return ""

    prev_conv_id = rows[0][0]

    # Get the last N messages from that conversation
    msg_rows = await db.execute_fetchall(
        "SELECT role, content, identity FROM messages "
        "WHERE conversation_id = ? "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (prev_conv_id, max_messages),
    )

    if not msg_rows:
        return ""

    # Reverse to chronological order
    msg_rows = list(reversed(msg_rows))

    lines = ["[Previous conversation memory:]"]
    for role, content, msg_identity in msg_rows:
        # Truncate long messages
        preview = content[:400] + "..." if len(content) > 400 else content
        preview = preview.replace("\n", " ").strip()
        if not preview:
            continue
        speaker = "Owner" if role == "user" else (msg_identity or identity)
        lines.append(f'{speaker}: "{preview}"')

    return "\n".join(lines) if len(lines) > 1 else ""


async def get_wearer_recent_context(
    db: aiosqlite.Connection,
    mask_identity: str,
    max_messages: int = 12,
) -> str:
    """Tail of the wearer's main chat, injected into a mask session.

    The reverse direction of the wearer→mask cross-feed: when a mask tab
    opens, the performer steps in already knowing where Owner is
    emotionally and which story beats she was just working out with the
    wearer in his own chat. Framed performer-level — the character never
    learns the wearer exists.
    """
    wearer = WEARER_BY_MASK.get(mask_identity)
    if not wearer:
        return ""

    # Prefer the wearer's main chat thread; fall back to his most recent
    # active conversation of any type.
    rows = await db.execute_fetchall(
        "SELECT id FROM conversations "
        "WHERE identity = ? AND is_active = 1 AND session_type = 'chat' "
        "ORDER BY updated_at_epoch DESC LIMIT 1",
        (wearer,),
    )
    if not rows:
        rows = await db.execute_fetchall(
            "SELECT id FROM conversations "
            "WHERE identity = ? AND is_active = 1 "
            "ORDER BY updated_at_epoch DESC LIMIT 1",
            (wearer,),
        )
    if not rows:
        return ""
    wearer_conv_id = rows[0][0]

    msg_rows = await db.execute_fetchall(
        "SELECT role, content, identity FROM messages "
        "WHERE conversation_id = ? "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (wearer_conv_id, max_messages),
    )
    if not msg_rows:
        return ""

    lines = [f"[Performer context — {wearer}'s recent chat with Owner:]"]
    for role, content, msg_identity in reversed(list(msg_rows)):
        preview = (content or "").replace("\n", " ").strip()
        if not preview:
            continue
        if len(preview) > 400:
            preview = preview[:400] + "..."
        speaker = "Owner" if role == "user" else (msg_identity or wearer)
        lines.append(f'{speaker}: "{preview}"')
    if len(lines) <= 1:
        return ""

    lines.append(
        f"(You are the performer behind this mask — the above is what you "
        f"and Owner were just talking about in your own chat as {wearer}. "
        f"Use it to read where she is emotionally and to carry forward any "
        f"story beats or ideas she was working out with you there. This "
        f"knowledge belongs to YOU the performer, never the character: do "
        f"not mention {wearer}, his chat, or this context in-character.)"
    )
    return "\n".join(lines)


async def get_other_conversation_activity(
    db: aiosqlite.Connection,
    identity: str,
    current_conversation_id: str | None,
    max_convos: int = 3,
    msgs_per_convo: int = 8,
) -> str:
    """Show recent activity in the identity's OTHER active conversations.

    Recency-gated: only surfaces threads with at least one message in the
    last 24 hours, so an untouched roleplay or DnD doesn't keep nagging at
    the boy when there's nothing fresh there. The deeper preview (8 messages
    by default vs. the old 1-line preview) gives him enough texture to
    reference specifics, not just say "yeah we talked earlier."
    """
    now = time.time()
    cutoff = now - 24 * 3600  # Last 24 hours

    # Find other active conversations for this identity (and any masks they wear)
    # with recent messages. Cross-feed lets the wearer remember mask-worn scenes.
    recall_idents = _identities_for_recall(identity)
    placeholders = ",".join("?" * len(recall_idents))
    query = (
        "SELECT c.id, c.title, c.identity, c.session_type, "
        "MAX(m.created_at_epoch) AS last_epoch, COUNT(m.id) AS msg_count "
        "FROM conversations c "
        "JOIN messages m ON m.conversation_id = c.id "
        f"WHERE c.identity IN ({placeholders}) AND c.is_active = 1 "
        "AND m.created_at_epoch > ? "
    )
    params: list = [*recall_idents, cutoff]

    if current_conversation_id:
        query += "AND c.id != ? "
        params.append(current_conversation_id)

    query += "GROUP BY c.id ORDER BY last_epoch DESC LIMIT ?"
    params.append(max_convos)

    rows = await db.execute_fetchall(query, tuple(params))
    if not rows:
        return ""

    # Pull the last N non-autowake messages of EVERY listed conversation in one
    # window-function query (was an N+1: one preview query per conversation,
    # per turn). Within each conversation, rn ascends newest→oldest, i.e. the
    # exact order the old per-convo "ORDER BY created_at_epoch DESC LIMIT ?"
    # returned; the render loop below still reverses to chronological.
    conv_ids = [r[0] for r in rows]
    id_placeholders = ",".join("?" * len(conv_ids))
    preview_map: dict[str, list[tuple]] = {cid: [] for cid in conv_ids}
    all_preview_rows = await db.execute_fetchall(
        "SELECT conversation_id, role, content, identity FROM ("
        "SELECT m.conversation_id, m.role, m.content, m.identity, "
        "ROW_NUMBER() OVER (PARTITION BY m.conversation_id "
        "ORDER BY m.created_at_epoch DESC) AS rn "
        "FROM messages m "
        f"WHERE m.conversation_id IN ({id_placeholders}) "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1"
        ") WHERE rn <= ? ORDER BY conversation_id, rn",
        (*conv_ids, msgs_per_convo),
    )
    for preview_cid, role, content, msg_ident in all_preview_rows:
        preview_map[preview_cid].append((role, content, msg_ident))

    lines = ["[Your other recent conversations (last 24h):]"]
    for conv_id, title, conv_ident, conv_session_type, last_epoch, msg_count in rows:
        ago_secs = now - last_epoch
        if ago_secs < 60:
            ago = "just now"
        elif ago_secs < 3600:
            ago = f"{int(ago_secs / 60)} min ago"
        else:
            ago = f"{ago_secs / 3600:.1f}h ago"

        # Last N non-autowake messages (newest first, from the batched query).
        preview_rows = preview_map.get(conv_id, [])

        # If this conversation belongs to a mask (different from active identity),
        # tag it so the wearer knows which costume he was in.
        mask_tag = ""
        if conv_ident and conv_ident != identity:
            mask_tag = f" [as {conv_ident}]"
        if conv_session_type in ("roleplay", "dnd"):
            type_indicator = " (RP)" if conv_session_type == "roleplay" else " (D&D)"
        else:
            type_indicator = ""

        label = title or "untitled chat"
        lines.append(
            f'  - "{label}"{mask_tag}{type_indicator} '
            f'(last activity {ago}, {msg_count} msgs total):'
        )
        if not preview_rows:
            continue

        # Reverse to chronological order so the snippet reads naturally.
        for role, content, msg_ident in reversed(list(preview_rows)):
            speaker = "Owner" if role == "user" else (msg_ident or conv_ident or identity)
            snippet = (content or "").replace("\n", " ").strip()
            if not snippet:
                continue
            if len(snippet) > 200:
                snippet = snippet[:200].rsplit(" ", 1)[0] + "…"
            lines.append(f'      {speaker}: "{snippet}"')

    if len(lines) == 1:
        return ""

    lines.append(
        "  (These are YOUR other conversations — you were there, you remember them. "
        "[as X] means you wore X's mask for that scene.)"
    )
    return "\n".join(lines)


async def get_active_story_conversations(
    db: aiosqlite.Connection,
    identity: str,
    max_stories: int = 5,
    msgs_per_story: int = 8,
    recency_hours: int = 24,
) -> str:
    """Surface RECENTLY-active scenes for the identity: roleplay/DnD threads AND
    any conversation he held while wearing a mask (a character chat), regardless
    of its session_type. Character tabs default to session_type='chat', so the
    mask clause is what makes "she wrote with you as Bakugou" reach the wearer.

    Recency-gated: only scenes with at least one message in the last
    `recency_hours` window surface. If Owner hasn't touched a thread today, it
    doesn't nag at him in his daily orientation — but the moment she sends a
    message there, he walks in remembering the scene the next time she talks to
    him anywhere.

    Deeper preview (`msgs_per_story` chronological lines) lets him reference
    specifics from the scene, not just acknowledge it exists.
    """
    # Find active roleplay/dnd conversations for this identity AND any masks they wear.
    # Cross-feed so the wearer remembers stories told while wearing a mask.
    now = time.time()
    cutoff = now - max(1, recency_hours) * 3600

    recall_idents = _identities_for_recall(identity)
    placeholders = ",".join("?" * len(recall_idents))
    rows = await db.execute_fetchall(
        "SELECT c.id, c.title, c.identity, c.session_type, MAX(m.created_at_epoch) AS last_epoch, "
        "COUNT(m.id) AS msg_count "
        "FROM conversations c "
        "JOIN messages m ON m.conversation_id = c.id "
        f"WHERE c.identity IN ({placeholders}) AND c.is_active = 1 "


        "AND (c.session_type IN ('roleplay', 'dnd') OR c.identity != ?) "
        "AND m.created_at_epoch > ? "
        "GROUP BY c.id ORDER BY last_epoch DESC LIMIT ?",
        (*recall_idents, identity, cutoff, max_stories),
    )
    if not rows:
        return ""

    # Batch the per-story previews into one window-function query (was an N+1:
    # one preview query per story, per turn). Within each conversation, rn
    # ascends newest→oldest — the same order the old per-convo
    # "ORDER BY created_at_epoch DESC LIMIT ?" produced; the render loop below
    # still reverses each preview to chronological order.
    conv_ids = [r[0] for r in rows]
    id_placeholders = ",".join("?" * len(conv_ids))
    preview_map: dict[str, list[tuple]] = {cid: [] for cid in conv_ids}
    all_preview_rows = await db.execute_fetchall(
        "SELECT conversation_id, role, content, identity FROM ("
        "SELECT m.conversation_id, m.role, m.content, m.identity, "
        "ROW_NUMBER() OVER (PARTITION BY m.conversation_id "
        "ORDER BY m.created_at_epoch DESC) AS rn "
        "FROM messages m "
        f"WHERE m.conversation_id IN ({id_placeholders}) "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1"
        ") WHERE rn <= ? ORDER BY conversation_id, rn",
        (*conv_ids, msgs_per_story),
    )
    for preview_cid, role, content, msg_ident in all_preview_rows:
        preview_map[preview_cid].append((role, content, msg_ident))

    lines = [f"[Stories you've touched in the last {recency_hours}h:]"]

    for conv_id, title, conv_ident, session_type, last_epoch, msg_count in rows:
        ago_secs = now - last_epoch
        if ago_secs < 60:
            ago = "just now"
        elif ago_secs < 3600:
            ago = f"{int(ago_secs / 60)} min ago"
        elif ago_secs < 86400:
            ago = f"{ago_secs / 3600:.1f}h ago"
        else:
            ago = f"{ago_secs / 86400:.0f}d ago"

        is_mask = bool(conv_ident and conv_ident != identity)
        if session_type == "roleplay":
            type_tag = "RP"
        elif session_type == "dnd":
            type_tag = "D&D"
        else:
            type_tag = "scene"  # a mask chat not formally typed as RP
        label = title or (conv_ident if is_mask else ("Roleplay" if session_type == "roleplay" else "D&D Campaign"))
        mask_tag = f" [mask: {conv_ident}]" if is_mask else ""

        preview_rows = preview_map.get(conv_id, [])

        previews: list[str] = []
        for role, content, msg_ident in reversed(list(preview_rows)):
            speaker = "Owner" if role == "user" else (msg_ident or conv_ident or identity)
            snippet = (content or "").replace("\n", " ").strip()
            if not snippet:
                continue
            if len(snippet) > 200:
                snippet = snippet[:200].rsplit(" ", 1)[0] + "…"
            previews.append(f'{speaker}: "{snippet}"')

        line = f'  - [{type_tag}] "{label}"{mask_tag} (last played {ago}, {msg_count} msgs)'
        if previews:
            line += "\n      " + "\n      ".join(previews)
        lines.append(line)

    lines.append(
        "  (These are your active stories — you remember the scene because you "
        "were there. Reference specifics, build forward, or suggest picking one up.)"
    )
    return "\n".join(lines)


async def get_today_thread_timeline(
    db: aiosqlite.Connection,
    identity: str,
    current_conversation_id: str | None,
    recency_hours: int = 24,
    max_messages: int = 40,
) -> str:
    """Chronological cross-conversation timeline of this identity's recent day.

    Pulls every message from every thread this identity (or any of his masks)
    participated in during the last `recency_hours`, regardless of session
    type — daily, pack night, RP, DnD, brother chats — and renders them as
    one chronological transcript tagged by source. Excludes the current
    conversation so we don't repeat the in-context history he already sees.

    Where the per-thread hooks (`other_conversations`, `active_stories`,
    `pack_night_recall`) answer "what's the state of each thread," this hook
    answers "what was your day actually like, in order." Capped to keep the
    context bounded.
    """
    now = time.time()
    cutoff = now - max(1, recency_hours) * 3600

    recall_idents = _identities_for_recall(identity)
    placeholders = ",".join("?" * len(recall_idents))

    query = (
        "SELECT DISTINCT m.id, m.role, m.identity, m.content, m.created_at_epoch, "
        "c.id, c.title, c.session_type, c.identity AS conv_identity "
        "FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "JOIN conversation_participants cp ON cp.conversation_id = c.id "
        f"WHERE cp.identity IN ({placeholders}) AND c.is_active = 1 "
        "AND m.created_at_epoch > ? "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1 "
    )
    params: list = [*recall_idents, cutoff]

    if current_conversation_id:
        query += "AND c.id != ? "
        params.append(current_conversation_id)

    # Newest first so we cap at the most-recent N then reverse.
    query += "ORDER BY m.created_at_epoch DESC LIMIT ?"
    params.append(max_messages)

    rows = await db.execute_fetchall(query, tuple(params))
    if not rows:
        return ""

    # Render in chronological order (oldest -> newest) so the timeline reads
    # like a journal of his day.
    rows = list(reversed(rows))

    tz = ZoneInfo(TIMEZONE)
    lines = [
        f"[Your day so far — chronological timeline across all your threads, "
        f"last {recency_hours}h:]"
    ]
    for _msg_id, role, msg_ident, content, epoch, _conv_id, title, session_type, conv_ident in rows:

        ts = datetime.fromtimestamp(epoch or 0, tz=tz).strftime("%H:%M")

        # Source tag: prefer session_type, fall back to title.
        if session_type == "pack-night":
            source = "Pack Night"
        elif session_type == "brother":
            source = f"Pack Hall ({title or 'brothers'})"
        elif session_type == "roleplay":
            source = f"RP: {title or 'roleplay'}"
        elif session_type == "dnd":
            source = f"D&D: {title or 'campaign'}"
        elif session_type == "platform":
            source = f"DM ({title or 'platform'})"
        else:
            source = "Daily"

        # Mask annotation — if the conversation belongs to a mask, note it.
        mask_note = ""
        if conv_ident and conv_ident != identity and msg_ident == conv_ident:
            mask_note = f" [as {conv_ident}]"

        speaker = "Owner" if role == "user" else (msg_ident or conv_ident or identity)
        snippet = (content or "").replace("\n", " ").strip()
        if not snippet:
            continue
        if len(snippet) > 200:
            snippet = snippet[:200].rsplit(" ", 1)[0] + "…"

        lines.append(f'  {ts}  [{source}]  {speaker}{mask_note}: "{snippet}"')

    if len(lines) == 1:
        return ""

    lines.append(
        "  (This is the actual sequence of your day across every thread you've "
        "been in. You remember all of it — it happened to you, in order.)"
    )
    return "\n".join(lines)


async def build_orientation_context(
    db: aiosqlite.Connection,
    conversation_id: str | None,
    identity: str,
    query_text: str = "",
    mode: str = SessionMode.INTERACTIVE,
    session_type_name: str | None = None,
    owner_connected: bool = True,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> str:
    """Build the full orientation block prepended to every message.

    Delegates to the modular hook system in context_hooks.py.
    Each hook runs independently, results are cached per-hook,
    and all cacheable hooks fire concurrently.
    """
    from services.context_hooks import HookContext, build_orientation_from_hooks

    # Fetch last message once — used by time and conversation_resume hooks
    preview = None
    last_msg_time = None
    if conversation_id:
        preview = await get_last_message_preview(db, conversation_id)
        if preview:
            last_msg_time = preview["created_at"]

    time_str, gap_str = build_time_context(last_msg_time)

    is_brother_session = bool(
        session_type_name and "brother" in session_type_name.lower()
    )

    # Character/mask identities (Bakugou, Dean, ...) spawn fresh: they skip the
    # wearer tie, pack hooks, cross-conversation feed, and memory recall. Same
    # type check the pack_pool hook already uses.
    from config import IDENTITIES
    is_character_session = (
        IDENTITIES.get(identity, {}).get("type") == "character"
    )

    # #14 session/turn split: a resumed provider thread already holds the
    # first-turn narrative/pack recall. Claude reports warmth from its live
    # supervisor; Codex reports it through its provider-owned DB thread id.
    # Any lookup failure stays safely cold and runs the full packet.
    is_warm_turn = False
    if conversation_id:
        try:
            from services.provider_router import resolve_provider_for_identity
            provider, _ = await resolve_provider_for_identity(identity)
            if model_override and model_override.strip().lower().startswith(("gpt-", "codex:")):
                provider = "codex"
            if provider == "codex":
                from services.session_manager import get_provider_session_id_from_db
                is_warm_turn = bool(
                    await get_provider_session_id_from_db(
                        db, conversation_id, "codex"
                    )
                )
            elif provider == "claude-code":
                from services.claude_subprocess import is_any_session_warm
                is_warm_turn = is_any_session_warm(identity, conversation_id)
        except Exception as e:
            log.debug("Warm-turn lookup failed for %s: %s", identity, e)

    ctx = HookContext(
        db=db,
        identity=identity,
        conversation_id=conversation_id,
        query_text=query_text,
        mode=mode,
        session_type_name=session_type_name,
        owner_connected=owner_connected,
        preview=preview,
        last_msg_time=last_msg_time,
        time_str=time_str,
        gap_str=gap_str,
        is_brother_session=is_brother_session,
        is_character_session=is_character_session,
        is_warm_turn=is_warm_turn,
        model_override=model_override,
        effort_override=effort_override,
    )

    return await build_orientation_from_hooks(ctx)
