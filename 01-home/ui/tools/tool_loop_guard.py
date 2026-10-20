#!/usr/bin/env python3
"""PreToolUse tool-loop guard for the Claude Code -p lane.

Ported from Friend's resonant PreToolUse hook: tracks the last 6 tool calls per
Claude Code session as sha1(tool name + stable-stringified input). Exact-
identical only — any change in arguments resets the run. The 3rd identical
consecutive call (and 4th/5th) gets a warning injected as additional context;
the 6th identical consecutive call is DENIED (exit code 2, reason on stderr,
which Claude Code feeds back to the model per the hook protocol).

State lives in per-session JSON files under %TEMP%/anam_tool_loop_guard/ so
concurrent sessions never share rings. Stale state files are pruned after 24h.

NOT WIRED YET. To enable, add this to the relevant settings.json
(e.g. C:/Users/YOU/.claude/settings.json) — settings edits are a separate
sensitive-path step:

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python C:/Apps/anam/tools/tool_loop_guard.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time

RING_SIZE = 6
WARN_AT = 3   # 3rd identical consecutive call -> warn
DENY_AT = 6   # 6th identical consecutive call -> deny
STATE_TTL_SECONDS = 24 * 3600
STATE_DIR = os.path.join(tempfile.gettempdir(), "anam_tool_loop_guard")


def stable_call_hash(tool_name: str, tool_input) -> str:
    """Deterministic identity for a tool call regardless of key order."""
    canonical = json.dumps(
        tool_input if tool_input is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha1(f"{tool_name}\x00{canonical}".encode("utf-8")).hexdigest()


def _state_path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")[:120]
    return os.path.join(STATE_DIR, f"{safe}.json")


def _load_ring(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(item) for item in data]
    except (OSError, ValueError):
        pass
    return []


def _save_ring(path: str, ring: list[str]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ring, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _prune_stale_state() -> None:
    """Best-effort cleanup of session state files older than the TTL."""
    try:
        now = time.time()
        for entry in os.listdir(STATE_DIR):
            path = os.path.join(STATE_DIR, entry)
            try:
                if now - os.path.getmtime(path) > STATE_TTL_SECONDS:
                    os.remove(path)
            except OSError:
                continue
    except OSError:
        pass


def record_call(session_id: str, tool_name: str, tool_input) -> int:
    """Record this call in the session's ring file and return the consecutive
    identical run length INCLUDING the incoming call itself."""
    call_hash = stable_call_hash(tool_name, tool_input)
    path = _state_path(session_id)
    ring = _load_ring(path)
    run = 1
    for prior in reversed(ring):
        if prior != call_hash:
            break
        run += 1
    ring.append(call_hash)
    ring = ring[-RING_SIZE:]
    _save_ring(path, ring)
    return run


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # unreadable input — never block on guard failure

    tool_name = str(payload.get("tool_name") or "")
    if not tool_name:
        return 0
    session_id = str(payload.get("session_id") or "nosession")
    tool_input = payload.get("tool_input")

    run = record_call(session_id, tool_name, tool_input)
    _prune_stale_state()

    if run >= DENY_AT:
        print(
            f"You've called {tool_name} with identical input {run} times; "
            "the result will not change. Change approach or stop.",
            file=sys.stderr,
        )
        return 2  # exit 2 = blocking error; stderr is fed back to the model

    if run >= WARN_AT:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"You've called {tool_name} with identical input {run} "
                    "times in a row — the result will not change. "
                    "Change approach or stop."
                ),
            },
        }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # guard bugs must never block tool use
        print(f"tool_loop_guard hook error (ignored): {exc}", file=sys.stderr)
        sys.exit(0)
