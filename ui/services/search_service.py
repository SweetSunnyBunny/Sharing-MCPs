"""Semantic search over message history using SQLite FTS5.

Uses the messages_fts virtual table (created by migration 009) for
full-text search, with optional identity filtering.
"""

# ANAM GUIDE: MESSAGE HISTORY TEXT SEARCH
# What: Keyword (full-text) search over saved chat messages — powers the "find that thing we said" lookups, with filters for identity, speaker, and date range.
# Called by: api/search.py (the /api/search route, used by the search box in static/js/chat.js), services/claude_api.py
# Edit here when: You want search results ranked, filtered, or previewed differently. (Semantic/meaning search lives in services/embedding_service.py, not here.)

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE

log = logging.getLogger(__name__)


async def search_messages(
    db: aiosqlite.Connection,
    query: str,
    identity: str | None = None,
    limit: int = 20,
    speaker: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Search messages using FTS5 MATCH.

    Filters:
      identity — scope to conversations owned by this identity
      speaker — "user"/"owner" or an identity name (who said it)
      after/before — YYYY-MM-DD, inclusive date window on created_at

    Returns a list of results with message id, content preview,
    identity, timestamp, conversation_id, and relevance rank.
    """
    if not query or not query.strip():
        return []

    # Sanitize query for FTS5 — escape special chars, use prefix matching
    clean_query = _sanitize_fts_query(query.strip())
    if not clean_query:
        return []

    # FTS5 MATCH query with rank ordering
    sql = """
        SELECT m.id, m.content, m.identity, m.role, m.created_at,
               m.conversation_id, c.title, c.identity as conv_identity,
               rank
        FROM messages_fts fts
        JOIN messages m ON m.rowid = fts.rowid
        JOIN conversations c ON m.conversation_id = c.id
        WHERE messages_fts MATCH ?
    """
    params = [clean_query]

    if identity:
        sql += " AND c.identity = ?"
        params.append(identity)

    if speaker:
        s = speaker.strip().lower()
        if s in ("user", "owner"):
            sql += " AND m.role = 'user'"
        else:
            sql += " AND m.role = 'assistant' AND lower(m.identity) = ?"
            params.append(s)

    if after:
        sql += " AND m.created_at >= ?"
        params.append(after)
    if before:
        sql += " AND m.created_at < ?"
        # Treat YYYY-MM-DD as exclusive end-of-day (< YYYY-MM-DD+1 logic handled by caller)
        params.append(before)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    try:
        rows = await db.execute_fetchall(sql, tuple(params))
    except Exception as e:
        log.warning("FTS5 search failed for query '%s': %s", query, e)
        return []

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    results = []

    for row in rows:
        msg_id, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity, rank = row

        # Build preview
        preview = (content or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200].rsplit(" ", 1)[0] + "..."

        # Time ago
        try:
            msg_dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).astimezone(tz)
            secs = (now - msg_dt).total_seconds()
            if secs < 3600:
                ago = f"{max(1, int(secs / 60))}m ago"
            elif secs < 86400:
                ago = f"{secs / 3600:.0f}h ago"
            else:
                ago = f"{int(secs / 86400)}d ago"
            formatted_time = msg_dt.strftime("%b %d, %I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            ago = ""
            formatted_time = created_at or ""

        speaker = "Owner" if role == "user" else (msg_identity or conv_identity or "Unknown")

        results.append({
            "id": msg_id,
            "content_preview": preview,
            "speaker": speaker,
            "role": role,
            "identity": conv_identity,
            "conversation_id": conv_id,
            "conversation_title": conv_title or "Untitled",
            "created_at": created_at,
            "time_ago": ago,
            "formatted_time": formatted_time,
            "rank": rank,
        })

    return results


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a query string for FTS5 MATCH.

    Removes special FTS5 operators and wraps terms for prefix matching.
    """
    # Remove FTS5 special characters
    for char in ['"', "'", "*", "(", ")", ":", "^", "{", "}", "~"]:
        query = query.replace(char, " ")

    # Split into terms and wrap each for prefix matching
    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return ""

    # Use simple term matching (FTS5 implicit AND)
    return " ".join(f'"{t}"' for t in terms[:10])  # Cap at 10 terms
