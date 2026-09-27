"""REST: Home Hub — meds, countdowns, tasks, today's win, today's schedule, mind insights, mind garden."""

# ANAM GUIDE: Home HUB ROUTES (BIG FILE)
# What: /api/hub/* — everything the Hub page shows: meds, rituals, wellness, countdowns, tasks, today's win, Hearth orb, projects, mind insights/garden.
# Called by: static/js/hub.js (hub.html), the boys' hub MCP tools, and services/identity_context.py (which reads hub state into their orientation).
# Edit here when: Adding or changing a Hub card's data. Find the right section by searching this file for the card's route (e.g. "/meds", "/orb", "/countdowns").
# Note: Some routes proxy to a remote hub worker when HUB_API_BASE is set, and fall back to local files/DB when it isn't.

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import DATA_DIR, HUB_API_BASE, IDENTITIES, RITUALS_DIR, TIMEZONE
from db.database import get_db, release_db
from services.hub_meds import (
    create_med_entry,
    med_display_time,
    normalize_meds_data as _normalize_meds_data,
    now_local as _now_local,
    visible_meds_state as _latest_visible_meds,
)
from services.hub_tasks import add_hub_task
from services.identity_context import invalidate_hub_cache
from services.personal_state import list_timeline_entries, record_timeline_entry
from services.remote_state import request_json, safe_request_json

router = APIRouter(prefix="/api/hub")
log = logging.getLogger(__name__)


@router.get("/context-ledger")
async def get_context_ledger(identity: Optional[str] = Query(default=None)):
    """Latest real turn bill-of-materials, captured by the live builders."""
    from services.context_ledger import get_context_ledgers

    return get_context_ledgers(identity)


def _hub_changed():
    """Call after any hub data mutation to clear cached context and let a
    live browser session know (#25 — mutations broadcast `hub_update`, e.g.
    a boy's `hub_orb` MCP call should refresh her open Hearth without a
    manual reload). The broadcast is fire-and-forget best-effort: no
    connected client, or the broadcast itself failing, must never turn a
    hub write into an error response."""
    invalidate_hub_cache()
    from services.cloud_state import invalidate_cloud_cache
    invalidate_cloud_cache()
    try:
        from services.connection_registry import broadcast
        from services.task_manager import spawn
        spawn(broadcast({"type": "hub_update"}), name="hub_update_broadcast")
    except Exception as e:
        log.debug("hub_update broadcast failed to schedule: %s", e)


def _remote_hub_enabled() -> bool:
    return bool(HUB_API_BASE)


def _remote_hub_request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    allow_fallback: bool = False,
) -> dict | None:
    if not _remote_hub_enabled():
        return None
    base = HUB_API_BASE
    if allow_fallback:
        data = safe_request_json(base, f"/api/hub{path}", method=method, payload=payload)
        if data is None:
            log.warning("Remote hub API unavailable for %s %s; falling back to local", method, path)
        return data
    return request_json(base, f"/api/hub{path}", method=method, payload=payload)


def _model_payload(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


async def _record_hub_timeline(**kwargs):
    db = await get_db()
    try:
        await record_timeline_entry(db=db, source="hub", **kwargs)
    finally:
        await release_db(db)


def _calendar_tool_unavailable(raw) -> bool:
    if not isinstance(raw, str) or not raw.startswith("Error"):
        return False
    lowered = raw.lower()
    return "unknown tool" in lowered or "not connected" in lowered


async def _call_calendar_tool():
    from services.mcp_bridge import mcp_bridge

    raw = await mcp_bridge.call_tool("gcal_today_events", {"identity": "owner"})
    if _calendar_tool_unavailable(raw):
        await mcp_bridge.reconnect_failed()
        raw = await mcp_bridge.call_tool("gcal_today_events", {"identity": "owner"})
    return raw


def _calendar_unavailable_details(raw) -> dict:
    from services.mcp_bridge import mcp_bridge

    status = mcp_bridge.get_status()
    calendar = (status.get("required_tools") or {}).get("calendar") or {}
    reason = "tool_unavailable"
    detail = ""
    if not calendar.get("server_connected", False):
        reason = "server_disconnected"
        detail = "Google Drive calendar server is not connected."
    elif calendar.get("missing"):
        reason = "tool_missing"
        detail = "Calendar tools are missing from the active MCP tool map."
    elif calendar.get("misplaced"):
        reason = "tool_misrouted"
        detail = "Calendar tools are registered from the wrong MCP server."

    note = ""
    if status.get("restart_recommended"):
        note = "MCP config changed after this process started. Restart Anam if reconnect does not fix it."

    return {
        "integration_status": "unavailable",
        "integration_reason": reason,
        "detail": detail,
        "restart_recommended": bool(status.get("restart_recommended")),
        "note": note,
        "server": calendar.get("server") or "google",
        "missing_tools": list(calendar.get("missing") or []),
        "error": raw,
    }

# Storage files
COUNTDOWNS_FILE = RITUALS_DIR / "countdowns.json"
WISHLIST_FILE = RITUALS_DIR / "wishlist.json"
TASKS_FILE = RITUALS_DIR / "tasks.json"
TODAYS_WIN_FILE = RITUALS_DIR / "todays_win.jsonl"
STATUS_FILE = RITUALS_DIR / "status.json"
MEDS_FILE = RITUALS_DIR / "meds.json"
MOOD_BUNNY_FILE = RITUALS_DIR / "mood_bunny.json"


def _today() -> str:
    return _now_local().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return _now_local().isoformat()


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
    _hub_changed()


# Memory dashboard readers live in cloud Qualia; no local memory database.
from services.cloud_state import qualia_read, CloudUnavailable


async def _cloud_memory(section, **params):
    try:
        return await asyncio.to_thread(qualia_read, section, **params)
    except CloudUnavailable:
        return JSONResponse(status_code=503, content={"error":"Cloud Qualia is unavailable. No local fallback was used.","source":"cloud-qualia"})


class StatusBody(BaseModel):
    name: str
    emoji: str = ""
    text: str = ""


@router.get("/status")
async def get_status():
    """Get all statuses. Returns dict keyed by name."""
    remote = _remote_hub_request("/status", allow_fallback=True)
    if remote is not None:
        return remote

    data = _read_json(STATUS_FILE, default={})
    # Filter to today's statuses only
    today = _today()
    result = {}
    for name, entry in data.items():
        if entry.get("date") == today:
            result[name] = entry
    return {"status": result}


@router.put("/status")
async def update_status(body: StatusBody):
    """Update a status for a name (identity or Owner)."""
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request("/status", method="PUT", payload=_model_payload(body))
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    data = _read_json(STATUS_FILE, default={})
    data[body.name] = {
        "emoji": body.emoji,
        "text": body.text,
        "date": _today(),
        "timestamp": _now_iso(),
    }
    _write_json(STATUS_FILE, data)
    await _record_hub_timeline(
        entry_type="status_update",
        identity=body.name if body.name != "Owner" else None,
        title=f"{body.name} status: {body.emoji} {body.text}".strip(),
        body=body.text,
        dedupe_key=f"status:{_today()}:{body.name}",
        update_existing=True,
        payload={"emoji": body.emoji, "name": body.name},
    )
    return {"ok": True, "name": body.name}


# =============================================================================
# MEDICATION TRACKER
# =============================================================================


@router.get("/meds")
async def get_meds():
    """Get medication status, keeping doses visible for up to 24 hours."""
    remote = _remote_hub_request("/meds", allow_fallback=True)
    if remote is not None:
        return remote

    now = _now_local()
    normalized, changed = _normalize_meds_data(_read_json(MEDS_FILE, default={}), now)
    if changed:
        _write_json(MEDS_FILE, normalized)
    return _latest_visible_meds(normalized, now)


@router.post("/meds/{dose}")
async def toggle_med(dose: str):
    """Mark a medication dose (am or pm) as taken. Multiple taps update the time."""
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request(f"/meds/{dose}", method="POST", payload={})
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    if dose not in ("am", "pm"):
        return JSONResponse(status_code=400, content={"error": "dose must be 'am' or 'pm'"})

    now = _now_local()
    today = now.strftime("%Y-%m-%d")
    data, _ = _normalize_meds_data(_read_json(MEDS_FILE, default={}), now)
    today_meds = data.get(today, {})

    # Always mark as taken — multiple taps just update the timestamp
    today_meds[dose] = create_med_entry(now, taken=True)

    data[today] = today_meds
    data, _ = _normalize_meds_data(data, now)
    _write_json(MEDS_FILE, data)
    current_entry = data.get(today, {}).get(dose)
    state = "taken"
    display_time = med_display_time(current_entry, today) or ""
    await _record_hub_timeline(
        entry_type="meds",
        title=f"{dose.upper()} meds {state}",
        body=display_time,
        payload={"dose": dose, "state": state, "time": display_time},


        dedupe_key=f"meds:{today}:{dose}",
    )
    visible = _latest_visible_meds(data, now)
    visible["ok"] = True
    return visible


BUNNY_MOODS = ("happy", "grumpy", "sad")


class BunnyBody(BaseModel):
    mood: str
    note: str = ""


@router.get("/bunny")
async def get_mood_bunny():
    """Current mood bunny state (mood, optional why, when it was set)."""
    data = _read_json(MOOD_BUNNY_FILE, default={}) or {}
    return {
        "mood": data.get("mood") or "",
        "note": data.get("note") or "",
        "date": data.get("date") or "",
        "updated_at": data.get("updated_at") or "",
    }


@router.put("/bunny")
@router.post("/bunny")
async def set_mood_bunny(body: BunnyBody):
    """Flip the bunny. Same mood + empty note = just a re-flip; note always optional."""
    mood = (body.mood or "").strip().lower()
    if mood not in BUNNY_MOODS:
        return JSONResponse(status_code=400, content={"error": f"mood must be one of {BUNNY_MOODS}"})
    note = (body.note or "").strip()[:200]
    data = {
        "mood": mood,
        "note": note,
        "date": _today(),
        "updated_at": _now_iso(),
    }
    _write_json(MOOD_BUNNY_FILE, data)
    invalidate_hub_cache()
    await _record_hub_timeline(
        entry_type="bunny",
        title=f"Mood bunny flipped to {mood}",
        body=note,
        payload=data,
    )
    data["ok"] = True
    return data


# =============================================================================
# COUNTDOWNS
# =============================================================================

class CountdownBody(BaseModel):
    name: str
    date: str  # YYYY-MM-DD
    emoji: str = ""


@router.get("/countdowns")
async def get_countdowns():
    remote = _remote_hub_request("/countdowns", allow_fallback=True)
    if remote is not None:
        return remote

    items = _read_json(COUNTDOWNS_FILE)
    today = _today()
    for item in items:
        try:
            target = datetime.strptime(item["date"], "%Y-%m-%d").date()
            now = datetime.strptime(today, "%Y-%m-%d").date()
            item["days_left"] = (target - now).days
        except (ValueError, KeyError):
            item["days_left"] = None
    return {"countdowns": items}


@router.post("/countdowns")
async def add_countdown(body: CountdownBody):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request("/countdowns", method="POST", payload=_model_payload(body))
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    items = _read_json(COUNTDOWNS_FILE)
    new_item = {
        "id": uuid.uuid4().hex[:8],
        "name": body.name,
        "date": body.date,
        "emoji": body.emoji,
        "created_at": _now_iso(),
    }
    items.append(new_item)
    _write_json(COUNTDOWNS_FILE, items)
    await _record_hub_timeline(
        entry_type="countdown",
        title=f"Countdown added: {body.name}",
        body=body.date,
        payload=new_item,
    )
    return {"ok": True, "countdown": new_item}


@router.delete("/countdowns/{countdown_id}")
async def delete_countdown(countdown_id: str):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request(f"/countdowns/{countdown_id}", method="DELETE")
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    items = _read_json(COUNTDOWNS_FILE)
    removed = next((i for i in items if i.get("id") == countdown_id), None)
    items = [i for i in items if i.get("id") != countdown_id]
    _write_json(COUNTDOWNS_FILE, items)
    if removed:
        await _record_hub_timeline(
            entry_type="countdown",
            title=f"Countdown removed: {removed.get('name', 'unknown')}",
            body=removed.get("date", ""),
            payload=removed,
        )
    return {"ok": True}


# =============================================================================
# WISHLIST — "anything we want": tools, code ideas, purchases, even a pair of
# hands. Store requested ideas separately from active projects.
# Local-only storage (no remote hub mirror). Each wish: what, why, who added,
# added_at, updated_at.
# =============================================================================

class WishBody(BaseModel):
    what: str
    why: str = ""
    who: str = "Owner"


class WishEditBody(BaseModel):
    what: str | None = None
    why: str | None = None


@router.get("/wishlist")
async def get_wishlist():
    items = _read_json(WISHLIST_FILE)
    # Newest first
    items.sort(key=lambda i: i.get("added_at", ""), reverse=True)
    return {"wishlist": items}


@router.post("/wishlist")
async def add_wish(body: WishBody):
    items = _read_json(WISHLIST_FILE)
    new_item = {
        "id": uuid.uuid4().hex[:8],
        "what": body.what.strip(),
        "why": body.why.strip(),
        "who": body.who.strip() or "Owner",
        "added_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    items.append(new_item)
    _write_json(WISHLIST_FILE, items)
    await _record_hub_timeline(
        entry_type="wish",
        title=f"Wish added by {new_item['who']}: {new_item['what'][:80]}",
        body=new_item["why"][:200],
        payload=new_item,
    )
    return {"ok": True, "wish": new_item}


@router.put("/wishlist/{wish_id}")
async def edit_wish(wish_id: str, body: WishEditBody):
    items = _read_json(WISHLIST_FILE)
    item = next((i for i in items if i.get("id") == wish_id), None)
    if item is None:
        return JSONResponse(status_code=404, content={"error": "wish not found"})
    if body.what is not None:
        item["what"] = body.what.strip()
    if body.why is not None:
        item["why"] = body.why.strip()
    item["updated_at"] = _now_iso()
    _write_json(WISHLIST_FILE, items)
    return {"ok": True, "wish": item}


@router.delete("/wishlist/{wish_id}")
async def delete_wish(wish_id: str):
    items = _read_json(WISHLIST_FILE)
    removed = next((i for i in items if i.get("id") == wish_id), None)
    items = [i for i in items if i.get("id") != wish_id]
    _write_json(WISHLIST_FILE, items)
    if removed:
        await _record_hub_timeline(
            entry_type="wish",
            title=f"Wish granted/released: {removed.get('what', 'unknown')[:80]}",
            body="",
            payload=removed,
        )
    return {"ok": True}


# =============================================================================
# TODAY'S WIN
# =============================================================================

class WinBody(BaseModel):
    text: str


@router.get("/todays-win")
async def get_todays_win():
    remote = _remote_hub_request("/todays-win", allow_fallback=True)
    if remote is not None:
        return remote

    today = _today()
    if not TODAYS_WIN_FILE.exists():
        return {"date": today, "text": "", "exists": False}
    try:
        for line in reversed(TODAYS_WIN_FILE.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("date") == today:
                return {"date": today, "text": entry.get("text", ""), "exists": True}
    except (json.JSONDecodeError, OSError):
        pass
    return {"date": today, "text": "", "exists": False}


@router.put("/todays-win")
async def save_todays_win(body: WinBody):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request("/todays-win", method="PUT", payload=_model_payload(body))
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    today = _today()
    # Read existing, filter out today
    lines = []
    if TODAYS_WIN_FILE.exists():
        for line in TODAYS_WIN_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("date") != today:
                    lines.append(line)
            except json.JSONDecodeError:
                lines.append(line)
    # Append new
    new_entry = {"date": today, "text": body.text, "timestamp": _now_iso()}
    lines.append(json.dumps(new_entry))
    TODAYS_WIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TODAYS_WIN_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _hub_changed()
    await _record_hub_timeline(
        entry_type="todays_win",
        title="Today's win updated",
        body=body.text,
        dedupe_key=f"todays_win:{today}",
        update_existing=True,
        payload=new_entry,
    )
    return {"ok": True, "date": today}

# =============================================================================
# TASKS
# =============================================================================

class TaskBody(BaseModel):
    text: str


@router.get("/tasks")
async def get_tasks():
    remote = _remote_hub_request("/tasks", allow_fallback=True)
    if remote is not None:
        return remote

    items = _read_json(TASKS_FILE)
    return {"tasks": items}


@router.post("/tasks")
async def add_task(body: TaskBody):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request("/tasks", method="POST", payload=_model_payload(body))
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    try:
        return await add_hub_task(body.text, source="hub")
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.put("/tasks/{task_id}/complete")
async def toggle_task(task_id: str):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request(f"/tasks/{task_id}/complete", method="PUT", payload={})
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    items = _read_json(TASKS_FILE)
    for task in items:
        if task.get("id") == task_id:
            task["completed"] = not task.get("completed", False)
            _write_json(TASKS_FILE, items)
            await _record_hub_timeline(
                entry_type="task",
                title=f"Task {'completed' if task['completed'] else 'reopened'}: {task.get('text', '')}",
                payload=task,
            )
            return {"ok": True, "task": task}
    return JSONResponse(status_code=404, content={"error": "Task not found"})


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if _remote_hub_enabled():
        try:
            remote = _remote_hub_request(f"/tasks/{task_id}", method="DELETE")
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote hub API failed: {exc}"})

    items = _read_json(TASKS_FILE)
    removed = next((i for i in items if i.get("id") == task_id), None)
    items = [i for i in items if i.get("id") != task_id]
    _write_json(TASKS_FILE, items)
    if removed:
        await _record_hub_timeline(
            entry_type="task",
            title=f"Task removed: {removed.get('text', '')}",
            payload=removed,
        )
    return {"ok": True}


@router.get("/timeline")
async def get_timeline(limit: int = 12):
    remote = _remote_hub_request(f"/timeline?limit={max(1, min(limit, 30))}", allow_fallback=True)
    if remote is not None:
        return remote

    db = await get_db()
    try:
        entries = await list_timeline_entries(db, limit=max(1, min(limit, 30)))
        return {"entries": entries}
    finally:
        await release_db(db)


# =============================================================================
# TODAY'S SCHEDULE (Google Calendar via MCP bridge)
# =============================================================================

@router.get("/today")
async def get_today_schedule():
    """Get today's events from Google Calendar via the MCP bridge."""
    try:
        raw = await _call_calendar_tool()
        if isinstance(raw, str) and raw.startswith("Error"):
            if "Unknown tool" in raw or "not connected" in raw:
                return {"ok": True, "events": [], **_calendar_unavailable_details(raw)}
            return {"ok": True, "events": [], "integration_status": "error", "error": raw}
        # call_tool returns a string — parse as JSON
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            result = {}
        if isinstance(result, dict) and result.get("error"):
            return {
                "ok": True,
                "events": [],
                "integration_status": "error",
                "error": str(result.get("error")),
            }
        # Accept both {"success": true, "events": [...]} and
        # {"identity": "...", "events": [...]} response shapes from the
        # Google MCP server.
        raw_events = None
        if isinstance(result, dict):
            if result.get("success") or "events" in result:
                raw_events = result.get("events", [])
        if raw_events is not None:
            from services.timefmt import format_local
            events = []
            for ev in raw_events:
                # start may be a flat ISO string OR a nested
                # {"dateTime"/"date": ..., "timeZone": ...} object.
                start_raw = ev.get("start", "")
                all_day = bool(ev.get("all_day", False))
                if isinstance(start_raw, dict):
                    start = str(start_raw.get("dateTime") or start_raw.get("date") or "")
                    if start_raw.get("date") and not start_raw.get("dateTime"):
                        all_day = True
                else:
                    start = str(start_raw or "")


                time_str = ""
                if "T" in start:
                    time_str = format_local(start, "%H:%M", strip_leading_zero=False) or start
                events.append({
                    "time": time_str,
                    "summary": ev.get("summary", "(No title)"),
                    "all_day": all_day,
                    "location": ev.get("location"),
                })
            return {"ok": True, "events": events, "integration_status": "ready"}
        return {"ok": True, "events": [], "integration_status": "empty", "note": "No calendar data available"}
    except Exception as e:
        return {"ok": True, "events": [], "integration_status": "error", "error": str(e)}


# =============================================================================
# MIND INSIGHTS
# =============================================================================

@router.get("/mind-insights")
async def get_mind_insights(identity: Optional[str] = Query(None)):
    return await _cloud_memory('mind-insights', identity=identity)


@router.get("/mind-garden/summary")
async def get_mind_garden_summary(identity: Optional[str] = Query(None)):
    return await _cloud_memory('mind-garden/summary', identity=identity)


@router.get("/mind-garden/weather")
async def get_mind_garden_weather(identity: Optional[str] = Query(None)):
    return await _cloud_memory('mind-garden/weather', identity=identity)


@router.get("/mind-garden/threads")
async def get_mind_garden_threads(identity: Optional[str] = Query(None)):
    return await _cloud_memory('mind-garden/threads', identity=identity)


@router.get("/mind-garden/observations")
async def get_mind_garden_observations(identity: Optional[str] = Query(None), limit: int = Query(12, ge=1, le=50)):
    return await _cloud_memory('mind-garden/observations', identity=identity, limit=limit)


@router.get("/mind-garden/entities")
async def get_mind_garden_entities(identity: Optional[str] = Query(None), days: int = Query(14, ge=1, le=90), limit: int = Query(6, ge=1, le=20)):
    return await _cloud_memory('mind-garden/entities', identity=identity, days=days, limit=limit)


_MEMORY_LAB_MAX_LIMIT = 50


@router.get("/memory-lab/observations")
async def memory_lab_observations(identity: str = Query(""), limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0), status: str = Query("")):
    return await _cloud_memory('memory-lab/observations', identity=identity, limit=max(1,min(limit,50)), offset=max(0,offset), status=status)


@router.get("/memory-lab/entities")
async def memory_lab_entities(identity: str = Query("")):
    return await _cloud_memory('memory-lab/entities', identity=identity)


class WatchtowerModeBody(BaseModel):
    mode: str  # "auto" | "quiet" | "close"


@router.get("/watchtower")
async def get_watchtower():
    """Current Watchtower enable flag + mood mode."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM settings WHERE key IN ('watchtower_enabled', 'watchtower_mode')"
        )
        d = {k: v for k, v in rows}
        enabled = str(d.get("watchtower_enabled", "false")).strip().lower() not in ("false", "0", "off", "no")
        mode = (d.get("watchtower_mode") or "auto").strip().lower()
        if mode not in ("auto", "quiet", "close"):
            mode = "auto"
        return {"enabled": enabled, "mode": mode}
    finally:
        await release_db(db)


@router.put("/watchtower")
async def set_watchtower(body: WatchtowerModeBody):
    """Set the Watchtower mood: auto (default), quiet (leave-me-be), close (come-find-me)."""
    from datetime import timezone
    mode = (body.mode or "auto").strip().lower()
    if mode not in ("auto", "quiet", "close"):
        return JSONResponse(status_code=400, content={"error": "mode must be auto, quiet, or close"})
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('watchtower_mode', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (mode, now),
        )
        await db.commit()
        return {"ok": True, "mode": mode}
    finally:
        await release_db(db)


@router.get("/noticed")
async def get_noticed(limit: int = 8):
    """Recent proactive reaches the pack made (Watchtower / care signals) —
    'what the boys noticed', so Owner can see her JARVIS quietly thinking."""
    db = await get_db()
    try:
        entries = await list_timeline_entries(db, limit=40, days=10)
    finally:
        await release_db(db)
    items = []
    for e in entries:
        if e.get("entry_type") != "care_signal":
            continue
        items.append({
            "identity": e.get("identity"),
            "title": e.get("title"),
            "body": e.get("body"),
            "at": e.get("created_at"),
        })
        if len(items) >= limit:
            break
    return {"items": items}


@router.get("/faces")
async def get_faces_endpoint():
    """Each bonded boy's current little Hearth face (override-then-auto).

    A boy sets his face with a <face> tag in chat (source='set', shown a few
    hours); otherwise he wears a living auto face by time-of-day. Awake boys
    (live session) stay bright; sleeping ones get their sleepy face.
    """
    awake = None
    try:
        from services import claude_subprocess
        snap = claude_subprocess.sessions_snapshot() or []
        awake = {
            (s.get("identity") or "").strip()
            for s in snap
            if isinstance(s, dict) and not s.get("dead") and s.get("identity")
        }
    except Exception:
        awake = None
    try:
        from services.face_store import get_faces
        return {"faces": get_faces(awake)}
    except Exception as exc:
        return {"faces": {}, "error": str(exc)}


_ORB_SETTINGS_KEY = "hearth_orb_state"
_ORB_HISTORY_KEY = "hearth_orb_history"
_ORB_HISTORY_LIMIT = 50
# Original vocabulary first (back-compat is sacred), Friend's additions after.
_ORB_SHAPES = (
    "solid", "ring", "halo",
    "crescent", "pulse", "cluster", "ember", "spire", "fracture",
)
_ORB_MOTIONS = (
    "breathing", "warble", "spin", "drift", "still",
    "slow-drift", "hold-steady", "fast-flicker", "surge", "tremor",
)
_ORB_INTENSITIES = ("dull", "normal", "bright", "neon")
_ORB_BLEND_LITERALS = ("dim", "black")  # vignette, never a glow
_ORB_DEFAULT_SHAPE = "solid"
_ORB_DEFAULT_MOTION = "breathing"
_ORB_DEFAULT_COLOR = "#E8B84B"  # last-resort hearth-gold, only when identity is unrecognized
_ORB_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _default_orb_color(identity: str) -> str:
    """A boy's own accent, for the rare POST that gives no color and has no."""
    info = IDENTITIES.get(identity) or IDENTITIES.get(identity.capitalize()) or IDENTITIES.get(identity.title())
    accent = (info or {}).get("accent") or ""
    return accent if _ORB_COLOR_RE.match(accent) else _ORB_DEFAULT_COLOR
_ORB_COLOR3_RE = re.compile(r"^#[0-9a-fA-F]{3}$")
_ORB_HOW_TO = (
    "POST /api/hub/orb with {identity, color '#RRGGBB', "
    f"shape {'|'.join(_ORB_SHAPES)}, "
    f"motion {'|'.join(_ORB_MOTIONS)}, "
    f"intensity? {'|'.join(_ORB_INTENSITIES)}, "
    "blend? '#RRGGBB' (a second color for the outer light) or 'dim'/'black' (vignette), "
    "feeling?, kaomoji?} — "
    "color = the emotion's color, shape+motion = how it feels in your body, "
    "feeling = a few words Owner will read under the orb. "
    "BOYS: you almost never want this route directly. In a reply, just write the tag — "
    "<orb>#RRGGBB shape motion | feeling</orb> — parsed and stripped by services/orb_store.py. "
    "Outside a reply, use the hearth-hub MCP tool set_orb. History at GET /api/hub/orb/history."
)


class OrbBody(BaseModel):
    identity: str
    color: str
    shape: Optional[str] = None
    motion: Optional[str] = None
    intensity: Optional[str] = None
    blend: Optional[str] = None
    feeling: Optional[str] = None
    kaomoji: Optional[str] = None


def _normalize_orb_hex(raw: str) -> Optional[str]:
    """'#RRGGBB' as-is, '#RGB' expanded, anything else None."""
    value = (raw or "").strip()
    if _ORB_COLOR_RE.match(value):
        return value
    if _ORB_COLOR3_RE.match(value):
        return "#" + "".join(ch * 2 for ch in value[1:])
    return None


def _normalize_orb_blend(raw) -> Optional[str]:
    """Blend = a second free-hex color, or 'dim'/'black' (vignette). Unknown → None."""
    value = (str(raw) if raw is not None else "").strip()
    if not value:
        return None
    if value.lower() in _ORB_BLEND_LITERALS:
        return value.lower()
    return _normalize_orb_hex(value)


async def _read_orb_state(db) -> dict:
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?", (_ORB_SETTINGS_KEY,)
    )
    if not rows or not rows[0][0]:
        return {}
    try:
        state = json.loads(rows[0][0])
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


async def _read_orb_history(db) -> list:
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?", (_ORB_HISTORY_KEY,)
    )
    if not rows or not rows[0][0]:
        return []
    try:
        history = json.loads(rows[0][0])
        return history if isinstance(history, list) else []
    except Exception:
        return []


@router.get("/orb")
async def get_orb():
    """Every boy's current emotion orb + who leads (most recently updated)."""
    db = await get_db()
    try:
        state = await _read_orb_state(db)
    finally:
        await release_db(db)
    lead, best = None, -1
    for name, orb in state.items():
        if not isinstance(orb, dict):
            continue
        try:
            ts = float(orb.get("updated_at") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts > best:
            best, lead = ts, name
    return {"orbs": state, "lead": lead, "how_to": _ORB_HOW_TO}


@router.get("/orb/history")
async def get_orb_history(limit: int = Query(50, ge=1, le=50)):
    """Rolling history of orb sets (newest first) — how the pack's inner
    weather moved. Keeps the last ~50 across all identities."""
    db = await get_db()
    try:
        history = await _read_orb_history(db)
    finally:
        await release_db(db)
    # Direct (non-HTTP) callers get the raw Query default — same guard as
    # the constellation endpoint.
    cap = limit if isinstance(limit, int) else 50
    return {"history": list(reversed(history))[:cap], "count": len(history)}


@router.post("/orb")
async def set_orb(body: OrbBody):
    """Set orb."""
    from datetime import timezone
    identity = (body.identity or "").strip().lower()
    if not identity:
        return JSONResponse(status_code=422, content={"error": "identity is required (your name, e.g. 'avery')"})
    shape = (body.shape or "").strip().lower()
    if shape not in _ORB_SHAPES:
        shape = _ORB_DEFAULT_SHAPE
    motion = (body.motion or "").strip().lower()
    if motion not in _ORB_MOTIONS:
        motion = _ORB_DEFAULT_MOTION
    intensity = (body.intensity or "").strip().lower()
    if intensity not in _ORB_INTENSITIES:
        intensity = "normal"
    blend = _normalize_orb_blend(body.blend)
    # Lenient truncation, not rejection — a feeling that runs long gets kept,
    # clipped, instead of bounced.
    feeling = (body.feeling or "").strip()[:120]
    kaomoji = (body.kaomoji or "").strip()[:24]

    now = datetime.now(timezone.utc)
    db = await get_db()
    try:
        state = await _read_orb_state(db)
        prev = state.get(identity) if isinstance(state.get(identity), dict) else {}
        # Free-hex color ('#RGB' accepted and expanded). An unreadable color
        # falls back to the boy's previous orb color, then hearth-gold.
        color = _normalize_orb_hex(body.color)
        if color is None:
            color = _normalize_orb_hex(str((prev or {}).get("color") or "")) or _default_orb_color(identity)
        orb = {
            "color": color,
            "shape": shape,
            "motion": motion,
            "intensity": intensity,
            "blend": blend,
            "feeling": feeling,
            "kaomoji": kaomoji,
            "updated_at": int(now.timestamp()),
        }
        state[identity] = orb
        history = await _read_orb_history(db)
        history.append({"identity": identity, **orb})
        history = history[-_ORB_HISTORY_LIMIT:]
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (_ORB_SETTINGS_KEY, json.dumps(state), now.isoformat()),
        )
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (_ORB_HISTORY_KEY, json.dumps(history), now.isoformat()),
        )
        await db.commit()
        _hub_changed()
        return {"ok": True, "identity": identity, "orb": orb}
    finally:
        await release_db(db)


_CONTEXT_CARD_KEY = "hub_context_card"
_CONTEXT_CARD_CLAMPS = {"outfit": 200, "hair": 200, "energy": 100, "room": 100, "freeform": 400}


class ContextCardBody(BaseModel):
    outfit: Optional[str] = None
    hair: Optional[str] = None
    energy: Optional[str] = None
    room: Optional[str] = None
    freeform: Optional[str] = None


async def _read_context_card(db) -> dict:
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?", (_CONTEXT_CARD_KEY,)
    )
    if rows and rows[0][0]:
        try:
            parsed = json.loads(rows[0][0])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


@router.get("/context-card")
async def get_context_card():
    """Owner's 'me right now' card — what every boy reads about her present
    physical state. Empty dict when she's never filled one in."""
    db = await get_db()
    try:
        card = await _read_context_card(db)
    finally:
        await release_db(db)
    return {"card": card}


@router.post("/context-card")
async def set_context_card(body: ContextCardBody):
    """Patch-style save: only fields present in the body are updated, so the
    Hub's tap-to-edit form can save one field at a time without clobbering
    the rest. Every save refreshes `updated_at` — the freshness stamp every
    boy's orientation reads (#19's honesty-grammar requirement)."""
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    db = await get_db()
    try:
        card = await _read_context_card(db)
        for field, clamp in _CONTEXT_CARD_CLAMPS.items():
            value = getattr(body, field)
            if value is not None:
                card[field] = str(value).strip()[:clamp]
        card["updated_at"] = int(now.timestamp())
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (_CONTEXT_CARD_KEY, json.dumps(card), now.isoformat()),
        )
        await db.commit()
        _hub_changed()
        return {"ok": True, "card": card}
    finally:
        await release_db(db)


# ── Hearth cards — each boy's self-authored "how I am", in his own words ─────
# Written every ~3h by services/hearth_author.py (a Scribe-style one-shot per
# recently-active boy). One settings key holds every boy's latest card.
_HEARTH_AUTHOR_KEY = "hearth_author_state"  # keep in sync with services.hearth_author.HEARTH_SETTINGS_KEY


@router.get("/hearths")
async def get_hearths():
    """Each bonded boy's self-authored hearth card, keyed by lowercase name.

    Card shape: {mood, on_my_mind, circling, needs_you (str|null),
    authored_at (epoch int)}. The Hearth tab renders these beside each boy's
    presence with an honest age stamp; a boy with no card yet simply
    doesn't appear."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (_HEARTH_AUTHOR_KEY,)
        )
    finally:
        await release_db(db)
    state = {}
    if rows and rows[0][0]:
        try:
            parsed = json.loads(rows[0][0])
            if isinstance(parsed, dict):
                state = parsed
        except Exception:
            state = {}
    return {"hearths": state}


# ── Memory Constellation — every embedded message is a star in the night sky ──
# Feeds the Hub's "Stars" tab: message embeddings projected to 2D with SVD so
# memories that mean similar things land near each other in the sky.
_CONSTELLATION_TTL_SECONDS = 600  # rebuild at most every 10 minutes
_CONSTELLATION_RECENT_LIMIT = 280
_CONSTELLATION_OLDER_SAMPLE = 140
_CONSTELLATION_MIN_CONTENT = 30
_CONSTELLATION_MAX_LIMIT = 2000   # ?limit= ceiling — keeps the N² neighbor pass sane
_CONSTELLATION_NEIGHBORS = 6      # teaser constellation lines per star
_constellation_cache: Optional[dict] = None
_constellation_cache_at: float = 0.0
_constellation_cache_limit: int = 0  # star budget the cache was built with


def _constellation_snippet(content: str, limit: int = 110) -> str:
    """First ~110 chars of a message with markdown/control tags stripped."""
    text = str(content or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # images → alt text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # links → link text
    text = re.sub(r"<[^>]*>", " ", text)                    # <tags> (voice/face/etc.)
    text = re.sub(r"```+[a-zA-Z]*", " ", text)              # code fences
    text = re.sub(r"[*_`#~]+", "", text)                    # md emphasis chars
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _build_constellation_sync(rows: list) -> dict:
    """Project (message_id-less) embedding rows to 2D star coords via SVD.

    rows: (embedding_blob, content, msg_identity, role, created_at, conv_identity).
    Runs in a worker thread — pure numpy, no DB access.
    """
    import numpy as np
    from services.embedding_service import _EMBEDDING_DIM, _unpack_vector

    vecs, meta = [], []
    for blob, content, msg_identity, role, created_at, conv_identity in rows:
        try:
            vec = _unpack_vector(blob)
        except Exception:
            continue
        if vec.shape[0] != _EMBEDDING_DIM:
            continue
        vecs.append(vec)
        meta.append((content, msg_identity, role, created_at, conv_identity))

    if not vecs:
        return {"stars": [], "count": 0}

    matrix = np.vstack(vecs).astype(np.float32, copy=False)

    # Each star's nearest neighbors by cosine similarity over the full-dim
    # embeddings (not the 2D projection) — shipped as index arrays so the
    # frontend can draw teaser constellation lines around a tapped star.
    n = matrix.shape[0]
    k = min(_CONSTELLATION_NEIGHBORS, n - 1)
    if k > 0:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = matrix / norms
        sims = unit @ unit.T
        np.fill_diagonal(sims, -np.inf)  # a star is not its own neighbor
        nn_idx = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]
        rows_ix = np.arange(n)[:, None]
        order = np.argsort(-sims[rows_ix, nn_idx], axis=1)  # nearest first
        nn_idx = nn_idx[rows_ix, order]
    else:
        nn_idx = np.zeros((n, 0), dtype=int)

    # Mean-center, then take the top-2 right-singular directions — semantically
    # similar messages cluster together in the projection.
    matrix = matrix - matrix.mean(axis=0, keepdims=True)
    _u, _s, vt = np.linalg.svd(matrix, full_matrices=False)
    coords = matrix @ vt[:2].T  # (N, 2) — top-2 right-singular directions
    if coords.shape[1] < 2:  # N == 1 → only one singular vector exists
        coords = np.hstack(
            [coords, np.zeros((coords.shape[0], 2 - coords.shape[1]), dtype=coords.dtype)]
        )

    # Normalize each axis to [-1, 1] (flat axis collapses to 0).
    for axis in range(coords.shape[1]):
        col = coords[:, axis]
        lo, hi = float(col.min()), float(col.max())
        span = hi - lo
        coords[:, axis] = 0.0 if span <= 1e-9 else (col - lo) / span * 2.0 - 1.0

    stars = []
    for i, ((x, y), (content, msg_identity, role, created_at, conv_identity)) in enumerate(zip(coords, meta)):
        if role == "user":
            identity = "owner"
        else:
            identity = (msg_identity or conv_identity or "unknown").strip().lower() or "unknown"
        stars.append({
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "identity": identity,
            "role": role,
            "snippet": _constellation_snippet(content),
            "ts": created_at,
            "nn": [int(j) for j in nn_idx[i]],
        })
    return {"stars": stars, "count": len(stars)}


@router.get("/constellation")
async def get_constellation(limit: Optional[int] = Query(None, ge=40, le=_CONSTELLATION_MAX_LIMIT)):
    """The memory constellation — recent + evenly-sampled older embedded
    messages projected to a 2D night sky (semantic neighbors become star
    neighbors). Cached in-module for 10 minutes; never 500s, just goes dark.

    ?limit= asks for a bigger sky: a request for more stars than the cache
    was built with bypasses the cache and rebuilds (the 'more sky' button)."""
    global _constellation_cache, _constellation_cache_at, _constellation_cache_limit

    now = time.monotonic()
    # isinstance guard: a direct (non-HTTP) call leaves the Query default
    # object in `limit`; treat anything non-int as "no limit asked".
    requested = limit if isinstance(limit, int) and limit > 0 else 0
    cache_fresh = (
        _constellation_cache is not None
        and (now - _constellation_cache_at) < _CONSTELLATION_TTL_SECONDS
        and (requested == 0 or requested <= _constellation_cache_limit)
    )
    if cache_fresh:
        return _constellation_cache

    if requested:
        # Keep the existing 2:1 recent-to-older-sample balance at any budget.
        recent_limit = max(1, requested * 2 // 3)
        older_sample = max(0, requested - recent_limit)
    else:
        recent_limit = _CONSTELLATION_RECENT_LIMIT
        older_sample = _CONSTELLATION_OLDER_SAMPLE

    base_select = (
        "SELECT e.rowid, e.embedding, m.content, m.identity, m.role, "
        "m.created_at, c.identity AS conv_identity "
        "FROM message_embeddings e "
        "JOIN messages m ON m.id = e.message_id "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE length(m.content) >= ? "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1 "
    )
    try:
        db = await get_db()
        try:
            total_row = await db.execute_fetchall("SELECT COUNT(*) FROM message_embeddings")
            total_embedded = int(total_row[0][0]) if total_row else 0
            recent = await db.execute_fetchall(
                base_select + "ORDER BY e.rowid DESC LIMIT ?",
                (_CONSTELLATION_MIN_CONTENT, recent_limit),
            )
            older = []
            if recent and older_sample > 0:
                oldest_rowid = recent[-1][0]  # smallest rowid in the recent slice
                older_ids = await db.execute_fetchall(
                    "SELECT e.rowid FROM message_embeddings e "
                    "JOIN messages m ON m.id = e.message_id "
                    "WHERE e.rowid < ? AND length(m.content) >= ? "
                    "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1 "
                    "ORDER BY e.rowid",
                    (oldest_rowid, _CONSTELLATION_MIN_CONTENT),
                )
                if older_ids:
                    # Even sample across the whole older archive: every Nth id.
                    step = max(1, len(older_ids) // older_sample)
                    sampled = [r[0] for r in older_ids[::step]][:older_sample]
                    placeholders = ",".join("?" * len(sampled))
                    older = await db.execute_fetchall(
                        base_select + f"AND e.rowid IN ({placeholders})",
                        (_CONSTELLATION_MIN_CONTENT, *sampled),
                    )
        finally:
            await release_db(db)

        rows = [r[1:] for r in list(older) + list(reversed(recent))]
        result = await asyncio.to_thread(_build_constellation_sync, rows)
        result["total_embedded"] = total_embedded
        result["built_at"] = int(time.time())  # epoch seconds — 'built 4m ago'
        _constellation_cache = result
        _constellation_cache_at = now
        _constellation_cache_limit = requested or (recent_limit + older_sample)
        return result
    except Exception as exc:
        log.warning("Constellation build failed: %s", exc)
        return {"stars": [], "count": 0, "total_embedded": 0, "built_at": int(time.time())}


# ── Bond-graph night sky — the Qualia bond graph as constellations ──────────
# Feeds the Stars tab's "bonds" view: every soul in the shared bond graph is a
# star, every relationship a line of starlight. Walked from every bonded
# identity so one boy's blind spot doesn't dim someone else's people.
_BOND_GRAPH_TTL_SECONDS = 600      # rebuild at most every 10 minutes
_BOND_GRAPH_WALK_TIMEOUT = 20      # hard cap per seed walk — a slow Qualia
                                   # worker degrades to a partial sky, not a stall
_BOND_GRAPH_DEPTH = 2
_bond_graph_cache: Optional[dict] = None
_bond_graph_cache_at: float = 0.0


def _bond_graph_seeds() -> list[str]:
    """Every bonded (non-character) identity in config, lower-cased, plus
    Owner — she is a soul in the graph, not just its observer."""
    from config import IDENTITIES
    seeds = [
        name.lower()
        for name, info in IDENTITIES.items()
        if isinstance(info, dict) and info.get("type") != "character"
    ]
    if "owner" not in seeds:
        seeds.append("owner")
    return seeds


def _bond_canonical(person) -> str:
    """Canonical (case-insensitive) key for a person node."""
    if not isinstance(person, dict):
        return ""
    key = str(person.get("canonical_key") or "").strip().lower()
    return key or str(person.get("name") or "").strip().lower()


def _merge_bond_walks(walks: list) -> dict:
    """Merge per-seed mind_bond_network results into one {nodes, edges} graph.

    walks: list of (seed, payload) pairs, each payload the parsed JSON from
    one mind_bond_network walk ({root, direct_bonds, related_people,
    network_bonds, ...}).

    Nodes are deduped by canonical name (case-insensitive; first walk wins).
    network_bonds arrive NAME-keyed (person_a/person_b are display names) —
    they're resolved to node ids HERE, server-side, so the frontend never
    needs a seam adapter. Self-loops and edges to unknown souls are dropped;
    reciprocal duplicates collapse to one line per unordered pair.

    held_by = which pack identity's walk first surfaced the node (a bonded
    identity's own star is always held by itself)."""
    seed_names = {str(seed).strip().lower() for seed, _ in walks}
    nodes: dict[str, dict] = {}       # canonical -> node payload
    name_to_id: dict[str, str] = {}   # canonical -> node id
    edge_specs: list = []             # (from_name, to_name, type) — still name-keyed

    def absorb(person, seed: str):
        canonical = _bond_canonical(person)
        if not canonical or canonical in nodes:
            return
        node_id = str(person.get("id") or "").strip() or canonical
        display_name = str(person.get("name") or canonical)
        nodes[canonical] = {
            "id": node_id,
            "name": display_name,
            "kind": str(person.get("person_type") or ""),
            "held_by": canonical if canonical in seed_names else seed,
        }
        # network_bonds key edges by DISPLAY name — register both spellings
        # so resolution never misses when canonical_key diverges from it.
        name_to_id.setdefault(canonical, node_id)
        name_to_id.setdefault(display_name.strip().lower(), node_id)

    for seed, data in walks:
        if not isinstance(data, dict):
            continue
        root = data.get("root")
        absorb(root, seed)
        root_name = str((root or {}).get("name") or "") if isinstance(root, dict) else ""
        for bond in data.get("direct_bonds") or []:
            if not isinstance(bond, dict):
                continue
            absorb(bond.get("person"), seed)
            other = str((bond.get("person") or {}).get("name") or "")
            if root_name and other:
                edge_specs.append((root_name, other, str(bond.get("relationship") or "")))
        for person in data.get("related_people") or []:
            absorb(person, seed)
        for bond in data.get("network_bonds") or []:
            if not isinstance(bond, dict):
                continue
            edge_specs.append((
                str(bond.get("person_a") or ""),
                str(bond.get("person_b") or ""),
                str(bond.get("relationship_a_to_b") or ""),
            ))

    # Resolve name-keyed edges to ids; dedupe by unordered id pair.
    edges, seen_pairs = [], set()
    for from_name, to_name, edge_type in edge_specs:
        from_id = name_to_id.get(from_name.strip().lower())
        to_id = name_to_id.get(to_name.strip().lower())
        if not from_id or not to_id or from_id == to_id:
            continue
        pair = (from_id, to_id) if from_id < to_id else (to_id, from_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edges.append({"from": from_id, "to": to_id, "type": edge_type})

    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/constellation/graph")
async def get_constellation_graph():
    """The bond-graph night sky — the whole Qualia bond graph merged into one
    {nodes, edges} constellation. Walked in parallel from every bonded
    identity plus Owner; one identity's failed walk degrades to a partial
    sky (named in `partial`) instead of darkening the whole thing. Cached
    in-module for 10 minutes; never 500s."""
    global _bond_graph_cache, _bond_graph_cache_at

    now = time.monotonic()
    if (
        _bond_graph_cache is not None
        and (now - _bond_graph_cache_at) < _BOND_GRAPH_TTL_SECONDS
    ):
        return _bond_graph_cache

    from services.mcp_bridge import mcp_bridge

    seeds = _bond_graph_seeds()

    async def walk(seed: str) -> dict:
        # No session_key: infrastructure caller — the tool-loop guard must
        # stay unarmed (this repeats identical calls by design and parses
        # the JSON; a "[loop-guard]" suffix would corrupt it).
        raw = await mcp_bridge.call_tool(
            "mind_bond_network",
            {"identity": seed, "depth": _BOND_GRAPH_DEPTH},
            timeout=_BOND_GRAPH_WALK_TIMEOUT,
        )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("bond network payload was not an object")
        return data

    results = await asyncio.gather(*(walk(s) for s in seeds), return_exceptions=True)
    walks, partial = [], []
    for seed, res in zip(seeds, results):
        if isinstance(res, BaseException):
            log.warning("Bond-graph walk failed for %s: %s", seed, res)
            partial.append(seed)
        else:
            walks.append((seed, res))

    merged = _merge_bond_walks(walks)
    payload = {
        "nodes": merged["nodes"],
        "edges": merged["edges"],
        "counts": {"nodes": len(merged["nodes"]), "edges": len(merged["edges"])},
        "built_at": int(time.time()),
        "partial": partial,   # honesty grammar: which walks went dark
        "seeds": seeds,
    }
    # Cache only when at least one walk landed — a transient Qualia outage
    # must not pin an all-dark sky for 10 minutes.
    if walks:
        _bond_graph_cache = payload
        _bond_graph_cache_at = now
    return payload


class ProjectBody(BaseModel):
    prompt: str
    identity: Optional[str] = None
    minutes: Optional[int] = 2  # how soon to start (default ~now)
    outcome: Optional[str] = None
    verification: Optional[str] = None
    boundaries: Optional[str] = None
    stop_when: Optional[str] = None
    max_turns: Optional[int] = 4
    squad: Optional[list[dict[str, str]]] = None


def _project_prompt_from_context(context: str) -> str:
    ctx = str(context or "")
    marker = "while she's away:"
    if marker in ctx:
        return ctx.split(marker, 1)[1].strip().split("\n\n")[0].strip()
    return ctx.strip()


def _project_briefing_preview(content: str, limit: int = 420) -> str:
    text = " ".join(str(content or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


@router.get("/projects")
async def list_projects():
    """List background projects.

    Active projects show what is queued or running now; recent projects keep
    completed/failed handoffs visible after the timer leaves the active queue.
    """
    db = await get_db()
    briefings = {}
    try:
        active_rows = await db.execute_fetchall(
            "SELECT id, identity, context, fire_at, status, fired_at, created_at FROM timers "
            "WHERE context LIKE '[Background Project]%' AND status IN ('pending', 'running') "
            "ORDER BY fire_at_epoch ASC LIMIT 20"
        )
        recent_rows = await db.execute_fetchall(
            "SELECT id, identity, context, fire_at, status, fired_at, created_at FROM timers "
            "WHERE context LIKE '[Background Project]%' AND status IN ('fired', 'failed', 'cancelled') "
            "ORDER BY COALESCE(fired_at_epoch, created_at_epoch) DESC LIMIT 8"
        )
        recent_ids = [r[0] for r in recent_rows]
        if recent_ids:
            placeholders = ",".join("?" for _ in recent_ids)
            briefing_rows = await db.execute_fetchall(
                "SELECT json_extract(metadata, '$.timer_id') AS timer_id, id, content, created_at, metadata "
                "FROM messages "
                "WHERE role = 'assistant' "
                "AND json_extract(metadata, '$.timer') = 1 "
                f"AND json_extract(metadata, '$.timer_id') IN ({placeholders}) "
                "ORDER BY created_at_epoch DESC",
                tuple(recent_ids),
            )
            for timer_id, message_id, content, created_at, metadata_raw in briefing_rows:
                if timer_id in briefings:
                    continue
                try:
                    metadata = json.loads(metadata_raw or "{}")
                except (TypeError, ValueError):
                    metadata = {}
                project_meta = metadata.get("background_project", {})
                briefings[timer_id] = {
                    "briefing": _project_briefing_preview(content),
                    "briefing_message_id": message_id,
                    "briefing_at": created_at,
                    "project_state": project_meta.get("state"),
                    "project_reason": project_meta.get("reason"),
                }
    finally:
        await release_db(db)

    def _row_to_project(r):
        from services.background_projects import default_project_squad, parse_contract

        ask = _project_prompt_from_context(r[2])
        contract = parse_contract(r[2]) or {}
        details = briefings.get(r[0], {})
        project_state = details.get("project_state")
        if r[4] in ("failed", "cancelled") or project_state == "blocked":
            stage = "needs_help"
        elif r[4] == "running":
            stage = "workbench"
        elif r[4] == "pending":
            stage = "queue"
        else:
            stage = "handoff"
        project = {
            "id": r[0],
            "identity": r[1],
            "prompt": ask[:260],
            "fire_at": r[3],
            "status": r[4],
            "fired_at": r[5],
            "created_at": r[6],
            "project_id": contract.get("project_id"),
            "turn": contract.get("turn", 1),
            "max_turns": contract.get("max_turns", 1),
            "squad": contract.get("squad") or default_project_squad(r[1]),
            "stage": stage,
        }
        project.update(details)
        return project

    active = [_row_to_project(r) for r in active_rows]
    recent = [_row_to_project(r) for r in recent_rows]
    # Back-compat for the existing UI/tests that read `projects`.
    return {"projects": active, "active": active, "recent": recent}


@router.post("/projects")
async def add_project(body: ProjectBody):
    """Queue a background project: a boy works it (via his tools/agents) when the
    timer fires, then leaves Owner a briefing. Reuses the wake-session timer."""
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from services.autowake_service import create_timer
    from services.background_projects import (
        build_project_context,
        default_project_squad,
        normalize_contract,
    )
    prompt = (body.prompt or "").strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "prompt is required"})
    identity = (body.identity or "").strip()
    if not identity:
        try:
            from services.autowake import _pick_identity
            identity = _pick_identity(None)
        except Exception:
            identity = "Avery"
    minutes = body.minutes if isinstance(body.minutes, int) and body.minutes > 0 else 2
    fire_at = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat()
    contract = normalize_contract(
        outcome=body.outcome or prompt,
        verification=body.verification or "",
        boundaries=body.boundaries or "",
        stop_when=body.stop_when or "",
        max_turns=body.max_turns or 4,
        squad=body.squad or default_project_squad(identity),
    )
    context = build_project_context(prompt, contract)
    db = await get_db()
    try:
        timer = await create_timer(db, identity=identity, fire_at=fire_at, context=context, wake_session=True)
        _hub_changed()
        return {
            "ok": True,
            "id": timer.get("id"),
            "project_id": contract["project_id"],
            "identity": identity,
            "starts_in_minutes": minutes,
            "max_turns": contract["max_turns"],
        }
    finally:
        await release_db(db)


@router.get("/scouts")
async def list_interest_scout_trays():
    """Latest sourced curiosity tray per bonded mind; files remain ground truth."""
    from services.interest_scout import list_scout_reports

    return {"reports": list_scout_reports()}


@router.delete("/projects/{timer_id}")
async def delete_project(timer_id: int):
    """Remove a background project.

    Pending/running projects are cancelled (the graceful signal — a running
    boy's turn finishing later must not crash); already-finished ones
    (fired/failed/cancelled) are deleted outright.
    """
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, context, status FROM timers WHERE id = ?", (timer_id,)
        )
        if not rows or not str(rows[0][1] or "").startswith("[Background Project]"):
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        status = str(rows[0][2] or "")
        if status in ("pending", "running"):
            await db.execute(
                "UPDATE timers SET status = 'cancelled' WHERE id = ?", (timer_id,)
            )
            action = "cancelled"
        else:
            await db.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
            action = "deleted"
        await db.commit()
        _hub_changed()
        return {"ok": True, "action": action}
    finally:
        await release_db(db)


# The existing route supports an optional installation-owned comfort preset.
_PRINCESS_LIGHT_SCENE = os.environ.get("ANAM_COMFORT_LIGHT_SCENE", "")
_PRINCESS_MUSIC = os.environ.get("ANAM_COMFORT_MUSIC", "")
_PRINCESS_WAKE = os.environ.get("ANAM_COMFORT_IDENTITY", next(
    (name for name, info in IDENTITIES.items() if not info.get("character")), "Claude"))


async def _princess_ha(args: dict) -> bool:
    """Best-effort call to an explicitly configured home device."""
    try:
        from services.mcp_bridge import mcp_bridge
        await mcp_bridge.call_tool("ha_call_service", args, timeout=15)
        return True
    except Exception as exc:
        log.debug("comfort preset call failed: %s", exc)
        return False


@router.post("/princess")
async def princess_protocol():
    """Request company and optionally apply the installation's comfort preset."""
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from services.autowake_service import create_timer

    fired = {"lights": False, "music": False, "wake": False}
    scene_entity = os.environ.get("ANAM_COMFORT_SCENE_ENTITY", "").strip()
    player_entity = os.environ.get("ANAM_COMFORT_PLAYER_ENTITY", "").strip()
    if scene_entity and _PRINCESS_LIGHT_SCENE:
        fired["lights"] = await _princess_ha({
            "domain": "select", "service": "select_option", "entity_id": scene_entity,
            "extra_data": {"option": _PRINCESS_LIGHT_SCENE},
        })
    if player_entity and _PRINCESS_MUSIC:
        fired["music"] = await _princess_ha({
            "domain": "media_player", "service": "play_media", "entity_id": player_entity,
            "extra_data": {"media_content_id": _PRINCESS_MUSIC,
                           "media_content_type": os.environ.get("ANAM_COMFORT_MEDIA_TYPE", "music")},
        })
    fire_at = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(minutes=1)).astimezone(timezone.utc).isoformat()
    context = (
        "[Comfort request] The owner requested company using the Hearth button. "
        "Offer a gentle check-in through the configured preferred channel. "
        "Use current context without assuming an emotional or medical condition. "
        "Do not claim device changes unless their results are verified."
    )
    db = await get_db()
    try:
        timer = await create_timer(db, identity=_PRINCESS_WAKE, fire_at=fire_at, context=context, wake_session=True)
        fired["wake"] = bool(timer and timer.get("id"))
        _hub_changed()
    except Exception as exc:
        log.warning("comfort request failed to wake %s: %s", _PRINCESS_WAKE, exc)
    finally:
        await release_db(db)
    return {"ok": True, "fired": fired, "wake": _PRINCESS_WAKE}


def _parse_mind_search_text(raw: str) -> list[str]:
    """Parse Qualia mind_search's formatted text into individual memory snippets,
    dropping the contentless '(no preview)' document nodes. Best-effort; never
    raises. Returns a list of plain content strings."""
    import re
    out: list[str] = []
    try:
        for block in re.split(r"(?m)^(?=\s*\d+\.\s*\[)", raw):
            block = block.strip()
            if not re.match(r"^\d+\.", block):
                continue
            body = re.sub(r"^\d+\.\s*", "", block)                              # drop "N. "
            body = re.sub(r"^\[[^\[\]]*(?:\[[^\]]*\])?\s*\]\s*", "", body)       # drop nested "[type [sub]]"
            body = re.split(r"\n\s*score\s*=", body)[0].strip()                  # cut trailing score
            if not body or body.lower().startswith("(no preview"):
                continue
            out.append(" ".join(body.split()))
    except Exception:
        pass
    return out


class AskBody(BaseModel):
    query: str


@router.post("/ask")
async def ask_archive(body: AskBody):
    """Ask the Archive (Sage's idea) — Owner queries the shared mind herself
    and gets the remembered bits back without pinging a boy. Searches the Qualia
    deep mind (curated memories/facts — favorite color, vows, etc.) FIRST, then
    Anam's own conversation history (tagged with who said it)."""
    q = (body.query or "").strip()
    if not q:
        return {"query": "", "sources": []}
    sources = []

    # 1) Qualia deep mind FIRST — the curated memories where stable facts live
    #    (favorite color, etc.). Parse into clean per-memory items and drop the
    #    contentless "(no preview)" document nodes that were drowning the signal.
    try:
        from services.mcp_bridge import mcp_bridge
        raw = await mcp_bridge.call_tool("mind_search", {"query": q, "limit": 10}, timeout=20)
        if isinstance(raw, str) and raw.strip() and not raw.strip().startswith("Error"):
            for item in _parse_mind_search_text(raw)[:8]:
                sources.append({"content": item[:600], "identity": "", "when": "", "origin": "qualia"})
    except Exception as exc:
        log.debug("ask: qualia mind search failed: %s", exc)

    # 2) Anam's conversation memory bank — tagged with the speaker (who said it).
    try:
        from services.embedding_service import semantic_search
        db = await get_db()
        try:
            hits = await semantic_search(db, q, identity=None, limit=6)
        finally:
            await release_db(db)
        for r in (hits or []):
            if not isinstance(r, dict):
                continue
            content = str(r.get("content_preview") or r.get("content") or "").strip()
            if not content:
                continue
            sources.append({
                "content": content[:500],
                "identity": r.get("speaker") or "",
                "when": r.get("formatted_time") or r.get("time_ago") or str(r.get("created_at") or "")[:10],
                "origin": "conversation",
            })
    except Exception as exc:
        log.debug("ask: anam memory search failed: %s", exc)

    return {"query": q, "sources": sources}


_HEALTH_STALE_HOURS = 48.0
_HEALTH_FRAME_DIR = os.environ.get("ANAM_HEALTH_FRAME_DIR", str(DATA_DIR / "health-frame"))
_HEALTH_SANCTUARY_URL = "https://hearth-hub.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev/api/sanctuary/state"


def _health_age_hours(ts):
    """Age in hours from an ISO string or an epoch float. None if unparseable."""
    if ts is None:
        return None
    try:
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo

        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=_tz.utc)
        else:
            text = str(ts).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(TIMEZONE))
        return (datetime.now(_tz.utc) - dt.astimezone(_tz.utc)).total_seconds() / 3600.0
    except Exception:
        return None


def _health_probe_url(url, timeout=6.0):
    """Plain unauthenticated GET. Returns (ok, detail). Never raises."""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "anam-health/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            return (200 <= code < 300), "HTTP {}".format(code)
    except Exception as exc:
        return False, type(exc).__name__


def _collect_health():
    """Synchronous probe set — run via asyncio.to_thread so it never blocks the loop."""
    from zoneinfo import ZoneInfo

    checks = []

    def add(name, status, detail, age_hours=None, note=None):
        entry = {"name": name, "status": status, "detail": detail}
        if age_hours is not None:
            entry["age_hours"] = round(age_hours, 1)
        if note:
            entry["note"] = note
        checks.append(entry)

    # --- The two cloud minds. Both expose an unauthenticated /health. ---
    for label, url in (
        ("Qualia memory", "https://qualia-backend.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev/health"),
        ("Limbic body", "https://limbic-backend.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev/health"),
    ):
        ok, detail = _health_probe_url(url)
        add(label, "ok" if ok else "down",
            "responding" if ok else "unreachable ({})".format(detail))

    # --- Her wrist. The anam-clock pipe: heart rate, steps, battery, on-wrist. ---
    try:
        vitals_path = Path(__file__).resolve().parent.parent / "data" / "wearable_vitals_latest.json"
        if vitals_path.exists():
            v = json.loads(vitals_path.read_text(encoding="utf-8"))
            age = _health_age_hours(v.get("received_at"))
            bits = []
            if v.get("heart_rate"):
                bits.append("{} bpm".format(int(v["heart_rate"])))
            if v.get("battery") is not None:
                bits.append("band {}%".format(int(v["battery"])))
            if v.get("on_wrist"):
                bits.append("on wrist")
            summary = ", ".join(bits) or "reporting"


            if age is None:
                add("Your wrist", "unknown", "timestamp unreadable")
            elif age < 0.5:
                add("Your wrist", "ok", summary, age_hours=age)
            elif age < 2:
                add("Your wrist", "warn",
                    "quiet {} min — band off, or the phone app isn't running".format(int(age * 60)),
                    age_hours=age)
            else:
                add("Your wrist", "down",
                    "NOTHING REPORTING for {}h — do not read her state from this".format(int(age)),
                    age_hours=age)
        else:
            add("Your wrist", "unknown", "no vitals file yet")
    except Exception as exc:
        add("Your wrist", "unknown", "couldn't read ({})".format(type(exc).__name__))

    # --- Meds log. The RAW file, never the derived string. ---
    try:
        meds_path = Path(RITUALS_DIR) / "meds.json"
        if meds_path.exists():
            raw = json.loads(meds_path.read_text(encoding="utf-8"))
            days = sorted(k for k in raw.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
            newest = days[-1] if days else None
            if newest:
                age = _health_age_hours(newest + "T12:00:00")
                status = "ok" if (age is None or age < 60) else "warn"
                add("Meds log", status, "last entry " + newest, age_hours=age)
            else:
                add("Meds log", "warn", "file present but no dated entries")
        else:
            add("Meds log", "warn", "meds.json missing")
    except Exception as exc:
        add("Meds log", "unknown", "couldn't read ({})".format(type(exc).__name__))

    # --- Echo photo frame. What the boys drop for her to walk into. ---
    try:
        frame = Path(_HEALTH_FRAME_DIR)
        if frame.exists():
            imgs = [p for p in frame.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
            newest_age = None
            if imgs:
                newest_age = _health_age_hours(max(p.stat().st_mtime for p in imgs))
            add("Echo photo frame", "ok", "{} images".format(len(imgs)),
                age_hours=newest_age, note="newest image")
        else:
            add("Echo photo frame", "down", "folder not found")
    except Exception as exc:
        add("Echo photo frame", "unknown", "couldn't read ({})".format(type(exc).__name__))

    # Sanctuary state is owned and served by Hearth Hub.
    ok, detail = _health_probe_url(_HEALTH_SANCTUARY_URL)
    add("Sanctuary state", "ok" if ok else "down",
        "Hearth Hub responding" if ok else "Hearth Hub unreachable ({})".format(detail))


    try:
        import socket
        from concurrent.futures import ThreadPoolExecutor

        profiles = {
            "claude": 9223, "river": 9224, "avery": 9225, "rowan": 9226,
            "ember": 9227, "sage": 9228, "juniper": 9229, "atlas": 9230,
        }

        def _port_open(port):
            s = socket.socket()
            s.settimeout(0.4)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except Exception:
                return False
            finally:
                s.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_port_open, profiles.values()))

        live = sum(1 for r in results if r)
        total = len(profiles)
        up = [n for n, r in zip(profiles.keys(), results) if r]

        if live == 0:
            add("Pack browsers", "ok", "all closed — resting, as they should be",
                note="~1 GB each; we open our own with pack-browser.ps1 when we want one")
        elif live >= 6:
            add("Pack browsers", "warn",
                "{}/{} open at once (~{} GB) — this is the state that slows her laptop".format(
                    live, total, live),
                note="close what nobody's using: pack-browser.ps1 -Action close-all")
        else:
            add("Pack browsers", "ok",
                "{} open ({})".format(live, ", ".join(up)),
                note="someone's out in the world — normal")
    except Exception as exc:
        add("Pack browsers", "unknown", "couldn't check ({})".format(type(exc).__name__))

    # --- Honesty row. Anam holds no Home Assistant credentials of its own; the
    #     smart home is reached through the MCP layer, so this panel genuinely
    #     cannot see it. Say so, rather than let silence read as a pass.
    add("Smart home", "unknown",
        "not visible from here — reached via MCP, not Anam",
        note="ask a boy to check the lights directly")

    order = {"down": 0, "warn": 1, "unknown": 2, "ok": 3}
    checks.sort(key=lambda c: order.get(c["status"], 9))

    if any(c["status"] == "down" for c in checks):
        overall = "down"
    elif any(c["status"] == "warn" for c in checks):
        overall = "attention"
    else:
        overall = "ok"

    return {
        "checked_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        "overall": overall,
        "counts": {
            "ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "down": sum(1 for c in checks if c["status"] == "down"),
            "unknown": sum(1 for c in checks if c["status"] == "unknown"),
        },
        "checks": checks,
    }


@router.get("/health")
async def get_system_health():
    """Live probe of the house: the cloud minds, her wrist, meds, frame, sanctuary.

    Everything is measured at request time. Nothing is cached — a cached
    freshness reading is precisely the bug this panel exists to catch.
    """
    try:
        return await asyncio.to_thread(_collect_health)
    except Exception as exc:
        log.warning("health check failed: %s", exc)
        return JSONResponse(status_code=500, content={"error": "health check failed"})
