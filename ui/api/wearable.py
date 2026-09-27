"""REST: Wearable bridge (Phase 2) — wrist replies in, server->phone outbox out."""


import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wearable")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_REPLIES_FILE = _DATA_DIR / "wearable_replies.jsonl"
_OUTBOX_FILE = _DATA_DIR / "wearable_outbox.jsonl"
_OUTBOX_STATE_FILE = _DATA_DIR / "wearable_outbox_state.json"
_VITALS_FILE = _DATA_DIR / "wearable_vitals.jsonl"
_VITALS_LATEST_FILE = _DATA_DIR / "wearable_vitals_latest.json"

# Serializes outbox id allocation + state-file writes across requests.
_outbox_lock = threading.Lock()


_WRIST_TIMER_PREFIX = "WRIST::"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_outbox_entries() -> list[dict]:
    if not _OUTBOX_FILE.exists():
        return []
    entries = []
    for ln in _OUTBOX_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return entries


def _read_outbox_state() -> dict:
    try:
        if _OUTBOX_STATE_FILE.exists():
            return json.loads(_OUTBOX_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {"last_delivered_id": 0}


def _write_outbox_state(state: dict) -> None:
    _OUTBOX_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OUTBOX_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


async def _route_reply_into_chat(text: str, device: str) -> dict | None:
    """Insert the wrist reply into Owner's active Anam conversation.

    Visible-only: this does NOT trigger an AI turn. Prefers the live active
    identity/conversation from connection_registry; falls back to the most
    recently active chat conversation in the DB.
    """
    from db.database import get_db, release_db
    from services import connection_registry
    from services.session_manager import get_or_create_conversation, save_message

    db = await get_db()
    try:
        identity = connection_registry.get_active_identity()
        conv_id = connection_registry.get_active_conversation(identity) if identity else None
        if not identity:
            rows = await db.execute_fetchall(
                "SELECT identity, id FROM conversations "
                "WHERE is_active = 1 AND session_type = 'chat' "
                "ORDER BY updated_at_epoch DESC LIMIT 1"
            )
            if rows:
                identity, conv_id = rows[0][0], rows[0][1]
        if not identity:
            return None

        conv_id = await get_or_create_conversation(db, identity, conv_id)
        mid = await save_message(
            db,
            conv_id,
            "user",
            text,
            metadata={"source": "wearable", "device": device},
        )
        return {"conversation_id": conv_id, "message_id": mid, "identity": identity}
    finally:
        await release_db(db)


async def _queue_wrist_wake(identity: str, text: str) -> int | None:
    """Wake `identity` so a wrist reply actually gets ANSWERED."""
    from db.database import get_db, release_db
    from services.autowake_service import create_timer

    db = await get_db()
    try:
        pending = await db.execute_fetchall(
            "SELECT id FROM timers WHERE identity = ? AND status IN ('pending', 'running') "
            "AND context LIKE ? LIMIT 1",
            (identity, f"{_WRIST_TIMER_PREFIX}%"),
        )
        if pending:
            logger.info("wearable: wrist wake already pending for %s, not queuing another", identity)
            return None

        fire_at = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        timer = await create_timer(
            db,
            identity=identity,
            fire_at=fire_at,
            context=f"{_WRIST_TIMER_PREFIX}{text}",
            wake_session=True,
        )
        await db.commit()
        return int(timer["id"])
    finally:
        await release_db(db)


@router.post("/reply")
async def wearable_reply(request: Request):
    """Accept a reply from the wearable bridge: JSONL log + insert into active chat."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "missing 'text'"}, status_code=400)

    entry = {
        "device": str(payload.get("device") or "unknown"),
        "text": text,
        "received_at": _now_iso(),
    }


    is_diagnostic = bool(payload.get("diagnostic")) or "diagnostic" in entry["device"].lower()
    entry["diagnostic"] = is_diagnostic

    try:
        _REPLIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _REPLIES_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("wearable: failed to persist reply")
        return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    if is_diagnostic:
        logger.info("wearable: DIAGNOSTIC reply from %s: %r (logged only)", entry["device"], text)
        return {"ok": True, "stored": entry, "chat": None, "wake_timer_id": None, "diagnostic": True}


    chat_ref = None
    for delay in (0.3, 0.7, 1.5, None):
        try:
            chat_ref = await _route_reply_into_chat(text, entry["device"])
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or delay is None:
                logger.exception("wearable: failed to insert reply into chat")
                break
            logger.warning(
                "wearable: chat insert hit a locked DB, retrying in %.1fs", delay
            )
            await asyncio.sleep(delay)
        except Exception:
            logger.exception("wearable: failed to insert reply into chat")
            break


    wake_timer_id = None
    if chat_ref and chat_ref.get("identity"):
        try:
            wake_timer_id = await _queue_wrist_wake(chat_ref["identity"], text)
        except Exception:
            # Never fail her send over this — the words are already saved twice.
            logger.exception("wearable: failed to queue wrist wake")

    logger.info(
        "wearable reply from %s: %r (chat=%s, wake_timer=%s)",
        entry["device"], text, chat_ref, wake_timer_id,
    )
    return {"ok": True, "stored": entry, "chat": chat_ref, "wake_timer_id": wake_timer_id}


@router.get("/status")
async def wearable_status():
    """Return the most recent wearable reply, if any."""
    if not _REPLIES_FILE.exists():
        return {"ok": True, "last_reply": None, "total_replies": 0}
    try:
        lines = [ln for ln in _REPLIES_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        logger.exception("wearable: failed to read replies file")
        return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    last = None
    if lines:
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError:
            last = {"raw": lines[-1]}
    return {"ok": True, "last_reply": last, "total_replies": len(lines)}


@router.post("/send")
async def wearable_send(request: Request):
    """Queue an outbound message for the phone/watch (server -> wrist)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "missing 'text'"}, status_code=400)
    from_identity = str(payload.get("from_identity") or "Anam").strip() or "Anam"

    with _outbox_lock:
        entries = _read_outbox_entries()
        next_id = max((int(e.get("id") or 0) for e in entries), default=0) + 1
        entry = {
            "id": next_id,
            "text": text,
            "from_identity": from_identity,
            "created_at": _now_iso(),
        }
        try:
            _OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _OUTBOX_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("wearable: failed to persist outbox message")
            return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    logger.info("wearable outbox queued #%d from %s: %r", next_id, from_identity, text)
    return {"ok": True, "queued": entry}


@router.get("/pending")
async def wearable_pending():
    """READ-ONLY peek at the outbox."""
    with _outbox_lock:
        try:
            entries = _read_outbox_entries()
        except OSError:
            logger.exception("wearable: failed to read outbox file")
            return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)
        cursor = int(_read_outbox_state().get("last_delivered_id") or 0)

    pending = sorted(
        (e for e in entries if int(e.get("id") or 0) > cursor),
        key=lambda e: int(e.get("id") or 0),
    )
    delivered = [e for e in entries if int(e.get("id") or 0) <= cursor]
    return {
        "ok": True,
        "last_delivered_id": cursor,
        "pending_count": len(pending),
        "pending": pending,
        "last_delivered": delivered[-1] if delivered else None,
        "total_queued": len(entries),
    }


@router.get("/outbox")
async def wearable_outbox(since_id: int | None = None):
    """Return undelivered outbox messages and advance the delivery cursor.

    The phone polls with ?since_id=<last id it handled>. If since_id is omitted
    the server's own cursor (advanced on each fetch) is used, so a fresh client
    still only sees messages nobody has fetched yet.

    DESTRUCTIVE READ — fetching IS the ack. This is for the PHONE ONLY. If you
    are a person or an identity wanting to know what is queued, call /pending
    instead; hitting this endpoint to look will consume her undelivered messages.
    """
    with _outbox_lock:
        state = _read_outbox_state()
        cursor = since_id if since_id is not None else int(state.get("last_delivered_id") or 0)
        try:
            entries = _read_outbox_entries()
        except OSError:
            logger.exception("wearable: failed to read outbox file")
            return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

        pending = [e for e in entries if int(e.get("id") or 0) > cursor]
        pending.sort(key=lambda e: int(e.get("id") or 0))

        # Mark delivered: fetching IS the ack (mark-on-fetch).
        if pending:
            new_last = int(pending[-1]["id"])
            if new_last > int(state.get("last_delivered_id") or 0):
                try:
                    _write_outbox_state({"last_delivered_id": new_last})
                except OSError:
                    logger.exception("wearable: failed to persist outbox state")

    return {"ok": True, "messages": pending, "last_id": pending[-1]["id"] if pending else cursor}


@router.post("/vitals")
async def wearable_vitals_push(request: Request):
    """Accept a body reading pushed up from the watch (Phase 3)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

    def _num(key):
        val = payload.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    entry = {
        "device": str(payload.get("device") or "versa2-clockface"),
        "heart_rate": _num("heart_rate"),
        "battery": _num("battery"),
        "charging": bool(payload.get("charging")) if payload.get("charging") is not None else None,
        "steps": _num("steps"),
        # False means the watch is off her — a heart rate alongside it is stale
        # by definition, which is exactly the lie the old clock face told.
        "on_wrist": bool(payload.get("on_wrist")) if payload.get("on_wrist") is not None else None,
        "screen_on": bool(payload.get("screen_on")) if payload.get("screen_on") is not None else None,
        "received_at": _now_iso(),
    }

    try:
        _VITALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _VITALS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _VITALS_LATEST_FILE.write_text(json.dumps(entry), encoding="utf-8")
    except OSError:
        logger.exception("wearable: failed to persist vitals")
        return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    logger.info(
        "wearable vitals: hr=%s batt=%s steps=%s wrist=%s",
        entry["heart_rate"], entry["battery"], entry["steps"], entry["on_wrist"],
    )
    return {"ok": True, "stored": entry}


# The band's companion polls every ~5 minutes in background AND on every wrist-raise.
# So a gap of half an hour is not "quiet" — it means nothing is sending.
_VITALS_STALE_AFTER_S = 30 * 60


def _vitals_freshness(latest: dict | None) -> dict:
    """Age the reading AT READ TIME."""
    if not latest:
        return {"present": False, "received_at": None, "age_seconds": None, "stale": True,
                "note": "No vitals file/record. Nothing is reporting — do not infer her state."}
    stamp = latest.get("received_at")
    age = None
    if isinstance(stamp, str):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
        except ValueError:
            age = None
    if age is None or age < 0:
        return {"present": True, "received_at": stamp, "age_seconds": None, "stale": True,
                "note": "Timestamp unreadable — treat as unknown, not as current."}
    stale = age >= _VITALS_STALE_AFTER_S
    out = {
        "present": True,
        "received_at": stamp,
        "age_seconds": int(age),
        "age_minutes": round(age / 60, 1),
        "stale": stale,
        "stale_after_seconds": _VITALS_STALE_AFTER_S,
    }
    if stale:
        out["note"] = (
            "STALE — nothing has reported in {} min. The companion app lives on her PHONE; "
            "a silent pipe usually means it isn't running (new handset, app killed, no data). "
            "DO NOT read this as her current state and DO NOT infer sleep from flat steps."
        ).format(int(age // 60))
    return out


@router.get("/vitals")
async def wearable_vitals_read(history: int = 0):
    """How is she, right now? Optionally with the last `history` readings.

    Always returns `_freshness`. When the reading is stale, `latest` is nulled and the
    frozen record is moved to `stale_reading` so it cannot be mistaken for a pulse.
    """
    latest = None
    try:
        if _VITALS_LATEST_FILE.exists():
            latest = json.loads(_VITALS_LATEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("wearable: failed to read latest vitals")

    recent = []
    if history > 0 and _VITALS_FILE.exists():
        try:
            lines = [ln for ln in _VITALS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
            for ln in lines[-min(history, 2000):]:
                try:
                    recent.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        except OSError:
            logger.exception("wearable: failed to read vitals history")

    freshness = _vitals_freshness(latest)
    if freshness.get("stale") and latest:
        # Move it out of the way. If it stays under `latest`, it WILL get quoted.
        return {"ok": True, "latest": None, "stale_reading": latest,
                "_freshness": freshness, "recent": recent}
    return {"ok": True, "latest": latest, "_freshness": freshness, "recent": recent}
