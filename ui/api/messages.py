"""REST: conversation history for display."""

# ANAM GUIDE: CONVERSATION HISTORY API
# What: Lets the browser list, read, rename, and delete conversations, plus save emoji reactions and curated memories on messages.
# Called by: static/js/chat.js, static/js/sidebar.js, and static/js/hub.js (the heavy lifting lives in services/session_manager.py and services/personal_state.py).
# Edit here when: adding a new emoji to the allowed reactions list (VALID_REACTIONS below) or adding a new conversation-history endpoint.

import json

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db.database import get_db, release_db
from services.rate_limit import limiter
from services.personal_state import create_curated_memory, delete_curated_memory, list_curated_memories
from services.session_manager import (
    get_messages, get_conversations, rename_conversation, delete_conversation,
    update_message_metadata,
)

router = APIRouter(prefix="/api/messages")

VALID_REACTIONS = {
    # Legacy string keys (backward compat with existing DB data)
    "heart", "star", "flame",
    # Emoji characters
    "\u2764\ufe0f", "\u2b50", "\U0001f525",
    "\U0001f923", "\U0001f970", "\U0001f605", "\U0001f979",
    "\U0001f92f", "\U0001f914", "\U0001f4aa\U0001f3fb",
    "\u2764\ufe0f\u200d\U0001f525",
    "\U0001f62d", "\U0001f43a", "\U0001f3a8", "\U0001f409",
    "\U0001f989", "\U0001f98b", "\U0001fabd",
    "\U0001f913", "\U0001f440", "\U0001f60d", "\U0001f97a",
    # Added batch 2
    "\U0001fae0", "\U0001fac2", "\U0001f624", "\U0001f608",
    "\U0001f480", "\U0001f60c", "\U0001f924", "\U0001f407",
    "\u270d\U0001f3fc", "\U0001f319",
}


@router.get("/conversations")
async def list_conversations(identity: str = None):
    db = await get_db()
    try:
        convos = await get_conversations(db, identity)
        return {"conversations": convos}
    finally:
        await release_db(db)


@router.get("/conversations/{conversation_id}")
async def get_conversation_messages(
    conversation_id: str, limit: int = 100, offset: int = 0
):
    db = await get_db()
    try:
        messages = await get_messages(db, conversation_id, limit, offset)
        return {"messages": messages}
    finally:
        await release_db(db)


@router.put("/conversations/{conversation_id}/rename")
async def rename_convo(conversation_id: str, title: str = Body(..., embed=True)):
    db = await get_db()
    try:
        ok = await rename_conversation(db, conversation_id, title)
        return {"ok": ok}
    finally:
        await release_db(db)


@router.delete("/conversations/{conversation_id}")
async def delete_convo(conversation_id: str):
    db = await get_db()
    try:
        ok = await delete_conversation(db, conversation_id)
        return {"ok": ok}
    finally:
        await release_db(db)


# ── Reactions ──

class ReactionBody(BaseModel):
    reaction: str
    reactor: str = "Owner"


@router.post("/{message_id}/react")
@limiter.limit("30/minute")
async def toggle_reaction(request: Request, message_id: str, body: ReactionBody):
    """Toggle a reaction on a message. Owner or any identity can react."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT metadata, conversation_id, role, identity, content "
            "FROM messages WHERE id = ?", (message_id,)
        )
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Message not found"})

        meta = json.loads(rows[0][0]) if rows[0][0] else {}
        conversation_id = rows[0][1]
        msg_role = rows[0][2]
        msg_identity = rows[0][3]
        msg_content = rows[0][4] or ""
        reactions = meta.get("reactions", {})


        if body.reaction not in VALID_REACTIONS and body.reaction not in reactions:
            return JSONResponse(status_code=400, content={"error": f"Invalid reaction. Use: {', '.join(VALID_REACTIONS)}"})

        # Each reaction is a list of reactor names
        reactors = reactions.get(body.reaction, [])
        if body.reactor in reactors:
            reactors.remove(body.reactor)
            added = False
        else:
            reactors.append(body.reactor)
            added = True
        reactions[body.reaction] = reactors
        meta["reactions"] = reactions

        await db.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta), message_id),
        )
        await db.commit()


        if added and body.reactor == "Owner" and msg_role == "assistant" and msg_identity:
            preview = msg_content.strip().replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60].rsplit(" ", 1)[0] + "…"
            try:
                from services.reaction_notices import queue_reaction_notice
                queue_reaction_notice(conversation_id, msg_identity, body.reaction, preview)
            except Exception:
                pass

        # Bust this conversation's context-hook cache so the identity sees the
        # reaction in its next turn instead of a stale (<=120s) cached copy.
        if conversation_id:
            try:
                from services.context_hooks import invalidate_hook_cache_for_conversation
                invalidate_hook_cache_for_conversation(conversation_id)
            except Exception:
                pass

        return {"ok": True, "reactions": reactions}
    finally:
        await release_db(db)


# ── Bookmark ──

@router.post("/{message_id}/bookmark")
async def toggle_bookmark(message_id: str):
    """Toggle bookmark on a message."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT metadata FROM messages WHERE id = ?", (message_id,)
        )
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Message not found"})

        meta = json.loads(rows[0][0]) if rows[0][0] else {}
        meta["bookmarked"] = not meta.get("bookmarked", False)

        await db.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta), message_id),
        )
        await db.commit()
        return {"ok": True, "bookmarked": meta["bookmarked"]}
    finally:
        await release_db(db)


# ── Search ──

class RememberBody(BaseModel):
    memory_type: str = "insight"
    summary: str
    detail: str = ""
    identity: str | None = None


@router.post("/{message_id}/remember")
async def remember_message(message_id: str, body: RememberBody):
    db = await get_db()
    try:
        memory = await create_curated_memory(
            db,
            message_id=message_id,
            memory_type=body.memory_type,
            summary=body.summary,
            detail=body.detail,
            identity=body.identity,
        )
        await update_message_metadata(db, message_id, {"remembered": True})
        return {"ok": True, "memory": memory}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.delete("/memories/{memory_id}")
async def remove_memory(memory_id: str):
    """Delete a curated memory (archives it and any auto-promoted profile fact)."""
    db = await get_db()
    try:
        ok = await delete_curated_memory(db, memory_id=memory_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Memory not found"})
        return {"ok": True}
    finally:
        await release_db(db)


@router.get("/memories")
async def list_memories(identity: str | None = None, limit: int = 20):
    db = await get_db()
    try:
        memories = await list_curated_memories(
            db,
            identity=identity,
            limit=max(1, min(limit, 50)),
        )
        return {"memories": memories}
    finally:
        await release_db(db)


@router.get("/search")
async def search_messages(q: str = "", identity: str = None, limit: int = 20):
    """Search messages using FTS5 full-text search with LIKE fallback."""
    if not q or len(q.strip()) < 2:
        return {"results": []}

    db = await get_db()
    try:
        # Try FTS5 first (faster, better ranking)
        results = await _fts5_search(db, q.strip(), identity, limit)
        if results is not None:
            return {"results": results}

        # Fallback to LIKE if FTS table doesn't exist yet
        return {"results": await _like_search(db, q.strip(), identity, limit)}
    finally:
        await release_db(db)


async def _fts5_search(db, query: str, identity: str | None, limit: int):
    """FTS5 full-text search with snippets and ranking."""
    try:
        # Sanitize: wrap each word in quotes for FTS5 safety
        words = query.split()
        fts_query = " ".join(f'"{w}"' for w in words)

        identity_clause = ""
        params: list = [fts_query]
        if identity:
            identity_clause = "AND m.identity = ? "
            params.append(identity)
        params.append(limit)

        rows = await db.execute_fetchall(
            f"""
            SELECT
                m.id, m.conversation_id, c.title, m.identity, m.role,
                snippet(messages_fts, 0, '<mark>', '</mark>', '...', 40),
                m.created_at
            FROM messages_fts
            JOIN messages m ON m.rowid = messages_fts.rowid
            JOIN conversations c ON c.id = m.conversation_id
            WHERE messages_fts MATCH ?
            {identity_clause}
            ORDER BY rank
            LIMIT ?
            """,
            tuple(params),
        )

        results = []
        for row in rows:
            results.append({
                "conversation_id": row[1],
                "conversation_title": row[2] or "",
                "identity": row[3],
                "message_id": row[0],
                "role": row[4],
                "snippet": row[5],
                "created_at": row[6],
            })
        return results
    except Exception:
        return None  # FTS table may not exist — fall back to LIKE


async def _like_search(db, query: str, identity: str | None, limit: int):
    """Fallback LIKE-based search."""
    keyword = f"%{query}%"
    sql = (
        "SELECT m.id, m.conversation_id, c.title, m.identity, m.role, "
        "m.content, m.created_at "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE (m.content LIKE ? OR c.title LIKE ?) "
    )
    params: list = [keyword, keyword]
    if identity:
        sql += "AND m.identity = ? "
        params.append(identity)
    sql += "ORDER BY m.created_at_epoch DESC LIMIT ?"
    params.append(limit)

    rows = await db.execute_fetchall(sql, tuple(params))
    results = []
    needle = query.lower()
    for row in rows:
        mid, conv_id, title, ident, role, content, created_at = row
        snippet = ""
        if content:
            lower = content.lower()
            idx = lower.find(needle)
            if idx >= 0:
                start = max(0, idx - 50)
                end = min(len(content), idx + len(needle) + 50)
                snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
            else:
                snippet = content[:100] + ("..." if len(content) > 100 else "")
        results.append({
            "conversation_id": conv_id,
            "conversation_title": title or "",
            "identity": ident,
            "message_id": mid,
            "role": role,
            "snippet": snippet,
            "created_at": created_at,
        })
    return results
