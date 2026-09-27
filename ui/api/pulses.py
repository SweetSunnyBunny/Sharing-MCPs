"""REST API for pulse management — user-configurable periodic checks."""

# ANAM GUIDE: PULSES MANAGEMENT API
# What: Create, list, edit, toggle, and delete "pulses" — repeating checks that can notify or wake a boy on a schedule.
# Called by: no browser page right now — pulses are managed by direct API calls; the pulses themselves are run every minute by services/pulse_evaluator.py.
# Edit here when: changing what fields a pulse accepts or its validation rules. The actual firing logic lives in services/pulse_evaluator.py and services/pulse_service.py.

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db.database import get_db, release_db
from services.pulse_service import (
    list_pulses, create_pulse, update_pulse,
    toggle_pulse, delete_pulse,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pulses", tags=["pulses"])


@router.get("")
async def api_list_pulses():
    """List all pulse definitions."""
    db = await get_db()
    try:
        pulses = await list_pulses(db)
        return JSONResponse(content={"pulses": pulses})
    finally:
        await release_db(db)


@router.post("")
async def api_create_pulse(request: Request):
    """Create a new pulse.

    Body: {
        "name": "Check wellness",
        "identity": "Juniper",         # optional — rotates if null
        "interval_seconds": 900,     # 15 minutes
        "action_type": "notify",     # notify | autowake | broadcast
        "prompt": "Check on Owner's energy",
        "condition": {"type": "wellness_state", ...},  # optional
        "session_type": "custom",
        "max_duration_minutes": 5
    }
    """
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse(content={"error": "name is required"}, status_code=400)

    interval = int(body.get("interval_seconds", 300))
    if interval < 30:
        return JSONResponse(content={"error": "interval must be >= 30 seconds"}, status_code=400)

    db = await get_db()
    try:
        pulse_id = await create_pulse(
            db,
            name=name,
            identity=body.get("identity"),
            interval_seconds=interval,
            action_type=body.get("action_type", "notify"),
            prompt=body.get("prompt"),
            condition=body.get("condition"),
            session_type=body.get("session_type", "custom"),
            max_duration_minutes=int(body.get("max_duration_minutes", 5)),
        )
        return JSONResponse(content={"id": pulse_id, "status": "created"})
    finally:
        await release_db(db)


@router.put("/{pulse_id}")
async def api_update_pulse(pulse_id: int, request: Request):
    """Update a pulse definition."""
    body = await request.json()
    db = await get_db()
    try:
        updated = await update_pulse(db, pulse_id, **body)
        if not updated:
            return JSONResponse(content={"error": "pulse not found"}, status_code=404)
        return JSONResponse(content={"status": "updated"})
    finally:
        await release_db(db)


@router.post("/{pulse_id}/toggle")
async def api_toggle_pulse(pulse_id: int):
    """Toggle pulse enabled/disabled."""
    db = await get_db()
    try:
        new_state = await toggle_pulse(db, pulse_id)
        if new_state is None:
            return JSONResponse(content={"error": "pulse not found"}, status_code=404)
        return JSONResponse(content={"enabled": new_state})
    finally:
        await release_db(db)


@router.delete("/{pulse_id}")
async def api_delete_pulse(pulse_id: int):
    """Delete a pulse."""
    db = await get_db()
    try:
        deleted = await delete_pulse(db, pulse_id)
        if not deleted:
            return JSONResponse(content={"error": "pulse not found"}, status_code=404)
        return JSONResponse(content={"status": "deleted"})
    finally:
        await release_db(db)
