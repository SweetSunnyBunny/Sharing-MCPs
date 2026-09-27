"""REST API for search across message history — keyword (FTS5) and semantic (embeddings)."""

# ANAM GUIDE: MESSAGE SEARCH API
# What: Searches the whole chat history three ways — exact words (keyword), by meaning (semantic), or both (hybrid) — plus endpoints to check and backfill the embeddings that power semantic search.
# Called by: the search box in static/js/chat.js; also the anam-context MCP (services/anam_context_queries.py) calls it over localhost so the boys can search their own history.
# Edit here when: changing search modes, result limits, or how hybrid results get merged. The actual search machinery lives in services/search_service.py and services/embedding_service.py.

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db.database import get_db, release_db
from services.search_service import search_messages

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])

_backfill_running = False


@router.post("")
async def api_search(request: Request):
    """Search messages using FTS5 (keyword) or embeddings (semantic).

    Body: {"query": "...", "identity": "optional", "limit": 20, "mode": "keyword|semantic|hybrid"}
    - mode "keyword" (default): FTS5 full-text search
    - mode "semantic": vector similarity search
    - mode "hybrid": both, merged and deduplicated
    Returns: list of search results with previews and metadata.
    """
    body = await request.json()
    query = body.get("query", "").strip()
    identity = body.get("identity")
    limit = min(body.get("limit", 20), 50)
    mode = body.get("mode", "keyword")

    if not query:
        return JSONResponse(content={"results": [], "query": "", "mode": mode})

    db = await get_db()
    try:
        if mode == "semantic":
            from services.embedding_service import semantic_search
            results = await semantic_search(db, query, identity=identity, limit=limit)
        elif mode == "hybrid":
            from services.embedding_service import semantic_search
            keyword_results = await search_messages(db, query, identity=identity, limit=limit)
            semantic_results = await semantic_search(db, query, identity=identity, limit=limit)
            # Merge: semantic first, then keyword results not already present
            seen_ids = set()
            results = []
            for r in semantic_results:
                seen_ids.add(r["id"])
                r["match_type"] = "semantic"
                results.append(r)
            for r in keyword_results:
                if r["id"] not in seen_ids:
                    r["match_type"] = "keyword"
                    results.append(r)
            results = results[:limit]
        else:
            results = await search_messages(db, query, identity=identity, limit=limit)

        return JSONResponse(content={
            "results": results,
            "query": query,
            "count": len(results),
            "mode": mode,
        })
    finally:
        await release_db(db)


@router.post("/embeddings/status")
async def embedding_status(request: Request):
    """Check embedding coverage — how many messages are embedded vs total."""
    from services.embedding_service import get_embed_failure_count

    db = await get_db()
    try:
        total_rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM messages WHERE length(content) >= 10"
        )
        embedded_rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM message_embeddings"
        )
        total = total_rows[0][0] if total_rows else 0
        embedded = embedded_rows[0][0] if embedded_rows else 0
        return JSONResponse(content={
            "total_messages": total,
            "embedded_messages": embedded,
            "coverage_pct": round(embedded / total * 100, 1) if total > 0 else 0,
            "embed_failures": get_embed_failure_count(),
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    finally:
        await release_db(db)


@router.post("/embeddings/backfill")
async def trigger_backfill(request: Request):
    """Manually trigger embedding backfill for unembedded messages."""
    import asyncio
    from services.embedding_service import run_backfill_loop

    global _backfill_running
    if _backfill_running:
        return JSONResponse(content={"status": "backfill_already_running"})

    async def _guarded_backfill():
        global _backfill_running
        try:
            await run_backfill_loop()
        finally:
            _backfill_running = False

    _backfill_running = True
    asyncio.create_task(_guarded_backfill())
    return JSONResponse(content={"status": "backfill_started"})
