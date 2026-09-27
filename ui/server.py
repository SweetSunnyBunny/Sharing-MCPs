"""Anam - FastAPI entry point + static serving."""

import hashlib
import json
import logging
import re
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import (
    CDN_BASE_URL,
    SANCTUARY_API_BASE,
    HEALTH_REQUIRE_GOOGLE_OAUTH,
    HEALTH_REQUIRE_MCP_CRITICAL,
    HOST,
    PORT,
    PUBLIC_BASE_URL,
    SITE_URL,
    STATIC_DIR,
    USE_DIRECT_API,
)
from db.database import get_db, release_db
from services.integration_owner import get_integration_owner_status

_ASSET_VERSION_PLACEHOLDER = "__ANAM_ASSET_VERSION__"
_PUBLIC_BASE_URL_PLACEHOLDER = "__ANAM_PUBLIC_BASE_URL__"
_CDN_URL_PLACEHOLDER = "__ANAM_CDN_URL__"
_CDN_URL_JS_PLACEHOLDER = "##ANAM_CDN_URL##"
_SANCTUARY_API_PLACEHOLDER = "##SANCTUARY_API_BASE##"
_API_URL_PLACEHOLDER = "##ANAM_API_URL##"


def _iter_versioned_assets():
    """Yield the files that define the app shell version."""
    for pattern in (
        "*.html",
        "css/*.css",
        "js/*.js",
        "assets/icons/*",
        "assets/hub/*",
        "fonts/*",
        "manifest.json",
        "sw.js",
    ):
        yield from STATIC_DIR.glob(pattern)


def _compute_asset_version() -> str:
    """Build a stable version from the current app shell contents."""
    digest = hashlib.sha256()
    for path in sorted({p for p in _iter_versioned_assets() if p.is_file()}):
        digest.update(path.relative_to(STATIC_DIR).as_posix().encode("utf-8"))
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()[:12]


ASSET_VERSION = _compute_asset_version()
CACHE_VERSION = ASSET_VERSION

from core.lifespan import lifespan, is_system_ready, get_server_start_time
from core.middleware import auth_and_cache_middleware

# Infrastructure readiness is now managed by core.lifespan

# Configure logging — write to both the terminal AND a log file at
# data/logs/anam.log so debugging long-running issues doesn't require
# scrolling through the terminal window.
#
# Rotation strategy: rotate ONLY at startup, never at runtime. The reason:
# Anam spawns per-conversation claude.exe children via pywinpty, and on
# Windows those children inherit open file handles. RotatingFileHandler's
# runtime os.rename() of the log file fails with WinError 32 when any
# child still holds the inherited handle — and CC sessions are persistent,
# so they always do. Pre-rotating at boot sidesteps the whole problem:
# nothing else has the file open yet, so the rename always succeeds, and
# during runtime the file just grows under a single plain FileHandler.
_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    from config import DATA_DIR
    from datetime import datetime
    _log_dir = DATA_DIR / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = _log_dir / "anam.log"

    # Startup rotation: if the previous run's log exceeds 10MB, archive it
    # with a timestamped suffix so this run starts with a clean file.
    _ROTATE_THRESHOLD_BYTES = 10 * 1024 * 1024
    if _log_path.exists():
        try:
            if _log_path.stat().st_size >= _ROTATE_THRESHOLD_BYTES:
                _archive = _log_dir / f"anam-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
                _log_path.rename(_archive)
        except OSError as _e:
            # Non-fatal — just append to the existing file if rotation fails.
            print(f"[anam] Startup log rotation skipped: {_e}", file=sys.stderr)

    # Prune old archived logs: keep the 6 most recent so the dir doesn't
    # grow without bound. (Best-effort — never fail startup over cleanup.)
    try:
        _archives = sorted(
            _log_dir.glob("anam-*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for _old in _archives[6:]:
            _old.unlink(missing_ok=True)
    except OSError:
        pass

    _file_handler = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _log_handlers.append(_file_handler)
except Exception as _e:
    # File logging is best-effort. If it fails, terminal output still works.
    print(f"[anam] Could not set up file logger: {_e}", file=sys.stderr)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=_log_handlers,
)
from services.log_redaction import install_redacting_formatters
install_redacting_formatters(
    _log_handlers,
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("anam")

# Lifespan and database setup has been moved to core.lifespan


from services.rate_limit import limiter

app = FastAPI(title="Anam", docs_url=None, redoc_url=None, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS - allow the configured public site origin when needed.
_cors_origins = []
for origin in (SITE_URL,):
    if origin and origin not in _cors_origins:
        _cors_origins.append(origin)

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.middleware("http")(auth_and_cache_middleware)


# ANAM GUIDE: ADD A NEW API AREA
# A new api/<feature>.py router is imported and included in this block. Keep
# substantial feature logic in services/; this file only assembles the app.
# Mount API routes
from api.auth import router as auth_router
from api.avatars import router as avatars_router
from api.audio import router as audio_router
from api.autowake import router as autowake_router
from api.chat import router as chat_router
from api.chat_http import router as chat_http_router
from api.documents import router as documents_router
from api.identity import router as identity_router
from api.gifs import router as gifs_router
from api.images import router as images_router
from api.messages import router as messages_router
from api.notifications import router as notifications_router
from api.rituals import router as rituals_router
from api.sanctuary import router as sanctuary_router
from api.settings import router as settings_router
from api.hub import router as hub_router
from api.voice import router as voice_router
from api.search import router as search_router
from api.games import router as games_router
from api.pulses import router as pulses_router
from api.echo_relay import router as echo_relay_router
from api.story_state import router as story_state_router
from api.world_feed import router as world_feed_router
from api.videos import router as videos_router
from api.canvases import router as canvases_router
from api.wearable import router as wearable_router
from api.phone import router as phone_router
from api.room import router as room_router
from api.crosstalk import router as crosstalk_router
from api.bridge import router as bridge_router
from api.call_llm import router as call_llm_router
from api.tool_gateway import router as tool_gateway_router
from api.computer_tools import router as computer_tools_router


app.include_router(auth_router)
app.include_router(avatars_router)
app.include_router(chat_router)
app.include_router(chat_http_router)
app.include_router(messages_router)
app.include_router(sanctuary_router)
app.include_router(identity_router)
app.include_router(voice_router)
app.include_router(images_router)
app.include_router(gifs_router)
app.include_router(documents_router)
app.include_router(audio_router)
app.include_router(autowake_router)
app.include_router(settings_router)
app.include_router(rituals_router)
app.include_router(notifications_router)
app.include_router(hub_router)
app.include_router(search_router)
app.include_router(games_router)
app.include_router(pulses_router)
app.include_router(echo_relay_router)
app.include_router(story_state_router)
app.include_router(world_feed_router)
app.include_router(videos_router)
app.include_router(canvases_router)
app.include_router(wearable_router)
app.include_router(phone_router)
app.include_router(room_router)
app.include_router(crosstalk_router)
app.include_router(bridge_router)
app.include_router(call_llm_router)
app.include_router(tool_gateway_router)
app.include_router(computer_tools_router)
from api.game_npc import router as game_npc_router
app.include_router(game_npc_router)


@app.get("/health")
async def health_check():
    """Unauthenticated health endpoint for monitoring."""
    from services.connection_registry import _connections
    from services.autowake import scheduler

    integration_owner = get_integration_owner_status()
    owns_integrations = bool(integration_owner.get("owner"))
    checks = {
        "ready": is_system_ready(),
        "uptime_seconds": round(time.time() - get_server_start_time(), 1),
        "integration_owner": integration_owner,
    }

    overall = "healthy"

    # DB connectivity + latency
    try:
        db = await get_db()
        t0 = time.time()
        await db.execute_fetchall("SELECT 1")
        db_latency = round((time.time() - t0) * 1000, 1)
        await release_db(db)
        checks["db"] = {"status": "ok", "latency_ms": db_latency}
    except Exception as e:
        log.warning("Health check DB error: %s", e)
        checks["db"] = {"status": "error", "error": "unavailable"}
        overall = "unhealthy"

    # MCP bridge powers hub integrations even when direct API mode is off.
    if not owns_integrations and integration_owner.get("exclusive"):
        checks["mcp"] = {
            "status": "standby",
            "required_for_health": False,
            "bridge_required_for_hub": True,
        }
    else:
        from services.mcp_bridge import mcp_bridge

        mcp_status = mcp_bridge.get_status()
        critical_failed = list(mcp_status.get("critical_failed", []))
        optional_failed = list(mcp_status.get("optional_failed", []))
        optional_pending = list(mcp_status.get("optional_pending", []))
        mcp_health = "degraded" if critical_failed else "ok"
        if optional_pending and not critical_failed:
            mcp_health = "warming_optional"
        checks["mcp"] = {
            "status": mcp_health,
            "required_for_health": HEALTH_REQUIRE_MCP_CRITICAL,
            "servers": mcp_status["server_count"],
            "tools": mcp_status["tool_count"],
            "critical_servers": mcp_status.get("critical_server_count", 0),
            "optional_servers": mcp_status.get("optional_server_count", 0),
            "critical_connected": mcp_status.get("critical_connected", 0),
            "optional_connected": mcp_status.get("optional_connected", 0),
            "duplicate_tool_count": mcp_status.get("duplicate_tool_count", 0),
            "open_circuits": mcp_status.get("open_circuits", {}),
            "bridge_required_for_hub": True,
        }
        from services.qualia_context import REQUIRED_TOOLS
        from services.context_hooks import get_hook
        available_tools = {tool["name"] for tool in mcp_bridge.get_tools()}
        missing_memory_tools = sorted(REQUIRED_TOOLS - available_tools)
        checks["qualia_memory"] = {
            "context_hook_loaded": get_hook("qualia_context") is not None,
            "required_tools": sorted(REQUIRED_TOOLS),
            "missing_tools": missing_memory_tools,
            "status": "ready" if not missing_memory_tools else "catalog_incomplete",
        }
        if critical_failed:
            checks["mcp"]["critical_failed"] = critical_failed
            if HEALTH_REQUIRE_MCP_CRITICAL and overall == "healthy":
                overall = "degraded"
        if optional_failed:
            checks["mcp"]["optional_failed"] = optional_failed
        if optional_pending:
            checks["mcp"]["optional_pending"] = optional_pending

    # Scheduler
    if not owns_integrations and integration_owner.get("exclusive"):
        checks["scheduler"] = {"status": "standby"}
    else:
        scheduler_running = scheduler.running if scheduler else False
        from services.scheduler_health import get_scheduler_health

        checks["scheduler"] = {
            "status": "ok" if scheduler_running else "stopped",
            **get_scheduler_health(),
        }
        if not scheduler_running:
            overall = "unhealthy"

    from services.process_health import get_process_health

    checks["process"] = get_process_health()

    # WebSocket connections
    checks["websockets"] = len(_connections)

    # OAuth token status
    if USE_DIRECT_API:
        from services.claude_api import get_token_status

        token_status = get_token_status()
        checks["oauth"] = token_status
        if token_status["status"] == "expired":
            if overall == "healthy":
                overall = "degraded"

    # Google OAuth token status
    try:
        from services.google_auth_health import get_google_token_status

        google_status = get_google_token_status()
        google_status["required_for_health"] = HEALTH_REQUIRE_GOOGLE_OAUTH
        checks["google_oauth"] = google_status
        if HEALTH_REQUIRE_GOOGLE_OAUTH and google_status["status"] == "expired":
            if overall == "healthy":
                overall = "degraded"
    except Exception:
        pass

    checks["status"] = overall
    status_code = 503 if overall == "unhealthy" else 200
    return JSONResponse(content=checks, status_code=status_code)


# ANAM GUIDE: SERVE BROWSER PAGES AND STATIC ASSETS
# Add an HTML page route beside /settings, /hub, and /gameroom below. Shared
# CSS/JS/images stay under static/ and the /static catch-all must remain last.
# Cache entries are (mtime_ns, size, rendered_content) so an edited page can be
# noticed without a restart. See _render_template for why that matters.
_template_cache: dict[tuple[str, str], tuple[int, int, str]] = {}

def _current_asset_version() -> str:
    """Return the app-shell version, which is FROZEN for the life of the process."""
    return ASSET_VERSION


def _render_template(path, *, media_type: str) -> Response:
    """Render a text asset with live values, re-reading it when the file changes."""
    cache_key = (str(path), media_type)
    version = _current_asset_version()

    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        # File vanished mid-flight: fall back to whatever we already rendered
        # rather than failing the page.
        stamp = None

    cached = _template_cache.get(cache_key)
    if cached is not None and (stamp is None or cached[:2] == stamp):
        content = cached[2]
    else:
        content = path.read_text(encoding="utf-8")
        # These tokens deliberately share names with the browser globals
        # (window.__ANAM_ASSET_VERSION__, window.__ANAM_PUBLIC_BASE_URL__).
        # Replace only their value slots. A global token replacement corrupts
        # the identifiers into syntax such as ``window.abc123`` and can also
        # duplicate the hash suffix when the legacy numeric-query pass runs.
        content = content.replace(
            f"?v={_ASSET_VERSION_PLACEHOLDER}",
            f"?v={version}",
        )
        content = content.replace(
            f"'{_ASSET_VERSION_PLACEHOLDER}'",
            json.dumps(version),
        )
        content = content.replace(
            f"JSON.parse('{_PUBLIC_BASE_URL_PLACEHOLDER}')",
            json.dumps(PUBLIC_BASE_URL or ""),
        )
        content = content.replace(_CDN_URL_JS_PLACEHOLDER, CDN_BASE_URL)
        content = content.replace(_CDN_URL_PLACEHOLDER, CDN_BASE_URL)
        content = content.replace(_SANCTUARY_API_PLACEHOLDER, SANCTUARY_API_BASE)
        # API stays same-origin; keep placeholder empty for the browser helper.
        content = content.replace(_API_URL_PLACEHOLDER, "")
        # Retire old all-numeric cache keys without matching the numeric prefix
        # of the current hexadecimal hash.
        content = re.sub(r"\?v=\d+(?=[\"'])", f"?v={version}", content)
        if stamp is None:
            _template_cache.pop(cache_key, None)
        else:
            _template_cache[cache_key] = (stamp[0], stamp[1], content)

    response = Response(content, media_type=media_type)
    if media_type == "text/html":
        response.headers["Cache-Control"] = "no-cache"
    return response


def _serve_html(path):
    return _render_template(path, media_type="text/html")


@app.get("/")
async def root():
    return _serve_html(STATIC_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    return _serve_html(STATIC_DIR / "settings.html")


@app.get("/diagnostics")
async def diagnostics_page():
    return _serve_html(STATIC_DIR / "diagnostics.html")


@app.get("/hub")
async def hub_page():
    return _serve_html(STATIC_DIR / "hub.html")

@app.get("/gameroom")
async def gameroom_page():
    return _serve_html(STATIC_DIR / "gameroom.html")


@app.get("/world-feed")
async def world_feed_page():
    """Private story-world social network."""
    return _serve_html(STATIC_DIR / "world-feed.html")


@app.get("/embodiment")
async def embodiment_page():
    """Pack-wide 3D body inspection and native voice-call lab."""
    return _serve_html(STATIC_DIR / "avatar-lab.html")


@app.get("/wrist")
async def wrist_page():
    """Owner's tap-anywhere reach door for the phone (works off home wifi).

    Same endpoint her Versa 2 hits, but over the public tunnel instead of the LAN
    IP the watch companion is pinned to — so it keeps working when she's out.
    Also reachable at /static/wrist.html, which needs no restart.
    """
    return _serve_html(STATIC_DIR / "wrist.html")


@app.get("/sw.js")
async def service_worker():
    response = _render_template(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


# Mount static files last (catch-all)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
    )
