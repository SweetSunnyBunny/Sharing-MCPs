"""CRUD operations for conditional triggers (impulses and watchers)."""

# ANAM GUIDE: TRIGGER STORAGE AND RULES
# What: Saves, lists, edits, and deletes the triggers themselves in the database, and
#       checks that a new trigger's condition is shaped correctly before saving it.
# Called by: services/trigger_engine.py (reads them to evaluate) and
#            services/claude_api.py (the boys' create/list/delete trigger tools).
# Edit here when: You add a new condition type (add it to VALID_CONDITION_TYPES here
#                 AND teach trigger_engine.py to evaluate it), or change what counts
#                 as a valid trigger.

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from config import IDENTITIES

log = logging.getLogger(__name__)

VALID_TRIGGER_TYPES = {"impulse", "watcher"}
VALID_ACTION_TYPES = {"autowake", "notify", "broadcast"}
VALID_CONDITION_TYPES = {
    "presence_state",       # {"type": "presence_state", "state": "offline|active|idle|connected"}
    "presence_transition",  # {"type": "presence_transition", "from": "offline", "to": "active"}
    "time_window",          # {"type": "time_window", "start": "09:00", "end": "17:00"}
    "routine_missing",      # {"type": "routine_missing", "schedule_name": "Morning Anchor", "by_hour": 9}
    "wellness_state",       # {"type": "wellness_state", "metric": "energy", "value": "low"}
    "inactivity",           # {"type": "inactivity", "minutes": 120}
    "compound_and",         # {"type": "compound_and", "conditions": [...]}
    "compound_or",          # {"type": "compound_or", "conditions": [...]}
}


def _validate_condition(condition: dict) -> None:
    """Validate condition JSON structure."""
    if not isinstance(condition, dict):
        raise ValueError("Condition must be a dict")
    ctype = condition.get("type")
    if ctype not in VALID_CONDITION_TYPES:
        raise ValueError(f"Invalid condition type: {ctype}. Valid: {VALID_CONDITION_TYPES}")
    if ctype in ("compound_and", "compound_or"):
        subs = condition.get("conditions", [])
        if not isinstance(subs, list) or not subs:
            raise ValueError(f"{ctype} requires a non-empty 'conditions' list")
        for sub in subs:
            _validate_condition(sub)


def _normalize_identity(identity: str) -> str:
    """Normalize identity name to title case."""
    for name in IDENTITIES:
        if name.lower() == identity.lower():
            return name
    raise ValueError(f"Unknown identity: {identity}. Valid: {list(IDENTITIES)}")


def _row_to_dict(row) -> dict:
    """Convert a DB row to a trigger dict."""
    return {
        "id": row[0],
        "name": row[1],
        "trigger_type": row[2],
        "identity": row[3],
        "condition_json": json.loads(row[4]) if isinstance(row[4], str) else row[4],
        "action_type": row[5],
        "prompt": row[6],
        "session_type": row[7],
        "max_duration_minutes": row[8],
        "enabled": bool(row[9]),
        "cooldown_minutes": row[10],
        "last_fired_at": row[11],
        "last_fired_at_epoch": row[12],
        "fire_count": row[13],
        "created_at": row[14],
        "created_at_epoch": row[15],
        "status": row[16] or "idle",
        "waiting_since_epoch": row[17],
    }


_SELECT_COLS = (
    "id, name, trigger_type, identity, condition_json, action_type, "
    "prompt, session_type, max_duration_minutes, enabled, cooldown_minutes, "
    "last_fired_at, last_fired_at_epoch, fire_count, created_at, created_at_epoch, "
    "COALESCE(status, 'idle'), waiting_since_epoch"
)


async def list_triggers(
    db: aiosqlite.Connection,
    identity: str | None = None,
    trigger_type: str | None = None,
    enabled_only: bool = False,
) -> list[dict]:
    """List triggers with optional filters."""
    query = f"SELECT {_SELECT_COLS} FROM triggers WHERE 1=1"
    params: list = []

    if identity:
        query += " AND identity = ?"
        params.append(_normalize_identity(identity))
    if trigger_type:
        if trigger_type not in VALID_TRIGGER_TYPES:
            raise ValueError(f"Invalid trigger_type: {trigger_type}")
        query += " AND trigger_type = ?"
        params.append(trigger_type)
    if enabled_only:
        query += " AND enabled = 1"

    query += " ORDER BY created_at_epoch DESC"
    rows = await db.execute_fetchall(query, tuple(params))
    return [_row_to_dict(row) for row in rows]


async def create_trigger(
    db: aiosqlite.Connection,
    *,
    name: str,
    trigger_type: str,
    identity: str,
    condition: dict,
    prompt: str = "",
    action_type: str = "autowake",
    session_type: str = "custom",
    max_duration_minutes: int = 15,
    cooldown_minutes: int = 30,
) -> dict:
    """Create a new trigger."""
    if not name or not name.strip():
        raise ValueError("Trigger name is required")
    name = name.strip()

    if trigger_type not in VALID_TRIGGER_TYPES:
        raise ValueError(f"Invalid trigger_type: {trigger_type}. Valid: {VALID_TRIGGER_TYPES}")

    identity = _normalize_identity(identity)
    _validate_condition(condition)

    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"Invalid action_type: {action_type}. Valid: {VALID_ACTION_TYPES}")

    if not 1 <= max_duration_minutes <= 120:
        raise ValueError("max_duration_minutes must be 1-120")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be >= 0")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_epoch = int(now.timestamp())

    cursor = await db.execute(
        "INSERT INTO triggers "
        "(name, trigger_type, identity, condition_json, action_type, prompt, "
        "session_type, max_duration_minutes, enabled, cooldown_minutes, "
        "fire_count, created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?)",
        (
            name, trigger_type, identity, json.dumps(condition), action_type,
            prompt, session_type, max_duration_minutes, cooldown_minutes,
            now_iso, now_epoch,
        ),
    )
    await db.commit()
    trigger_id = cursor.lastrowid

    rows = await db.execute_fetchall(
        f"SELECT {_SELECT_COLS} FROM triggers WHERE id = ?", (trigger_id,)
    )
    return _row_to_dict(rows[0])


async def update_trigger(
    db: aiosqlite.Connection,
    trigger_id: int,
    *,
    updates: dict,
) -> bool:
    """Update a trigger. Returns True if changed."""
    allowed = {
        "name", "trigger_type", "identity", "condition", "condition_json", "action_type",
        "prompt", "session_type", "max_duration_minutes", "cooldown_minutes",
    }
    set_parts = []
    params = []

    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "identity":
            value = _normalize_identity(value)
        elif key == "trigger_type" and value not in VALID_TRIGGER_TYPES:
            raise ValueError(f"Invalid trigger_type: {value}")
        elif key == "action_type" and value not in VALID_ACTION_TYPES:
            raise ValueError(f"Invalid action_type: {value}")
        elif key == "condition":
            key = "condition_json"
            _validate_condition(value)
            value = json.dumps(value)
        elif key == "condition_json":
            if isinstance(value, dict):
                _validate_condition(value)
                value = json.dumps(value)
            elif isinstance(value, str):
                _validate_condition(json.loads(value))
        elif key == "max_duration_minutes":
            value = int(value)
            if not 1 <= value <= 120:
                raise ValueError("max_duration_minutes must be 1-120")
        elif key == "cooldown_minutes":
            value = int(value)
            if value < 0:
                raise ValueError("cooldown_minutes must be >= 0")

        set_parts.append(f"{key} = ?")
        params.append(value)

    if not set_parts:
        return False

    params.append(trigger_id)
    result = await db.execute(
        f"UPDATE triggers SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params),
    )
    await db.commit()
    return result.rowcount > 0


async def delete_trigger(db: aiosqlite.Connection, trigger_id: int) -> bool:
    """Delete a trigger. Returns True if deleted."""
    result = await db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
    await db.commit()
    return result.rowcount > 0


async def toggle_trigger(db: aiosqlite.Connection, trigger_id: int) -> tuple[bool, bool | None]:
    """Toggle a trigger's enabled state. Returns (changed, enabled_now)."""
    result = await db.execute(
        "UPDATE triggers SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (trigger_id,),
    )
    await db.commit()
    if result.rowcount == 0:
        return False, None
    rows = await db.execute_fetchall("SELECT enabled FROM triggers WHERE id = ?", (trigger_id,))
    return True, bool(rows[0][0]) if rows else None


async def set_waiting(db: aiosqlite.Connection, trigger_id: int) -> None:
    """Park a matched trigger whose identity was busy as 'waiting'.

    The evaluator fires waiting triggers on a later tick, as soon as the
    identity frees, without re-evaluating conditions — the matched moment
    was real (presence events age out of the eval window).
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE triggers SET status = 'waiting', waiting_since_epoch = ? WHERE id = ?",
        (int(now.timestamp()), trigger_id),
    )
    await db.commit()


async def clear_waiting(db: aiosqlite.Connection, trigger_id: int) -> None:
    """Return a waiting trigger to 'idle' without firing it (e.g. wait expired)."""
    await db.execute(
        "UPDATE triggers SET status = 'idle', waiting_since_epoch = NULL WHERE id = ?",
        (trigger_id,),
    )
    await db.commit()


async def mark_fired(
    db: aiosqlite.Connection,
    trigger_id: int,
    disable_if_impulse: bool = True,
) -> None:
    """Record that a trigger has fired."""
    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE triggers SET last_fired_at = ?, last_fired_at_epoch = ?, "
        "fire_count = fire_count + 1, status = 'idle', waiting_since_epoch = NULL "
        "WHERE id = ?",
        (now.isoformat(), int(now.timestamp()), trigger_id),
    )
    if disable_if_impulse:
        await db.execute(
            "UPDATE triggers SET enabled = 0 WHERE id = ? AND trigger_type = 'impulse'",
            (trigger_id,),
        )
    await db.commit()
