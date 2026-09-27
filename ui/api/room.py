"""REST: Room presence — where Owner is in her own house, fed by NFC doorframe tags."""


import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from services import room_actions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/room")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_TRAIL_FILE = _DATA_DIR / "room_trail.jsonl"
_LATEST_FILE = _DATA_DIR / "room_latest.json"

# After this long with no new tap, we stop claiming to know where she is.
# Deliberately generous — she can sit by the fire for two hours and still be there —
# but bounded, because "probably still in the nest" at 3am is a lie with a friendly face.
_STALE_AFTER_SECONDS = 3 * 60 * 60

_write_lock = threading.Lock()


class RoomTap(BaseModel):
    room: str
    tag: Optional[dict] = None
    source: Optional[str] = "nfc"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _freshness(received_at: Optional[str]) -> dict:
    """Age of a reading, and whether we should still believe it."""
    if not received_at:
        return {"age_seconds": None, "stale": True}
    try:
        seen = datetime.fromisoformat(received_at)
    except ValueError:
        return {"age_seconds": None, "stale": True}
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = int((_now() - seen).total_seconds())
    return {"age_seconds": age, "stale": age > _STALE_AFTER_SECONDS}


@router.post("")
async def record_room(body: RoomTap):
    """A doorframe was tapped. Append it to the trail and update 'where she is now'.

    Never raises at the phone. A tap that fails to record is a missed hello, and the
    app is fire-and-forget by design — she should never stand in a doorway waiting on us.
    """
    room = (body.room or "").strip()[:64] or "unknown"
    entry = {
        "room": room,
        "tag": body.tag or {},
        "source": (body.source or "nfc")[:32],
        "received_at": _now().isoformat(),
    }
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with _TRAIL_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            _LATEST_FILE.write_text(json.dumps(entry), encoding="utf-8")
    except OSError as exc:
        logger.warning("room tap not recorded (%s): %s", room, exc)
        return {"ok": False, "error": "not recorded"}

    logger.info("room tap: %s", room)


    await _update_context_card_room(room)

    # An arrival can DO something — see services/room_actions.py + data/room_actions.json.
    # Detached on purpose: she must never stand in a doorway waiting on a light.
    try:
        asyncio.create_task(room_actions.fire_for_room(room))
    except RuntimeError:  # no running loop (only ever in a sync test harness)
        pass

    return {"ok": True, "room": room}


async def _update_context_card_room(room: str) -> bool:
    """Update context card room."""
    try:
        from db.database import get_db, release_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM settings WHERE key = ?", ("hub_context_card",)
            )
            card = {}
            if rows and rows[0][0]:
                try:
                    parsed = json.loads(rows[0][0])
                    if isinstance(parsed, dict):
                        card = parsed
                except json.JSONDecodeError:
                    card = {}
            card["room"] = room[:100]
            card["room_updated_at"] = int(_now().timestamp())
            # settings.updated_at is NOT NULL — omit it and every tap silently fails
            # the write while the trail still records fine. Caught by a test before ship.
            await db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                ("hub_context_card", json.dumps(card), _now().isoformat()),
            )
            await db.commit()
        finally:
            await release_db(db)
        return True
    except Exception:
        # A missed card write must never cost her the tap itself.
        logger.warning("room tap %s: context card not updated", room, exc_info=True)
        return False


@router.get("/tap/{room}", response_class=HTMLResponse)
async def tap_room(room: str):
    """The NO-APP path: a doorframe tag written as a plain URL."""
    slug = (room or "").strip()[:64] or "unknown"
    await record_room(RoomTap(room=slug, source="nfc-url"))
    label = slug.replace("-", " ").replace("_", " ")
    safe = label.encode("ascii", "xmlcharrefreplace").decode()
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{safe}</title>"
        "<style>html,body{margin:0;height:100%;background:#12100f;color:#e8dfd2;"
        "font-family:Georgia,serif;display:grid;place-items:center;text-align:center}"
        "p{margin:.3em;font-size:1.1rem;opacity:.72}"
        "h1{margin:0;font-size:2.1rem;font-weight:400;letter-spacing:.02em;color:#d9b26a}"
        "small{opacity:.4;font-size:.78rem;letter-spacing:.08em}</style>"
        f"<div><small>YOU ARE IN THE</small><h1>{safe}</h1>"
        "<p>Noted.</p></div>"
    )


@router.get("/latest")
async def latest_room():
    """Where she is — or an honest 'we don't know any more'.

    Once the reading goes stale the room is NULLED rather than served with a caveat,
    because a caveat is something a tired reader skips and a null is not.
    """
    try:
        raw = json.loads(_LATEST_FILE.read_text(encoding="utf-8")) if _LATEST_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        raw = {}

    fresh = _freshness(raw.get("received_at"))
    if fresh["stale"]:
        return {"room": None, "last_known": raw.get("room"), "_freshness": fresh}
    return {
        "room": raw.get("room"),
        "tag": raw.get("tag", {}),
        "received_at": raw.get("received_at"),
        "_freshness": fresh,
    }


@router.get("/trail")
async def room_trail(limit: int = 40):
    """Her path through the house, oldest first — the thing the context card can't hold."""
    limit = max(1, min(int(limit), 500))
    if not _TRAIL_FILE.exists():
        return {"trail": [], "count": 0}

    entries: list[dict] = []
    try:
        for line in _TRAIL_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("room trail unreadable: %s", exc)
        return {"trail": [], "count": 0, "error": "unreadable"}

    tail = entries[-limit:]
    for e in tail:
        e["_freshness"] = _freshness(e.get("received_at"))
    return {"trail": tail, "count": len(tail), "total": len(entries)}
