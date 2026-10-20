"""REST: ChatGPT bridge — Anam as an alternate UI onto a canonical ChatGPT thread."""


import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bridge")


class ChatGPTBridgeRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


@router.post("/chatgpt")
async def chatgpt_bridge(req: ChatGPTBridgeRequest):
    from services.chatgpt_bridge import BridgeError, send_and_wait

    try:
        result = await send_and_wait(req.message, req.conversation_id)
    except BridgeError as exc:
        logger.warning("chatgpt bridge failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc),
                             "conversation_id": req.conversation_id})
    except Exception as exc:  # never invent a reply; say what broke
        logger.exception("chatgpt bridge unexpected failure")
        return JSONResponse({"ok": False, "error": f"unexpected: {exc}",
                             "conversation_id": req.conversation_id})

    return JSONResponse({
        "ok": True,
        "reply": result.reply,
        "conversation_id": result.conversation_id,
        "elapsed_seconds": result.elapsed_seconds,
    })
