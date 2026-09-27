"""Pulse service — persistent, user-configurable periodic checks.

Pulses are lightweight recurring tasks with optional conditions.
Unlike autowake schedules (cron-based, fire full sessions), pulses
run at shorter intervals and can fire notifications, broadcasts,
or lightweight autowake sessions.

Schema: pulses table (migration 013)
"""

# ANAM GUIDE: PULSE DEFINITIONS STORAGE
# What: Saves, lists, updates, and deletes pulse definitions (name, how often, what action) in the pulses database table.
# Called by: api/pulses.py (the Hub's pulse editor) and services/pulse_evaluator.py (which fires them).
# Edit here when: adding a new field to a pulse or changing how due-ness is calculated.

import json
import logging
import time
from datetime import datetime, timezone

import aiosqlite

from config import TIMEZONE
from services.time_utils import utc_now_iso_epoch
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


async def list_pulses(db: aiosqlite.Connection) -> list[dict]:
    """List all pulse definitions."""
    rows = await db.execute_fetchall(
        "SELECT id, name, identity, interval_seconds, condition_json, "
        "action_type, prompt, session_type, max_duration_minutes, "
        "enabled, last_fired_at, fire_count, created_at "
        "FROM pulses ORDER BY created_at DESC"
    )
    return [
        {
            "id": r[0], "name": r[1], "identity": r[2],
            "interval_seconds": r[3],
            "condition": json.loads(r[4]) if r[4] else None,
            "action_type": r[5], "prompt": r[6],
            "session_type": r[7], "max_duration_minutes": r[8],
            "enabled": bool(r[9]), "last_fired_at": r[10],
            "fire_count": r[11], "created_at": r[12],
        }
        for r in rows
    ]


async def create_pulse(
    db: aiosqlite.Connection,
    name: str,
    identity: str | None,
    interval_seconds: int,
    action_type: str = "notify",
    prompt: str | None = None,
    condition: dict | None = None,
    session_type: str = "custom",
    max_duration_minutes: int = 5,
) -> int:
    """Create a new pulse definition. Returns the pulse ID."""
    now_iso, now_epoch = utc_now_iso_epoch()
    condition_json = json.dumps(condition) if condition else None

    cursor = await db.execute(
        "INSERT INTO pulses "
        "(name, identity, interval_seconds, condition_json, action_type, "
        "prompt, session_type, max_duration_minutes, enabled, created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (name, identity, interval_seconds, condition_json, action_type,
         prompt, session_type, max_duration_minutes, now_iso, now_epoch),
    )
    await db.commit()
    return cursor.lastrowid


async def update_pulse(
    db: aiosqlite.Connection,
    pulse_id: int,
    **fields,
) -> bool:
    """Update pulse fields. Returns True if pulse was found."""
    allowed = {
        "name", "identity", "interval_seconds", "condition_json",
        "action_type", "prompt", "session_type", "max_duration_minutes",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "condition" in fields:
        updates["condition_json"] = json.dumps(fields["condition"]) if fields["condition"] else None

    if not updates:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [pulse_id]
    result = await db.execute(
        f"UPDATE pulses SET {set_clause} WHERE id = ?", tuple(values)
    )
    await db.commit()
    return result.rowcount > 0


async def toggle_pulse(db: aiosqlite.Connection, pulse_id: int) -> bool | None:
    """Toggle pulse enabled state. Returns new state or None if not found."""
    rows = await db.execute_fetchall(
        "SELECT enabled FROM pulses WHERE id = ?", (pulse_id,)
    )
    if not rows:
        return None
    new_state = 0 if rows[0][0] else 1
    await db.execute(
        "UPDATE pulses SET enabled = ? WHERE id = ?", (new_state, pulse_id)
    )
    await db.commit()
    return bool(new_state)


async def delete_pulse(db: aiosqlite.Connection, pulse_id: int) -> bool:
    """Delete a pulse. Returns True if found."""
    result = await db.execute("DELETE FROM pulses WHERE id = ?", (pulse_id,))
    await db.commit()
    return result.rowcount > 0


async def get_due_pulses(db: aiosqlite.Connection) -> list[dict]:
    """Get pulses that are due to fire (enabled + interval elapsed)."""
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    rows = await db.execute_fetchall(
        "SELECT id, name, identity, interval_seconds, condition_json, "
        "action_type, prompt, session_type, max_duration_minutes, "
        "last_fired_at_epoch, fire_count "
        "FROM pulses WHERE enabled = 1"
    )

    due = []
    for r in rows:
        pulse_id, name, identity, interval, cond_json, action_type, prompt, \
            session_type, max_dur, last_epoch, fire_count = r

        # Check if interval has elapsed
        if last_epoch and (now_epoch - last_epoch) < interval:
            continue

        pulse = {
            "id": pulse_id, "name": name, "identity": identity,
            "interval_seconds": interval,
            "condition": json.loads(cond_json) if cond_json else None,
            "action_type": action_type, "prompt": prompt,
            "session_type": session_type,
            "max_duration_minutes": max_dur,
            "fire_count": fire_count or 0,
        }
        due.append(pulse)

    return due


async def mark_pulse_fired(db: aiosqlite.Connection, pulse_id: int):
    """Mark a pulse as just fired."""
    now_iso, now_epoch = utc_now_iso_epoch()
    await db.execute(
        "UPDATE pulses SET last_fired_at = ?, last_fired_at_epoch = ?, "
        "fire_count = fire_count + 1 WHERE id = ?",
        (now_iso, now_epoch, pulse_id),
    )
    await db.commit()
