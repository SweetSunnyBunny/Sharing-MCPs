"""House snapshot poller — background pre-fetch for turn-blocking reads."""

# ANAM GUIDE: HOUSE SNAPSHOT CACHE
# What: quietly re-fetches weather, calendar, and Home Hub data every few minutes so chat turns read a fresh copy from memory instead of waiting on the internet.
# Called by: services/autowake.py registers the polling job; services/context_hooks.py, services/identity_context.py, and services/trigger_engine.py read the cached snapshots.
# Edit here when: you want to change how often house data refreshes (POLL_INTERVAL_MINUTES) or add a new kind of data to pre-fetch (write a new _poll_* function).

import asyncio
import logging
import time as _time
from typing import Any

log = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = 5

# key -> {"data": Any, "fetched_at": float (monotonic)}
_SNAPSHOTS: dict[str, dict] = {}


def get_snapshot(key: str) -> tuple[Any, float | None]:
    """Return (data, age_seconds) for a snapshot key.

    (None, None) when nothing has ever been fetched successfully — callers
    should fall back to their own live path in that case.
    """
    entry = _SNAPSHOTS.get(key)
    if not entry or entry.get("data") is None:
        return None, None
    return entry["data"], max(_time.monotonic() - entry["fetched_at"], 0.0)


def _store(key: str, data: Any) -> None:
    # Last-good carry-forward: a failed/empty fetch must never clobber a
    # prior good value. Only a real payload replaces the snapshot.
    if data is None:
        return
    _SNAPSHOTS[key] = {"data": data, "fetched_at": _time.monotonic()}


async def _poll_weather() -> None:
    try:
        from services.mcp_bridge import mcp_bridge

        txt = await mcp_bridge.call_tool("wt_weather_home", {}, timeout=8)
        if isinstance(txt, str) and txt.strip() and not txt.lower().startswith("error"):
            _store("weather", txt)
    except Exception as exc:
        log.debug("House snapshot: weather poll failed: %s", exc)


async def _poll_calendar() -> None:
    try:
        from services.mcp_bridge import mcp_bridge

        raw = await mcp_bridge.call_tool("gcal_today_events", {"identity": "owner"}, timeout=10)
        if isinstance(raw, str) and not raw.startswith("Error"):
            _store("calendar", raw)
    except Exception as exc:
        log.debug("House snapshot: calendar poll failed: %s", exc)


# snapshot key -> (base-url env, path) for the Home Hub's remote endpoints.
_HUB_ENDPOINTS = {
    "hub_status": ("hub", "/api/hub/status"),
    "hub_meds": ("hub", "/api/hub/meds"),
    "hub_rituals": ("rituals", "/api/rituals/list"),
    "hub_win": ("hub", "/api/hub/todays-win"),
    "hub_tasks": ("hub", "/api/hub/tasks"),
    "hub_countdowns": ("hub", "/api/hub/countdowns"),
}


async def _poll_hub() -> None:
    """Pre-fetch the Home Hub's remote endpoints as raw JSON payloads."""
    from config import HUB_API_BASE, RITUALS_API_BASE
    from services.remote_state import safe_request_json

    bases = {"hub": HUB_API_BASE or None, "rituals": RITUALS_API_BASE or HUB_API_BASE or None}

    for key, (base_name, path) in _HUB_ENDPOINTS.items():
        base = bases.get(base_name)
        if not base:
            continue
        try:
            payload = await asyncio.to_thread(safe_request_json, base, path, 5.0)
            if payload is not None:
                _store(key, payload)
        except Exception as exc:
            log.debug("House snapshot: %s poll failed: %s", key, exc)


async def poll_house_snapshot() -> None:
    """APScheduler entry point — refresh every tracked snapshot in parallel."""
    try:
        await asyncio.gather(_poll_weather(), _poll_calendar(), _poll_hub())
    except Exception as exc:
        log.error("House snapshot poll error: %s", exc)
