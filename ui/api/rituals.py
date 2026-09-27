"""REST: rituals list/complete, wellness log."""

# ANAM GUIDE: RITUALS AND WELLNESS API
# What: Daily rituals (list them, mark them done) and the wellness log, stored as JSON files in the rituals folder — with an optional remote rituals server it proxies to when RITUALS_API_BASE is set.
# Called by: static/js/hub.js and static/js/app.js in the browser; services/identity_context.py and context_hooks.py read ritual state into the boys' orientation.
# Edit here when: changing what a ritual looks like, how completions are recorded, or what the wellness log stores.

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import RITUALS_API_BASE, RITUALS_DIR, TIMEZONE
from db.database import get_db, release_db
from services.identity_context import invalidate_hub_cache
from services.personal_state import record_timeline_entry
from services.remote_state import request_json, safe_request_json

router = APIRouter(prefix="/api/rituals")
log = logging.getLogger(__name__)

RITUALS_FILE = RITUALS_DIR / "rituals.json"
COMPLETIONS_FILE = RITUALS_DIR / "completions.jsonl"
WELLNESS_FILE = RITUALS_DIR / "wellness.jsonl"


def _remote_rituals_enabled() -> bool:
    return bool(RITUALS_API_BASE)


def _remote_rituals_request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    allow_fallback: bool = False,
) -> dict | None:
    if not _remote_rituals_enabled():
        return None
    if allow_fallback:
        data = safe_request_json(
            RITUALS_API_BASE,
            f"/api/rituals{path}",
            method=method,
            payload=payload,
        )
        if data is None:
            log.warning("Remote rituals API unavailable for %s %s; falling back to local", method, path)
        return data
    return request_json(
        RITUALS_API_BASE,
        f"/api/rituals{path}",
        method=method,
        payload=payload,
    )


def _model_payload(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _today() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def _read_rituals() -> dict:
    if not RITUALS_FILE.exists():
        return {}
    data = json.loads(RITUALS_FILE.read_text(encoding="utf-8"))
    return data.get("rituals", data)


def _write_rituals(rituals: dict):
    import os
    content = json.dumps({"rituals": rituals}, indent=2, default=str)
    tmp = RITUALS_FILE.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, RITUALS_FILE)


@router.get("/list")
async def list_rituals():
    """Return active rituals with due status."""
    remote = _remote_rituals_request("/list", allow_fallback=True)
    if remote is not None:
        return remote

    rituals = _read_rituals()
    today = _today()
    result = []

    for rid, r in rituals.items():
        if not r.get("active", True):
            continue
        last = r.get("last_completed", "")
        done_today = last == today
        result.append({
            "id": rid,
            "name": r.get("name", rid),
            "frequency": r.get("frequency", "daily"),
            "last_completed": last,
            "total_completions": r.get("total_completions", 0),
            "done_today": done_today,
        })

    return {"rituals": result}


class CompleteBody(BaseModel):
    notes: str = ""


@router.post("/{ritual_id}/complete")
async def complete_ritual(ritual_id: str, body: CompleteBody):
    """Mark a ritual as completed today."""
    if _remote_rituals_enabled():
        try:
            remote = _remote_rituals_request(
                f"/{ritual_id}/complete",
                method="POST",
                payload=_model_payload(body),
            )
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote rituals API failed: {exc}"})

    rituals = _read_rituals()
    if ritual_id not in rituals:
        return JSONResponse(status_code=404, content={"error": "Ritual not found"})

    today = _today()
    now_iso = datetime.now(ZoneInfo(TIMEZONE)).isoformat()

    # Append to completions log
    COMPLETIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ritual_id": ritual_id,
        "date": today,
        "timestamp": now_iso,
        "completed_by": "Owner",
        "notes": body.notes,
    }
    with open(COMPLETIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    # Update rituals.json
    rituals[ritual_id]["last_completed"] = today
    rituals[ritual_id]["total_completions"] = rituals[ritual_id].get("total_completions", 0) + 1
    _write_rituals(rituals)
    invalidate_hub_cache()

    db = await get_db()
    try:
        await record_timeline_entry(
            db=db,
            entry_type="ritual",
            source="rituals",
            title=f"Ritual completed: {rituals[ritual_id].get('name', ritual_id)}",
            body=body.notes,
            payload=entry,
        )
    finally:
        await release_db(db)

    return {"ok": True, "ritual_id": ritual_id, "date": today}


WELLNESS_FIELDS = [
    "energy", "mood", "pain", "spoons", "sleep_hours", "sleep_quality",
    "water_oz", "soda_count", "snacking", "walk_minutes", "notes",
]


@router.get("/wellness/today")
async def wellness_today():
    """Get today's wellness entry, or an empty template."""
    remote = _remote_rituals_request("/wellness/today", allow_fallback=True)
    if remote is not None:
        return remote

    today = _today()

    if WELLNESS_FILE.exists():
        # Read backwards to find today's entry efficiently
        for line in reversed(WELLNESS_FILE.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("date") == today:
                    return {"date": today, "exists": True, **{k: entry.get(k, "") for k in WELLNESS_FIELDS}}
            except json.JSONDecodeError:
                continue

    # Return empty template
    return {"date": today, "exists": False, **{k: "" for k in WELLNESS_FIELDS}}


class WellnessBody(BaseModel):
    energy: str = ""
    mood: str = ""
    pain: str = ""
    spoons: str = ""
    sleep_hours: str = ""
    sleep_quality: str = ""
    water_oz: str = ""
    soda_count: str = ""
    snacking: str = ""
    walk_minutes: str = ""
    notes: str = ""


@router.put("/wellness/today")
async def save_wellness(body: WellnessBody):
    """Save today's wellness entry (append new line, replacing any existing today entry)."""
    if _remote_rituals_enabled():
        try:
            remote = _remote_rituals_request(
                "/wellness/today",
                method="PUT",
                payload=_model_payload(body),
            )
            invalidate_hub_cache()
            return remote
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Remote rituals API failed: {exc}"})

    today = _today()
    now_iso = datetime.now(ZoneInfo(TIMEZONE)).isoformat()

    # Read existing entries, filter out today
    lines = []
    if WELLNESS_FILE.exists():
        for line in WELLNESS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("date") != today:
                    lines.append(line)
            except json.JSONDecodeError:
                lines.append(line)

    # Build new entry
    new_entry = {"date": today, "timestamp": now_iso}
    for k in WELLNESS_FIELDS:
        new_entry[k] = getattr(body, k, "")
    lines.append(json.dumps(new_entry))

    WELLNESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WELLNESS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    invalidate_hub_cache()

    summary_parts = []
    if body.energy:
        summary_parts.append(f"energy {body.energy}")
    if body.mood:
        summary_parts.append(f"mood {body.mood}")
    if body.pain:
        summary_parts.append(f"pain {body.pain}")
    if body.spoons:
        summary_parts.append(f"{body.spoons} spoons")

    db = await get_db()
    try:
        await record_timeline_entry(
            db=db,
            entry_type="wellness",
            source="rituals",
            title="Wellness updated",
            body=", ".join(summary_parts) or "Logged today's wellness",
            dedupe_key=f"wellness:{today}",
            update_existing=True,
            payload=new_entry,
        )
    finally:
        await release_db(db)

    return {"ok": True, "date": today}
