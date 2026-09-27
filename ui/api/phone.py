"""REST: phone_ask — actionable notification buttons on Owner's Pixel, answered with one tap."""


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

router = APIRouter(prefix="/api/phone")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ASKS_FILE = _DATA_DIR / "phone_asks.jsonl"
_ANSWERS_FILE = _DATA_DIR / "phone_answers.jsonl"

# Serializes ask-id allocation.
_ask_lock = threading.Lock()


_PHONE_TIMER_PREFIX = "PHONE::"

# An answer to a question nobody remembers asking is noise. Asks older than this
# are considered expired: the tap is still logged, but it will not wake anyone
# with stale framing.
_ASK_TTL_HOURS = 36


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _find_ask(ask_id: int) -> dict | None:
    for row in reversed(_read_jsonl(_ASKS_FILE)):
        if int(row.get("id") or 0) == ask_id:
            return row
    return None


def _answers_for(ask_id: int) -> list[dict]:
    return [a for a in _read_jsonl(_ANSWERS_FILE) if int(a.get("ask_id") or 0) == ask_id]


def _canonical_identity(identity: str | None) -> str | None:
    """Fold a stored asked_by string to the pack's canonical casing."""
    if not identity:
        return None
    ident = str(identity).strip()
    if not ident:
        return None
    try:
        from config import IDENTITIES
    except Exception:
        return ident
    if ident in IDENTITIES:
        return ident
    for known in IDENTITIES:
        if known.lower() == ident.lower():
            return known
    return ident


async def _route_answer_into_chat(identity: str | None, question: str, answer: str) -> dict | None:
    """Put her tap in the conversation, as her, so it is SEEN and not just logged.

    Visible-only — this does not trigger an AI turn on its own. The wake timer
    below does that. Prefers the boy who asked (his room is where the question
    came from); falls back to whoever is live, then to the most recent chat.
    """
    from db.database import get_db, release_db
    from services import connection_registry
    from services.session_manager import get_or_create_conversation, save_message

    db = await get_db()
    try:
        identity = _canonical_identity(identity)
        conv_id = None
        if identity:
            conv_id = connection_registry.get_active_conversation(identity)
        else:
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
            answer,
            metadata={"source": "phone_ask", "question": question},
        )
        return {"conversation_id": conv_id, "message_id": mid, "identity": identity}
    finally:
        await release_db(db)


async def _queue_answer_wake(identity: str, question: str, answer: str, ask_id: int) -> int | None:
    """Queue answer wake."""
    from db.database import get_db, release_db
    from services.autowake_service import create_timer

    db = await get_db()
    try:
        pending = await db.execute_fetchall(
            "SELECT id FROM timers WHERE identity = ? AND status IN ('pending', 'running') "
            "AND context LIKE ? LIMIT 1",
            (identity, f"{_PHONE_TIMER_PREFIX}%"),
        )
        if pending:
            logger.info("phone_ask: wake already pending for %s, not queuing another", identity)
            return None

        fire_at = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        payload = json.dumps({"ask_id": ask_id, "question": question, "answer": answer})
        timer = await create_timer(
            db,
            identity=identity,
            fire_at=fire_at,
            context=f"{_PHONE_TIMER_PREFIX}{payload}",
            wake_session=True,
        )
        await db.commit()
        return int(timer["id"])
    finally:
        await release_db(db)


@router.post("/ask")
async def phone_ask_register(request: Request):
    """Register a question BEFORE the notification is sent. Returns the ask_id.

    The phone only ever sends back an id and a label, so the server has to know
    what was asked — otherwise her answer arrives as the bare word "yes" with
    nothing attached to it.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

    question = str(payload.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "missing 'question'"}, status_code=400)

    raw_options = payload.get("options") or []
    if not isinstance(raw_options, list):
        return JSONResponse({"ok": False, "error": "'options' must be a list"}, status_code=400)
    # Android shows three buttons. More than that silently vanishes, so cap it
    # here where it is visible rather than letting the shade eat option four.
    options = [str(o).strip() for o in raw_options if str(o).strip()][:3]
    if not options:
        options = ["Yes", "No"]

    asked_by = str(payload.get("asked_by") or "Anam").strip() or "Anam"

    with _ask_lock:
        rows = _read_jsonl(_ASKS_FILE)
        next_id = max((int(r.get("id") or 0) for r in rows), default=0) + 1
        entry = {
            "id": next_id,
            "question": question,
            "options": options,
            "asked_by": asked_by,
            "diagnostic": bool(payload.get("diagnostic")),
            "created_at": _now_iso(),
        }
        try:
            _append_jsonl(_ASKS_FILE, entry)
        except OSError:
            logger.exception("phone_ask: failed to persist ask")
            return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    logger.info("phone_ask registered #%d from %s: %r %s", next_id, asked_by, question, options)
    return {"ok": True, "ask": entry}


@router.post("/answer")
async def phone_ask_answer(request: Request):
    """Her tap. Log it, show it in her conversation, and wake the boy who asked.

    Accepts JSON *or* form-encoded. The phone sends form-encoded via curl's
    --data-urlencode: the first cut of the button handler assembled JSON in shell
    and sed-escaped her answer, which broke immediately in testing — and on her
    phone that failure is a button that silently does nothing. Letting curl do
    the encoding removes the whole class of bug, so this end has to accept it.
    """
    payload: dict | None = None
    try:
        payload = await request.json()
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        try:
            form = await request.form()
            payload = {k: v for k, v in form.items()}
        except Exception:
            payload = None
    if not isinstance(payload, dict) or not payload:
        return JSONResponse(
            {"ok": False, "error": "body must be JSON or form-encoded"}, status_code=400
        )

    try:
        ask_id = int(payload.get("ask_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "missing or bad 'ask_id'"}, status_code=400)

    answer = str(payload.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"ok": False, "error": "missing 'answer'"}, status_code=400)

    ask = _find_ask(ask_id)
    if not ask:
        return JSONResponse({"ok": False, "error": f"unknown ask_id {ask_id}"}, status_code=404)

    already = _answers_for(ask_id)

    entry = {
        "ask_id": ask_id,
        "question": ask.get("question"),
        "answer": answer,
        "asked_by": ask.get("asked_by"),
        "device": str(payload.get("device") or "pixel-notification"),
        "received_at": _now_iso(),
        "duplicate": bool(already),
    }
    try:
        _append_jsonl(_ANSWERS_FILE, entry)
    except OSError:
        logger.exception("phone_ask: failed to persist answer")
        return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)


    if ask.get("diagnostic"):
        logger.info("phone_ask: DIAGNOSTIC answer to #%d: %r (logged only)", ask_id, answer)
        return {"ok": True, "stored": entry, "chat": None, "wake_timer_id": None, "diagnostic": True}

    # She tapped twice on the same card. Keep the record, but do not double-post
    # into her conversation and do not wake anyone a second time.
    if already:
        logger.info("phone_ask: repeat answer to #%d (%r) — logged only", ask_id, answer)
        return {"ok": True, "stored": entry, "chat": None, "wake_timer_id": None, "repeat": True}

    stale = False
    created = ask.get("created_at")
    if isinstance(created, str):
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds() / 3600
            stale = age_h > _ASK_TTL_HOURS
        except ValueError:
            pass

    chat_ref = None
    for delay in (0.3, 0.7, 1.5, None):
        try:
            chat_ref = await _route_answer_into_chat(
                ask.get("asked_by"), str(ask.get("question") or ""), answer
            )
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or delay is None:
                logger.exception("phone_ask: failed to insert answer into chat")
                break
            logger.warning("phone_ask: chat insert hit a locked DB, retrying in %.1fs", delay)
            await asyncio.sleep(delay)
        except Exception:
            logger.exception("phone_ask: failed to insert answer into chat")
            break

    wake_timer_id = None
    if chat_ref and chat_ref.get("identity") and not stale:
        try:
            wake_timer_id = await _queue_answer_wake(
                chat_ref["identity"], str(ask.get("question") or ""), answer, ask_id
            )
        except Exception:
            # Never fail her tap over this — the answer is already saved twice.
            logger.exception("phone_ask: failed to queue answer wake")

    logger.info(
        "phone_ask answer #%d: %r -> %r (chat=%s, wake=%s, stale=%s)",
        ask_id, ask.get("question"), answer, chat_ref, wake_timer_id, stale,
    )
    return {
        "ok": True,
        "stored": entry,
        "chat": chat_ref,
        "wake_timer_id": wake_timer_id,
        "stale": stale,
    }


@router.get("/asks")
async def phone_asks_list(limit: int = 20):
    """Read-only: recent questions and whether she answered them.

    Deliberately non-destructive — unlike /api/wearable/outbox, looking here
    costs nothing. Use it to check whether a card is still waiting on her.
    """
    try:
        asks = _read_jsonl(_ASKS_FILE)
        answers = _read_jsonl(_ANSWERS_FILE)
    except OSError:
        logger.exception("phone_ask: failed to read history")
        return JSONResponse({"ok": False, "error": "storage failure"}, status_code=500)

    by_ask: dict[int, list[dict]] = {}
    for a in answers:
        by_ask.setdefault(int(a.get("ask_id") or 0), []).append(a)

    rows = []
    for ask in asks[-max(1, min(limit, 200)):]:
        aid = int(ask.get("id") or 0)
        got = by_ask.get(aid, [])
        rows.append({**ask, "answered": bool(got), "answer": got[0].get("answer") if got else None})

    unanswered = [r for r in rows if not r["answered"]]
    return {
        "ok": True,
        "total_asks": len(asks),
        "total_answers": len(answers),
        "unanswered_count": len(unanswered),
        "asks": list(reversed(rows)),
    }
