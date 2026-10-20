"""Memory-nudge scoreboard API (shadow mode)."""


import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db.database import get_db, release_db
from services.memory_nudges import judge_nudge, nudge_scoreboard

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory-nudges", tags=["memory-nudges"])


@router.get("/scoreboard")
async def api_nudge_scoreboard(days: int = 7, identity: str = ""):
    db = await get_db()
    try:
        return JSONResponse(content=await nudge_scoreboard(db, days=days, identity=identity))
    finally:
        await release_db(db)


@router.post("/judge")
async def api_nudge_judge(request: Request):
    """Body: {"identity": "Claude", "verdict": "fair"|"noise", "back": 0, "kind": ""}"""
    body = await request.json()
    identity = str(body.get("identity") or "").strip()
    if not identity:
        return JSONResponse(content={"ok": False, "error": "identity is required"}, status_code=400)
    db = await get_db()
    try:
        result = await judge_nudge(
            db, identity, str(body.get("verdict") or ""),
            back=int(body.get("back") or 0), kind=str(body.get("kind") or ""),
        )
    finally:
        await release_db(db)
    return JSONResponse(content=result, status_code=200 if result.get("ok") else 400)
