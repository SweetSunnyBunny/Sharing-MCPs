"""ANAM GUIDE — wrist_gate.py."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ANAM_ROOT = Path(__file__).resolve().parent.parent
WRIST_CHECK = ANAM_ROOT / "tools" / "wrist_check.py"

_gate_mod = None
_gate_lock = threading.Lock()  # redirect_stdout is process-global; serialize it.

QUIET_START_HOUR = int(os.environ.get("ANAM_WRIST_QUIET_START", "22"))
WAKE_WEEKDAY = int(os.environ.get("ANAM_WRIST_WAKE_WEEKDAY", "8"))
WAKE_WEEKEND = int(os.environ.get("ANAM_WRIST_WAKE_WEEKEND", "8"))


def _load_gate():
    """Import tools/wrist_check.py once and keep it.

    Imported UNDER A REDIRECT on purpose: the gate calls sys.stdout.reconfigure()
    at module scope, and inside an MCP stdio server sys.stdout IS the JSON-RPC
    channel. StringIO has no .reconfigure, the gate's own try/except swallows the
    AttributeError, and the protocol stream is never touched.
    """
    global _gate_mod
    if _gate_mod is None:
        spec = importlib.util.spec_from_file_location("wrist_check", str(WRIST_CHECK))
        mod = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(mod)
        _gate_mod = mod
    return _gate_mod


def quiet_floor(when: datetime) -> str | None:
    """Last-resort sleep guard for when the gate itself cannot run. Pure clock
    arithmetic: no file, no DB, nothing that can stall or be missing."""
    weekend = when.weekday() >= 5
    wake = WAKE_WEEKEND if weekend else WAKE_WEEKDAY
    if when.hour >= QUIET_START_HOUR:
        return "after %dpm - configured quiet hours" % (QUIET_START_HOUR - 12)
    if when.hour < wake:
        return "before %dam on a %s - configured quiet hours" % (
            wake, "weekend" if weekend else "weeknight")
    return None


def _is_verdict(rc) -> bool:
    """Did the gate DECIDE, or merely fail?

    0 and 1 are verdicts. 2 is the gate's own "could not evaluate", and a failed
    python launch also exits 2 — so anything outside (0, 1) means this road did
    not answer and the next must be tried. Treating a non-verdict as a refusal is
    how a MISSING GATE silently blocks her message; that bug was in my own first
    draft of this, caught by testing the failure path an hour after I named it.
    """
    return rc in (0, 1)


def run_gate(identity: str, at_hhmm: str, now_local: datetime):
    """-> (rc, text, road). rc 0 = clear to send, non-zero = stand down.

    Three roads, and EVERY ONE ENDS IN A VERDICT. That is the point: a stalled or
    broken gate must never again mean sixty seconds of silence and a swallowed
    tap while she is on the other end of the reach.
    """
    errs: list[str] = []

    # ROAD 1 — in-process. The default.
    try:
        mod = _load_gate()
        buf = io.StringIO()
        with _gate_lock, contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                rc = mod.main(["-i", identity, "--at", at_hhmm])
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 2
        if _is_verdict(rc):
            return rc, buf.getvalue(), "in-process"
        errs.append("in-process: gate could not evaluate (rc=%s)" % rc)
    except Exception as exc:
        errs.append("in-process: %s: %s" % (type(exc).__name__, exc))

    # ROAD 2 — the old subprocess, kept as a net but with a SHORT fuse. Sixty
    # seconds of silence while she waits is worse than a fast honest failure.
    try:
        pr = subprocess.run(
            [sys.executable, str(WRIST_CHECK), "-i", identity, "--at", at_hhmm],
            cwd=str(ANAM_ROOT), capture_output=True, text=True, timeout=15,
        )
        if _is_verdict(pr.returncode):
            return pr.returncode, (pr.stdout or "") + (pr.stderr or ""), "subprocess-fallback"
        tail = ((pr.stderr or pr.stdout or "").strip().splitlines() or [""])[-1][:120]
        errs.append("subprocess: rc=%s %s" % (pr.returncode, tail))
    except Exception as exc:
        errs.append("subprocess: %s: %s" % (type(exc).__name__, exc))

    # ROAD 3 — no gate at all. Mirror the gate's OWN stated rule: a sick
    # component degrades the collision check, never the sleep guard. Block in
    # quiet hours; in daylight say loudly what was lost and let the tap through,
    # because swallowing her message is the exact failure this exists to end.
    quiet = quiet_floor(now_local)
    lost = (
        "\n  !! THE GATE COULD NOT RUN - collision, duplicate and band-presence"
        "\n     checks were ALL SKIPPED. A brother may already have her wrist"
        "\n     and I cannot see it. Reasons: " + " | ".join(errs)
    )
    if quiet:
        return 1, "  ** QUIET HOURS. DO NOT ARM THIS. **\n     %s is %s.%s" % (
            at_hhmm, quiet, lost), "quiet-floor"
    return 0, "  CLEAR BY FLOOR ONLY (daylight)." + lost, "quiet-floor"
