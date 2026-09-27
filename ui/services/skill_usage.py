"""Best-effort local skill telemetry and guarded improvement staging."""

# ANAM GUIDE: SKILL USAGE COUNTER
# What: Keeps a simple tally of which skills get used and when (data/skill-usage.json), plus a queue file for proposed skill improvements awaiting review.
# Called by: services/skill_runtime.py (records each use), scripts/anam_doctor.py and scripts/anam_context_mcp.py (read the stats)
# Edit here when: You want to change what gets counted about skill usage or where the tally files live.

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from config import DATA_DIR


USAGE_PATH = DATA_DIR / "skill-usage.json"
PENDING_PATH = DATA_DIR / "skill-improvement-pending.jsonl"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_usage() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_usage(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="skill-usage-", suffix=".json", dir=str(DATA_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(name, USAGE_PATH)
    finally:
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass


def record_skill_usage(skills: Iterable, *, identity: str | None = None) -> None:
    now = _now()
    with _lock:
        data = _load_usage()
        for skill in skills:
            key = f"{getattr(skill, 'source', 'local')}:{getattr(skill, 'name', 'unknown')}"
            entry = data.setdefault(key, {"count": 0})
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_used_at"] = now
            entry["last_identity"] = identity
            entry["path"] = str(getattr(skill, "path", ""))
        _write_usage(data)


def get_skill_usage() -> dict:
    with _lock:
        return _load_usage()


def stage_skill_improvement(
    skill_name: str,
    reason: str,
    proposed_change: str,
    *,
    source: str = "agent",
) -> dict:
    """Append a proposal for human review; never modify a skill automatically."""
    skill_name = str(skill_name or "").strip()[:120]
    reason = str(reason or "").strip()[:2000]
    proposed_change = str(proposed_change or "").strip()[:8000]
    if not skill_name or not reason or not proposed_change:
        raise ValueError("skill_name, reason, and proposed_change are required")
    item = {
        "id": str(uuid.uuid4()),
        "created_at": _now(),
        "status": "pending_review",
        "source": str(source or "agent")[:80],
        "skill_name": skill_name,
        "reason": reason,
        "proposed_change": proposed_change,
    }
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with PENDING_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def list_pending_improvements(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit or 50), 200))
    try:
        lines = PENDING_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[dict] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("status") == "pending_review":
            items.append(item)
        if len(items) >= limit:
            break
    return items
