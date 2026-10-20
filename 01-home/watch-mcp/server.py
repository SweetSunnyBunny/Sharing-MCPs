'Local mcp adapter.'
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

ANAM_ROOT = Path(os.environ.get("ANAM_ROOT", str(Path(__file__).resolve().parents[1] / "ui")))
ENV_FILE = ANAM_ROOT / ".env"
REPLIES_FILE = ANAM_ROOT / "data" / "wearable_replies.jsonl"
BASE = os.environ.get("ANAM_URL", "http://127.0.0.1:8790").rstrip("/")
MAX_CHARS = 70

mcp = FastMCP("watch")


def _anam_key() -> str:
    'Anam key.'
    configured = os.environ.get("ANAM_API_KEY", "").strip()
    if configured:
        return configured
    try:
        m = re.search(r"^ANAM_API_KEY=(.+)$", ENV_FILE.read_text(encoding="utf-8", errors="replace"), re.M | re.I)
        return m.group(1).strip().strip('"').strip() if m else ""
    except Exception:
        return ""


def _api(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {_anam_key()}",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


_TOOLS_DIR = ANAM_ROOT / "tools"


if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    from wrist_gate import run_gate as _run_gate
except Exception as _e:
    _GATE_ERR = "%s: %s" % (type(_e).__name__, _e)

    def _run_gate(identity, at_hhmm, now_local):
        return 1, "UI wrist gate is unavailable; configure ANAM_ROOT before sending.", "missing-gate"


@mcp.tool()
def watch_tap(text: str, identity: str = "") -> str:
    'Queue a wrist message after the configured UI gate. Identity is required; maximum 70 characters.'
    text = (text or "").strip()
    identity = (identity or "").strip()
    if not identity:
        return "Refused: say who you are (identity), so the tap on her wrist carries your name, not someone else's."
    if not text:
        return "Refused: empty text."
    if len(text) > MAX_CHARS:
        return f"Refused: {len(text)} chars — the band truncates past {MAX_CHARS}. Shorten it."


    now = datetime.now(ZoneInfo(os.environ.get("ANAM_TIMEZONE", "UTC")))
    rc, gate_out, road = _run_gate(identity, now.strftime("%H:%M"), now)
    if rc != 0:
        return "GATE SAYS DO NOT SEND — standing down.\n" + gate_out.strip()
    res = _api("/api/wearable/send", {"text": text, "from_identity": identity})
    if res.get("ok"):
        q = res.get("queued", {})
        return f"Queued tap #{q.get('id')} from {identity} ({len(text)} chars). Gate was clear [{road}]:\n{gate_out.strip()}"
    return f"Send failed: {res}"


@mcp.tool()
def watch_inbox(limit: int = 10) -> str:
    'Read recent wrist replies, without changing a cursor.'
    if not REPLIES_FILE.exists():
        return "No replies file yet — she has never replied from the band, or the path moved."
    lines = [ln for ln in REPLIES_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    out = []
    for ln in lines[-max(1, min(limit, 100)):]:
        try:
            e = json.loads(ln)
            out.append(f"[{e.get('received_at') or e.get('created_at') or '?'}] {e.get('text') or e.get('raw') or ln}")
        except json.JSONDecodeError:
            out.append(ln)
    return f"{len(lines)} replies total. Last {len(out)}:\n" + "\n".join(out)


@mcp.tool()
def watch_pending() -> str:
    'Read undelivered messages without advancing the delivery cursor.'
    return json.dumps(_api("/api/wearable/pending"), ensure_ascii=False, indent=1)


@mcp.tool()
def watch_vitals(history: int = 0) -> str:
    'Read vitals and freshness metadata. Stale data is last-known; one frame cannot establish attention or sleep.'
    res = _api(f"/api/wearable/vitals?history={max(0, min(history, 500))}")
    return json.dumps(res, ensure_ascii=False, indent=1)


@mcp.tool()
def watch_status() -> str:
    'Read last-reply metadata and status.'
    return json.dumps(_api("/api/wearable/status"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    mcp.run()
