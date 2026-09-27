"""REST API for the persistent Canvas/artifact system (#32)."""

# ANAM GUIDE: CANVAS LIBRARY ROUTES
# What: /api/canvases — list, open, delete, share, and unshare the saved <canvas> artifacts the boys make in chat.
# Called by: static/js/canvas.js (the canvas library UI on the chat page); new canvases are SAVED by services/canvas_store.py, not here.
# Edit here when: Changing who can see/share a canvas, pagination, or adding library features. For how canvases get created, look in canvas_store.py.

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import IDENTITIES
from db.database import get_db, release_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/canvases", tags=["canvases"])

_MAX_LIMIT = 100


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "identity": row["identity"],
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "content": row["content"],
        "source_message_id": row["source_message_id"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
async def list_canvases(identity: str, limit: int = 20, offset: int = 0):
    """Canvases visible to `identity`: owned by them, or shared with them.
    Honest pagination -- returns total count alongside the page."""
    if identity not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {identity}"})
    limit = max(1, min(limit, _MAX_LIMIT))
    offset = max(0, offset)

    db = await get_db()
    try:
        total_rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM canvases c "
            "LEFT JOIN canvas_shares s ON s.canvas_id = c.id AND s.shared_with_identity = ? "
            "WHERE c.identity = ? OR s.shared_with_identity IS NOT NULL",
            (identity, identity),
        )
        total = total_rows[0][0] if total_rows else 0

        rows = await db.execute_fetchall(
            "SELECT c.*, "
            "  CASE WHEN c.identity = ? THEN NULL ELSE c.identity END AS shared_by "
            "FROM canvases c "
            "LEFT JOIN canvas_shares s ON s.canvas_id = c.id AND s.shared_with_identity = ? "
            "WHERE c.identity = ? OR s.shared_with_identity IS NOT NULL "
            "ORDER BY c.pinned DESC, c.created_at_epoch DESC "
            "LIMIT ? OFFSET ?",
            (identity, identity, identity, limit, offset),
        )
        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["shared_by"] = row["shared_by"]
            items.append(item)

        return {"items": items, "total": total, "limit": limit, "offset": offset}
    finally:
        await release_db(db)


async def _load_canvas_if_visible(db, canvas_id: int, identity: str):
    rows = await db.execute_fetchall(
        "SELECT * FROM canvases WHERE id = ?", (canvas_id,),
    )
    if not rows:
        return None, "not_found"
    row = rows[0]
    if row["identity"] == identity:
        return row, None
    share_rows = await db.execute_fetchall(
        "SELECT 1 FROM canvas_shares WHERE canvas_id = ? AND shared_with_identity = ?",
        (canvas_id, identity),
    )
    if share_rows:
        return row, None
    return None, "forbidden"


@router.get("/{canvas_id}")
async def get_canvas(canvas_id: int, identity: str):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {identity}"})

    db = await get_db()
    try:
        row, error = await _load_canvas_if_visible(db, canvas_id, identity)
        if error == "not_found":
            return JSONResponse(status_code=404, content={"error": "Canvas not found"})
        if error == "forbidden":
            return JSONResponse(status_code=403, content={"error": "Not visible to this identity"})
        item = _row_to_dict(row)
        item["shared_by"] = None if row["identity"] == identity else row["identity"]
        return item
    finally:
        await release_db(db)


@router.delete("/{canvas_id}")
async def delete_canvas(canvas_id: int, identity: str):
    """Owner-only. canvas_shares rows cascade-delete via the FK ON DELETE
    CASCADE (db/database.py sets PRAGMA foreign_keys=ON per connection)."""
    if identity not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {identity}"})

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT identity FROM canvases WHERE id = ?", (canvas_id,),
        )
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Canvas not found"})
        if rows[0]["identity"] != identity:
            return JSONResponse(status_code=403, content={"error": "Only the owner can delete this canvas"})

        await db.execute("DELETE FROM canvases WHERE id = ?", (canvas_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await release_db(db)


@router.post("/{canvas_id}/share")
async def share_canvas(canvas_id: int, request: Request):
    """Body: {"identity": "<owner>", "share_with": "<other identity>"}.
    Owner-only -- sharing a canvas someone else shared to you isn't
    supported (keeps the visibility model to a single flat owner+shares
    layer, no re-sharing chains to reason about)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    identity = (payload.get("identity") or "").strip()
    share_with = (payload.get("share_with") or "").strip()
    if identity not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {identity}"})
    if share_with not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {share_with}"})
    if share_with == identity:
        return JSONResponse(status_code=400, content={"error": "Cannot share a canvas with its own owner"})

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT identity FROM canvases WHERE id = ?", (canvas_id,),
        )
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Canvas not found"})
        if rows[0]["identity"] != identity:
            return JSONResponse(status_code=403, content={"error": "Only the owner can share this canvas"})

        from datetime import datetime, timezone
        await db.execute(
            "INSERT OR IGNORE INTO canvas_shares (canvas_id, shared_with_identity, shared_at) "
            "VALUES (?, ?, ?)",
            (canvas_id, share_with, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return {"ok": True, "shared_with": share_with}
    finally:
        await release_db(db)


@router.delete("/{canvas_id}/share/{shared_identity}")
async def unshare_canvas(canvas_id: int, shared_identity: str, identity: str):
    """Owner-only revoke. `identity` (query param) must be the canvas owner;
    `shared_identity` (path) is whose access is being removed."""
    if identity not in IDENTITIES:
        return JSONResponse(status_code=400, content={"error": f"Unknown identity: {identity}"})

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT identity FROM canvases WHERE id = ?", (canvas_id,),
        )
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Canvas not found"})
        if rows[0]["identity"] != identity:
            return JSONResponse(status_code=403, content={"error": "Only the owner can revoke a share"})

        await db.execute(
            "DELETE FROM canvas_shares WHERE canvas_id = ? AND shared_with_identity = ?",
            (canvas_id, shared_identity),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await release_db(db)
