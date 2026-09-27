"""Small observable heartbeat for APScheduler."""

# ANAM GUIDE: SCHEDULER HEARTBEAT MONITOR
# What: Watches the background job scheduler (the thing that fires autowakes and timers) and keeps a record of its last success/error, so diagnostics can tell if the clock stopped ticking.
# Called by: server.py (installs it at startup), api/autowake.py (health readout), services/autowake.py
# Edit here when: You want to change what gets tracked about scheduler health or how "the scheduler looks dead" is decided.

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED


_lock = threading.Lock()
_installed_on: set[int] = set()
_state: dict[str, Any] = {
    "last_event_at": None,
    "last_success_at": None,
    "last_error_at": None,
    "last_job_id": None,
    "last_error": None,
    "last_heartbeat_at": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scheduler_heartbeat() -> None:
    """A cheap scheduled job whose successful execution proves the loop ticks."""
    return None


def _listener(event) -> None:
    now = _now()
    with _lock:
        _state["last_event_at"] = now
        _state["last_job_id"] = getattr(event, "job_id", None)
        if getattr(event, "exception", None) or event.code in {EVENT_JOB_ERROR, EVENT_JOB_MISSED}:
            _state["last_error_at"] = now
            _state["last_error"] = str(getattr(event, "exception", None) or "job missed")[:500]
        else:
            _state["last_success_at"] = now
            if getattr(event, "job_id", None) == "scheduler_heartbeat":
                _state["last_heartbeat_at"] = now


def install_scheduler_health(scheduler) -> None:
    key = id(scheduler)
    if key in _installed_on:
        return
    scheduler.add_listener(_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    _installed_on.add(key)


def get_scheduler_health() -> dict[str, Any]:
    with _lock:
        snapshot = dict(_state)
    heartbeat = snapshot.get("last_heartbeat_at")
    if heartbeat:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)).total_seconds()
            snapshot["heartbeat_age_seconds"] = round(max(0.0, age), 1)
            snapshot["heartbeat_status"] = "ok" if age <= 90 else "stale"
        except (TypeError, ValueError):
            snapshot["heartbeat_status"] = "unknown"
    else:
        snapshot["heartbeat_status"] = "warming"
    return snapshot
