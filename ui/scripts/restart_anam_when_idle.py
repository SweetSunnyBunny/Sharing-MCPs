"""Restart only Anam after every provider turn has safely finished.

Launch this helper from a supervisor-owned process, not from Anam's own process
tree. It waits for the authenticated runtime report to stay idle, restarts the
Anam service through the stack supervisor, and records live verification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "runtime" / "anam-stack.json"
RECEIPT = ROOT / "data" / "runtime" / "anam-armed-restart.json"
REQUEST = ROOT / "data" / "runtime" / "anam-stack.request.json"
RUNTIME_URL = "http://127.0.0.1:8790/api/tool-gateway/runtime"


def record(status: str, **details) -> None:
    payload = {
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
        "helper_pid": os.getpid(),
        **details,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECEIPT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, RECEIPT)


def runtime_report(api_key: str) -> dict:
    request = Request(
        RUNTIME_URL,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def current_anam_pid() -> int | None:
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return int(manifest["services"]["anam"]["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def queue_supervisor_restart(reason: str) -> None:
    """Queue the restart; the surviving supervisor owns final verification."""
    payload = {
        "action": "restart",
        "service": "anam",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "receipt_name": RECEIPT.name,
    }
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = REQUEST.with_suffix(".armed.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, REQUEST)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="code activation")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--settle-seconds", type=float, default=8.0)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("ANAM_API_KEY", "")
    if not api_key:
        record("failed", reason="ANAM_API_KEY is unavailable")
        return 1

    old_pid = current_anam_pid()
    record("waiting_for_provider_idle", reason=args.reason, old_pid=old_pid)
    deadline = time.monotonic() + args.timeout_seconds
    idle_since: float | None = None

    while time.monotonic() < deadline:
        try:
            processes = runtime_report(api_key).get("codex_processes", [])
            busy = sorted(
                str(entry.get("identity") or "unknown")
                for entry in processes
                if entry.get("busy")
            )
        except Exception:
            idle_since = None
            time.sleep(2)
            continue

        if busy:
            idle_since = None
        elif idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= args.settle_seconds:
            break
        time.sleep(2)
    else:
        record(
            "failed",
            reason="Provider turns did not reach a stable idle window",
            requested_reason=args.reason,
        )
        return 1

    record("restarting_anam_only", reason=args.reason, old_pid=old_pid)
    queue_supervisor_restart(args.reason)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        record("failed", reason=f"{type(exc).__name__}: {exc}")
        raise
