# ANAM GUIDE: LOGIN GATE AND REQUEST FILTER
# What: The front door — every web request passes through here. It checks the session
#       cookie (or API key) before letting anyone in, redirects strangers to login,
#       allows the local network through, and turns off caching for static files.
# Called by: server.py registers it on the FastAPI app; it runs on every request.
# Edit here when: A page/API path should be public (no login), auth cookies behave
#                 oddly, or local-network access rules need to change.

import hmac
import logging
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi.responses import JSONResponse, RedirectResponse

import os

from config import (
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    AUTH_ENABLED,
    PUBLIC_BASE_URL,
    SESSION_MAX_AGE_DAYS,
    SESSION_RENEW_ENABLED,
    SESSION_RENEW_WINDOW_DAYS,
)

_ANAM_API_KEY = os.getenv("ANAM_API_KEY", "")
from db.database import get_db, release_db
from services.auth_cache import cache_session_token, get_cached_session_expiry
from services.session_auth import hash_session_token

log = logging.getLogger("anam.middleware")

_PUBLIC_BASE_HOST = (urlparse(PUBLIC_BASE_URL).hostname or "").lower() if PUBLIC_BASE_URL else ""

def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    host = host.lower()
    return host in {"localhost", "127.0.0.1", "::1"}

def _is_local_network_host(host: str) -> bool:
    if not host:
        return False
    normalized = host.strip().strip("[]").lower()
    if _is_loopback_host(normalized):
        return True
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized.endswith(".local")
    return addr.is_private or addr.is_loopback or addr.is_link_local

def _canonical_redirect_url(request) -> str | None:
    if not PUBLIC_BASE_URL or not _PUBLIC_BASE_HOST:
        return None

    current_host = (request.headers.get("host", request.url.netloc) or "").split(":", 1)[0].lower()
    if not current_host or current_host == _PUBLIC_BASE_HOST or _is_local_network_host(current_host):
        return None

    public = urlparse(PUBLIC_BASE_URL)
    path = request.url.path or "/"
    query = str(request.url.query)
    next_url = f"{public.scheme}://{public.netloc}{path}"
    if query:
        next_url = f"{next_url}?{query}"
    return next_url

async def auth_and_cache_middleware(request, call_next):
    """Auth gate (if enabled) + no-cache for static files."""
    path = request.url.path
    canonical_redirect = _canonical_redirect_url(request)
    if canonical_redirect:
        return RedirectResponse(canonical_redirect, status_code=307)

    session_token = None
    session_expiry_epoch = None
    token_hash = None
    authenticated = False

    # Auth check
    if AUTH_ENABLED:
        # Public paths
        public = (
            path.startswith("/auth/")
            or path.startswith("/static/css/main.css")
            or path.startswith("/static/fonts/")
            or path.startswith("/static/assets/icons/")
            or path.startswith("/api/images/file/")  # needed for direct API image URL fetches
            or path.startswith("/api/voice/file/")  # needed for Echo audio playback
            or path.startswith("/api/videos/file/")  # Sora renders linked on Discord
            or path.startswith("/api/room/tap/")  # NFC doorframe tags opened by the phone's
            # browser — no Bearer token exists in that context, and a login wall at her own
            # doorway would make the tag useless. Writes presence only; reads nothing back.
            or path == "/v1/chat/completions"  # ElevenLabs call-mode custom LLM;
            # api/call_llm.py enforces its own Bearer secret (ANAM_CALL_LLM_KEY)
            # and returns 503 if that secret is not configured.
            or path == "/api/wearable/reply"  # Phase-1 wrist replies from AnamCompanion (LAN;
            # no secret on the phone yet — Phase 2 replaces this with device registration)

            or path == "/sw.js"
            or path == "/static/manifest.json"
            or path == "/health"
        )
        if not public:
            # Bearer token auth for external integrations (Alexa, etc.).
            # Constant-time compare to avoid leaking length/prefix via timing.
            auth_header = request.headers.get("authorization", "")
            if _ANAM_API_KEY and hmac.compare_digest(
                auth_header.encode("utf-8"),
                f"Bearer {_ANAM_API_KEY}".encode("utf-8"),
            ):
                authenticated = True

            session_token = request.cookies.get("anam_session")

            if session_token:
                session_expiry_epoch = get_cached_session_expiry(session_token)
                if session_expiry_epoch:
                    # Cache hit - session is valid
                    authenticated = True
                else:
                    # Cache miss or stale - check DB
                    token_hash = hash_session_token(session_token)
                    db = await get_db()
                    try:
                        rows = await db.execute_fetchall(
                            "SELECT expires_at_epoch FROM sessions WHERE token_hash = ?",
                            (token_hash,),
                        )
                        if rows and rows[0][0]:
                            session_expiry_epoch = int(rows[0][0])
                            now_epoch = int(datetime.now(timezone.utc).timestamp())
                            if now_epoch <= session_expiry_epoch:
                                authenticated = True
                                cache_session_token(session_token, session_expiry_epoch)
                    finally:
                        await release_db(db)

            if not authenticated:
                if path.startswith("/api/") or path.startswith("/ws/"):
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Not authenticated"},
                    )
                return RedirectResponse("/auth/login")

    response = await call_next(request)

    # Sliding session renewal: extend active sessions near expiry.
    if (
        AUTH_ENABLED
        and authenticated
        and session_token
        and session_expiry_epoch
        and SESSION_RENEW_ENABLED
    ):
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        renew_window_secs = max(SESSION_RENEW_WINDOW_DAYS, 0) * 86400
        seconds_remaining = session_expiry_epoch - now_epoch
        if 0 < seconds_remaining <= renew_window_secs:
            token_hash = token_hash or hash_session_token(session_token)
            renewed_expires_epoch = now_epoch + (SESSION_MAX_AGE_DAYS * 86400)
            renewed_expires_iso = datetime.fromtimestamp(
                renewed_expires_epoch, tz=timezone.utc
            ).isoformat()
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE sessions SET expires_at = ?, expires_at_epoch = ? "
                    "WHERE token_hash = ?",
                    (renewed_expires_iso, renewed_expires_epoch, token_hash),
                )
                await db.commit()
                cache_session_token(session_token, renewed_expires_epoch)
                response.set_cookie(
                    "anam_session",
                    session_token,
                    domain=AUTH_COOKIE_DOMAIN,
                    httponly=True,
                    secure=AUTH_COOKIE_SECURE,
                    samesite=AUTH_COOKIE_SAMESITE,
                    max_age=SESSION_MAX_AGE_DAYS * 86400,
                )
            except Exception as e:
                log.exception("Failed to renew session expiry: %s", e)
            finally:
                await release_db(db)

    # Smart caching for static files:
    # Versioned assets (?v=N) can be cached aggressively - the version bump forces re-fetch.
    # Non-versioned static files get a short cache with revalidation.
    if path.startswith("/static/"):
        query = str(request.url.query)
        if "v=" in query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
    return response
