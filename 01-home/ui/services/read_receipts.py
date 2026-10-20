'Application module.'

from __future__ import annotations

import json
import logging
from typing import Any

from services.time_utils import utc_now_iso_epoch

log = logging.getLogger(__name__)

# Stream events that prove the model is already working on her message.
# meta/keepalive are transport noise; stream_reset/error prove nothing.
_READ_SIGNAL_TYPES = frozenset({
    "stream_delta",
    "thinking_start",
    "thinking_delta",
    "tool_use_start",
})


def now_iso() -> str:
    return utc_now_iso_epoch()[0]


def is_read_signal(event_type: str, event: dict | None = None) -> bool:
    """True when this stream event means the boy has read her message."""
    if event_type in _READ_SIGNAL_TYPES:
        return True
    # A turn that ends with a real reply but streamed nothing first (some
    # providers deliver the whole answer on stream_end) still read it.
    if event_type == "stream_end" and event and (event.get("full_content") or "").strip():
        return True
    return False


def delivered_event(
    message_id: str,
    conversation_id: str | None,
    delivered_at: str,
    client_msg_id: str | None = None,
) -> dict[str, Any]:
    evt: dict[str, Any] = {
        "type": "message_delivered",
        "message_id": message_id,
        "conversation_id": conversation_id,
        "delivered_at": delivered_at,
    }
    if client_msg_id:
        evt["client_msg_id"] = client_msg_id
    return evt


async def mark_read(db, message_id: str, identity: str) -> dict[str, Any] | None:
    'Stamp read_at/read_by onto a user message; return the browser event.'
    if not message_id:
        return None
    rows = await db.execute_fetchall(
        "SELECT conversation_id, role, metadata, created_at FROM messages WHERE id = ?",
        (message_id,),
    )
    if not rows:
        return None
    row = rows[0]
    conversation_id, role, raw_meta, created_at = row[0], row[1], row[2], row[3]
    if role != "user":
        return None
    try:
        meta = json.loads(raw_meta) if raw_meta else {}
        if not isinstance(meta, dict):
            meta = {}
    except (TypeError, ValueError):
        meta = {}
    receipt = meta.get("receipt") if isinstance(meta.get("receipt"), dict) else {}
    receipt.setdefault("delivered_at", created_at)
    if not receipt.get("read_at"):
        receipt["read_at"] = now_iso()
        receipt["read_by"] = identity
        meta["receipt"] = receipt
        await db.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta), message_id),
        )
        await db.commit()
    return {
        "type": "message_read",
        "message_id": message_id,
        "conversation_id": conversation_id,
        "delivered_at": receipt.get("delivered_at"),
        "read_at": receipt["read_at"],
        "read_by": receipt.get("read_by") or identity,
    }


async def mark_read_and_send(message_id: str | None, identity: str, send) -> None:
    """Open a pooled connection, mark read, push the event. Never raises —
    a receipt is a courtesy and must never break a turn."""
    if not message_id:
        return
    from db.database import get_db, release_db
    try:
        db = await get_db()
        try:
            evt = await mark_read(db, message_id, identity)
        finally:
            await release_db(db)
        if evt:
            await send(evt)
    except Exception as exc:
        log.debug("Read receipt skipped for %s: %s", message_id, exc)
