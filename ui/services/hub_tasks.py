"""Shared helpers for Home Hub tasks."""

# ANAM GUIDE: HUB TASK LIST HELPERS
# What: adds and saves tasks on the Home Hub task list (tasks.json), whether the ask comes from a web page or a background job.
# Called by: api/hub.py (the Hub page's Tasks section) and services/autowake.py (boys adding tasks during autonomous sessions).
# Edit here when: you want to change how tasks are stored, deduplicated, or what extra info rides along with a new task.

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import HUB_API_BASE, RITUALS_DIR, TIMEZONE
from services.identity_context import invalidate_hub_cache
from services.personal_state import record_timeline_entry
from services.remote_state import request_json

TASKS_FILE = RITUALS_DIR / "tasks.json"


def _now_iso() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat()


def _read_json(path: Path, default=None):
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, default=str)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    invalidate_hub_cache()


async def add_hub_task(
    text: str,
    *,
    source: str = "anam",
    identity: str | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> dict:
    """Add a Hub task from API routes or background workers.

    Background workers cannot safely import the FastAPI route module. This keeps
    local/remote Hub task behavior available without tying it to request handlers.
    """
    task_text = str(text or "").strip()
    if not task_text:
        raise ValueError("task text is required")

    if HUB_API_BASE:
        remote = await asyncio.to_thread(
            request_json,
            HUB_API_BASE,
            "/api/hub/tasks",
            method="POST",
            payload={"text": task_text},
        )
        invalidate_hub_cache()
        return remote

    metadata = {
        "source": source,
        "identity": identity,
        "dedupe_key": dedupe_key,
    }
    if payload:
        metadata["payload"] = payload

    items = _read_json(TASKS_FILE)
    if dedupe_key:
        for task in items:
            if not isinstance(task, dict) or task.get("completed", False):


                continue
            task_meta = task.get("metadata")
            if isinstance(task_meta, dict) and task_meta.get("dedupe_key") == dedupe_key:
                return {"ok": True, "task": task, "deduped": True}

    for task in items:
        if isinstance(task, dict) and not task.get("completed", False) and task.get("text") == task_text:
            return {"ok": True, "task": task, "deduped": True}

    new_task = {
        "id": uuid.uuid4().hex[:8],
        "text": task_text,
        "completed": False,
        "created_at": _now_iso(),
        "metadata": metadata,
    }
    items.append(new_task)
    _write_json(TASKS_FILE, items)

    await record_timeline_entry(
        entry_type="task",
        title=f"Task added: {task_text}",
        source=source,
        identity=identity,
        payload=new_task,
        dedupe_key=f"hub-task:{dedupe_key}" if dedupe_key else None,
    )
    return {"ok": True, "task": new_task}
