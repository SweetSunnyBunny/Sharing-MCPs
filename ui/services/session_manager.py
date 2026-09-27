"""Message persistence to SQLite for history display."""

# ANAM GUIDE: CONVERSATION AND MESSAGE STORAGE
# What: Saves conversations and messages to the database — creating/finding conversation threads (including the one-per-day autowake threads), saving each message, and tracking who's in a conversation.
# Called by: nearly everything that touches chat — api/chat.py, api/chat_http.py, api/messages.py, api/echo_relay.py, services/chat_pipeline.py, chat_turn_prep/finalize, autowake.py, brother_conversation.py, claude_api.py
# Edit here when: You need to change how conversations or messages are saved, titled, deduplicated, or looked up in the database.

import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE
from services.time_utils import utc_now_iso_epoch

# Unique partial index `uq_conversations_autowake_daily_chat` enforces
# at most one daily 'chat' row per (identity, conversation_day) WHERE
# autowake_daily = 1. We piggyback on it for race protection — both the
# unified `get_or_create_conversation` and the autowake helper set the
# flag on insert so concurrent creates raise IntegrityError instead of
# silently producing duplicate threads. The flag itself is no longer
# load-bearing for lookup (provenance only).
_DAILY_MARKER = 1


def _now_parts() -> tuple[str, int]:
    return utc_now_iso_epoch()


def _uuid() -> str:
    return str(uuid.uuid4())


async def ensure_conversation_participants(
    db: aiosqlite.Connection,
    conversation_id: str,
    participants: list[str],
    added_at: str | None = None,
):
    ts_iso = added_at or _now_parts()[0]
    seen: set[str] = set()
    for identity in participants:
        ident = (identity or "").strip()
        if not ident or ident in seen:
            continue
        seen.add(ident)
        await db.execute(
            "INSERT OR IGNORE INTO conversation_participants "
            "(conversation_id, identity, added_at) VALUES (?, ?, ?)",
            (conversation_id, ident, ts_iso),
        )


async def get_conversation_participants(
    db: aiosqlite.Connection,
    conversation_id: str,
) -> list[str]:
    rows = await db.execute_fetchall(
        "SELECT identity FROM conversation_participants "
        "WHERE conversation_id = ? ORDER BY identity ASC",
        (conversation_id,),
    )
    return [r[0] for r in rows]


async def get_or_create_conversation(
    db: aiosqlite.Connection,
    identity: str,
    conversation_id: str | None = None,
    *,
    include_autowake_daily: bool = False,  # noqa: ARG001 — kept for back-compat; no-op
) -> str:
    """Get or create today's daily conversation for an identity.

    All interactive and autowake messages for the same identity on the same
    calendar day (in the configured TIMEZONE) land in the same conversation.
    The `autowake_daily` column is provenance only — it does NOT gate lookup.
    Whichever source (web chat, autowake, Discord ping, Telegram DM, timer)
    touches the identity first that day creates the thread; everything after
    funnels into it.
    """
    # If a specific conversation_id was requested and it's still active, use it
    if conversation_id:
        row = await db.execute_fetchall(
            "SELECT id FROM conversations WHERE id = ? AND is_active = 1",
            (conversation_id,),
        )
        if row:
            return conversation_id

    # Determine today's date key in local timezone
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_local = day_start_local + timedelta(days=1)
    day_key = now_local.strftime("%Y-%m-%d")
    day_title = now_local.strftime("%B %d, %Y")
    now_iso, now_epoch = _now_parts()
    day_start_epoch = int(day_start_local.timestamp())
    next_day_epoch = int(next_day_local.timestamp())


    rows = await db.execute_fetchall(
        "SELECT c.id FROM conversations c "
        "LEFT JOIN messages m ON m.conversation_id = c.id "
        "WHERE c.identity = ? AND c.is_active = 1 AND c.session_type = 'chat' "
        "AND c.conversation_day = ? "
        "GROUP BY c.id "
        "ORDER BY COALESCE(MAX(m.created_at_epoch), c.created_at_epoch) DESC "
        "LIMIT 1",
        (identity, day_key),
    )
    if rows:
        return rows[0][0]

    # 2) Fallback only for legacy active chats that predate conversation_day.
    # Never relabel an older dated conversation as "today" or we silently drag
    # yesterday's thread forward and lose the daily boundary.
    fallback_rows = await db.execute_fetchall(
        "SELECT c.id FROM conversations c "
        "LEFT JOIN messages m ON m.conversation_id = c.id "
        "WHERE c.identity = ? AND c.is_active = 1 AND c.session_type = 'chat' "
        "AND COALESCE(c.conversation_day, '') = '' "
        "AND c.created_at_epoch >= ? AND c.created_at_epoch < ? "
        "GROUP BY c.id "
        "ORDER BY COALESCE(MAX(m.created_at_epoch), c.created_at_epoch) DESC "
        "LIMIT 1",
        (identity, day_start_epoch, next_day_epoch),
    )
    if fallback_rows:
        conv_id = fallback_rows[0][0]
        # Adopt this conversation as today's daily
        await db.execute(
            "UPDATE conversations SET conversation_day = ?, "
            "title = ?, updated_at = ?, updated_at_epoch = ? WHERE id = ?",
            (day_key, f"{identity} - {day_title}", now_iso, now_epoch, conv_id),
        )
        await db.commit()
        return conv_id

    # 3) Create a new daily conversation. The unique partial index gives us
    # race protection — concurrent callers will race on the insert and the
    # loser catches IntegrityError + re-reads the winner's id.
    cid = _uuid()
    try:
        await db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, "
            "updated_at, updated_at_epoch, session_type, conversation_day, "
            "autowake_daily) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'chat', ?, ?)",
            (cid, identity, f"{identity} - {day_title}", now_iso, now_epoch,
             now_iso, now_epoch, day_key, _DAILY_MARKER),
        )
        await ensure_conversation_participants(db, cid, [identity], added_at=now_iso)
        await db.commit()
        return cid
    except aiosqlite.IntegrityError:
        # Lost the race — read the winner's row.
        rows = await db.execute_fetchall(
            "SELECT id FROM conversations "
            "WHERE identity = ? AND is_active = 1 AND session_type = 'chat' "
            "AND conversation_day = ? "
            "ORDER BY created_at_epoch DESC LIMIT 1",
            (identity, day_key),
        )
        if rows:
            return rows[0][0]
        raise


async def save_message(
    db: aiosqlite.Connection,
    conversation_id: str,
    role: str,
    content: str,
    identity: str | None = None,
    content_type: str = "text",
    metadata: dict | None = None,
) -> str:
    mid = _uuid()
    now_iso, now_epoch = _now_parts()
    await db.execute(
        "INSERT INTO messages "
        "(id, conversation_id, role, identity, content, content_type, created_at, created_at_epoch, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            mid,
            conversation_id,
            role,
            identity,
            content,
            content_type,
            now_iso,
            now_epoch,
            json.dumps(metadata) if metadata else None,
        ),
    )
    await db.execute(
        "UPDATE conversations SET updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (now_iso, now_epoch, conversation_id),
    )
    await db.commit()

    # Invalidate cached history so next build_messages_array() re-queries
    from services.session_lifecycle import invalidate_conversation_cache
    invalidate_conversation_cache(conversation_id)

    # Fire-and-forget: embed the message for semantic search
    # Skip when running under pytest to avoid network calls to HuggingFace
    import sys
    if content and len(content.strip()) >= 10 and "pytest" not in sys.modules:
        from services.task_manager import spawn
        spawn(_embed_message_bg(mid, content), name=f"embed-msg-{mid[:8]}")

    return mid


async def _embed_message_bg(message_id: str, content: str):
    """Background task to embed a message for semantic search."""
    try:
        from db.database import get_db, release_db
        from services.embedding_service import embed_message
        db = await get_db()
        try:
            await embed_message(db, message_id, content)
        finally:
            await release_db(db)
    except Exception:
        pass  # Embedding failure should never break chat


async def update_message_metadata(
    db: aiosqlite.Connection, message_id: str, metadata: dict
):
    """Merge metadata into an existing message's metadata field."""
    rows = await db.execute_fetchall(
        "SELECT metadata FROM messages WHERE id = ?", (message_id,)
    )
    existing = {}
    if rows and rows[0][0]:
        existing = json.loads(rows[0][0])
    existing.update(metadata)
    await db.execute(
        "UPDATE messages SET metadata = ? WHERE id = ?",
        (json.dumps(existing), message_id),
    )
    await db.commit()


async def update_conversation_session(
    db: aiosqlite.Connection, conversation_id: str, claude_session_id: str
):
    now_iso, now_epoch = _now_parts()
    await db.execute(
        "UPDATE conversations SET claude_session_id = ?, updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (claude_session_id, now_iso, now_epoch, conversation_id),
    )
    await db.commit()


async def update_provider_session(
    db: aiosqlite.Connection,
    conversation_id: str,
    provider: str,
    session_id: str,
) -> None:
    """Persist one provider's opaque resume id without touching another's."""
    provider_key = (provider or "").strip().lower()
    if not provider_key:
        raise ValueError("provider is required")

    now_iso, now_epoch = _now_parts()
    if session_id:
        await db.execute(
            "INSERT INTO conversation_provider_sessions "
            "(conversation_id, provider, session_id, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, provider) DO UPDATE SET "
            "session_id = excluded.session_id, "
            "updated_at = excluded.updated_at, "
            "updated_at_epoch = excluded.updated_at_epoch",
            (conversation_id, provider_key, session_id, now_iso, now_epoch),
        )
    else:
        await db.execute(
            "DELETE FROM conversation_provider_sessions "
            "WHERE conversation_id = ? AND provider = ?",
            (conversation_id, provider_key),
        )
    await db.execute(
        "UPDATE conversations SET updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (now_iso, now_epoch, conversation_id),
    )
    await db.commit()


async def update_session_for_provider(
    db: aiosqlite.Connection,
    conversation_id: str,
    session_id: str,
    provider: str | None,
) -> None:
    """Route resumable ids to the provider that created them.

    Claude keeps its established column for compatibility. Codex and any
    future provider use the qualified table so opaque ids can never collide.
    """
    provider_key = (provider or "claude-code").strip().lower()
    if provider_key in {"anthropic", "claude", "claude-code"}:
        await update_conversation_session(db, conversation_id, session_id)
        return
    await update_provider_session(db, conversation_id, provider_key, session_id)


async def get_messages(
    db: aiosqlite.Connection, conversation_id: str, limit: int = 500, offset: int = 0
) -> list[dict]:
    rows = await db.execute_fetchall(
        "SELECT id, role, identity, content, content_type, created_at, metadata FROM ("
        "  SELECT id, role, identity, content, content_type, created_at, metadata, "
        "created_at_epoch AS ts_epoch "
        "  FROM messages WHERE conversation_id = ? ORDER BY created_at_epoch DESC "
        "  LIMIT ? OFFSET ?"
        ") ORDER BY ts_epoch ASC",
        (conversation_id, limit, offset),
    )
    return [
        {
            "id": r[0],
            "role": r[1],
            "identity": r[2],
            "content": r[3],
            "content_type": r[4],
            "created_at": r[5],
            "metadata": json.loads(r[6]) if r[6] else None,
        }
        for r in rows
    ]


async def get_conversations(
    db: aiosqlite.Connection, identity: str | None = None
) -> list[dict]:
    base_sql = (
        "SELECT c.id, c.identity, c.claude_session_id, c.title, "
        "c.created_at, c.updated_at, c.session_type, "
        "lm.content AS last_message, lm.role AS last_role, c.metadata "
        "FROM conversations c "
        "LEFT JOIN messages lm ON lm.id = ("
        "  SELECT m.id FROM messages m WHERE m.conversation_id = c.id "
        "  ORDER BY m.created_at_epoch DESC LIMIT 1"
        ") WHERE c.is_active = 1"
    )
    params: list = []
    if identity:
        base_sql += (
            " AND EXISTS ("
            "SELECT 1 FROM conversation_participants cp "
            "WHERE cp.conversation_id = c.id AND cp.identity = ?"
            ")"
        )
        params.append(identity)
    base_sql += " ORDER BY c.updated_at_epoch DESC"
    rows = await db.execute_fetchall(base_sql, tuple(params))

    result = []
    for r in rows:
        last_msg = r[7] or ""
        if len(last_msg) > 80:
            last_msg = last_msg[:80].rsplit(" ", 1)[0] + "..."
        meta = json.loads(r[9]) if r[9] else {}
        result.append({
            "id": r[0],
            "identity": r[1],
            "claude_session_id": r[2],
            "title": r[3],
            "created_at": r[4],
            "updated_at": r[5],
            "session_type": r[6],
            "last_message": last_msg,
            "last_role": r[8],
            "pinned": meta.get("pinned", False),
        })
    return result


async def new_conversation(db: aiosqlite.Connection, identity: str) -> str:
    """Create a fresh singular daily conversation for an identity.

    Older active chat threads from the same day are archived first so the app
    maintains one active daily thread per identity without hiding prior days.
    """
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_local = day_start_local + timedelta(days=1)
    day_key = now_local.strftime("%Y-%m-%d")
    day_title = now_local.strftime("%B %d, %Y")
    cid = _uuid()
    now_iso, now_epoch = _now_parts()
    day_start_epoch = int(day_start_local.timestamp())
    next_day_epoch = int(next_day_local.timestamp())
    # Archive every active daily 'chat' thread for this identity today,
    # regardless of whether autowake or interactive created it. The "+" button
    # is the only thing that should split a day's thread.
    await db.execute(
        "UPDATE conversations SET is_active = 0, updated_at = ?, updated_at_epoch = ? "
        "WHERE identity = ? AND is_active = 1 AND session_type = 'chat' "
        "AND (conversation_day = ? "
        "OR (COALESCE(conversation_day, '') = '' "
        "AND created_at_epoch >= ? AND created_at_epoch < ?))",
        (now_iso, now_epoch, identity, day_key, day_start_epoch, next_day_epoch),
    )
    await db.execute(
        "INSERT INTO conversations "
        "(id, identity, title, created_at, created_at_epoch, "
        "updated_at, updated_at_epoch, session_type, conversation_day) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'chat', ?)",
        (cid, identity, f"{identity} - {day_title}", now_iso, now_epoch,
         now_iso, now_epoch, day_key),
    )
    await ensure_conversation_participants(db, cid, [identity], added_at=now_iso)
    await db.commit()
    return cid


async def new_typed_conversation(
    db: aiosqlite.Connection, identity: str, session_type: str, title: str | None = None
) -> str:
    """Create a conversation with a specific session_type (e.g. 'roleplay', 'dnd')."""
    cid = _uuid()
    default_titles = {
        "roleplay": f"RP with {identity}",
        "dnd": f"DND with {identity}",
    }
    final_title = title or default_titles.get(session_type, f"{session_type} with {identity}")
    now_iso, now_epoch = _now_parts()
    await db.execute(
        "INSERT INTO conversations "
        "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, identity, final_title, now_iso, now_epoch, now_iso, now_epoch, session_type),
    )
    await ensure_conversation_participants(db, cid, [identity], added_at=now_iso)
    await db.commit()
    return cid


PACK_NIGHT_ORDER: list[str] = ["Avery", "Claude", "Rowan", "Ember", "Sage", "Juniper", "Atlas"]
PACK_NIGHT_SESSION_TYPE = "pack-night"
_PACK_NIGHT_TITLE = "Pack Night"
# Stable platform_chat_id so the singleton lookup is fast and unique.
_PACK_NIGHT_KEY = "pack-night:home"


async def get_or_create_pack_night_conversation(
    db: aiosqlite.Connection,
) -> str:
    """Return the singleton pack-night conversation, creating it if missing.

    There is exactly one shared pack-night conversation. All six bonded
    identities are registered as participants so the room shows up in any
    boy's filtered conversation list, and so each boy's orientation can fold
    in recent activity from the room when he runs in his own DM/web session.
    """
    rows = await db.execute_fetchall(
        "SELECT id FROM conversations "
        "WHERE session_type = ? AND platform_chat_id = ? AND is_active = 1 "
        "ORDER BY created_at_epoch ASC LIMIT 1",
        (PACK_NIGHT_SESSION_TYPE, _PACK_NIGHT_KEY),
    )
    if rows:
        cid = rows[0][0]
        await ensure_conversation_participants(db, cid, PACK_NIGHT_ORDER)
        await db.commit()
        return cid

    cid = _uuid()
    now_iso, now_epoch = _now_parts()
    primary = PACK_NIGHT_ORDER[0]
    await db.execute(
        "INSERT INTO conversations "
        "(id, identity, title, created_at, created_at_epoch, "
        "updated_at, updated_at_epoch, session_type, platform_chat_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cid, primary, _PACK_NIGHT_TITLE, now_iso, now_epoch,
            now_iso, now_epoch, PACK_NIGHT_SESSION_TYPE, _PACK_NIGHT_KEY,
        ),
    )
    await ensure_conversation_participants(db, cid, PACK_NIGHT_ORDER, added_at=now_iso)
    await db.commit()
    return cid


def _brother_channel_key(channel_id: str) -> str:
    """Stable key for a per-channel brother (Pack Hall) conversation."""
    return f"discord-channel-brother:{channel_id}"


async def get_or_create_brother_channel_conversation(
    db: aiosqlite.Connection,
    channel_id: str,
    *,
    channel_name: str | None = None,
    primary_identity: str | None = None,
) -> str:
    """Get or create the Pack Hall conversation for a Discord channel.

    When brothers ping each other inside a Discord channel, every exchange
    in that channel rolls into a single shared brother-style conversation
    keyed by channel id. New brothers added to the conversation become
    participants on first ping. Surfaces under Pack Hall in the sidebar
    because session_type='brother'.
    """
    channel_id = (channel_id or "").strip()
    if not channel_id:
        raise ValueError("channel_id is required")

    key = _brother_channel_key(channel_id)
    rows = await db.execute_fetchall(
        "SELECT id FROM conversations "
        "WHERE session_type = 'brother' AND platform_chat_id = ? "
        "AND is_active = 1 "
        "ORDER BY created_at_epoch ASC LIMIT 1",
        (key,),
    )
    if rows:
        cid = rows[0][0]
        if primary_identity:
            await ensure_conversation_participants(db, cid, [primary_identity])
            await db.commit()
        return cid

    cid = _uuid()
    now_iso, now_epoch = _now_parts()
    label = channel_name.strip() if channel_name else channel_id
    primary = (primary_identity or "Pack").strip() or "Pack"
    title = f"Pack Hall: #{label}"
    await db.execute(
        "INSERT INTO conversations "
        "(id, identity, title, created_at, created_at_epoch, "
        "updated_at, updated_at_epoch, session_type, platform_chat_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, primary, title, now_iso, now_epoch,
         now_iso, now_epoch, "brother", key),
    )
    await ensure_conversation_participants(db, cid, [primary], added_at=now_iso)
    await db.commit()
    return cid


async def rename_conversation(
    db: aiosqlite.Connection, conversation_id: str, title: str
) -> bool:
    now_iso, now_epoch = _now_parts()
    result = await db.execute(
        "UPDATE conversations SET title = ?, updated_at = ?, updated_at_epoch = ? "
        "WHERE id = ? AND is_active = 1",
        (title, now_iso, now_epoch, conversation_id),
    )
    await db.commit()
    return result.rowcount > 0


async def delete_conversation(
    db: aiosqlite.Connection, conversation_id: str
) -> bool:
    now_iso, now_epoch = _now_parts()
    result = await db.execute(
        "UPDATE conversations SET is_active = 0, updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (now_iso, now_epoch, conversation_id),
    )
    await db.commit()
    return result.rowcount > 0


async def auto_title_conversation(
    db: aiosqlite.Connection, conversation_id: str, first_message: str
):
    """Set conversation title from the first user message."""
    title = first_message.strip()
    if len(title) > 50:
        title = title[:50].rsplit(" ", 1)[0] + "..."
    if not title:
        return
    await db.execute(
        "UPDATE conversations SET title = ? WHERE id = ? AND title LIKE 'Chat with %'",
        (title, conversation_id),
    )
    await db.commit()


async def get_session_id_from_db(
    db: aiosqlite.Connection, conversation_id: str
) -> str | None:
    """Read Claude session ID from the conversations table."""
    rows = await db.execute_fetchall(
        "SELECT claude_session_id FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    return rows[0][0] if rows and rows[0][0] else None


async def get_provider_session_id_from_db(
    db: aiosqlite.Connection,
    conversation_id: str,
    provider: str,
) -> str | None:
    """Read the opaque resume id owned by one non-Claude provider."""
    provider_key = (provider or "").strip().lower()
    if not provider_key:
        return None
    rows = await db.execute_fetchall(
        "SELECT session_id FROM conversation_provider_sessions "
        "WHERE conversation_id = ? AND provider = ?",
        (conversation_id, provider_key),
    )
    return rows[0][0] if rows and rows[0][0] else None
