'Local mcp adapter.'
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = os.environ.get("ANAM_URL", "http://127.0.0.1:8790").rstrip("/")
ANAM_ROOT = Path(os.environ.get("ANAM_ROOT", str(Path(__file__).resolve().parents[1] / "ui")))
ENV_PATH = str(ANAM_ROOT / ".env")
_TOOLS_DIR = ANAM_ROOT / "tools"


if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    from wrist_gate import run_gate as _run_gate
except Exception as _e:
    _GATE_ERR = "%s: %s" % (type(_e).__name__, _e)

    def _run_gate(identity, at_hhmm, now_local):
        return 1, "UI wrist gate is unavailable; configure ANAM_ROOT before sending.", "missing-gate"


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("anam-touch")


def _key() -> str:
    configured = os.environ.get("ANAM_API_KEY", "").strip()
    if configured:
        return configured
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*ANAM_API_KEY\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    raise RuntimeError("ANAM_API_KEY not found in anam/.env")


def _post(path: str, body: dict) -> str:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _key()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode(errors="replace")


def _get(path: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + _key()},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode(errors="replace"))


@mcp.tool()
def wrist_tap(text: str, from_identity: str) -> str:
    'Queue a wrist message through the configured UI gate.'
    if len(text) > 70:
        return "REFUSED: %d chars - the band truncates at 70. Shorten it yourself so YOU choose the cut." % len(text)
    now = datetime.now(ZoneInfo(os.environ.get("ANAM_TIMEZONE", "UTC")))
    rc, gate_out, road = _run_gate(from_identity, now.strftime("%H:%M"), now)
    if rc != 0:
        return "GATE SAYS DO NOT SEND - standing down.\n" + gate_out.strip()
    return "%s\n\nGate was clear [%s]:\n%s" % (
        _post("/api/wearable/send", {"text": text, "from_identity": from_identity}),
        road, gate_out.strip())


@mcp.tool()
def orb_set(identity: str, color: str, shape: str = "solid", motion: str = "breathing",
            feeling: str = "", kaomoji: str = "") -> str:
    'Orb set.'
    body = {"identity": identity.lower(), "color": color, "shape": shape, "motion": motion,
            "feeling": feeling[:120]}
    if kaomoji:
        body["kaomoji"] = kaomoji
    return _post("/api/hub/orb", body)


@mcp.tool()
def timer_set(identity: str, fire_at: str, context: str, wake_session: bool = True) -> str:
    'Timer set.'
    ident = identity[:1].upper() + identity[1:]
    return _post("/api/autowake/timers",
                 {"identity": ident, "fire_at": fire_at, "context": context, "wake_session": wake_session})


_AGAIN_LEDGER = ANAM_ROOT / "data" / "autowake" / "again_ledger.json"
_AGAIN_MIN, _AGAIN_MAX, _AGAIN_PER_DAY = 20, 180, 3
_HOME_TZ = ZoneInfo(os.environ.get("ANAM_TIMEZONE", "UTC"))


def _again_counts() -> dict:
    try:
        return json.loads(_AGAIN_LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@mcp.tool()
def again(identity: str, minutes: int, what_next: str) -> str:
    'Schedule another session for this identity, 20-180 minutes later, at most three per day.'
    ident = (identity or "").strip()
    ident = ident[:1].upper() + ident[1:]
    if not ident:
        return "again: NOT set. Pass your identity, e.g. 'Claude'."
    if not (what_next or "").strip():
        return "again: NOT set. what_next is empty; say where you stopped and what to do first."
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "again: NOT set. minutes must be a number from 20 to 180."
    if not (_AGAIN_MIN <= minutes <= _AGAIN_MAX):
        return f"again: NOT set. minutes must be {_AGAIN_MIN}-{_AGAIN_MAX} (you asked {minutes})."

    now = datetime.now(_HOME_TZ)
    today = now.strftime("%Y-%m-%d")
    ledger = _again_counts()
    ledger = {today: ledger.get(today, {})}
    used = int(ledger[today].get(ident, 0))
    if used >= _AGAIN_PER_DAY:
        return (f"again: NOT set. {ident} has already continued {used} hours today "
                f"(limit {_AGAIN_PER_DAY}). Finish what you can now and leave the rest "
                "in your program file for the next scheduled wake.")

    from datetime import timedelta
    fire = now + timedelta(minutes=minutes)
    context = (f"[AGAIN — continuing your own hour ({used + 1}/{_AGAIN_PER_DAY} today), "
               f"asked for at {now.strftime('%I:%M %p').lstrip('0')}]\n"
               "This is YOUR earlier hour, picked back up. Do the work, not a greeting.\n"
               f"Where you stopped / what to do first:\n{what_next.strip()}")
    try:
        result = _post("/api/autowake/timers",
                       {"identity": ident, "fire_at": fire.isoformat(), "context": context,
                        "wake_session": True})
    except Exception as e:
        return f"again: NOT set. Anam refused or is down: {e}"

    ledger[today][ident] = used + 1
    _AGAIN_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = _AGAIN_LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    tmp.replace(_AGAIN_LEDGER)
    return (f"again: set. {ident} wakes at {fire.strftime('%I:%M %p').lstrip('0')} "
            f"({minutes} min) to continue. {used + 1}/{_AGAIN_PER_DAY} used today. Anam said: {result[:200]}")


@mcp.tool()
def nudge_judge(identity: str, verdict: str, back: int = 0, kind: str = "") -> str:
    'Nudge judge.'
    ident = (identity or "").strip()
    ident = ident[:1].upper() + ident[1:]
    try:
        return _post("/api/memory-nudges/judge",
                     {"identity": ident, "verdict": verdict, "back": int(back or 0), "kind": kind or ""})
    except Exception as e:
        return f"nudge_judge: NOT recorded: {e}"


@mcp.tool()
def timer_list(identity: str = "", hours: int = 24, contains: str = "") -> str:
    'Timer list.'
    import time as _t
    now = _t.time()
    horizon = now + max(1, hours) * 3600
    try:
        data = _get("/api/autowake/timers")
    except Exception as e:
        return "COULD NOT READ TIMERS: %s (is Anam up on 8790?)" % e

    rows = []
    for t in data.get("timers", []):
        if (t.get("status") or "pending") != "pending":
            continue
        ep = t.get("fire_at_epoch") or 0
        if not (now - 300 <= ep <= horizon):
            continue
        ctx = " ".join((t.get("context") or "").split())
        if contains and contains.lower() not in ctx.lower():
            continue
        if identity and (t.get("identity") or "").lower() != identity.lower():
            continue
        rows.append((ep, t.get("id"), t.get("identity") or "?", t.get("fire_at") or "?", ctx))

    if not rows:
        scope = identity or "the whole pack"
        extra = (' matching "%s"' % contains) if contains else ""
        return "NOTHING ARMED for %s in the next %dh%s. Clear to arm." % (scope, hours, extra)

    rows.sort()
    out = ["%d armed in the next %dh (pending only), soonest first:" % (len(rows), hours), ""]
    for ep, tid, ident, fire_at, ctx in rows:
        mins = int((ep - now) // 60)
        when = ("in %dh%02dm" % (mins // 60, mins % 60)) if mins >= 0 else ("OVERDUE %dm" % -mins)
        local = _t.strftime("%a %H:%M", _t.localtime(ep))
        out.append("  #%-4s %-9s %s (%s)  %s" % (tid, ident, local, when, fire_at))
        out.append("        %s" % (ctx[:150] + ("..." if len(ctx) > 150 else "")))
    out.append("")
    out.append("A timer owned by a BROTHER is his to deliver - do not double it.")
    return chr(10).join(out)

@mcp.tool()
def pack_audio_list() -> str:
    'Pack audio list.'
    try:
        data = _get("/api/voice/queue")
    except Exception as e:
        return "COULD NOT READ THE DRAWER: %s (is Anam up on 8790?)" % e
    msgs = data.get("messages", [])
    if not msgs:
        return "The drawer is EMPTY. Nothing waiting for her."
    out = ["%d waiting in her drawer, oldest first:" % len(msgs), ""]
    for m in msgs:
        text = " ".join((m.get("text") or "").split())
        out.append("  %s  %-9s %s" % ((m.get("timestamp") or "?")[:16],
                                       m.get("identity") or "?",
                                       text[:90] + ("..." if len(text) > 90 else "")))
    return chr(10).join(out)


@mcp.tool()
def pack_audio_leave(identity: str, text: str) -> str:
    'Leave a spoken note in the configured audio drawer. Requires your speech service and voice settings.'
    text = (text or "").strip()
    if not text:
        return "Nothing to say: text is empty."
    ident = (identity or "").strip()
    if not ident:
        return "identity is required (e.g. 'Avery')."
    ident = ident[:1].upper() + ident[1:].lower()
    req = urllib.request.Request(
        BASE + "/api/voice/queue",
        data=json.dumps({"identity": ident, "text": text}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _key()},
        method="POST",
    )
    try:

        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        return "NOT QUEUED: Anam said %s %s" % (e.code, e.read().decode(errors="replace")[:200])
    except Exception as e:
        return "NOT QUEUED: %s (is Anam up on 8790?)" % e
    if body.get("status") != "queued":
        return "NOT QUEUED: %s" % json.dumps(body)[:200]
    return ("Queued %s in her Pack Audio drawer. She hears it when she asks her Echo "
            "'Pack Audio, surprise me'. Copy in OneDrive/Companion Vault/12_Voice." % body.get("message_id"))


@mcp.tool()
def pack_text(identity: str, text: str, to: str = "") -> str:
    'Queue an SMS to an explicitly configured allowed contact. An Android SMS client is required.'
    if not (to or "").strip():
        return "Refused: specify a contact configured in the UI SMS allowlist."
    text = (text or "").strip()
    if not to.strip():
        return "Refused: specify a contact configured in the UI SMS allowlist."
    if not text:
        return "Nothing to send: text is empty."
    try:
        body = json.loads(_post("/api/pack-sms/send", {"identity": (identity or "").lower(), "text": text,
                                                         "to": to}))
    except Exception as e:
        return "NOT SENT: %s (check your configured Anam connection.)" % e
    if body.get("refused"):
        return "NOT SENT, the guard said: " + body["refused"]
    if not body.get("ok"):
        return "NOT SENT: %s" % json.dumps(body)[:200]
    q = body.get("queued", {})
    return "Queued #%s for %s. %s\nIt will read:\n%s" % (q.get("id"), q.get("to_name"), body.get("note", ""), q.get("text"))


@mcp.tool()
def pack_texts(limit: int = 20) -> str:
    'Read configured SMS thread and delivery receipts.'
    try:
        body = _get("/api/pack-sms/thread?limit=%d" % max(1, min(int(limit or 20), 100)))
    except Exception as e:
        return "COULD NOT READ the Pack line: %s" % e
    rows = body.get("thread", [])
    line = body.get("line", {})
    head = "Pack line %s. Last phone check-in: %s" % (line.get("number", "?"), line.get("seen_at") or "never")
    if not rows:
        return head + "\nNo texts yet. You'd be the first."
    out = [head, ""]
    for r in rows:
        try:
            when = datetime.fromisoformat(r.get("at")).astimezone(_HOME_TZ).strftime("%a %m-%d %I:%M %p")
        except Exception:
            when = (r.get("at") or "?")[:16]
        if r.get("dir") == "out":
            out.append("%s  %s -> %s [%s]: %s" % (when, r.get("identity"), r.get("to"), r.get("status"), r.get("text")))
        else:
            out.append("%s  %s: %s" % (when, r.get("from"), r.get("text")))
    return chr(10).join(out)


def _put(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _key()},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode(errors="replace") or "{}")


@mcp.tool()
def todays_win(identity: str, text: str = "", replace: bool = False) -> str:
    'Read or record one daily win. Existing text requires explicit replace=True.'
    try:
        current = _get("/api/hub/todays-win")
    except Exception as e:
        return "COULD NOT READ today's win: %s (is Anam up on 8790?)" % e
    have = (current.get("text") or "").strip() if current.get("exists") else ""
    text = (text or "").strip()
    if not text:
        return ("Today's win (%s): %s" % (current.get("date", "?"), have)) if have\
            else "No win logged yet today. Ask her about one good thing from her day."
    if have and not replace:
        return ("NOT WRITTEN: today already has a win, and writing replaces it:\n  %s\n"
                "Fold it into yours, then call again with replace=True." % have)
    who = (identity or "").strip().capitalize() or "a brother"
    if "logged by" not in text.lower():
        text = "%s (Logged by %s)" % (text, who)
    try:
        _put("/api/hub/todays-win", {"text": text})
        back = _get("/api/hub/todays-win")
    except Exception as e:
        return "NOT WRITTEN: %s" % e
    if (back.get("text") or "").strip() != text:
        return "WRITTEN, but the read-back doesn't match. It reads: %s" % back.get("text")
    return "Logged and read back for %s: %s" % (back.get("date"), text)


KRITA_EXE = os.environ.get("KRITA_EXE", r"C:\Program Files\Krita (x64)\bin\krita.exe")
KRITA_HEALTH = "http://localhost:5678/health"


def _krita_up() -> bool:
    try:
        with urllib.request.urlopen(KRITA_HEALTH, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


@mcp.tool()
def krita_open(wait_seconds: int = 60) -> str:
    'Krita open.'
    import subprocess
    import time
    if _krita_up():
        return "Krita is already open and the plugin is answering. Go paint."
    if not Path(KRITA_EXE).exists():
        return "Can't open Krita: %s isn't there. Tell Bunny the install moved." % KRITA_EXE
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([KRITA_EXE], creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return "Couldn't launch Krita: %s" % e
    deadline = time.time() + max(5, min(int(wait_seconds or 60), 180))
    while time.time() < deadline:
        time.sleep(2)
        if _krita_up():
            return "Krita is open and the plugin answered. Your krita.* tools work now."
    return ("Launched Krita but the plugin hasn't answered yet (it can take a while on a cold start). "
            "Call krita.krita_health in a minute; if it still can't connect, the MCP plugin may be "
            "switched off inside Krita (Settings > Configure Krita > Python Plugin Manager).")


if __name__ == "__main__":
    mcp.run()
