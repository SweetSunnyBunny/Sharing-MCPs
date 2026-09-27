"""Read-only conversation-history queries shared by the Anam context MCP."""

# ANAM GUIDE: CONTEXT MCP HISTORY QUERIES
# What: Read-only lookups into the chat database (search past messages, pull a conversation window) for the anam-context MCP tools the boys use to remember.
# Called by: scripts/anam_context_mcp.py (the standalone anam-context MCP server that runs as its own little process).
# Edit here when: changing what the boys' history-recall tools return — search behavior, message windows, truncation limits. It only reads the database, never writes.

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import DB_PATH

# This module runs inside the standalone anam-context MCP subprocess (stdio
# transport, its own process, no access to the FastAPI app's async loop or
# loaded embedding model) — so semantic recall (#12) reaches the live server
# over loopback HTTP instead of importing services.embedding_service
# directly. Bounded timeout + never-raise: a down/slow server just means
# search_history() falls back to keyword-only, same as before #12 existed.
_SEARCH_API_URL = "http://localhost:8790/api/search"
_SEARCH_API_TIMEOUT_S = 8.0


@contextmanager
def _connect(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w'-]+", query or "", flags=re.UNICODE)
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms[:12])


def _message(row: sqlite3.Row, *, anchor_id: str | None = None) -> dict[str, Any]:
    content = str(row["content"] or "")
    if len(content) > 4000:
        content = content[:4000] + "\n[...message truncated]"
    return {
        "id": row["id"],
        "role": row["role"],
        "identity": row["identity"],
        "created_at": row["created_at"],
        "content": content,
        "is_anchor": row["id"] == anchor_id,
    }


def _conversation_messages(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    anchor_rowid: int | None = None,
    window: int = 5,
) -> list[dict]:
    if anchor_rowid is None:
        rows = conn.execute(
            "SELECT rowid, id, role, identity, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
        return [_message(row) for row in rows]

    before = conn.execute(
        "SELECT rowid, id, role, identity, content, created_at FROM messages "
        "WHERE conversation_id = ? AND rowid < ? ORDER BY rowid DESC LIMIT ?",
        (conversation_id, anchor_rowid, window),
    ).fetchall()[::-1]
    anchor = conn.execute(
        "SELECT rowid, id, role, identity, content, created_at FROM messages WHERE rowid = ?",
        (anchor_rowid,),
    ).fetchall()
    after = conn.execute(
        "SELECT rowid, id, role, identity, content, created_at FROM messages "
        "WHERE conversation_id = ? AND rowid > ? ORDER BY rowid LIMIT ?",
        (conversation_id, anchor_rowid, window),
    ).fetchall()
    anchor_id = anchor[0]["id"] if anchor else None
    return [_message(row, anchor_id=anchor_id) for row in [*before, *anchor, *after]]


def _bookend(conn: sqlite3.Connection, conversation_id: str, *, newest: bool) -> list[dict]:
    direction = "DESC" if newest else "ASC"
    rows = conn.execute(
        "SELECT rowid, id, role, identity, content, created_at FROM messages "
        f"WHERE conversation_id = ? AND role IN ('user', 'assistant') ORDER BY rowid {direction} LIMIT 3",
        (conversation_id,),
    ).fetchall()
    if newest:
        rows = rows[::-1]
    return [_message(row) for row in rows]


def _semantic_hits_via_api(
    query: str, *, identity: str | None, limit: int
) -> list[dict] | None:
    """POST to the live Anam server's /api/search (mode=semantic).

    Returns the raw `results` list (each with at least "id" and
    "conversation_id"), or None on any failure — unreachable server, bad
    JSON, timeout. Never raises.
    """
    body = json.dumps({
        "query": query,
        "identity": identity,
        "limit": limit,
        "mode": "semantic",
    }).encode("utf-8")
    req = urllib.request.Request(
        _SEARCH_API_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_SEARCH_API_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("results")
        return results if isinstance(results, list) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def _semantic_result_blocks(
    conn: sqlite3.Connection,
    hits: list[dict],
    *,
    window: int,
    limit: int,
    seen: set[str],
) -> list[dict]:
    """Turn raw semantic-API hits into the same bookended block shape the
    keyword FTS path already returns, one block per distinct conversation."""
    blocks: list[dict] = []
    for hit in hits:
        if len(blocks) >= limit:
            break
        conversation_id = str(hit.get("conversation_id") or "")
        message_id = str(hit.get("id") or "")
        if not conversation_id or not message_id or conversation_id in seen:
            continue
        anchor = conn.execute(
            "SELECT rowid FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if not anchor:
            continue
        conv_row = conn.execute(
            "SELECT title, identity FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not conv_row:
            continue
        seen.add(conversation_id)
        blocks.append({
            "conversation_id": conversation_id,
            "title": conv_row["title"] or "Untitled",
            "identity": conv_row["identity"],
            "match_message_id": message_id,
            "match_created_at": hit.get("created_at"),
            "match_type": "semantic",
            "bookend_start": _bookend(conn, conversation_id, newest=False),
            "messages": _conversation_messages(
                conn, conversation_id, anchor_rowid=int(anchor["rowid"]), window=window,
            ),
            "bookend_end": _bookend(conn, conversation_id, newest=True),
        })
    return blocks


def search_history(
    query: str,
    *,
    identity: str | None = None,
    speaker: str | None = None,
    limit: int = 5,
    window: int = 5,
    mode: str = "hybrid",
    db_path: Path | str = DB_PATH,
) -> dict:
    """Find distinct conversations and return the match in usable context."""
    match = _fts_query(query)
    if not match:
        return {"ok": False, "error": "query must contain searchable words"}
    limit = max(1, min(int(limit or 5), 10))
    window = max(1, min(int(window or 5), 20))
    want_semantic = mode in ("semantic", "hybrid") and not speaker

    with _connect(db_path) as conn:
        seen: set[str] = set()
        results: list[dict] = []

        if want_semantic:
            hits = _semantic_hits_via_api(query, identity=identity, limit=limit)
            if hits:
                results.extend(
                    _semantic_result_blocks(conn, hits, window=window, limit=limit, seen=seen)
                )

        if mode == "semantic" and results:
            return {"ok": True, "query": query, "count": len(results), "results": results}

        sql = (
            "SELECT m.rowid AS message_rowid, m.id, m.conversation_id, m.role, m.identity, "
            "m.content, m.created_at, c.title, c.identity AS conversation_identity, "
            "bm25(messages_fts) AS rank FROM messages_fts "
            "JOIN messages m ON m.rowid = messages_fts.rowid "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE messages_fts MATCH ?"
        )
        params: list[Any] = [match]
        if identity:
            sql += " AND c.identity = ?"
            params.append(identity)
        if speaker:
            if speaker.strip().lower() in {"owner", "user"}:
                sql += " AND m.role = 'user'"
            else:
                sql += " AND m.role = 'assistant' AND lower(m.identity) = lower(?)"
                params.append(speaker)
        sql += " ORDER BY rank, m.rowid DESC LIMIT 200"

        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError as exc:
            if results:
                return {"ok": True, "query": query, "count": len(results), "results": results}
            return {"ok": False, "error": f"history search failed: {exc}"}

        for row in rows:
            if len(results) >= limit:
                break
            conversation_id = str(row["conversation_id"])
            if conversation_id in seen:
                continue
            seen.add(conversation_id)
            results.append({
                "conversation_id": conversation_id,
                "title": row["title"] or "Untitled",
                "identity": row["conversation_identity"],
                "match_message_id": row["id"],
                "match_created_at": row["created_at"],
                "match_type": "keyword",
                "bookend_start": _bookend(conn, conversation_id, newest=False),
                "messages": _conversation_messages(
                    conn,
                    conversation_id,
                    anchor_rowid=int(row["message_rowid"]),
                    window=window,
                ),
                "bookend_end": _bookend(conn, conversation_id, newest=True),
            })
        return {"ok": True, "query": query, "count": len(results), "results": results}


def read_conversation(
    conversation_id: str,
    *,
    around_message_id: str | None = None,
    window: int = 10,
    db_path: Path | str = DB_PATH,
) -> dict:
    """Read a whole short conversation or a bounded window in a long one."""
    window = max(1, min(int(window or 10), 30))
    with _connect(db_path) as conn:
        conversation = conn.execute(
            "SELECT id, identity, title, created_at, updated_at, session_type "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            return {"ok": False, "error": "conversation not found"}

        anchor_rowid = None
        if around_message_id:
            anchor = conn.execute(
                "SELECT rowid FROM messages WHERE id = ? AND conversation_id = ?",
                (around_message_id, conversation_id),
            ).fetchone()
            if not anchor:
                return {"ok": False, "error": "anchor message not found in conversation"}
            anchor_rowid = int(anchor["rowid"])

        messages = _conversation_messages(
            conn,
            conversation_id,
            anchor_rowid=anchor_rowid,
            window=window,
        )
        truncated = False
        if anchor_rowid is None and len(messages) > 40:
            messages = [*messages[:20], *messages[-10:]]
            truncated = True
        return {
            "ok": True,
            "conversation": dict(conversation),
            "messages": messages,
            "truncated": truncated,
        }


def recent_conversations(
    *,
    identity: str | None = None,
    limit: int = 10,
    db_path: Path | str = DB_PATH,
) -> dict:
    limit = max(1, min(int(limit or 10), 30))
    with _connect(db_path) as conn:
        sql = (
            "SELECT c.id, c.identity, c.title, c.session_type, c.updated_at, "
            "(SELECT content FROM messages m WHERE m.conversation_id = c.id "
            " ORDER BY m.rowid DESC LIMIT 1) AS latest_content "
            "FROM conversations c WHERE c.is_active = 1"
        )
        params: list[Any] = []
        if identity:
            sql += " AND c.identity = ?"
            params.append(identity)
        sql += " ORDER BY c.updated_at_epoch DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return {
            "ok": True,
            "count": len(rows),
            "conversations": [
                {
                    **dict(row),
                    "latest_content": str(row["latest_content"] or "")[:500],
                }
                for row in rows
            ],
        }
