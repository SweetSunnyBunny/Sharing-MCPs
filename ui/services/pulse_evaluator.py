"""Pulse evaluator — fires due pulses on their configured intervals.

Runs every 60 seconds via APScheduler. For each due pulse:
1. Optionally evaluates a condition (reuses trigger_engine conditions)
2. Fires the configured action (notify, broadcast, or autowake)
3. Marks the pulse as fired
"""

# ANAM GUIDE: PULSE FIRING ENGINE
# What: Every minute, checks which pulses (short recurring check-ins) are due and fires their action — a notification, a broadcast, or a mini autowake.
# Called by: the scheduler set up in services/autowake.py (runs every 60 seconds).
# Edit here when: changing what happens when a pulse fires or how its condition is checked. (Pulse definitions themselves live in services/pulse_service.py.)

import logging

from db.database import get_db, release_db
from services.pulse_service import get_due_pulses, mark_pulse_fired

log = logging.getLogger(__name__)


async def run_pulse_evaluator():
    """Check all enabled pulses and fire those whose interval has elapsed."""
    db = await get_db()
    try:
        due = await get_due_pulses(db)
    finally:
        await release_db(db)

    if not due:
        return

    for pulse in due:
        try:
            await _evaluate_and_fire(pulse)
        except Exception as e:
            log.warning("Pulse %s (%d) failed: %s", pulse["name"], pulse["id"], e)


async def _evaluate_and_fire(pulse: dict):
    """Evaluate condition (if any) and fire the pulse action."""
    # If the pulse has a condition, evaluate it
    condition = pulse.get("condition")
    if condition:
        try:
            from services.trigger_engine import evaluate_condition
            if not await evaluate_condition(condition):
                return  # Condition not met, skip
        except ImportError:
            log.debug("trigger_engine not available for pulse condition evaluation")
        except Exception as e:
            log.debug("Pulse %s condition eval failed: %s", pulse["name"], e)
            return

    action_type = pulse.get("action_type", "notify")
    identity = pulse.get("identity")
    prompt = pulse.get("prompt", "")

    log.info("Firing pulse: %s (action=%s, identity=%s)", pulse["name"], action_type, identity)

    if action_type == "notify":
        # Send a push notification
        from services.notifications import send_notification
        await send_notification(
            title=f"Pulse: {pulse['name']}",
            body=prompt[:120] if prompt else pulse["name"],
            url="/",
        )

    elif action_type == "broadcast":
        # Send a WebSocket event to connected clients
        from services.connection_registry import broadcast
        await broadcast({
            "type": "pulse_event",
            "pulse_id": pulse["id"],
            "name": pulse["name"],
            "identity": identity,
            "prompt": prompt,
        })

    elif action_type == "autowake":
        # Fire a lightweight autonomous session
        from services.autowake import run_lightweight_pulse_session
        await run_lightweight_pulse_session(
            identity=identity,
            pulse_name=pulse["name"],
            prompt=prompt,
            session_type=pulse.get("session_type", "custom"),
            max_duration=pulse.get("max_duration_minutes", 5),
        )

    # Mark as fired
    db = await get_db()
    try:
        await mark_pulse_fired(db, pulse["id"])
    finally:
        await release_db(db)
