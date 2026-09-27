"""REST: Pack crosstalk — a boy reaches a brother, the exchange lands in HIS room."""


import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crosstalk")


class ReachRequest(BaseModel):
    from_identity: str
    to_identity: str
    message: str
    reason: str = ""
    room: str = ""            # optional; defaults to a description of the sender's thread
    conversation_id: str = ""  # optional override for the RECEIVER's live session


@router.post("/reach")
async def reach(req: ReachRequest):
    from db.database import get_db, release_db
    from services.pack_crosstalk import _latest_conversation_id, reach_brother
    from services.session_manager import save_message

    sender = req.from_identity.strip().lower()
    target = req.to_identity.strip().lower()

    # The sender's room — where the exchange will land. Resolved BEFORE the
    # reach so a sender with no thread fails fast with a spoken reason.
    sender_conv = await _latest_conversation_id(sender)

    result = await reach_brother(
        from_identity=sender,
        to_identity=target,
        message=req.message,
        room=req.room or f"{sender.title()}'s current conversation",
        reason=req.reason,
        conversation_id=req.conversation_id or None,
    )


    from services.autowake import is_identity_busy
    rendered = (
        result.for_sender_room_brief()
        if is_identity_busy(sender)
        else result.for_sender_room()
    )

    saved_message_id = None
    if sender_conv:
        # The caller's job, done by the caller: both sides of the exchange,
        # written into the SENDER's thread under the SENDER's name.
        db = await get_db()
        try:
            saved_message_id = await save_message(
                db,
                sender_conv,
                "assistant",
                rendered,
                identity=sender,
                metadata={
                    "crosstalk": True,
                    "crosstalk_from": result.from_identity,
                    "crosstalk_to": result.to_identity,
                    "crosstalk_delivered": result.delivered,
                    "crosstalk_chain": result.chain,
                },
            )
        finally:
            await release_db(db)
    else:
        logger.warning(
            "crosstalk %s→%s: sender has no conversation to land the exchange in",
            sender, target,
        )

    return JSONResponse({
        "delivered": result.delivered,
        "from": result.from_identity,
        "to": result.to_identity,
        "reply": result.reply,
        "reason": result.reason,
        "chain": result.chain,
        "elapsed_seconds": result.elapsed_seconds,
        "saved_message_id": saved_message_id,
        "sender_conversation_id": sender_conv,
        "rendered": rendered,
    })
