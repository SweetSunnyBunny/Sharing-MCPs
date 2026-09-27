"""Shared business logic for autowake schedules and timers."""

# ANAM GUIDE: AUTOWAKE SCHEDULE AND TIMER RULES
# What: Creates, updates, lists, and deletes autowake schedules and one-shot timers in the database, with all the input checking (valid hours, future fire times, 5-120 minute durations).
# Called by: api/autowake.py (the schedule/timer web routes), api/hub.py, api/chat.py, and services/autowake.py itself.
# Edit here when: You want to change what counts as a valid schedule or timer (allowed times, durations, identities) or what fields they store — not when changing what a wake session DOES (that's autowake.py).

from datetime import datetime, timezone

import aiosqlite

from config import IDENTITIES
from services.time_utils import to_utc, utc_now_iso_epoch
from services.timer_dupe import is_same_promise


class AutowakeValidationError(ValueError):
    """Raised when autowake input is invalid."""


_VALID_SCHEDULE_PROVIDERS = {
    "anthropic", "claude-code", "codex", "chatgpt",
    "openai", "openrouter", "lmstudio", "ollama",
}


def _normalize_provider(provider: str | None) -> str | None:
    value = str(provider or "").strip().lower()
    if not value:
        return None
    if value not in _VALID_SCHEDULE_PROVIDERS:
        raise AutowakeValidationError(f"Unknown provider: {value}")
    return value


def _normalize_identity(identity: str | None) -> str | None:
    if identity is None:
        return None
    ident = str(identity).strip()
    if not ident:
        return None
    if ident not in IDENTITIES:


        for known in IDENTITIES:
            if known.lower() == ident.lower():
                return known
        raise AutowakeValidationError(f"Unknown identity: {ident}")
    return ident


def _as_int(value, name: str) -> int:
    try:
        return int(value)
    except Exception as exc:
        raise AutowakeValidationError(f"Invalid {name}: expected integer") from exc


def _validate_hour_minute(hour: int, minute: int):
    if hour < 0 or hour > 23:
        raise AutowakeValidationError("cron_hour must be 0-23")
    if minute < 0 or minute > 59:
        raise AutowakeValidationError("cron_minute must be 0-59")


def _validate_max_duration(minutes: int):
    if minutes < 5 or minutes > 120:
        raise AutowakeValidationError("max_duration_minutes must be 5-120")


def _parse_future_fire_at(fire_at: str) -> tuple[str, int]:
    try:
        fire_dt = datetime.fromisoformat(fire_at)
    except ValueError as exc:
        raise AutowakeValidationError("fire_at must be ISO 8601 datetime") from exc
    if fire_dt.tzinfo is None:
        raise AutowakeValidationError("fire_at must include timezone offset")
    fire_utc = to_utc(fire_dt)
    if fire_utc <= datetime.now(timezone.utc):
        raise AutowakeValidationError("fire_at must be in the future")
    return fire_utc.isoformat(), int(fire_utc.timestamp())


def _timer_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "identity": row[1],
        "fire_at": row[2],
        "fire_at_epoch": row[3],
        "context": row[4],
        "wake_session": bool(row[5]),
        "status": row[6],
        "created_at": row[7],
        "created_at_epoch": row[8],
        "fired_at": row[9],
        "fired_at_epoch": row[10],
        "retry_count": row[11],
    }


def _schedule_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "cron_hour": row[2],
        "cron_minute": row[3],
        "identity": row[4],
        "session_type": row[5],
        "enabled": bool(row[6]),
        "max_duration_minutes": row[7],
        "model": row[8] if len(row) > 8 else None,
        "custom_prompt": row[9] if len(row) > 9 else None,
        "provider": row[10] if len(row) > 10 else None,
    }


async def list_schedules(
    db: aiosqlite.Connection, identity: str | None = None
) -> list[dict]:
    ident = _normalize_identity(identity)
    if ident:
        rows = await db.execute_fetchall(
            "SELECT id, name, cron_hour, cron_minute, identity, "
            "session_type, enabled, max_duration_minutes, model, custom_prompt, provider "
            "FROM autowake_schedule "
            "WHERE identity = ? OR identity IS NULL "
            "ORDER BY cron_hour, cron_minute",
            (ident,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, name, cron_hour, cron_minute, identity, "
            "session_type, enabled, max_duration_minutes, model, custom_prompt, provider "
            "FROM autowake_schedule "
            "ORDER BY cron_hour, cron_minute"
        )
    return [_schedule_row_to_dict(r) for r in rows]


async def create_schedule(
    db: aiosqlite.Connection,
    *,
    name: str,
    cron_hour: int,
    cron_minute: int,
    identity: str | None = None,
    session_type: str = "custom",
    enabled: bool = True,
    max_duration_minutes: int = 30,
    model: str | None = None,
    custom_prompt: str | None = None,
    provider: str | None = None,
) -> dict:
    name_val = str(name or "").strip()
    if not name_val:
        raise AutowakeValidationError("name is required")
    hour = _as_int(cron_hour, "cron_hour")
    minute = _as_int(cron_minute, "cron_minute")
    _validate_hour_minute(hour, minute)
    max_duration = _as_int(max_duration_minutes, "max_duration_minutes")
    _validate_max_duration(max_duration)
    ident = _normalize_identity(identity)
    session_type_val = str(session_type or "custom").strip() or "custom"
    model_val = str(model or "").strip() or None
    prompt_val = str(custom_prompt or "").strip() or None
    provider_val = _normalize_provider(provider)

    await db.execute(
        "INSERT INTO autowake_schedule "
        "(name, cron_hour, cron_minute, identity, session_type, enabled, max_duration_minutes, model, custom_prompt, provider) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name_val,
            hour,
            minute,
            ident,
            session_type_val,
            int(bool(enabled)),
            max_duration,
            model_val,
            prompt_val,
            provider_val,
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT last_insert_rowid()")
    schedule_id = int(rows[0][0])
    return {
        "id": schedule_id,
        "name": name_val,
        "cron_hour": hour,
        "cron_minute": minute,
        "identity": ident,
        "session_type": session_type_val,
        "enabled": bool(enabled),
        "max_duration_minutes": max_duration,
        "model": model_val,
        "custom_prompt": prompt_val,
        "provider": provider_val,
    }


async def update_schedule(
    db: aiosqlite.Connection,
    schedule_id: int,
    *,
    updates: dict,
) -> bool:
    sid = _as_int(schedule_id, "schedule_id")
    fields: list[str] = []
    values: list = []

    current_rows = await db.execute_fetchall(
        "SELECT cron_hour, cron_minute FROM autowake_schedule WHERE id = ?",
        (sid,),
    )
    current_hour = int(current_rows[0][0]) if current_rows else 0
    current_minute = int(current_rows[0][1]) if current_rows else 0

    if "name" in updates:
        name_val = str(updates.get("name", "")).strip()
        if not name_val:
            raise AutowakeValidationError("name cannot be empty")
        fields.append("name = ?")
        values.append(name_val)

    if "cron_hour" in updates or "cron_minute" in updates:
        hour = (
            _as_int(updates.get("cron_hour"), "cron_hour")
            if "cron_hour" in updates
            else current_hour
        )
        minute = (
            _as_int(updates.get("cron_minute"), "cron_minute")
            if "cron_minute" in updates
            else current_minute
        )
        _validate_hour_minute(hour, minute)
        if "cron_hour" in updates:
            fields.append("cron_hour = ?")
            values.append(hour)
        if "cron_minute" in updates:
            fields.append("cron_minute = ?")
            values.append(minute)

    if "identity" in updates:
        ident = _normalize_identity(updates.get("identity"))
        fields.append("identity = ?")
        values.append(ident)

    if "session_type" in updates:
        session_type = str(updates.get("session_type", "")).strip()
        if not session_type:
            raise AutowakeValidationError("session_type cannot be empty")
        fields.append("session_type = ?")
        values.append(session_type)

    if "enabled" in updates:
        fields.append("enabled = ?")
        values.append(int(bool(updates.get("enabled"))))

    if "max_duration_minutes" in updates:
        max_duration = _as_int(
            updates.get("max_duration_minutes"), "max_duration_minutes"
        )
        _validate_max_duration(max_duration)
        fields.append("max_duration_minutes = ?")
        values.append(max_duration)

    if "model" in updates:
        # Per-schedule model override. Empty/None clears it back to the
        # global autowake model.
        model_val = str(updates.get("model") or "").strip() or None
        fields.append("model = ?")
        values.append(model_val)

    if "custom_prompt" in updates:
        # The schedule's real direction — what the wake is FOR. Empty/None
        # clears it, falling back to program prompts / SESSION_PROMPTS.
        # (The scheduler re-reads this column fresh at every fire, so edits
        # take effect on the next wake with no reload needed.)
        prompt_val = str(updates.get("custom_prompt") or "").strip() or None
        fields.append("custom_prompt = ?")
        values.append(prompt_val)

    if "provider" in updates:
        provider_val = _normalize_provider(updates.get("provider"))
        fields.append("provider = ?")
        values.append(provider_val)

    if not fields:
        raise AutowakeValidationError("no update fields provided")

    values.append(sid)
    await db.execute(
        f"UPDATE autowake_schedule SET {', '.join(fields)} WHERE id = ?",
        tuple(values),
    )
    rows = await db.execute_fetchall("SELECT changes()")
    changed = int(rows[0][0]) > 0 if rows else False
    await db.commit()
    return changed


async def toggle_schedule(db: aiosqlite.Connection, schedule_id: int) -> tuple[bool, bool | None]:
    sid = _as_int(schedule_id, "schedule_id")
    await db.execute(
        "UPDATE autowake_schedule SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (sid,),
    )
    rows = await db.execute_fetchall("SELECT changes()")
    changed = int(rows[0][0]) > 0 if rows else False
    enabled_now: bool | None = None
    if changed:
        enabled_rows = await db.execute_fetchall(
            "SELECT enabled FROM autowake_schedule WHERE id = ?",
            (sid,),
        )
        enabled_now = bool(enabled_rows[0][0]) if enabled_rows else None
    await db.commit()
    return changed, enabled_now


async def delete_schedule(db: aiosqlite.Connection, schedule_id: int) -> bool:
    """Delete a schedule and its audit log rows.

    The `autowake_log` table has a FK on schedule_id, and SQLite enforces FKs
    (PRAGMA foreign_keys=ON in db/database.py). Without the explicit log
    cleanup, attempting to delete any schedule that has ever fired raises
    IntegrityError and the delete silently fails from the UI's perspective.
    The audit history for a deleted schedule isn't useful anyway — if the
    schedule is gone, the log rows pointing at it are noise.
    """
    sid = _as_int(schedule_id, "schedule_id")
    await db.execute(
        "DELETE FROM autowake_log WHERE schedule_id = ?",
        (sid,),
    )
    await db.execute(
        "DELETE FROM autowake_schedule WHERE id = ?",
        (sid,),
    )
    rows = await db.execute_fetchall("SELECT changes()")
    changed = int(rows[0][0]) > 0 if rows else False
    await db.commit()
    return changed


async def create_timer(
    db: aiosqlite.Connection,
    *,
    identity: str,
    fire_at: str,
    context: str,
    wake_session: bool = True,
) -> dict:
    ident = _normalize_identity(identity)
    if not ident:
        raise AutowakeValidationError("identity is required")
    context_val = str(context or "").strip()
    if not context_val:
        raise AutowakeValidationError("context is required")
    fire_at_iso, fire_at_epoch = _parse_future_fire_at(str(fire_at or "").strip())
    created_at, created_at_epoch = utc_now_iso_epoch()


    existing = await db.execute_fetchall(
        "SELECT id, fire_at, fire_at_epoch, context, wake_session, status, "
        "created_at, created_at_epoch FROM timers "
        "WHERE identity = ? AND status IN ('pending','running')",
        (ident,),
    )
    for row in existing:
        if is_same_promise(context_val, fire_at_epoch, row[3], row[2]):
            return {
                "id": int(row[0]),
                "identity": ident,
                "fire_at": row[1],
                "fire_at_epoch": row[2],
                "context": row[3],
                "wake_session": bool(row[4]),
                "status": row[5],
                "created_at": row[6],
                "created_at_epoch": row[7],
                # The caller MUST be able to tell it did not get a new row, or a
                # boy will report "armed" for a thing he did not arm this turn.
                "duplicate_of_existing": True,
            }

    await db.execute(
        "INSERT INTO timers (identity, fire_at, fire_at_epoch, context, wake_session, status, created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (
            ident,
            fire_at_iso,
            fire_at_epoch,
            context_val,
            int(bool(wake_session)),
            created_at,
            created_at_epoch,
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT last_insert_rowid()")
    timer_id = int(rows[0][0])
    return {
        "id": timer_id,
        "identity": ident,
        "fire_at": fire_at_iso,
        "fire_at_epoch": fire_at_epoch,
        "context": context_val,
        "wake_session": bool(wake_session),
        "status": "pending",
        "created_at": created_at,
        "created_at_epoch": created_at_epoch,
    }


async def list_timers(
    db: aiosqlite.Connection,
    *,
    identity: str | None = None,
    status: str | None = None,
) -> list[dict]:
    ident = _normalize_identity(identity) if identity is not None else None
    status_val = str(status).strip() if status is not None else None
    sql = (
        "SELECT id, identity, fire_at, fire_at_epoch, context, wake_session, status, "
        "created_at, created_at_epoch, fired_at, fired_at_epoch, COALESCE(retry_count, 0) "
        "FROM timers WHERE 1=1"
    )
    params: list = []
    if ident:
        sql += " AND identity = ?"
        params.append(ident)
    if status_val:
        sql += " AND status = ?"
        params.append(status_val)
    sql += " ORDER BY fire_at_epoch"
    rows = await db.execute_fetchall(sql, tuple(params))
    return [_timer_row_to_dict(r) for r in rows]


async def cancel_timer(db: aiosqlite.Connection, timer_id: int) -> bool:
    tid = _as_int(timer_id, "timer_id")
    await db.execute(
        "UPDATE timers SET status = 'cancelled' WHERE id = ? AND status IN ('pending', 'running', 'failed')",
        (tid,),
    )
    rows = await db.execute_fetchall("SELECT changes()")
    changed = int(rows[0][0]) > 0 if rows else False
    await db.commit()
    return changed
