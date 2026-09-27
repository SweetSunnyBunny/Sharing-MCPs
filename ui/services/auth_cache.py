"""In-memory auth session cache shared across request handlers."""

# ANAM GUIDE: LOGIN TOKEN QUICK CACHE
# What: Remembers recently-checked login tokens for 5 minutes so every page load doesn't hit the database.
# Called by: api/auth.py (login/logout) and core/middleware.py (the gate that checks every request).
# Edit here when: You want logins to be re-checked more or less often (the 300-second TTL), or logout isn't taking effect fast enough.

import time
from datetime import datetime, timezone

_CACHE_TTL_SECONDS = 300
_MAX_CACHE_ENTRIES = 200
_auth_cache: dict[str, tuple[float, int]] = {}


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def get_cached_session_expiry(token: str) -> int | None:
    """Return session expiry epoch for a valid cached token, else None."""
    if not token:
        return None

    entry = _auth_cache.get(token)
    if not entry:
        return None

    cache_until_ts, session_expiry_epoch = entry
    if cache_until_ts <= time.time() or int(session_expiry_epoch) <= _now_epoch():
        _auth_cache.pop(token, None)
        return None

    return int(session_expiry_epoch)


def cache_session_token(token: str, session_expiry_epoch: int):
    """Cache a validated session token for a short TTL."""
    if not token:
        return

    expiry_epoch = int(session_expiry_epoch)
    if expiry_epoch <= _now_epoch():
        _auth_cache.pop(token, None)
        return

    now_ts = time.time()
    _auth_cache[token] = (now_ts + _CACHE_TTL_SECONDS, expiry_epoch)

    if len(_auth_cache) > _MAX_CACHE_ENTRIES:
        stale = [k for k, (cache_until, _) in _auth_cache.items() if cache_until <= now_ts]
        for key in stale:
            _auth_cache.pop(key, None)


def revoke_session_token(token: str):
    """Drop a token from the cache immediately (e.g., on logout)."""
    if token:
        _auth_cache.pop(token, None)
