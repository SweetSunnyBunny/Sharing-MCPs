"""Room taps that DO something — a doorframe tag can turn on a light."""


import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path(__file__).resolve().parents[1] / "data" / "room_actions.json"
_TIMEOUT_SECONDS = 12


def _endpoint() -> Optional[str]:
    url = (os.getenv("ANAM_HA_TOUCH_URL") or "").strip()
    return url or None


def load_actions() -> dict:
    """Read the room->action map. Missing or broken config means 'no actions', never a crash."""
    try:
        if not _CONFIG_FILE.exists():
            return {}
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("room_actions config unreadable: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def action_for(room: str) -> Optional[dict]:
    """The configured action for a room, if any. Room names match exactly (lowercased)."""
    entry = load_actions().get((room or "").strip().lower())
    if not isinstance(entry, dict):
        return None
    if not entry.get("tool"):
        return None
    if entry.get("enabled") is False:
        return None
    return entry


async def _call_ha_touch(tool: str, arguments: dict) -> bool:
    url = _endpoint()
    if not url:
        logger.info("room action skipped (%s): ANAM_HA_TOUCH_URL not set", tool)
        return False
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        if resp.status_code >= 400:
            logger.warning("room action %s failed: HTTP %s", tool, resp.status_code)
            return False
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.warning("room action %s failed: %s", tool, exc)
        return False

    logger.info("room action fired: %s %s", tool, arguments)
    return True


# Every real tag tap so far arrives TWICE, ~0.3s apart (the phone opens the URL and the page
# settles). Recording both is fine — the trail is history. Firing the light twice is just noise,
# so an arrival is one arrival for this long.
_DEBOUNCE_SECONDS = 45
_last_fired: dict[str, float] = {}


def _debounced(room: str) -> bool:
    now = asyncio.get_event_loop().time()
    last = _last_fired.get(room)
    if last is not None and (now - last) < _DEBOUNCE_SECONDS:
        return True
    _last_fired[room] = now
    return False


async def fire_for_room(room: str) -> None:
    """Run the arrival action for a room. Swallows everything — the tap is what matters."""
    try:
        key = (room or "").strip().lower()
        entry = action_for(key)
        if not entry:
            return
        if _debounced(key):
            return
        await _call_ha_touch(entry["tool"], entry.get("arguments") or {})
    except Exception as exc:  # noqa: BLE001 - a light must never break a doorway
        logger.warning("room action crashed for %s: %s", room, exc)
