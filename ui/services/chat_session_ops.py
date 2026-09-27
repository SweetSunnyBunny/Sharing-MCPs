"""Conversation/session operations used by the websocket chat handler."""

# ANAM GUIDE: CONVERSATION HISTORY LOADER
# What: Opens, creates, and pins conversations, and loads past messages for the browser — trimming huge tool results down to short previews so old chats open fast.
# Called by: api/chat.py (the websocket handler) and api/chat_http.py when the chat page loads history, switches identity, or starts a new conversation.
# Edit here when: Old messages load wrong or slow, you want to change how much of a tool result shows in history, or new-conversation/pin behavior needs adjusting.

import json
from copy import deepcopy

from db.database import get_db, release_db
from services.document_visibility import visible_chat_documents
from services.session_manager import (
    get_messages,
    get_or_create_conversation,
    new_conversation,
    new_typed_conversation,
)

_HISTORY_TOOL_TEXT_LIMIT = 4000
_HISTORY_TOOL_INPUT_LIMIT = 2000


def _truncate_text(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n[truncated {len(text) - limit} chars for history view]"


def _compact_tool_for_history(tool: dict) -> dict:
    compact = {}
    for key in ("tool_name", "tool_id", "status"):
        if key in tool:
            compact[key] = tool[key]

    if tool.get("input"):
        compact["input"] = _truncate_text(tool["input"], _HISTORY_TOOL_INPUT_LIMIT)

    content = tool.get("content")
    if isinstance(content, str):
        compact["content"] = _truncate_text(content, _HISTORY_TOOL_TEXT_LIMIT)
    elif content:
        compact["content"] = _truncate_text(content, _HISTORY_TOOL_TEXT_LIMIT)

    return compact


def _compact_history_message(message: dict) -> dict:
    """Return a browser-friendly message payload without mutating DB metadata.

    Stored tool metadata can contain huge raw MCP/agent results. The LLM context
    path still reads the full DB record; history rendering only needs a compact
    preview so chat startup stays responsive.
    """
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return message

    compact_message = dict(message)
    compact_meta = deepcopy(metadata)

    documents = compact_meta.get("documents")
    if isinstance(documents, list):
        compact_meta["documents"] = visible_chat_documents(documents)
        if compact_meta["documents"]:
            compact_meta["document"] = compact_meta["documents"][0]
        else:
            compact_meta.pop("documents", None)
            compact_meta.pop("document", None)

    tools = compact_meta.get("tools")
    if isinstance(tools, list):
        compact_meta["tools"] = [
            _compact_tool_for_history(tool) if isinstance(tool, dict) else tool
            for tool in tools
        ]

    compact_message["metadata"] = compact_meta
    return compact_message


def _compact_history_messages(messages: list[dict]) -> list[dict]:
    return [_compact_history_message(message) for message in messages]


async def switch_identity_conversation(identity: str) -> str:
    """Get or create the active chat conversation for an identity."""
    db = await get_db()
    try:
        return await get_or_create_conversation(db, identity)
    finally:
        await release_db(db)


async def create_conversation(identity: str, session_type: str) -> tuple[str, str]:
    """Create a new conversation and return (conversation_id, session_type)."""
    db = await get_db()
    try:
        if session_type in ("roleplay", "dnd"):
            conversation_id = await new_typed_conversation(db, identity, session_type)
            return conversation_id, session_type
        conversation_id = await new_conversation(db, identity)
        return conversation_id, "chat"
    finally:
        await release_db(db)


async def load_history_payload(
    identity: str,
    conversation_id: str | None,
    limit: int = 50,
) -> tuple[dict, str, str]:
    """Load recent conversation history and return payload plus updated state."""
    limit = max(1, min(int(limit or 50), 500))
    db = await get_db()
    try:
        active_conversation = await get_or_create_conversation(db, identity, conversation_id, include_autowake_daily=True)
        messages = await get_messages(db, active_conversation, limit=limit)
        total_rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (active_conversation,),
        )
        total_count = total_rows[0][0] if total_rows else 0
        has_more = total_count > limit
        st_rows = await db.execute_fetchall(
            "SELECT session_type FROM conversations WHERE id = ?",
            (active_conversation,),
        )
        session_type = (st_rows[0][0] or "chat") if st_rows else "chat"
    finally:
        await release_db(db)

    payload = {
        "type": "history",
        "conversation_id": active_conversation,
        "identity": identity,
        "messages": _compact_history_messages(messages),
        "session_type": session_type,
        "has_more": has_more,
        "total_count": total_count,
    }
    return payload, active_conversation, session_type


async def load_more_payload(conversation_id: str, offset: int, limit: int) -> dict:
    """Load older messages for a conversation."""
    db = await get_db()
    try:
        messages = await get_messages(db, conversation_id, limit=limit, offset=offset)
        total_rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        total_count = total_rows[0][0] if total_rows else 0
        has_more = (offset + limit) < total_count
    finally:
        await release_db(db)

    return {
        "type": "history_older",
        "messages": _compact_history_messages(messages),
        "has_more": has_more,
        "offset": offset,
        "total_count": total_count,
    }


async def prepare_regeneration(conversation_id: str) -> str:
    """Delete the last assistant message and return the preceding user text."""
    from services.session_lifecycle import invalidate_conversation_cache

    db = await get_db()
    last_user_text = ""
    try:
        rows = await db.execute_fetchall(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at_epoch DESC LIMIT 5",
            (conversation_id,),
        )
        last_assistant_id = None
        for row in rows:
            if row[1] == "assistant" and not last_assistant_id:
                last_assistant_id = row[0]
            elif row[1] == "user" and last_assistant_id:
                last_user_text = row[2] or ""
                break
        if last_assistant_id:
            await db.execute(
                "DELETE FROM messages WHERE id = ?",
                (last_assistant_id,),
            )
            await db.commit()
            invalidate_conversation_cache(conversation_id)
    finally:
        await release_db(db)
    return last_user_text


async def update_conversation_pin(conversation_id: str, pinned: bool) -> dict:
    """Persist pinned state for a conversation and return the outbound payload."""
    db = await get_db()
    try:
        meta_rows = await db.execute_fetchall(
            "SELECT metadata FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        meta = json.loads(meta_rows[0][0]) if meta_rows and meta_rows[0][0] else {}
        meta["pinned"] = pinned
        await db.execute(
            "UPDATE conversations SET metadata = ? WHERE id = ?",
            (json.dumps(meta), conversation_id),
        )
        await db.commit()
    finally:
        await release_db(db)

    return {
        "type": "conversation_pinned",
        "conversation_id": conversation_id,
        "pinned": pinned,
    }
