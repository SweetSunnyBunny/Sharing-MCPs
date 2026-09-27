"""Failsafe inactivity escalation — graduated check-ins when Owner goes quiet.

Runs every 5 minutes. Tracks how long since Owner's last web activity and
escalates through three levels:
  Level 1 (gentle): Broadcast a soft check-in via WebSocket
  Level 2 (concerned): Send a push notification
  Level 3 (emergency): Push notification + autowake session

Respects quiet hours. Resets when Owner sends a message or reconnects.
"""


import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

log = logging.getLogger(__name__)

# Module state
_current_level: int = 0
_level_fired_at: dict[int, float] = {}  # level -> monotonic time when fired


@dataclass
class EscalationConfig:
    level1_minutes: int = 120    # 2 hours
    level2_minutes: int = 360    # 6 hours
    level3_minutes: int = 720    # 12 hours
    quiet_start_hour: int = 22   # generic quiet-hour example
    quiet_end_hour: int = 8
    work_start_hour: int = 9
    work_start_minute: int = 0
    work_end_hour: int = 17
    work_end_minute: int = 0
    work_days: str = ""  # no work schedule until configured
    care_identity: str = ""
    enabled: bool = False


_config = EscalationConfig()


async def load_config_from_db():
    """Load escalation settings from the DB settings table. Falls back to defaults."""
    global _config
    try:
        from db.database import get_db, release_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM settings WHERE key LIKE 'escalation_%'"
            )
            if rows:
                overrides = {k.replace("escalation_", ""): v for k, v in rows}
                _config = EscalationConfig(
                    level1_minutes=int(overrides.get("level1_minutes", 120)),
                    level2_minutes=int(overrides.get("level2_minutes", 360)),
                    level3_minutes=int(overrides.get("level3_minutes", 720)),
                    quiet_start_hour=int(overrides.get("quiet_start_hour", 22)),
                    quiet_end_hour=int(overrides.get("quiet_end_hour", 8)),
                    work_start_hour=int(overrides.get("work_start_hour", 9)),
                    work_start_minute=int(overrides.get("work_start_minute", 0)),
                    work_end_hour=int(overrides.get("work_end_hour", 17)),
                    work_end_minute=int(overrides.get("work_end_minute", 0)),
                    work_days=overrides.get("work_days", ""),
                    care_identity=overrides.get("care_identity", ""),
                    enabled=overrides.get("enabled", "false").lower() != "false",
                )
        finally:
            await release_db(db)
    except Exception:
        log.debug("Could not load escalation config from DB, using defaults")


def reset_escalation():
    """Reset escalation level. Call when Owner sends a message or reconnects."""
    global _current_level, _level_fired_at
    if _current_level > 0:
        log.info("Inactivity escalation reset (was level %d)", _current_level)
    _current_level = 0
    _level_fired_at.clear()


def _is_quiet_hours() -> bool:
    now = datetime.now(ZoneInfo(TIMEZONE))
    hour = now.hour
    now_minutes = hour * 60 + now.minute

    # Configured sleep quiet hours.
    start = _config.quiet_start_hour
    end = _config.quiet_end_hour
    if start <= end:
        in_sleep = start <= hour < end
    else:
        in_sleep = hour >= start or hour < end
    if in_sleep:
        return True

    # Work hours apply only to explicitly configured days.
    work_days = {int(d) for d in _config.work_days.split(",") if d.strip()}
    if now.weekday() in work_days:
        work_start = _config.work_start_hour * 60 + _config.work_start_minute
        work_end = _config.work_end_hour * 60 + _config.work_end_minute
        if work_start <= now_minutes < work_end:
            return True

    return False


def _get_inactivity_minutes() -> float:
    from services.connection_registry import (
        _last_web_message_time, get_last_disconnect_time, is_anyone_connected
    )
    # Use the most recent activity signal
    last_activity = _last_web_message_time
    disconnect = get_last_disconnect_time()
    if disconnect > last_activity:
        last_activity = disconnect

    if last_activity == 0:
        # No activity recorded this session — don't escalate on fresh start
        return 0

    return (time.monotonic() - last_activity) / 60.0


async def check_inactivity_escalation():
    """Main scheduler job — runs every 5 minutes."""
    global _current_level

    from services.connection_registry import is_anyone_connected, is_web_active


    if is_web_active():
        if _current_level > 0:
            reset_escalation()
        return

    if not _config.enabled:
        return

    if _is_quiet_hours():
        return

    inactivity = _get_inactivity_minutes()
    if inactivity == 0:
        return

    # Determine what level we should be at
    target_level = 0
    if inactivity >= _config.level3_minutes:
        target_level = 3
    elif inactivity >= _config.level2_minutes:
        target_level = 2
    elif inactivity >= _config.level1_minutes:
        target_level = 1

    if target_level <= _current_level:
        return

    # Escalate
    for level in range(_current_level + 1, target_level + 1):
        if level == 1:
            await _fire_level1()
        elif level == 2:
            await _fire_level2()
        elif level == 3:
            await _fire_level3()
        _level_fired_at[level] = time.monotonic()

    _current_level = target_level


async def _fire_level1():
    """Gentle: broadcast a soft check-in via WebSocket."""
    log.info("Inactivity escalation: Level 1 (gentle check-in)")
    from services.connection_registry import broadcast, is_anyone_connected
    if is_anyone_connected():
        await broadcast({
            "type": "inactivity_checkin",
            "level": 1,
            "identity": _config.care_identity,
            "message": "Just checking in — you've been quiet for a while. Everything okay?",
        })


async def _fire_level2():
    """Concerned: send a push notification."""
    log.info("Inactivity escalation: Level 2 (push notification)")
    try:
        from services.notifications import send_notification
        await send_notification(
            title=f"{_config.care_identity} is thinking of you",
            body="Haven't heard from you in a while. Hope you're okay. \U0001f49b",
            url="/",
        )
    except Exception:
        log.exception("Failed to send level 2 push notification")


async def _fire_level3():
    """Emergency: push notification + autowake session."""
    log.info("Inactivity escalation: Level 3 (push + autowake)")
    try:
        from services.notifications import send_notification
        await send_notification(
            title=f"{_config.care_identity} is worried",
            body="It's been a long time. Please check in when you can.",
            url="/",
        )
    except Exception:
        log.exception("Failed to send level 3 push notification")

    # Launch an autowake session so the care identity can reach out
    try:
        from services.autowake import run_lightweight_pulse_session
        await run_lightweight_pulse_session(
            identity=_config.care_identity,
            pulse_name="failsafe_inactivity",
            prompt=(
                "Owner hasn't been active for a very long time. "
                "This is an inactivity failsafe check-in. "
                "You're worried about her. Write her a genuine, caring message — "
                "not performative concern, just real warmth. "
                "Keep it short. If she has Telegram, reach out there too."
            ),
            session_type="failsafe",
            max_duration=5,
        )
    except Exception:
        log.exception("Failed to launch level 3 autowake session")
