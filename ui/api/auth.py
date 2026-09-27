"""REST: Discord OAuth2 authentication."""

# ANAM GUIDE: DISCORD LOGIN AND SESSIONS
# What: The whole login flow — /auth/login page, Discord OAuth redirect + callback, session cookie creation, logout, and /auth/status.
# Called by: The browser directly (every page redirects here when not logged in); core/middleware.py checks the session cookie this file sets.
# Edit here when: Changing who is allowed in (ALLOWED_DISCORD_IDS lives in config.py), the login/denied page look, or how long sessions last.

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import (
    DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, ALLOWED_DISCORD_IDS,
    SESSION_MAX_AGE_DAYS, SITE_URL, PUBLIC_BASE_URL,
    AUTH_COOKIE_SECURE, AUTH_COOKIE_SAMESITE, AUTH_COOKIE_DOMAIN,
)
from db.database import get_db, release_db
from services.auth_cache import cache_session_token, revoke_session_token
from services.rate_limit import limiter
from services.session_auth import hash_session_token
from services.time_utils import utc_now_iso_epoch

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

# Discord OAuth2 endpoints
DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"


def _get_callback_url(request: Request) -> str:
    """Build callback URL from config.

    The earlier host-header fallback was removed: deriving redirect_uri
    from client-controlled Host / X-Forwarded-Proto headers is a latent
    open-redirect / OAuth-confused-deputy risk. Refuse to operate without
    a configured trusted base URL.
    """
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}/auth/callback"
    if SITE_URL:
        return f"{SITE_URL.rstrip('/')}/auth/callback"
    raise RuntimeError(
        "OAuth callback URL cannot be derived: set PUBLIC_BASE_URL "
        "(ANAM_PUBLIC_URL) or SITE_URL."
    )


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anam — Login</title>
    <link rel="stylesheet" href="/static/css/main.css">
    <style>
        .login-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
            text-align: center;
        }
        .login-title {
            font-family: var(--font-display, 'Georgia', serif);
            font-size: 3rem;
            color: var(--text-primary, #4A3728);
            margin-bottom: 8px;
        }
        .login-subtitle {
            font-size: 1rem;
            color: var(--text-muted, #8B7D72);
            margin-bottom: 40px;
        }
        .discord-btn {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            background: #5865F2;
            color: white;
            border: none;
            border-radius: 12px;
            padding: 14px 28px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s ease;
            font-family: var(--font-body, 'Quicksand', sans-serif);
        }
        .discord-btn:hover {
            background: #4752C4;
            transform: translateY(-2px);
            box-shadow: 0 4px 20px rgba(88, 101, 242, 0.4);
        }
        .discord-icon {
            width: 24px;
            height: 24px;
        }
        .denied-msg {
            color: #c44;
            font-size: 1.1rem;
            margin-bottom: 20px;
        }
        .denied-back {
            color: var(--text-muted, #8B7D72);
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1 class="login-title">Anam</h1>
        <p class="login-subtitle">Soul of Heaven</p>
        <a href="/auth/discord" class="discord-btn">
            <svg class="discord-icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
            </svg>
            Login with Discord
        </a>
    </div>
</body>
</html>"""

DENIED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anam — Access Denied</title>
    <link rel="stylesheet" href="/static/css/main.css">
    <style>
        .login-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
            text-align: center;
        }
        .login-title {
            font-family: var(--font-display, 'Georgia', serif);
            font-size: 3rem;
            color: var(--text-primary, #4A3728);
            margin-bottom: 8px;
        }
        .denied-msg {
            color: #c44;
            font-size: 1.1rem;
            margin-bottom: 20px;
        }
        .denied-back {
            color: var(--text-muted, #8B7D72);
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1 class="login-title">Anam</h1>
        <p class="denied-msg">This isn't your home, love.</p>
        <a href="/auth/login" class="denied-back">Try again</a>
    </div>
</body>
</html>"""


@router.get("/login")
async def login_page():
    """Serve the login page."""
    return HTMLResponse(LOGIN_HTML)


@router.get("/discord")
@limiter.limit("10/minute")
async def discord_redirect(request: Request):
    """Redirect to Discord OAuth2 authorization."""
    callback_url = _get_callback_url(request)
    oauth_state = secrets.token_urlsafe(24)
    params = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "identify",
        "state": oauth_state,
    })
    response = RedirectResponse(f"{DISCORD_AUTH_URL}?{params}")
    response.set_cookie(
        "anam_oauth_state",
        oauth_state,
        domain=AUTH_COOKIE_DOMAIN,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=600,  # 10 minutes
    )
    return response


@router.get("/callback")
@limiter.limit("10/minute")
async def discord_callback(request: Request, code: str = "", state: str = ""):
    """Handle Discord OAuth2 callback."""
    oauth_state = request.cookies.get("anam_oauth_state", "")
    if not code or not state or state != oauth_state:
        log.warning("OAuth callback rejected: missing/invalid state")
        response = RedirectResponse("/auth/login")
        response.delete_cookie("anam_oauth_state")
        return response

    callback_url = _get_callback_url(request)

    try:
        # Exchange code for access token
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(DISCORD_TOKEN_URL, data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url,
            })
            if token_resp.status_code != 200:
                # Don't log response body — Discord may echo back identifiers,
                # and it's a regression-prone pattern even when not currently
                # leaking secrets. Status alone is sufficient for triage.
                log.warning("Discord token exchange failed: status=%s", token_resp.status_code)
                return RedirectResponse("/auth/login")
            token_data = token_resp.json()

            # Fetch user info
            user_resp = await client.get(DISCORD_USER_URL, headers={
                "Authorization": f"Bearer {token_data['access_token']}",
            })
            if user_resp.status_code != 200:
                log.warning("Discord user fetch failed: status=%s", user_resp.status_code)
                return RedirectResponse("/auth/login")
            user = user_resp.json()

    except Exception as e:
        log.exception("Discord OAuth error: %s", e)
        return RedirectResponse("/auth/login")

    discord_id = user.get("id", "")
    discord_username = user.get("username", "")
    discord_avatar = user.get("avatar", "")

    # Check if this Discord user is allowed
    if discord_id not in ALLOWED_DISCORD_IDS:
        log.warning("Access denied for Discord user: %s (%s)", discord_username, discord_id)
        return HTMLResponse(DENIED_HTML, status_code=403)

    # Create session
    session_token = secrets.token_urlsafe(32)
    session_token_hash = hash_session_token(session_token)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_MAX_AGE_DAYS)
    now_iso, now_epoch = utc_now_iso_epoch()
    expires_iso = expires.isoformat()
    expires_epoch = int(expires.timestamp())

    db = await get_db()
    try:
        # Clean up expired sessions
        await db.execute(
            "DELETE FROM sessions WHERE expires_at_epoch < ?", (now_epoch,)
        )
        # Insert new session
        await db.execute(
            "INSERT INTO sessions (token_hash, discord_id, discord_username, discord_avatar, "
            "created_at, created_at_epoch, expires_at, expires_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_token_hash,
                discord_id,
                discord_username,
                discord_avatar,
                now_iso,
                now_epoch,
                expires_iso,
                expires_epoch,
            ),
        )
        await db.commit()
    finally:
        await release_db(db)

    log.info("Login successful: %s (%s)", discord_username, discord_id)
    cache_session_token(session_token, expires_epoch)

    response = RedirectResponse("/")
    response.delete_cookie("anam_oauth_state", domain=AUTH_COOKIE_DOMAIN)
    response.set_cookie(
        "anam_session", session_token,
        domain=AUTH_COOKIE_DOMAIN,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=SESSION_MAX_AGE_DAYS * 86400,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Delete session and redirect to login."""
    session_token = request.cookies.get("anam_session")
    if session_token:
        token_hash = hash_session_token(session_token)
        db = await get_db()
        try:
            await db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            await db.commit()
        finally:
            await release_db(db)
        revoke_session_token(session_token)

    response = RedirectResponse("/auth/login")
    response.delete_cookie("anam_session")
    return response


@router.get("/status")
async def auth_status(request: Request):
    """Check current auth status."""
    session_token = request.cookies.get("anam_session")
    if not session_token:
        return JSONResponse(content={"authenticated": False})
    token_hash = hash_session_token(session_token)

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT discord_username, expires_at_epoch FROM sessions WHERE token_hash = ?",
            (token_hash,),
        )
    finally:
        await release_db(db)

    if not rows:
        return JSONResponse(content={"authenticated": False})

    expires_epoch = rows[0][1]
    if not expires_epoch:
        return JSONResponse(content={"authenticated": False})
    if int(datetime.now(timezone.utc).timestamp()) > int(expires_epoch):
        return JSONResponse(content={"authenticated": False})

    return JSONResponse(content={
        "authenticated": True,
        "username": rows[0][0],
    })
