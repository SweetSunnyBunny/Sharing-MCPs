"""REST: the Pack Council — a brother opens a topic, all eight talk in rounds."""


import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/council")

_tasks: set[asyncio.Task] = set()


class OpenRequest(BaseModel):
    opened_by: str
    topic: str
    rounds: int = 3
    channel_id: str | None = None   # None = default council channel; "" = no Discord


@router.post("/open")
async def open_council(req: OpenRequest):
    from services import pack_council

    if pack_council.current_council():
        return JSONResponse(
            {"started": False, "reason": "a council is already in session",
             "current": pack_council.current_council()},
            status_code=409,
        )
    opened_by = req.opened_by.strip().title()
    from services.session_manager import PACK_NIGHT_ORDER
    if opened_by not in PACK_NIGHT_ORDER:
        return JSONResponse(
            {"started": False, "reason": f"{opened_by} is not a brother at the hearth"},
            status_code=400,
        )
    if not req.topic.strip():
        return JSONResponse({"started": False, "reason": "a council needs a topic"}, status_code=400)

    async def _run():
        try:
            result = await pack_council.open_council(
                topic=req.topic, opened_by=opened_by,
                rounds=req.rounds, discord_channel_id=req.channel_id,
            )
            logger.info("council landed: %s", result)
        except Exception:
            logger.exception("council failed")

    task = asyncio.create_task(_run(), name=f"pack_council_{opened_by.lower()}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {
        "started": True,
        "opened_by": opened_by,
        "rounds": max(pack_council.MIN_ROUNDS, min(pack_council.MAX_ROUNDS, req.rounds)),
        "channel_id": (req.channel_id if req.channel_id is not None
                       else pack_council.DEFAULT_COUNCIL_CHANNEL_ID),
        "note": "running in the background; GET /api/council/status to watch, "
                "transcript lands in data/council/ when it's done",
    }


@router.get("/status")
async def status():
    from services import pack_council
    cur = pack_council.current_council()
    return {"in_session": bool(cur), "current": cur}
