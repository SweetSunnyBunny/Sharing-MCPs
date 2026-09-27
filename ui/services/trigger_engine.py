"""Trigger evaluation engine — checks conditions and fires matching triggers.

Runs as an APScheduler job every 30 seconds. Evaluates all enabled triggers
against current state, fires matching ones via autowake session machinery.
"""

# ANAM GUIDE: TRIGGER CONDITION CHECKER
# What: The "if this, then wake a boy" brain — every 30 seconds it checks each enabled
#       trigger's condition (time window, her presence, inactivity, calendar, etc.)
#       and fires the ones that match via an autowake session.
# Called by: the APScheduler loop in services/autowake.py; services/pulse_evaluator.py
#            also reuses its condition-checking.
# Edit here when: You want to add a NEW kind of condition (add an _eval_* function and
#                 its branch in evaluate_condition), or change how firing/cooldowns work.
#                 Creating/editing the triggers themselves lives in trigger_service.py.

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE

log = logging.getLogger(__name__)


# ── Condition evaluators ─────────────────────────────────────────────

async def evaluate_condition(condition: dict, db=None) -> bool:
    """Evaluate a single condition against current state. Returns True if matched."""
    ctype = condition.get("type")

    if ctype == "presence_state":
        return _eval_presence_state(condition)
    elif ctype == "presence_transition":
        return _eval_presence_transition(condition)
    elif ctype == "active_identity":
        return _eval_active_identity(condition)
    elif ctype == "time_window":
        return _eval_time_window(condition)
    elif ctype == "day_of_week":
        return _eval_day_of_week(condition)
    elif ctype == "inactivity":
        return _eval_inactivity(condition)
    elif ctype == "wellness_state":
        return _eval_wellness_state(condition)
    elif ctype == "routine_missing":
        return await _eval_routine_missing(condition, db)
    elif ctype == "pack_audio_stale":
        return _eval_pack_audio_stale(condition)
    elif ctype == "calendar_within":
        return await _eval_calendar_within(condition)
    elif ctype == "compound_and":
        results = await asyncio.gather(
            *[evaluate_condition(sub, db) for sub in condition.get("conditions", [])]
        )
        return all(results)
    elif ctype == "compound_or":
        results = await asyncio.gather(
            *[evaluate_condition(sub, db) for sub in condition.get("conditions", [])]
        )
        return any(results)
    else:
        log.warning("Unknown condition type: %s", ctype)
        return False


def _eval_day_of_week(condition: dict) -> bool:
    """True when today (local TIMEZONE) is one of condition["days"]."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config import TIMEZONE
    days = [str(d).lower()[:3] for d in condition.get("days", [])]
    # isoweekday (1=Mon..7=Sun) → fixed English names; %a would follow the
    # system locale and silently break on non-English Windows.
    _names = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
    today = _names[datetime.now(ZoneInfo(TIMEZONE)).isoweekday()]
    return today in days


def _eval_presence_state(condition: dict) -> bool:
    """Check if current presence matches the required state."""
    from services.connection_registry import get_presence_state
    required = condition.get("state", "").lower()
    current = get_presence_state()
    return current == required


def _eval_presence_transition(condition: dict) -> bool:
    """Check if a presence transition happened recently."""
    from services.connection_registry import get_recent_events

    from_state = condition.get("from", "").lower()
    to_state = condition.get("to", "").lower()
    max_age = condition.get("max_age", 120)  # check last 2 minutes by default

    events = get_recent_events(max_age=max_age)

    for event in events:
        if event.event_type == "user_arrived" and from_state == "offline" and to_state == "active":
            return True
        if event.event_type == "user_departed" and from_state == "active" and to_state == "offline":
            return True
        if event.event_type == "identity_switched":
            if from_state == event.data.get("from", "").lower() and to_state == event.data.get("to", "").lower():
                return True
        if event.event_type == "user_went_idle" and to_state == "idle":
            return True

    return False


def _eval_active_identity(condition: dict) -> bool:
    """Check which identity Owner is currently chatting with.

    Supports operator: 'eq' (default) / 'ne' — case-insensitive match.
    A None active_identity compares as the empty string (so 'ne Avery' is True
    when no one is selected, which is usually what a gate wants).
    """
    from services.connection_registry import get_active_identity

    current = (get_active_identity() or "").lower()
    required = condition.get("identity", "").lower()
    op = condition.get("operator", "eq")

    if op == "ne":
        return current != required
    return current == required


def _eval_time_window(condition: dict) -> bool:
    """Check if current time is within a window."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    current_minutes = now.hour * 60 + now.minute

    start_str = condition.get("start", "00:00")
    end_str = condition.get("end", "23:59")

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
    except (ValueError, TypeError):
        return False

    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes <= end_minutes
    else:
        # Wraps midnight (e.g., 22:00 - 06:00)
        return current_minutes >= start_minutes or current_minutes <= end_minutes


def _eval_inactivity(condition: dict) -> bool:
    """Check if Owner has been inactive for N minutes."""
    from services.connection_registry import _last_web_message_time

    required_minutes = condition.get("minutes", 120)

    if _last_web_message_time == 0:
        return True  # Never sent a message = inactive

    elapsed = time.monotonic() - _last_web_message_time
    return elapsed >= (required_minutes * 60)


def _eval_pack_audio_stale(condition: dict) -> bool:
    """True if no boy has queued a pack-audio voice note in N days.

    "The drawer" = pack-audio-queued voice notes (filenames begin with
    "queued-" in VOICE_DIR). Returns True when the newest such file is older
    than `days` days, or when no queued notes exist at all. Used to nudge
    whoever's active to consider leaving Owner a voice message.
    """
    from config import VOICE_DIR

    days = condition.get("days", 5)
    threshold_seconds = days * 86400

    try:
        candidates = list(VOICE_DIR.glob("queued-*.mp3"))
    except Exception as e:
        log.debug("pack_audio_stale: VOICE_DIR scan failed: %s", e)
        return False

    if not candidates:
        return True  # Never queued anything = definitively stale

    newest_mtime = max(p.stat().st_mtime for p in candidates)
    age_seconds = time.time() - newest_mtime
    return age_seconds >= threshold_seconds


def _parse_calendar_event_start(event: dict, tz: ZoneInfo) -> datetime | None:
    """A gcal_today_events event's start as a tz-aware datetime, or None.

    None for all-day events (no single "starts in N minutes" moment) and
    for anything unparseable — the caller skips those events rather than
    treating a parse failure as a match.
    """
    if not isinstance(event, dict) or event.get("all_day"):
        return None
    start_raw = event.get("start")
    if isinstance(start_raw, dict):
        raw = start_raw.get("dateTime")
        if not raw:
            return None  # {"date": ...}-only shape is an all-day event
    else:
        raw = start_raw
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except (ValueError, TypeError):
        return None


async def _eval_calendar_within(condition: dict) -> bool:
    """True when today's calendar has an event starting within N minutes
    from now (#23) — "your meeting starts in 15" watcher.

    Lazy enrichment: reads the house-snapshot poller's cached calendar
    fetch (#24) instead of making a live gcal call from inside the 30s
    trigger loop — a slow/down Google backend can never stall evaluation.
    No snapshot yet, unparseable data, or no matching event all evaluate
    False; a broken calendar must never falsely fire a watcher.
    """
    try:
        from services.house_snapshot import get_snapshot

        raw, _age = get_snapshot("calendar")
        if not raw:
            return False
        parsed = json.loads(raw)
        events = parsed.get("events") if isinstance(parsed, dict) else None
        if not events:
            return False

        minutes = condition.get("minutes", 15)
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)
        window_end = now + timedelta(minutes=minutes)

        for event in events:
            start = _parse_calendar_event_start(event, tz)
            if start is not None and now <= start <= window_end:
                return True
        return False
    except Exception as e:
        log.debug("calendar_within evaluation failed: %s", e)
        return False


def _eval_wellness_state(condition: dict) -> bool:
    """Check Owner's wellness metrics."""
    try:
        wellness = _load_today_wellness()
        if not wellness:
            return False

        metric = condition.get("metric", "")
        required_value = condition.get("value", "")
        operator = condition.get("operator", "eq")

        actual = wellness.get(metric)
        if actual is None:
            return False

        if operator == "eq":
            return str(actual).lower() == str(required_value).lower()
        elif operator == "in":
            values = [v.strip().lower() for v in str(required_value).split(",")]
            return str(actual).lower() in values
        elif operator == "gt":
            return float(actual) > float(required_value)
        elif operator == "lt":
            return float(actual) < float(required_value)
        else:
            return str(actual).lower() == str(required_value).lower()
    except Exception as e:
        log.debug("Wellness condition evaluation failed: %s", e)
        return False


def _load_today_wellness() -> dict | None:
    """Load today's wellness data from the JSONL file."""
    from config import RITUALS_DIR

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    wellness_file = RITUALS_DIR / "wellness.jsonl"

    if not wellness_file.exists():
        return None

    try:
        lines = wellness_file.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines):
            entry = json.loads(line.strip())
            if entry.get("date") == today:
                return entry
    except Exception:
        pass
    return None


async def _eval_routine_missing(condition: dict, db) -> bool:
    """Check if a specific routine hasn't fired today by a certain hour."""
    if db is None:
        return False

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    by_hour = condition.get("by_hour", 12)

    # Only relevant if we're past the deadline hour
    if now.hour < by_hour:
        return False

    schedule_name = condition.get("schedule_name", "")
    if not schedule_name:
        return False

    # Check autowake_log for today
    today_str = now.strftime("%Y-%m-%d")
    rows = await db.execute_fetchall(
        "SELECT 1 FROM autowake_log al "
        "JOIN autowake_schedule s ON al.schedule_id = s.id "
        "WHERE s.name = ? AND al.started_at LIKE ? AND al.status = 'completed' "
        "LIMIT 1",
        (schedule_name, f"{today_str}%"),
    )
    return len(rows) == 0  # True if routine hasn't fired


# ── Main evaluator loop ──────────────────────────────────────────────

# How long a matched-but-busy trigger stays queued before we let it go.
# Generous on purpose — autonomous sessions cap at ~15 min, so an hour of
# continuous busy means something else is wrong and the moment has passed.
_WAITING_EXPIRY_SECONDS = 60 * 60


async def run_trigger_evaluator():
    """Evaluate all enabled triggers — called every 30s by APScheduler."""
    from db.database import get_db, release_db
    from services.trigger_service import (
        list_triggers, mark_fired, set_waiting, clear_waiting,
    )
    from services.autowake import is_identity_busy

    db = await get_db()
    try:
        triggers = await list_triggers(db, enabled_only=True)
        if not triggers:
            return

        now_epoch = int(time.time())

        for trigger in triggers:
            try:
                # A previously matched trigger parked because its boy was busy:
                # fire it as soon as he frees, WITHOUT re-evaluating conditions —
                # the matched moment was real (presence events age out of the
                # eval window, so re-checking would silently drop the fire).
                if trigger.get("status") == "waiting":
                    waiting_since = trigger.get("waiting_since_epoch") or 0
                    if waiting_since and (now_epoch - waiting_since) > _WAITING_EXPIRY_SECONDS:
                        log.info(
                            "Trigger %s: queued fire expired — %s never freed up within %ds",
                            trigger["name"], trigger["identity"], _WAITING_EXPIRY_SECONDS,
                        )
                        await clear_waiting(db, trigger["id"])
                        continue
                    if is_identity_busy(trigger["identity"]):
                        continue  # still busy — stay queued
                    log.info(
                        "Trigger %s: %s freed up — firing queued match",
                        trigger["name"], trigger["identity"],
                    )
                    if await _fire_trigger(db, trigger):
                        await mark_fired(db, trigger["id"], disable_if_impulse=True)
                    # else: lost the lock race — stays waiting for the next tick
                    continue

                # Check cooldown
                if trigger["last_fired_at_epoch"]:
                    cooldown_secs = trigger["cooldown_minutes"] * 60
                    if (now_epoch - trigger["last_fired_at_epoch"]) < cooldown_secs:
                        continue

                # Parse and evaluate condition
                condition = trigger["condition_json"]
                if isinstance(condition, str):
                    condition = json.loads(condition)

                matched = await evaluate_condition(condition, db=db)
                if not matched:
                    continue

                # Trigger matched — fire it
                log.info(
                    "Trigger matched: %s (%s) for %s",
                    trigger["name"], trigger["trigger_type"], trigger["identity"],
                )

                if await _fire_trigger(db, trigger):
                    await mark_fired(db, trigger["id"], disable_if_impulse=True)
                else:
                    # Identity busy — queue the matched moment instead of
                    # silently losing it (impulses used to disable unfired,
                    # watchers used to start cooldown with no fire).
                    log.info(
                        "Trigger %s: %s is busy — queued as waiting",
                        trigger["name"], trigger["identity"],
                    )
                    await set_waiting(db, trigger["id"])

            except Exception as e:
                log.error("Error evaluating trigger %s: %s", trigger["name"], e)

    except Exception as e:
        log.error("Trigger evaluator error: %s", e)
    finally:
        await release_db(db)


async def _fire_trigger(db, trigger: dict) -> bool:
    """Execute a trigger's action.

    Returns True when the action was delivered (or consumed), False only when
    it could not run because the identity was busy — the caller queues those
    as 'waiting' instead of marking them fired.
    """
    action = trigger["action_type"]

    if action == "autowake":
        return await _fire_autowake(db, trigger)
    elif action == "notify":
        await _fire_notify(trigger)
        return True
    elif action == "broadcast":
        await _fire_broadcast(trigger)
        return True
    else:
        # Consume unknown actions so a bad row can't re-match every tick.
        log.warning("Unknown action_type: %s", action)
        return True


async def _fire_autowake(db, trigger: dict) -> bool:
    """Fire a trigger as an autowake session.

    Returns False when the identity is busy (lock not acquired) so the caller
    can queue the matched trigger instead of losing it. Returns True once the
    session was attempted — even if the stream errored, the moment is consumed
    (same semantics as before, when mark_fired ran unconditionally).
    """
    from services.autowake import (
        acquire_identity, release_identity,
        _clean_reply_tags,
        _get_or_create_daily_autowake_conversation,
        _spawn_voice_if_tagged,
        _stream_autonomous,
    )
    from services.session_lifecycle import build_orientation_context, SessionMode
    from services.connection_registry import is_anyone_connected, broadcast
    from services.notifications import send_notification

    identity = trigger["identity"]

    if not await acquire_identity(identity):
        log.info("Trigger %s skipped — %s is busy", trigger["name"], identity)
        return False

    try:
        owner_connected = is_anyone_connected()

        conv_id, _, _ = await _get_or_create_daily_autowake_conversation(db, identity)

        # Build the user message
        prompt = trigger.get("prompt") or f"Trigger fired: {trigger['name']}"
        user_msg = f"[Trigger: {trigger['name']}] {prompt}"

        # Save user message
        from services.session_manager import save_message
        await save_message(
            db, conv_id, "user", user_msg,
            metadata=json.dumps({
                "trigger": True,
                "trigger_id": trigger["id"],
                "trigger_name": trigger["name"],
                "trigger_type": trigger["trigger_type"],
            }),
        )

        # Build context
        context_block = await build_orientation_context(
            db, conv_id, identity,
            mode=SessionMode.AUTONOMOUS,
            session_type_name=f"trigger:{trigger['name']}",
            owner_connected=owner_connected,
        )

        # Stream response
        full_content = ""
        async for event in _stream_autonomous(
            db, identity, conv_id, user_msg, context_block,
            session_name=f"trigger:{trigger['name']}",
            owner_connected=owner_connected,
        ):
            if event.get("type") == "stream_end":
                full_content = event.get("full_content", "")

        # Save response — same tag handling as any autowake reply:
        # <face> applied, <react> stripped, <voice> becomes real audio.
        full_content = _clean_reply_tags(identity, full_content)
        if full_content:
            trigger_msg_id = await save_message(
                db, conv_id, "assistant", full_content,
                identity=identity,
                metadata=json.dumps({
                    "trigger": True,
                    "trigger_id": trigger["id"],
                    "trigger_name": trigger["name"],
                }),
            )
            _spawn_voice_if_tagged(identity, trigger_msg_id, full_content)


            if not owner_connected:
                snippet = full_content[:100] + "..." if len(full_content) > 100 else full_content
                await send_notification(
                    title=f"{identity} (trigger: {trigger['name']})",
                    body=snippet,
                )

            # Broadcast to connected clients
            if owner_connected:
                await broadcast({
                    "type": "trigger_fired",
                    "trigger_name": trigger["name"],
                    "identity": identity,
                    "content_preview": full_content[:200],
                })

    except Exception as e:
        log.error("Failed to fire autowake trigger %s: %s", trigger["name"], e)
    finally:
        release_identity(identity)
    return True


async def _fire_notify(trigger: dict) -> None:
    """Fire a trigger as a push notification only (no session)."""
    from services.notifications import send_notification
    await send_notification(
        title=f"{trigger['identity']}: {trigger['name']}",
        body=trigger.get("prompt") or "Trigger fired",
    )


async def _fire_broadcast(trigger: dict) -> None:
    """Fire a trigger as a WebSocket broadcast (no session)."""
    from services.connection_registry import broadcast
    await broadcast({
        "type": "trigger_fired",
        "trigger_name": trigger["name"],
        "identity": trigger["identity"],
        "prompt": trigger.get("prompt", ""),
    })
