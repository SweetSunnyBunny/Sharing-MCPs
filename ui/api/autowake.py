"""REST: Autowake schedule CRUD + failsafe settings."""

# ANAM GUIDE: AUTOWAKE SETTINGS ROUTES
# What: /api/autowake/* — create/edit/toggle the boys' wake schedules and one-off timers, plus failsafe and care-signal settings and scheduler health.
# Called by: static/js/settings.js (Settings Hub → Autowake tab); the real scheduling engine is services/autowake.py + services/autowake_service.py.
# Edit here when: Adding a new schedule/timer field or endpoint. For WHAT happens when a wake fires, edit services/autowake.py instead.

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import IDENTITIES
from db.database import get_db, release_db
from services.autowake import load_and_schedule, scheduler, FAILSAFE_DEFAULTS, get_care_signal_settings
from services.autowake_service import (
    AutowakeValidationError,
    list_schedules,
    create_schedule,
    update_schedule,
    delete_schedule,
    toggle_schedule,
    create_timer,
    list_timers,
    cancel_timer,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autowake")


class ScheduleCreate(BaseModel):
    name: str
    cron_hour: int = Field(ge=0, le=23)
    cron_minute: int = Field(ge=0, le=59)
    identity: str | None = None
    session_type: str = "custom"
    enabled: bool = True
    max_duration_minutes: int = Field(default=30, ge=5, le=120)
    model: str | None = None  # per-schedule model override (e.g. "claude-fable-5")
    custom_prompt: str | None = None  # the wake's real direction; None = program/session default
    provider: str | None = None  # per-schedule provider override; empty = normal routing


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_hour: int | None = Field(default=None, ge=0, le=23)
    cron_minute: int | None = Field(default=None, ge=0, le=59)
    identity: str | None = None
    session_type: str | None = None
    enabled: bool | None = None
    max_duration_minutes: int | None = Field(default=None, ge=5, le=120)
    model: str | None = None  # per-schedule model override; empty string clears
    custom_prompt: str | None = None  # empty string clears back to program/session default
    provider: str | None = None  # empty string clears back to normal routing


def _to_update_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


@router.get("/schedules")
async def get_schedules(identity: str | None = None):
    db = await get_db()
    try:
        return {"schedules": await list_schedules(db, identity=identity)}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.post("/schedules")
async def post_schedule(body: ScheduleCreate):
    db = await get_db()
    try:
        schedule = await create_schedule(
            db,
            name=body.name,
            cron_hour=body.cron_hour,
            cron_minute=body.cron_minute,
            identity=body.identity,
            session_type=body.session_type,
            enabled=body.enabled,
            max_duration_minutes=body.max_duration_minutes,
            model=body.model,
            custom_prompt=body.custom_prompt,
            provider=body.provider,
        )
        await load_and_schedule()
        return {"status": "created", **schedule}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.put("/schedules/{schedule_id}")
async def put_schedule(schedule_id: int, body: ScheduleUpdate):
    updates = _to_update_dict(body)
    db = await get_db()
    try:
        changed = await update_schedule(db, schedule_id, updates=updates)
        if changed:
            await load_and_schedule()
        return {"status": "updated" if changed else "not_found", "id": schedule_id}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.delete("/schedules/{schedule_id}")
async def remove_schedule(schedule_id: int):
    db = await get_db()
    try:
        changed = await delete_schedule(db, schedule_id)
        if changed:
            await load_and_schedule()
        return {"status": "deleted" if changed else "not_found", "id": schedule_id}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.post("/schedules/{schedule_id}/toggle")
async def flip_schedule(schedule_id: int):
    db = await get_db()
    try:
        changed, enabled_now = await toggle_schedule(db, schedule_id)
        if changed:
            await load_and_schedule()
        return {
            "status": "toggled" if changed else "not_found",
            "id": schedule_id,
            "enabled": enabled_now,
        }
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.get("/failsafe")
async def get_failsafe():
    db = await get_db()
    try:
        settings = dict(FAILSAFE_DEFAULTS)
        rows = await db.execute_fetchall(
            "SELECT key, value FROM settings WHERE key LIKE 'failsafe_%'"
        )
        for row in rows:
            settings[row[0]] = row[1]
        return settings
    finally:
        await release_db(db)


class FailsafeUpdate(BaseModel):
    failsafe_enabled: str | None = None
    failsafe_gentle_minutes: str | None = None
    failsafe_concerned_minutes: str | None = None
    failsafe_emergency_minutes: str | None = None


class CareSignalUpdate(BaseModel):
    care_signals_enabled: str | None = None
    care_signal_low_energy_identity: str | None = None
    care_signal_low_energy_delay_minutes: str | None = None
    care_signal_meds_identity: str | None = None
    care_signal_meds_delay_minutes: str | None = None
    care_signal_am_meds_hour: str | None = None
    care_signal_pm_meds_hour: str | None = None


@router.put("/failsafe")
async def update_failsafe(body: FailsafeUpdate):
    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        for key in [
            "failsafe_enabled",
            "failsafe_gentle_minutes",
            "failsafe_concerned_minutes",
            "failsafe_emergency_minutes",
        ]:
            val = getattr(body, key)
            if val is not None:
                await db.execute(
                    "INSERT INTO settings (key, value, updated_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
                    (key, val, now, val, now),
                )
        await db.commit()
        return {"status": "updated"}
    finally:
        await release_db(db)


@router.get("/status")
async def scheduler_status():
    """Return scheduler health and next fire times."""
    from services.scheduler_health import get_scheduler_health

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_fire": (
                job.next_run_time.isoformat() if job.next_run_time else None
            ),
        })
    return {"running": scheduler.running, "jobs": jobs, "heartbeat": get_scheduler_health()}


@router.post("/reload")
async def reload_schedules():
    """Re-register all schedules with APScheduler.

    Useful after direct DB inserts or bulk edits outside the REST API.
    """
    await load_and_schedule()
    return {"status": "reloaded", "jobs": len(scheduler.get_jobs())}


@router.get("/care-signals")
async def care_signal_status():
    db = await get_db()
    try:
        settings = await get_care_signal_settings(db)
        rows = await db.execute_fetchall(
            "SELECT id, identity, fire_at, context, status FROM timers "
            "WHERE context LIKE '[Care signal:%' AND status IN ('pending', 'running') "
            "ORDER BY fire_at_epoch ASC LIMIT 10"
        )
        timers = [
            {
                "id": row[0],
                "identity": row[1],
                "fire_at": row[2],
                "context": row[3],
                "status": row[4],
            }
            for row in rows
        ]
        return {"settings": settings, "timers": timers}
    finally:
        await release_db(db)


@router.put("/care-signals")
async def update_care_signals(body: CareSignalUpdate):
    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        payload = _to_update_dict(body)
        for identity_key in ("care_signal_low_energy_identity", "care_signal_meds_identity"):
            value = payload.get(identity_key)
            if value is not None and value not in IDENTITIES:
                return JSONResponse(status_code=400, content={"error": f"Unknown identity: {value}"})
        for delay_key in ("care_signal_low_energy_delay_minutes", "care_signal_meds_delay_minutes"):
            value = payload.get(delay_key)
            if value is not None:
                try:
                    if int(value) < 1:
                        raise ValueError
                except Exception:
                    return JSONResponse(status_code=400, content={"error": f"Invalid delay: {delay_key}"})
        for hour_key in ("care_signal_am_meds_hour", "care_signal_pm_meds_hour"):
            value = payload.get(hour_key)
            if value is not None:
                try:
                    if not (0 <= int(value) <= 23):
                        raise ValueError
                except Exception:
                    return JSONResponse(status_code=400, content={"error": f"Invalid hour (0-23): {hour_key}"})
        for key, value in payload.items():
            if value is None:
                continue
            await db.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
                (key, value, now, value, now),
            )
        await db.commit()
        return {"status": "updated"}
    finally:
        await release_db(db)


class TimerCreate(BaseModel):
    identity: str
    fire_at: str
    context: str
    wake_session: bool = True


@router.post("/timers")
async def post_timer(body: TimerCreate):
    db = await get_db()
    try:
        timer = await create_timer(
            db,
            identity=body.identity,
            fire_at=body.fire_at,
            context=body.context,
            wake_session=body.wake_session,
        )
        log.info(
            "Timer created: #%d for %s at %s",
            timer["id"],
            timer["identity"],
            timer["fire_at"],
        )
        return {"status": "created", **timer}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.get("/timers")
async def get_timers(identity: str | None = None, status: str | None = None):
    db = await get_db()
    try:
        timers = await list_timers(db, identity=identity, status=status)
        if status is None:
            timers = [t for t in timers if t["status"] not in ("fired", "cancelled")]
        return {"timers": timers}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)


@router.delete("/timers/{timer_id}")
async def remove_timer(timer_id: int):
    db = await get_db()
    try:
        changed = await cancel_timer(db, timer_id)
        return {"status": "cancelled" if changed else "not_found_or_terminal", "id": timer_id}
    except AutowakeValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await release_db(db)
