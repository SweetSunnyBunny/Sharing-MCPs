"""Per-identity busy lock: one autonomous session per boy at a time."""


import asyncio
import logging
import time

from config import IDENTITIES
from services import connection_registry

log = logging.getLogger(__name__)

# Per-identity async lock — prevents concurrent sessions for the same identity.
# Using locks instead of a plain dict eliminates TOCTOU race conditions.
_identity_locks: dict[str, asyncio.Lock] = {
    name: asyncio.Lock() for name in IDENTITIES
}
# Track which identities are busy (for status reporting — the lock is the real guard)
_active_sessions: dict[str, bool] = {}
# Track when each identity lock was acquired — used to detect stale locks
_lock_acquired_at: dict[str, float] = {}
_STALE_LOCK_SECONDS = 600  # 10 minutes — no autonomous session should run longer


def is_identity_busy(identity: str) -> bool:
    """Check if identity is currently in a session (non-blocking check).

    Includes stale-lock detection for logging/status purposes.
    We intentionally avoid force-releasing the lock here because this
    code path does not own the session lifecycle and releasing blindly
    can allow overlapping autonomous runs for the same identity.
    """
    lock = _identity_locks.get(identity)
    if lock and lock.locked():
        acquired = _lock_acquired_at.get(identity, 0)
        if acquired and (time.monotonic() - acquired) > _STALE_LOCK_SECONDS:
            log.warning(
                "Detected stale autowake lock for %s (held %.0fs); keeping lock in place to avoid overlap",
                identity, time.monotonic() - acquired,
            )
        return True
    if _active_sessions.get(identity, False):
        return True

    try:
        if connection_registry.is_web_active():
            live = connection_registry.get_active_identity()
            if live and live.lower() == identity.lower():
                return True
    except Exception:
        pass  # never let presence bookkeeping take down the scheduler
    return False


async def acquire_identity(identity: str) -> bool:
    """Try to acquire the identity lock (non-blocking). Returns True if acquired."""
    lock = _identity_locks.get(identity)
    if not lock:
        return False
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.01)
        _active_sessions[identity] = True
        _lock_acquired_at[identity] = time.monotonic()
        return True
    except asyncio.TimeoutError:
        return False


def release_identity(identity: str):
    """Release the identity lock."""
    lock = _identity_locks.get(identity)
    if lock and lock.locked():
        _active_sessions[identity] = False
        _lock_acquired_at.pop(identity, None)
        lock.release()
