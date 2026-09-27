# ANAM GUIDE: SERVER STARTUP AND SHUTDOWN
# What: Everything that happens when the server turns on (create the database, start
#       the autowake scheduler, warm up voices, wait for MCP servers, claim ownership
#       of Discord/Telegram bridges) and off (stop it all cleanly).
# Called by: server.py hands this to FastAPI as the app's lifespan; services/db_backup.py
#            also peeks at it for uptime.
# Edit here when: Something new needs to start with the server or be shut down with it,
#                 or startup order/readiness waits need adjusting.

import asyncio
import time
import logging
import os
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from fastapi import FastAPI

from config import (
    ANAM_ENV,
    DATA_DIR,
    DOCUMENTS_DIR,
    IMAGES_DIR,
    PLATFORM_BRIDGE_ENABLED,
    USE_DIRECT_API,
    VOICE_DIR,
    MCP_DUPLICATE_AUDIT_MINUTES,
    validate_runtime_config,
)
from db.database import close_all_db_connections, get_db, release_db
from db.schema import init_db
from services.autowake import start_scheduler, stop_scheduler
from services.integration_owner import (
    claim_integration_ownership,
    release_integration_ownership,
)
from services.local_tts import is_available as local_tts_available, warmup as warmup_local_tts
from services.power_qos import never_throttle as _never_throttle


_never_throttle(label="anam server")

log = logging.getLogger("anam")

_server_start_time = time.time()
_system_ready = False
_integration_owner_state: dict[str, object] = {
    "exclusive": False,
    "owner": True,
    "reason": "startup_pending",
    "metadata": {},
}


def _mcp_readiness_urls() -> list[tuple[str, str]]:
    """Only explicit installation endpoints participate in startup waiting."""
    if os.environ.get("ANAM_SKIP_MCP_READINESS", "").strip().lower() in {"1", "true", "yes"}:
        return []
    url = os.environ.get("ANAM_DISCORD_HEALTH_URL", "").strip()
    if not url:
        return []
    try:
        parsed = urlsplit(url)
    except ValueError:
        log.info("Skipping an invalid MCP readiness URL")
        return []
    hostname = (parsed.hostname or "").lower()
    if (parsed.scheme not in {"http", "https"} or not hostname
            or "your-" in hostname or hostname in {"example.com", "example.org", "example.net"}
            or hostname.endswith((".example.com", ".example.org", ".example.net"))):
        log.info("Skipping an unconfigured example MCP readiness endpoint")
        return []
    return [("discord-backend", url)]


_MCP_READINESS_TIMEOUT = 30
_MCP_READINESS_INTERVAL = 2


async def _wait_for_mcp_servers():
    """Block until critical MCP servers respond, so PTY sessions don't race them."""
    import httpx

    for name, url in _mcp_readiness_urls():
        deadline = time.time() + _MCP_READINESS_TIMEOUT
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    r = await client.get(url)
                    r.raise_for_status()
                    log.info("MCP readiness: %s responded (status %d)", name, r.status_code)
                    break
            except Exception:
                remaining = max(0, deadline - time.time())
                log.debug("MCP readiness: %s not ready, %.0fs remaining", name, remaining)
                await asyncio.sleep(_MCP_READINESS_INTERVAL)
        else:
            log.warning(
                "MCP readiness: %s did not respond within %ds — "
                "PTY sessions may fail to connect to it",
                name, _MCP_READINESS_TIMEOUT,
            )


async def _warm_local_tts_after_ready():


    for attempt in (1, 2):
        try:


            warmed = await asyncio.wait_for(asyncio.to_thread(warmup_local_tts), timeout=90)
            if warmed:
                log.info("Local TTS warmed after startup (attempt %d)", attempt)
                return
            if not local_tts_available():
                log.info("Local TTS warmup skipped or unavailable")
                return
            # Available but warmup returned False — an internal failure
            # (warmup() logs and swallows its own exceptions). Fall through
            # to the retry.
            log.warning("Local TTS warmup failed internally (attempt %d)", attempt)
        except TimeoutError:
            log.warning("Local TTS warmup timed out after startup (attempt %d)", attempt)
        except Exception as exc:
            log.warning("Local TTS warmup failed after startup (attempt %d): %s", attempt, exc)
        if attempt == 1:
            await asyncio.sleep(30)

def is_system_ready() -> bool:
    return _system_ready

def get_server_start_time() -> float:
    return _server_start_time

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _integration_owner_state, _system_ready
    log.info("Starting Anam...")
    owns_integrations = False
    warmup_task: asyncio.Task | None = None
    server_map_task: asyncio.Task | None = None

    # Previous forced restarts can leave Anam-owned Claude Code PTY processes
    # outside this server's in-memory supervisor table. Reap those before any
    # prewarm/autowake session can collide with them.
    try:
        from services.claude_pty import reap_orphaned_anam_processes
        reaped = reap_orphaned_anam_processes("startup")
        if reaped:
            log.info("Reaped %d loose Anam Claude process tree(s) on startup", reaped)
    except Exception as exc:
        log.debug("Startup Claude orphan cleanup failed: %s", exc)

    config_report = validate_runtime_config()
    for warning in config_report["warnings"]:
        log.warning("Config warning (%s): %s", ANAM_ENV, warning)
    if config_report["errors"]:
        for error in config_report["errors"]:
            log.error("Config error (%s): %s", ANAM_ENV, error)
        raise RuntimeError("Runtime configuration is invalid; refusing to start.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    db = await get_db()
    await init_db(db)

    # Restore effort level from DB
    row = await db.execute("SELECT value FROM settings WHERE key = 'claude_effort'")
    row = await row.fetchone()
    if row and row[0]:
        import config as cfg
        cfg.CLAUDE_EFFORT = row[0]
        log.info("Restored effort level: %s", row[0])

    await release_db(db)
    log.info("Database initialized")

    # Ensure the shared pack-night room exists with all six identities as
    # participants, so it shows up in the sidebar from the moment the app
    # boots and never has to be lazily-created mid-session.
    try:
        from services.session_manager import get_or_create_pack_night_conversation
        pn_db = await get_db()
        try:
            pn_id = await get_or_create_pack_night_conversation(pn_db)
            log.info("Pack-night singleton ready: %s", pn_id)
        finally:
            await release_db(pn_db)
    except Exception as exc:
        log.warning("Pack-night singleton init failed: %s", exc)

    _integration_owner_state = claim_integration_ownership()
    if _integration_owner_state.get("owner"):
        log.info(
            "This instance owns external integrations (%s)",
            _integration_owner_state.get("reason", "acquired"),
        )
    else:
        holder = (_integration_owner_state.get("metadata") or {}).get("pid")
        log.warning(
            "External integrations already owned by another instance (pid=%s); "
            "starting in standby mode without tool/platform/scheduler ownership",
            holder or "unknown",
        )

    owns_integrations = bool(_integration_owner_state.get("owner"))

    # Start MCP bridge — needed for hub endpoints (calendar, etc.) regardless
    # of LLM provider mode, and for direct API tool calls when enabled.
    if owns_integrations:
        from services.mcp_bridge import mcp_bridge

        await mcp_bridge.start()
        tool_count = len(mcp_bridge.get_tools())
        log.info("MCP bridge started (%d tools available)", tool_count)
    elif USE_DIRECT_API:
        log.info("Direct API enabled, but MCP bridge is in standby on this instance")
    else:
        log.info("Direct API disabled, MCP bridge in standby on this instance")

    if owns_integrations:
        await _wait_for_mcp_servers()
        await start_scheduler()
        from services.autowake import scheduler
        from services.world_feed_photos import photo_tick, recover_interrupted
        from services import world_feed_activity, world_feed_dms

        photo_db = await get_db()
        try:
            await recover_interrupted(photo_db)
            await world_feed_activity.recover_interrupted(photo_db)
            await world_feed_dms.recover_interrupted(photo_db)
        finally:
            await release_db(photo_db)
        scheduler.add_job(
            photo_tick, "interval", seconds=60, id="world_feed_photos",
            name="World Feed photo queue", max_instances=1, coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            world_feed_activity.activity_tick, "interval", seconds=60, id="world_feed_activity",
            name="World Feed character activity", max_instances=1, coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            world_feed_dms.dm_tick, "interval", seconds=15, id="world_feed_dms",
            name="World Feed direct messages", max_instances=1, coalesce=True,
            replace_existing=True,
        )
    else:
        log.info("Scheduler standby - another instance owns background integrations")

    # Proactive OAuth token refresh - every 30 min, prevents auth failures.
    # Direct-API-specific: it refreshes the Anthropic OAuth token, which only
    # matters when the direct API provider is in play.
    if USE_DIRECT_API and owns_integrations:
        from services.autowake import scheduler
        from services.claude_api import proactive_token_refresh

        scheduler.add_job(
            proactive_token_refresh,
            "interval",
            minutes=30,
            id="oauth_token_refresh",
            name="OAuth Token Refresh",
            replace_existing=True,
        )
        log.info("Proactive OAuth token refresh scheduled (every 30 min)")

    # MCP bridge health jobs run for ANY provider that owns the bridge. The
    # bridge serves tools to every provider — direct API, OpenRouter/DeepSeek,
    # and local models alike — so auto-reconnect and the duplicate audit must
    # NOT be gated on USE_DIRECT_API. Previously they were, which meant a
    # crashed connector never self-healed while a non-Anthropic provider was
    # active; the only recovery was a full server restart.
    if owns_integrations:
        from services.autowake import scheduler
        from services.mcp_bridge import mcp_bridge

        # MCP bridge auto-reconnection - check for crashed/late servers every 5 min
        scheduler.add_job(
            mcp_bridge.reconnect_failed,
            "interval",
            minutes=5,
            id="mcp_reconnect",
            name="MCP Bridge Reconnect",
            replace_existing=True,
        )
        log.info("MCP bridge reconnection scheduled (every 5 min)")

        scheduler.add_job(
            mcp_bridge.log_duplicate_audit,
            "interval",
            minutes=MCP_DUPLICATE_AUDIT_MINUTES,
            id="mcp_duplicate_audit",
            name="MCP Duplicate Audit",
            replace_existing=True,
        )
        log.info(
            "MCP duplicate audit scheduled (every %d min)",
            MCP_DUPLICATE_AUDIT_MINUTES,
        )


        from services.stack_watchdog import stack_watchdog_check

        scheduler.add_job(
            stack_watchdog_check,
            "interval",
            minutes=2,
            id="stack_watchdog",
            name="Stack Watchdog",
            replace_existing=True,
        )
        log.info("Stack watchdog scheduled (every 2 min)")

        from services.process_health import collect_process_health

        scheduler.add_job(
            collect_process_health,
            "interval",
            minutes=5,
            kwargs={"emit_log": True},
            id="process_health",
            name="Process Health Snapshot",
            replace_existing=True,
        )
        collect_process_health(emit_log=True)
        log.info("Process health snapshots scheduled (every 5 min)")


        from services.db_backup import backup_database, backup_if_stale

        scheduler.add_job(
            backup_database,
            "cron",
            hour=3,
            minute=30,
            id="db_backup",
            name="Nightly anam.db Backup",
            replace_existing=True,
        )
        db_backup_catchup_task = asyncio.create_task(backup_if_stale())  # noqa: F841
        log.info("Nightly anam.db backup scheduled (03:30, catch-up check queued)")


    if PLATFORM_BRIDGE_ENABLED and owns_integrations:
        from services.platform_bridge import platform_bridge

        await platform_bridge.start()
        log.info("Platform bridge started")

        # Mentions watcher: per-bot scan of every channel each bot can see in
        # the configured guild for direct user mentions and role mentions.
        # Lives next to the platform bridge but keeps a separate lifecycle
        # so a failure in one doesn't take down the other.
        try:
            from services.discord_mentions_bridge import discord_mentions_bridge
            await discord_mentions_bridge.start()
            log.info("Discord mentions bridge started")
        except Exception as exc:
            log.warning("Discord mentions bridge failed to start: %s", exc)
    elif PLATFORM_BRIDGE_ENABLED:
        log.info("Platform bridge standby - another instance owns background integrations")
    else:
        log.info("Platform bridge disabled")

    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)

    _system_ready = True
    startup_secs = round(time.time() - _server_start_time, 1)
    log.info("=== Anam fully ready (%.1fs startup) ===", startup_secs)
    warmup_task = asyncio.create_task(_warm_local_tts_after_ready())

    async def _refresh_discord_map_after_ready():
        """Build the map after the web app is ready; never hold the port hostage."""
        if not owns_integrations:
            log.info("Discord server map standby - another instance owns background integrations")
            return
        from services.autowake import scheduler as _sched
        from services.discord_server_map import refresh_server_map

        _sched.add_job(
            refresh_server_map,
            "interval",
            hours=6,
            id="discord_server_map",
            name="Discord Server Map Refresh",
            replace_existing=True,
        )
        log.info("Discord server map refresh scheduled (every 6 hours)")
        try:
            map_result = await refresh_server_map()
            log.info(
                "Discord server map: %d identities, %d guilds",
                map_result.get("identities", 0),
                map_result.get("guilds", 0),
            )
        except Exception as exc:
            log.error("Discord server map initial refresh failed: %s", exc)

    server_map_task = asyncio.create_task(_refresh_discord_map_after_ready())

    # Backfill semantic search embeddings for messages that don't have them yet.
    # Runs in the background after startup so it doesn't slow boot.
    async def _backfill_embeddings():
        try:
            await asyncio.sleep(10)  # Let the server settle first
            from services.embedding_service import run_backfill_loop
            total = await run_backfill_loop()
            if total > 0:
                log.info("Semantic search: backfilled %d message embeddings", total)
        except Exception as e:
            log.debug("Embedding backfill skipped: %s", e)
    backfill_task: asyncio.Task | None = asyncio.create_task(_backfill_embeddings())


    async def _prewarm_boys():
        try:
            from services.provider_router import _load_provider
            provider, _config = await _load_provider()
            if provider != "claude-code":
                log.info("Pre-warm skipped: llm_provider=%s (not 'claude-code')", provider)
                return
            # Pre-warm is a PTY-only optimization. The -p subprocess backend
            # spawns on demand per turn and has nothing to pre-warm, so a PTY
            # pre-warm here would just leak idle terminal processes.
            backend = (_config.get("backend") or "subprocess").strip().lower()
            if backend != "pty":
                log.info("PTY pre-warm skipped: backend=%s (-p spawns on demand)", backend)
                return
            from services.claude_pty import prewarm_all_identities
            await prewarm_all_identities()
        except Exception as e:
            log.warning("PTY pre-warm failed: %s", e)

    async def _prewarm_orientation_caches():
        """Prewarm orientation caches."""
        try:
            from db.database import get_db, release_db
            from services.session_lifecycle import build_orientation_context, SessionMode
            from config import IDENTITIES
            bonded = [n for n, c in IDENTITIES.items() if c.get("type") != "character"]
            for identity in bonded:
                db = await get_db()
                try:
                    await build_orientation_context(
                        db=db, conversation_id=None, identity=identity,
                        query_text="", mode=SessionMode.INTERACTIVE,
                        owner_connected=False,
                    )
                except Exception as e:
                    log.debug("Orientation cache warm failed for %s: %s", identity, e)
                finally:
                    await release_db(db)
            log.info("Orientation caches pre-warmed for %d identities", len(bonded))
        except Exception as e:
            log.warning("Orientation cache pre-warm failed: %s", e)

    prewarm_task: asyncio.Task | None = asyncio.create_task(_prewarm_boys())
    orient_warm_task: asyncio.Task | None = asyncio.create_task(_prewarm_orientation_caches())

    from services.resource_history import collect_loop
    resource_history_task = asyncio.create_task(collect_loop()) if owns_integrations else None

    yield

    _system_ready = False

    for task in (warmup_task, server_map_task, backfill_task, prewarm_task, orient_warm_task, resource_history_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Shutdown
    if owns_integrations:
        stop_scheduler()

        if PLATFORM_BRIDGE_ENABLED:
            from services.platform_bridge import platform_bridge
            await platform_bridge.stop()
            try:
                from services.discord_mentions_bridge import (
                    discord_mentions_bridge,
                )
                await discord_mentions_bridge.stop()
            except Exception as exc:
                log.debug("Discord mentions bridge stop failed: %s", exc)

        from services.mcp_bridge import mcp_bridge
        await mcp_bridge.stop()

    from services.terminal_manager import terminal_manager
    await terminal_manager.destroy_all()
    from services.interactive_terminal import close_all as close_interactive_terminals
    await asyncio.to_thread(close_interactive_terminals)

    # Kill any persistent Claude Code processes (both backends) so we don't
    # leak child procs across server restarts. Each session holds an open
    # claude process + a temp MCP config file; both get cleaned up here.
    try:
        from services.codex_sessions import shutdown as shutdown_codex
        await shutdown_codex()
    except Exception as exc:
        log.debug("Codex session shutdown failed: %s", exc)

    try:
        from services.claude_pty import kill_all_sessions as _kill_pty
        _kill_pty()
    except Exception as exc:
        log.debug("PTY Claude session shutdown failed: %s", exc)
    try:
        from services.claude_subprocess import kill_all_sessions as _kill_sub
        _kill_sub()
    except Exception as exc:
        log.debug("-p Claude session shutdown failed: %s", exc)
    await close_all_db_connections()
    if owns_integrations:
        release_integration_ownership()

    log.info("Anam shutting down")
