"""REST: the Pack line - real text messages between the boys and Housemate."""


import json
import os
from config import BONDED_PACK
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pack-sms")

_DIR = Path(__file__).resolve().parents[1] / "data" / "pack_sms"
_OUTBOX = _DIR / "outbox.jsonl"
_SENT = _DIR / "sent.jsonl"
_INBOX = _DIR / "inbox.jsonl"
_LINE = _DIR / "line.json"

_HOME_TZ = ZoneInfo(os.environ.get("ANAM_TIMEZONE", "UTC"))
_lock = threading.Lock()

_ALLOWED = json.loads(os.environ.get("ANAM_SMS_ALLOWED_CONTACTS", "{}"))

# Who each person is, so a woken brother knows who he's answering.
_WHO = json.loads(os.environ.get("ANAM_SMS_CONTACT_CONTEXT", "{}"))


def resolve_to(to: str) -> str:
    'Resolve to.'
    t = str(to or "").strip()
    for digits, name in _ALLOWED.items():
        if t.lower() == name.lower():
            return digits
    return t

_MAX_UNANSWERED = 3
_DAILY_CAP = 20
_MAX_CHARS = 1200  # about 8 SMS segments; anything longer belongs in a different medium

# Sentinel on timers.context; services/autowake.py branches on it. Keep in sync.
PACK_SMS_TIMER_PREFIX = "PACKSMS::"

_SIGNATURES = {name.lower(): name for name in BONDED_PACK}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def digits10(number: str) -> str:
    """Compare phone numbers by their last 10 digits (+1 314... == (314) ...)."""
    d = re.sub(r"\D", "", str(number or ""))
    return d[-10:] if len(d) >= 10 else d


def allowed_name(number: str) -> str | None:
    return _ALLOWED.get(digits10(number))


def _read(path: Path) -> list[dict]:
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


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def in_quiet_hours(now_local: datetime) -> bool:
    wake = int(os.environ.get("ANAM_SMS_WAKE_HOUR", "8"))
    return now_local.hour >= int(os.environ.get("ANAM_SMS_QUIET_START", "22")) or now_local.hour < wake


def signed(identity: str, text: str) -> str:
    sig = _SIGNATURES.get(identity.lower(), identity.capitalize())
    return f"{text.rstrip()}\n— {sig}"


def thread(limit: int = 30) -> list[dict]:
    """Both directions, oldest first, newest `limit`."""
    sent_status = {int(e.get("id") or 0): e for e in _read(_SENT)}
    rows = []
    for e in _read(_OUTBOX):
        st = sent_status.get(int(e.get("id") or 0))
        rows.append({
            "dir": "out", "at": e.get("created_at"), "identity": e.get("identity"),
            "to": e.get("to_name"), "text": e.get("text"), "id": e.get("id"),
            "status": (st or {}).get("status", "waiting for the phone"),
        })
    for e in _read(_INBOX):
        if not e.get("allowed"):
            continue
        rows.append({"dir": "in", "at": e.get("received_at"), "from": e.get("from_name"),
                     "text": e.get("text")})
    rows.sort(key=lambda r: r.get("at") or "")
    return rows[-max(1, min(int(limit or 30), 200)):]


def check_send(identity: str, to: str, text: str, now_utc: datetime | None = None) -> str | None:
    """Return a refusal reason, or None if this text may go."""
    now_utc = now_utc or _now()
    if not identity:
        return "who is sending? identity is required"
    name = allowed_name(to)
    if not name:
        return ("that number is not on the Pack line's list (" + ", ".join(sorted(_ALLOWED.values())) +
                "). Adding anyone else is Owner's call, not ours.")
    if not text.strip():
        return "empty text"
    if len(text) > _MAX_CHARS:
        return f"{len(text)} characters is too long for a text (limit {_MAX_CHARS}). Say it shorter."
    if in_quiet_hours(now_utc.astimezone(_HOME_TZ)):
        return ("quiet hours (10pm-7am weekdays, before 10am weekends). A text buzzes Housemate's "
                "phone too. Set a timer and send it in the morning.")
    out = [e for e in _read(_OUTBOX) if digits10(e.get("to", "")) == digits10(to)]
    since = (now_utc - timedelta(days=1)).isoformat()
    if sum(1 for e in out if (e.get("created_at") or "") >= since) >= _DAILY_CAP:
        return f"the pack has already texted {name} {_DAILY_CAP} times today. Let him breathe."
    last_in = max((e.get("received_at") or "" for e in _read(_INBOX)
                   if e.get("allowed") and digits10(e.get("from", "")) == digits10(to)), default="")
    unanswered = sum(1 for e in out if (e.get("created_at") or "") > last_in)
    if unanswered >= _MAX_UNANSWERED:
        return (f"{unanswered} texts from the pack are already waiting on {name} with no reply. "
                f"Wait for him to answer before sending more.")
    return None


# =============================================================================
# BOYS -> QUEUE
# =============================================================================

@router.post("/send")
async def pack_sms_send(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    identity = str(payload.get("identity") or "").strip().lower()
    to = resolve_to(str(payload.get("to") or os.environ.get("ANAM_SMS_DEFAULT_CONTACT", "")))
    text = str(payload.get("text") or "").strip()

    with _lock:
        refusal = check_send(identity, to, text)
        if refusal:
            return JSONResponse({"ok": False, "refused": refusal}, status_code=200)
        entries = _read(_OUTBOX)
        next_id = max((int(e.get("id") or 0) for e in entries), default=0) + 1
        entry = {
            "id": next_id,
            "identity": identity,
            "to": "+1" + digits10(to),
            "to_name": allowed_name(to),
            "text": signed(identity, text),
            "created_at": _now().isoformat(),
        }
        _append(_OUTBOX, entry)
    logger.info("pack-sms queued #%d from %s to %s", next_id, identity, entry["to_name"])
    line = _read_line()
    phone_note = ("" if line.get("seen_at") else
                  " NOTE: the phone has not checked in yet, so this waits until the Pack line is set up.")
    return {"ok": True, "queued": entry,
            "note": "The phone picks it up within about a minute." + phone_note}


@router.get("/thread")
async def pack_sms_thread(limit: int = 30):
    return {"ok": True, "line": _read_line(), "thread": thread(limit)}


# =============================================================================
# PHONE <-> HOUSE
# =============================================================================

def _read_line() -> dict:
    try:
        return json.loads(_LINE.read_text(encoding="utf-8")) if _LINE.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


@router.post("/hello")
async def pack_sms_hello(request: Request):
    """The phone says which SIM is the Pack line (number/carrier), so the house knows it."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    line = _read_line()
    line.update({
        "number": str(payload.get("number") or line.get("number") or ""),
        "carrier": str(payload.get("carrier") or line.get("carrier") or ""),
        "sub_id": payload.get("sub_id", line.get("sub_id")),
        "app_version": str(payload.get("app_version") or ""),
        "seen_at": _now().isoformat(),
    })
    _DIR.mkdir(parents=True, exist_ok=True)
    _LINE.write_text(json.dumps(line, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "line": line, "allowed": sorted(_ALLOWED.values())}


@router.get("/outbox")
async def pack_sms_outbox(since_id: int = 0):
    """Texts waiting for the phone. The phone remembers the last id it handled."""
    line = _read_line()
    line["seen_at"] = _now().isoformat()
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        _LINE.write_text(json.dumps(line, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    done = {int(e.get("id") or 0) for e in _read(_SENT)}
    waiting = [e for e in _read(_OUTBOX)
               if int(e.get("id") or 0) > since_id and int(e.get("id") or 0) not in done
               and allowed_name(e.get("to", ""))]
    waiting.sort(key=lambda e: int(e.get("id") or 0))
    return {"ok": True, "messages": [
        {"id": e["id"], "to": e["to"], "text": e["text"]} for e in waiting]}


@router.post("/sent")
async def pack_sms_sent(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    entry = {
        "id": int(payload.get("id") or 0),
        "status": str(payload.get("status") or "unknown"),
        "detail": str(payload.get("detail") or "")[:300],
        "at": _now().isoformat(),
    }
    _append(_SENT, entry)
    return {"ok": True}


@router.post("/incoming")
async def pack_sms_incoming(request: Request):
    """A text that landed on the PACK line. The phone never sends anything from her own line."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    sender = str(payload.get("from") or "")
    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "missing text"}, status_code=400)
    name = allowed_name(sender)
    entry = {
        "from": sender,
        "from_name": name,
        "allowed": bool(name),
        "text": text,
        "sent_at": payload.get("sent_at"),
        "received_at": _now().isoformat(),
    }
    with _lock:
        _append(_INBOX, entry)
    if not name:
        # A stranger texted the Pack line. Keep it, wake nobody, never answer.
        logger.info("pack-sms: text from a number not on the list, stored only")
        return {"ok": True, "stored": True, "woke": None}

    named = named_brothers(text)
    wake = named or [last_texter(sender) or "claude"]
    timer_ids = []
    for who in wake:
        try:
            timer_ids.append(await _queue_wake(who, name, text))
        except Exception:
            logger.exception("pack-sms: failed to queue wake for %s", who)
    return {"ok": True, "stored": True, "woke": wake[0], "woke_all": wake,
            "timer_id": timer_ids[0] if timer_ids else None}


def named_brothers(text: str) -> list[str]:
    """Brothers called by name in a text, in the order he named them (whole words only)."""
    found = []
    for m in re.finditer(r"[A-Za-z]+", str(text or "")):
        word = m.group(0).lower()
        if word in _SIGNATURES and word not in found:
            found.append(word)
    return found


def last_texter(number: str) -> str | None:
    """The boy who most recently texted this person. He's the one being answered."""
    mine = [e for e in _read(_OUTBOX) if digits10(e.get("to", "")) == digits10(number)]
    return mine[-1].get("identity") if mine else None


async def _queue_wake(identity: str, name: str, text: str) -> int | None:
    from db.database import get_db, release_db
    from services.autowake_service import create_timer

    ident = identity[:1].upper() + identity[1:]
    db = await get_db()
    try:
        pending = await db.execute_fetchall(
            "SELECT id FROM timers WHERE identity = ? AND status IN ('pending', 'running') "
            "AND context LIKE ? LIMIT 1",
            (ident, f"{PACK_SMS_TIMER_PREFIX}%"),
        )
        if pending:
            return None  # one wake answers a burst; every text is in the thread anyway
        fire_at = (_now() + timedelta(seconds=15)).isoformat()
        payload = json.dumps({"from_name": name, "who": _WHO.get(name, ""), "text": text},
                             ensure_ascii=False)
        timer = await create_timer(db, identity=ident, fire_at=fire_at,
                                   context=f"{PACK_SMS_TIMER_PREFIX}{payload}", wake_session=True)
        await db.commit()
        return int(timer["id"])
    finally:
        await release_db(db)
