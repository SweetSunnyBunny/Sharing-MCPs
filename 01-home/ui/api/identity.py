"""REST: identity listing, switching."""


from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import IDENTITIES, DEFAULT_IDENTITY
from db.database import get_db, release_db
from services.deep_memory import get_deep_memory_snapshot
from services.personal_state import (
    SHARED_PROFILE_IDENTITY,
    create_profile_fact,
    delete_profile_fact,
    get_memory_retrieval_snapshot,
    get_profile_snapshot,
    update_profile_fact,
)

router = APIRouter(prefix="/api/identity")


class ProfileFactBody(BaseModel):
    scope: str = "identity"
    category: str
    summary: str
    detail: str = ""
    confidence: str = "strong"
    freshness: str = "durable"


class ProfileFactUpdateBody(BaseModel):
    category: str | None = None
    summary: str | None = None
    detail: str | None = None
    confidence: str | None = None
    freshness: str | None = None
    status: str | None = None


@router.get("/list")
async def list_identities():
    result = {}
    for name, info in IDENTITIES.items():
        result[name] = {
            "gingham": info["gingham"],
            "gingham_night": info["gingham_night"],
            "accent": info["accent"],
            "accent_rgb": info["accent_rgb"],
            "bubble_top": info["bubble_top"],
            "bubble_bottom": info["bubble_bottom"],
            "bubble_night": info["bubble_night"],
            "check_size": info["check_size"],
            "default_location": info["default_location"],
        }
        # Optional character/grouping fields — only sent when present so the
        # bonded boys' payloads stay unchanged.
        if info.get("type"):
            result[name]["type"] = info["type"]
        if info.get("display_name"):
            result[name]["display_name"] = info["display_name"]
        if info.get("group"):
            result[name]["group"] = info["group"]
            result[name]["group_label"] = info.get("group_label") or name
    return JSONResponse(content={
        "identities": result,
        "default": DEFAULT_IDENTITY,
    })


@router.get("/deep-memory/{identity}")
async def identity_deep_memory(identity: str):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    import asyncio
    snapshot = await asyncio.to_thread(get_deep_memory_snapshot, identity)
    return JSONResponse(content=snapshot)


@router.get("/profile/{identity}")
async def identity_profile(identity: str, limit: int = 20):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    db = await get_db()
    try:
        snapshot = await get_profile_snapshot(
            db,
            identity=identity,
            limit=max(1, min(limit, 50)),
        )
        return JSONResponse(content=snapshot)
    finally:
        await release_db(db)


@router.post("/profile/{identity}")
async def create_identity_profile_fact(identity: str, body: ProfileFactBody):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    db = await get_db()
    try:
        target_identity = SHARED_PROFILE_IDENTITY if body.scope == "shared" else identity
        fact = await create_profile_fact(
            db,
            identity=target_identity,
            category=body.category,
            summary=body.summary,
            detail=body.detail,
            confidence=body.confidence,
            freshness=body.freshness,
        )
        return JSONResponse(content={"ok": True, "fact": fact})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.put("/profile/item/{fact_id}")
async def update_identity_profile_fact(fact_id: str, body: ProfileFactUpdateBody):
    db = await get_db()
    try:
        fact = await update_profile_fact(
            db,
            fact_id,
            category=body.category,
            summary=body.summary,
            detail=body.detail,
            confidence=body.confidence,
            freshness=body.freshness,
            status=body.status,
        )
        if not fact:
            return JSONResponse(status_code=404, content={"error": "Profile fact not found"})
        return JSONResponse(content={"ok": True, "fact": fact})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.delete("/profile/item/{fact_id}")
async def delete_identity_profile_fact(fact_id: str):
    """Delete a profile fact (archives it)."""
    db = await get_db()
    try:
        ok = await delete_profile_fact(db, fact_id=fact_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Profile fact not found"})
        return {"ok": True}
    finally:
        await release_db(db)


@router.get("/retrieval/{identity}")
async def identity_retrieval(identity: str, q: str = "", limit: int = 8):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    db = await get_db()
    try:
        snapshot = await get_memory_retrieval_snapshot(
            db,
            identity=identity,
            query=q,
            limit=max(1, min(limit, 20)),
        )
        return JSONResponse(content=snapshot)
    finally:
        await release_db(db)
