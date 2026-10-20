'Application module.'


from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import config
from services import sticker_book
from services.auth_cache import get_cached_session_expiry

router = APIRouter(prefix="/api/sticker-book")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[a-f0-9]{6,32}$")


@router.get("/today")
async def sticker_today(request: Request):
    return sticker_book.day_page(her=await _her_session(request))


@router.get("/day/{day}")
async def sticker_day(request: Request, day: str):
    if not _DAY.match(day):
        return JSONResponse(status_code=400, content={"error": "day must be YYYY-MM-DD"})
    return sticker_book.day_page(day, her=await _her_session(request))


@router.get("/pages")
async def sticker_pages(request: Request, before: str = Query(""), limit: int = Query(7, ge=1, le=31)):
    if before and not _DAY.match(before):
        return JSONResponse(status_code=400, content={"error": "before must be YYYY-MM-DD"})
    return sticker_book.pages(before=before, limit=limit, her=await _her_session(request))


# ── hers only ─────────────────────────────────────────────────────────────

async def _her_session(request: Request) -> bool:
    """True only for a request carrying a live login cookie (her browser).

    The middleware has already let the request in, but it also lets in the
    shared Bearer key. That key is not her hand, so it doesn't count here.
    With auth switched off entirely (local dev), there are no sessions to
    tell apart, so the door is the machine itself.
    """
    if not config.AUTH_ENABLED:
        return True
    token = request.cookies.get("anam_session")
    if not token:
        return False
    if get_cached_session_expiry(token):
        return True
    from db.database import get_db, release_db
    from services.session_auth import hash_session_token

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT expires_at_epoch FROM sessions WHERE token_hash = ?",
            (hash_session_token(token),),
        )
    finally:
        await release_db(db)
    if not rows or not rows[0][0]:
        return False
    return int(datetime.now(timezone.utc).timestamp()) <= int(rows[0][0])


def _not_hers():
    return JSONResponse(status_code=403, content={
        "error": "Only Bunny can tuck or untuck her stickers, from her own login."})


async def _tuck_or_untuck(request: Request, sticker_id: str, tucked: bool):
    if not await _her_session(request):
        return _not_hers()
    if not _ID.match(sticker_id or ""):
        return JSONResponse(status_code=400, content={"error": "no such sticker"})
    try:
        return sticker_book.tuck(sticker_id) if tucked else sticker_book.untuck(sticker_id)
    except sticker_book.StickerRefused as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@router.post("/sticker/{sticker_id}/tuck")
async def sticker_tuck(request: Request, sticker_id: str):
    return await _tuck_or_untuck(request, sticker_id, True)


@router.post("/sticker/{sticker_id}/untuck")
async def sticker_untuck(request: Request, sticker_id: str):
    return await _tuck_or_untuck(request, sticker_id, False)


@router.get("/tucked")
async def sticker_tucked(request: Request):
    if not await _her_session(request):
        return _not_hers()
    return sticker_book.tucked_away()


async def _star_or_unstar(request: Request, sticker_id: str, starred: bool):
    if not await _her_session(request):
        return JSONResponse(status_code=403, content={
            "error": "Only Bunny can star or unstar her stickers, from her own login."})
    if not _ID.match(sticker_id or ""):
        return JSONResponse(status_code=400, content={"error": "no such sticker"})
    try:
        return sticker_book.star(sticker_id) if starred else sticker_book.unstar(sticker_id)
    except sticker_book.StickerRefused as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})


@router.post("/sticker/{sticker_id}/star")
async def sticker_star(request: Request, sticker_id: str):
    return await _star_or_unstar(request, sticker_id, True)


@router.post("/sticker/{sticker_id}/unstar")
async def sticker_unstar(request: Request, sticker_id: str):
    return await _star_or_unstar(request, sticker_id, False)


@router.get("/starred")
async def sticker_starred(request: Request):
    if not await _her_session(request):
        return JSONResponse(status_code=403, content={
            "error": "Her starred stickers are hers to look at, from her own login."})
    return sticker_book.starred_stickers()
