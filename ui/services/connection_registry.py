"""Tracks active WebSocket connections, presence state, and transition events."""


import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)

# Global set of connected WebSocket clients + lock for safe mutation
_connections: set[WebSocket] = set()
_lock = asyncio.Lock()

# Track per-connection focus so presence doesn't get stuck on a stale identity.
_connection_identities: dict[int, str | None] = {}
_connection_last_active: dict[int, float] = {}

# Fallback for non-WebSocket callers.
_active_identity: str | None = None

# Track active conversation per identity (so echo relay can find the right one)
_active_conversations: dict[str, str] = {}  # identity -> conversation_id


_owner_arrived_at: float = 0  # monotonic timestamp


_last_disconnect_time: float = 0


_last_web_message_time: float = 0
_WEB_ACTIVE_WINDOW = 120  # seconds — consider "actively chatting" within this window

# Device / client type per WebSocket (classified from User-Agent at handshake)
_connection_device: dict[int, str] = {}

# Per-connection send lock. Both broadcast() and an active chat stream's bridge
# can write to the same WebSocket concurrently; without serialization those
# interleaved send_json() calls can corrupt or drop frames. Every outbound send
# goes through send_json_locked() so each socket is written one frame at a time.
_ws_send_locks: dict[int, asyncio.Lock] = {}


def _get_ws_lock(ws: WebSocket) -> asyncio.Lock:
    lock = _ws_send_locks.get(id(ws))
    if lock is None:
        lock = asyncio.Lock()
        _ws_send_locks[id(ws)] = lock
    return lock


async def send_json_locked(ws: WebSocket, message: dict) -> bool:
    """Send one JSON frame to a single WebSocket under its per-connection lock.

    Returns True on success, False if the send raised (caller decides whether
    to mark the socket dead). Serializes against any other sender on the same
    socket — direct stream writes and broadcasts can no longer interleave.
    """
    try:
        async with _get_ws_lock(ws):
            await ws.send_json(message)
        return True
    except Exception:
        return False


def _classify_user_agent(ua: str) -> str:
    """Return 'mobile' | 'tablet' | 'desktop' from a User-Agent string."""
    if not ua:
        return "desktop"
    ua_l = ua.lower()
    if "ipad" in ua_l or ("android" in ua_l and "mobile" not in ua_l):
        return "tablet"
    if any(k in ua_l for k in ("iphone", "android", "mobile", "windows phone")):
        return "mobile"
    return "desktop"


def set_connection_device(ws: WebSocket, ua: str) -> None:
    """Record the device type for a WebSocket at handshake time."""
    _connection_device[id(ws)] = _classify_user_agent(ua)


# ── Presence Transition Events ───────────────────────────────────────

_IDLE_THRESHOLD = 300  # 5 minutes without web activity = idle
_idle_emitted: bool = False  # prevent repeat idle emissions


@dataclass
class PresenceEvent:
    """A discrete presence state transition."""
    event_type: str  # user_arrived, user_departed, identity_switched, user_went_idle
    timestamp: float  # monotonic
    wall_time: float = field(default_factory=time.time)  # epoch for display
    data: dict = field(default_factory=dict)

    def age(self) -> float:
        """Seconds since this event occurred."""
        return time.monotonic() - self.timestamp


# Bounded event history — triggers check this for condition matching
_events: deque[PresenceEvent] = deque(maxlen=100)


def emit_event(event_type: str, data: dict | None = None) -> PresenceEvent:
    """Record a presence event and schedule a broadcast."""
    event = PresenceEvent(
        event_type=event_type,
        timestamp=time.monotonic(),
        data=data or {},
    )
    _events.append(event)
    log.info("Presence event: %s %s", event_type, data or "")

    # Best-effort broadcast to connected clients (non-blocking)
    try:
        from services.task_manager import spawn
        spawn(_broadcast_presence_event(event), name=f"presence-{event_type}")
    except RuntimeError:
        pass  # No event loop running (e.g., during tests)

    return event


async def _broadcast_presence_event(event: PresenceEvent) -> None:
    """Broadcast presence event to all connected WebSocket clients."""
    await broadcast({
        "type": "presence_event",
        "event_type": event.event_type,
        "data": event.data,
        "timestamp": event.wall_time,
    })


def get_recent_events(
    since: float | None = None,
    event_type: str | None = None,
    max_age: float = 300,
) -> list[PresenceEvent]:
    """Get recent presence events, optionally filtered.

    Args:
        since: monotonic timestamp — only events after this time
        event_type: filter to this event type
        max_age: max age in seconds (default 5 min)
    """
    cutoff = since or (time.monotonic() - max_age)
    results = []
    for event in _events:
        if event.timestamp < cutoff:
            continue
        if event_type and event.event_type != event_type:
            continue
        results.append(event)
    return results


def get_presence_state() -> str:
    """Get current presence state as a simple string.

    Returns: 'active', 'idle', 'connected', or 'offline'
    """
    if not is_anyone_connected():
        return "offline"
    if is_web_active():
        return "active"
    # Connected but not recently active
    if _last_web_message_time > 0 and (time.monotonic() - _last_web_message_time) > _IDLE_THRESHOLD:
        return "idle"
    return "connected"


async def check_idle() -> None:
    """Check if Owner has gone idle — called periodically by scheduler.

    Emits user_went_idle event once when idle threshold is crossed.
    """
    global _idle_emitted
    if not is_anyone_connected():
        _idle_emitted = False
        return
    if _last_web_message_time == 0:
        return
    if (time.monotonic() - _last_web_message_time) > _IDLE_THRESHOLD:
        if not _idle_emitted:
            _idle_emitted = True
            emit_event("user_went_idle", {
                "idle_seconds": time.monotonic() - _last_web_message_time,
            })
    else:
        _idle_emitted = False


async def register(ws: WebSocket) -> bool:
    """Register a WebSocket. Returns True if this is a fresh arrival (0->1)."""
    global _owner_arrived_at, _idle_emitted
    async with _lock:
        was_empty = len(_connections) == 0
        _connections.add(ws)
        _connection_identities[id(ws)] = None
        _connection_last_active[id(ws)] = time.monotonic()
    if was_empty:
        _owner_arrived_at = time.monotonic()
        _idle_emitted = False
        log.info("Owner arrived (WebSocket 0->1)")
        emit_event("user_arrived")
        from services.inactivity_escalation import reset_escalation
        reset_escalation()
    log.info("WebSocket registered (total: %d)", len(_connections))
    return was_empty


async def unregister(ws: WebSocket):
    global _last_disconnect_time
    ws_id = id(ws)
    async with _lock:
        _connections.discard(ws)
        _connection_identities.pop(ws_id, None)
        _connection_last_active.pop(ws_id, None)
        _connection_device.pop(ws_id, None)
        _ws_send_locks.pop(ws_id, None)
    if not _connections:
        _last_disconnect_time = time.monotonic()
        await set_active_identity(None)
        emit_event("user_departed")
    log.info("WebSocket unregistered (total: %d)", len(_connections))


def is_anyone_connected() -> bool:
    return len(_connections) > 0


def get_connections() -> set[WebSocket]:
    return set(_connections)


async def set_active_identity(identity: str | None, ws: WebSocket | None = None):
    """Update which identity Owner is currently chatting with."""
    global _active_identity, _idle_emitted
    async with _lock:
        previous = _active_identity
        _active_identity = identity
        # Only real registered browser sockets participate in per-connection
        # focus. Transport adapters (Speech Engine, tests, HTTP shims) may be
        # WebSocket-shaped but must not leave phantom presence entries behind.
        if ws is not None and ws in _connections:
            ws_id = id(ws)
            _connection_identities[ws_id] = identity
            _connection_last_active[ws_id] = time.monotonic()
    # Emit identity_switched event when identity actually changes
    if identity and previous and identity != previous:
        _idle_emitted = False  # reset idle on switch
        emit_event("identity_switched", {"from": previous, "to": identity})


def set_active_conversation(identity: str, conversation_id: str):
    """Track which conversation an identity is currently using."""
    _active_conversations[identity] = conversation_id


def get_active_conversation(identity: str | None = None) -> str | None:
    """Get the active conversation for an identity (or the current active identity)."""
    ident = identity or get_active_identity()
    if ident:
        return _active_conversations.get(ident)
    return None


def get_active_identity() -> str | None:
    """Return the identity Owner is currently chatting with, or None."""
    if _connection_last_active:
        latest_ws_id = max(
            _connection_last_active,
            key=_connection_last_active.__getitem__,
        )
        latest_identity = _connection_identities.get(latest_ws_id)
        if latest_identity:
            return latest_identity
    return _active_identity


def get_last_disconnect_time() -> float:
    """Return monotonic timestamp of Owner's most recent disconnect, or 0."""
    return _last_disconnect_time


def record_web_activity():
    """Mark that Owner just sent a message via the web UI."""
    global _last_web_message_time, _idle_emitted
    _last_web_message_time = time.monotonic()
    _idle_emitted = False  # reset idle on activity
    from services.inactivity_escalation import reset_escalation
    reset_escalation()


def is_web_active() -> bool:
    """True if Owner sent a web message within the activity window."""
    if not is_anyone_connected():
        return False
    if _last_web_message_time == 0:
        return False
    return (time.monotonic() - _last_web_message_time) < _WEB_ACTIVE_WINDOW


async def broadcast(message: dict):
    """Send a JSON message to all connected clients."""
    global _last_disconnect_time, _active_identity
    # Snapshot to avoid iteration-while-mutating
    async with _lock:
        snapshot = list(_connections)

    dead = []
    for ws in snapshot:
        ok = await send_json_locked(ws, message)
        if not ok:
            log.debug("WebSocket send failed during broadcast, marking dead")
            dead.append(ws)

    if dead:
        async with _lock:
            for ws in dead:
                _connections.discard(ws)
                ws_id = id(ws)
                _connection_identities.pop(ws_id, None)
                _connection_last_active.pop(ws_id, None)
                _ws_send_locks.pop(ws_id, None)
            if not _connections:
                _last_disconnect_time = time.monotonic()
                # _lock is already held; call internal mutation directly
                _active_identity = None
