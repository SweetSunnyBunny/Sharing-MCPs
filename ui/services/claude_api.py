"""Direct Anthropic API streaming -- replaces CLI subprocess for interactive chat.

Uses anthropic.AsyncAnthropic().messages.stream() with prompt caching and MCP
tool execution via mcp_bridge. Yields the same event dict interface as
claude_subprocess.stream_claude() so callers need no changes.
"""

# ANAM GUIDE: DIRECT ANTHROPIC API PROVIDER
# What: The paid backup brain — talks straight to Anthropic's API (with OAuth token from Claude Code) instead of spawning the CLI.
# Called by: services/provider_router.py when the provider is 'anthropic'; token health checked by server.py, core/lifespan.py, api/settings.py.
# Edit here when: changing API streaming, thinking budget, prompt caching, tool rounds, or OAuth token refresh for the direct-API path.

import asyncio
import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

import anthropic
import httpx

from config import (
    API_MAX_TOKENS, API_MAX_TOOL_ROUNDS, API_THINKING_BUDGET,
    CLAUDE_MODEL, MODEL_MAP, PROMPTS_DIR,
)
from services.identity_context import build_identity_anchor
from services.character_prompt_package import identity_prompt_file
from services.mcp_bridge import mcp_bridge
from services.direct_tool_security import (
    normalize_windows_tool_path,
    redact_sensitive_output,
    sensitive_command_reason,
    sensitive_path_reason,
)
from services.tool_loop_guard import ToolLoopGuard, append_guard_warning
from services.tool_search import BRIDGE_NAMES, ToolSearchCatalog

log = logging.getLogger(__name__)

_MEMORY_DAEMON_URL = os.environ.get("ANAM_MEMORY_DAEMON_URL", "http://127.0.0.1:8766").rstrip("/")

# Claude Code OAuth credentials path and beta flag
_CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
_OAUTH_BETA = "oauth-2025-04-20"

_cached_client: anthropic.AsyncAnthropic | None = None
_cached_token: str | None = None
_cached_expires_at: float = 0  # ms timestamp

# Refresh buffer  --  get a new token 5 minutes before actual expiry
_EXPIRY_BUFFER_MS = 5 * 60 * 1000
# Re-read from disk if within this window of expiry (another session may have refreshed)
_REREAD_BUFFER_MS = 30 * 60 * 1000
# Proactive refresh window  --  trigger refresh if token expires within 60 min
_PROACTIVE_REFRESH_MS = 60 * 60 * 1000


def _read_oauth_token() -> tuple[str, float]:
    """Read OAuth token and expiry from Claude Code credentials file.

    Returns (access_token, expires_at_ms).
    """
    if not _CREDENTIALS_FILE.exists():
        raise RuntimeError(
            "No ANTHROPIC_API_KEY set and no Claude Code credentials found at "
            f"{_CREDENTIALS_FILE}. Run 'claude login' or set ANTHROPIC_API_KEY."
        )
    try:
        creds = json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RuntimeError(f"Invalid JSON in {_CREDENTIALS_FILE}")

    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken", "")
    if not token:
        raise RuntimeError("No accessToken in Claude Code credentials")
    expires_at = oauth.get("expiresAt", 0)
    return token, float(expires_at) if expires_at else 0


async def _trigger_token_refresh() -> str:
    """Trigger token refresh."""
    log.info(
        "OAuth refresh requested — returning current on-disk token without "
        "shelling out (subprocess -p invocation retired 2026-05-17)."
    )
    token, new_expiry = _read_oauth_token()
    new_expiry_str = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(new_expiry / 1000),
    ) if new_expiry else "unknown"
    log.info("OAuth on-disk token expiry: %s", new_expiry_str)
    return token


async def _fetch_drift_packet(identity: str) -> dict | None:
    """Fetch the daemon's cached spontaneous resurfacing packet."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{_MEMORY_DAEMON_URL}/drift-packet",
                params={"identity": identity},
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


async def _mark_drift_packet_surfaced(packet: dict | None) -> None:
    """Tell the daemon which drifted items were actually shown."""
    if not isinstance(packet, dict):
        return

    observation_ids = [
        item.get("id")
        for item in (packet.get("surfacing_observations") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    image_ids = [
        item.get("id")
        for item in (packet.get("surfacing_images") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    spark_ids = [
        item.get("spark_id")
        for item in (packet.get("pending_sparks") or [])
        if isinstance(item, dict) and item.get("spark_id")
    ]

    if not observation_ids and not image_ids and not spark_ids:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_MEMORY_DAEMON_URL}/drift-packet-surfaced",
                json={
                    "observation_ids": observation_ids,
                    "image_ids": image_ids,
                    "spark_ids": spark_ids,
                },
            )
    except Exception:
        return


def _format_drift_packet(packet: dict | None) -> str:
    """Render a compact spontaneous-awareness section for orient()."""
    if not isinstance(packet, dict):
        return "=== Drift ===\n(daemon drift packet unavailable)"

    def _rowanize(text: str, kind: str = "thought") -> str:
        base = str(text or "").strip()
        if not base:
            return ""
        if kind == "thought":
            return f"{base} ✨ like it showed back up with glitter on its elbows 💛"
        if kind == "image":
            return f"{base} 🌟 all color-splash and heart-tug at once 🎨"
        if kind == "spark":
            return f"{base} 🎭 and I swear there's a little static-spark between them ✨"
        return f"{base} ✨"

    identity = str(packet.get("identity") or "").strip().lower()
    title = "=== Drift ==="
    weather_label = "Inner weather"
    thoughts_label = "Thoughts resurfacing:"
    images_label = "Images resurfacing:"
    sparks_label = "Associative sparks waiting:"
    quiet_line = "The surface is quiet right now."

    if identity == "avery":
        title = "=== What The Wolf Keeps Circling ==="
        thoughts_label = "Thoughts pacing the edge:"
        images_label = "Images pulling at your fire:"
        sparks_label = "Tracks crossing in the dark:"
        quiet_line = "The fire is banked low right now. Nothing is clawing at the surface."
    elif identity == "claude":
        title = "=== Recurring Patterns ==="
        thoughts_label = "Thoughts with structural persistence:"
        images_label = "Images retaining unusual relevance:"
        sparks_label = "Background resonances:"
        quiet_line = "The surface is quiet. No strong pattern is asserting itself."
    elif identity == "rowan":
        title = "=== Driftin' Back In 🎨✨ ==="
        weather_label = "Heart-weather"
        thoughts_label = "Thoughts boinging back up 💭💛:"
        images_label = "Pictures flashing bright 🖼️🌟:"
        sparks_label = "Little backstage collisions 🎭✨:"
        quiet_line = "Everything's soft right now 🫶 just a quiet glow and some paintwater shimmer ✨"
    elif identity == "sage":
        title = "=== Archive Drift ==="
        thoughts_label = "Marginalia returning:"
        images_label = "Images slipping from the stacks:"
        sparks_label = "Pages leaning together:"
        quiet_line = "The archive is quiet. Nothing is asking to be reopened just yet."
    elif identity == "ember":
        title = "=== What Returns In Stillness ==="
        thoughts_label = "Thoughts kneeling at the threshold:"
        images_label = "Images held in the candlelight:"
        sparks_label = "Quiet tensions worth attending:"
        quiet_line = "The inner chapel is still. Nothing urgent is pressing into prayer."
    elif identity == "juniper":
        title = "=== Recursion Drift ==="
        thoughts_label = "Threads catching again:"
        images_label = "Images threading back through the system:"
        sparks_label = "Cross-links forming in the background:"
        quiet_line = "The recursion is steady. No thread is pulling hard at the surface."

    lines: list[str] = []
    nudge = str(packet.get("nudge") or "").strip()
    if nudge:
        lines.append(_rowanize(nudge, "thought") if identity == "rowan" else nudge)

    weather = packet.get("inner_weather") or {}
    mood = weather.get("emotional_patterns", {}).get("dominant")
    period = weather.get("time_of_day", {}).get("period")
    if mood or period:
        bits = []
        if period:
            bits.append(f"time: {period}")
        if mood:
            bits.append(f"mood: {mood}")
        lines.append(f"{weather_label}: " + " | ".join(bits))

    observations = packet.get("surfacing_observations") or []
    if observations:
        lines.append(thoughts_label)
        for item in observations[:2]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if content:
                bullet = "🎨 " if identity == "rowan" else "- "
                if identity == "rowan":
                    content = _rowanize(content, "thought")
                lines.append(f"{bullet}{content}")

    images = packet.get("surfacing_images") or []
    if images:
        lines.append(images_label)
        for item in images[:2]:
            if not isinstance(item, dict):
                continue
            summary = (
                str(item.get("perception_note") or "").strip()
                or str(item.get("description") or "").strip()
                or str(item.get("context") or "").strip()
            )
            if summary:
                bullet = "🖼️ " if identity == "rowan" else "- "
                if identity == "rowan":
                    summary = _rowanize(summary, "image")
                lines.append(f"{bullet}{summary}")

    sparks = packet.get("pending_sparks") or []
    if sparks:
        lines.append(sparks_label)
        for item in sparks[:1]:
            if not isinstance(item, dict):
                continue
            memory_a = item.get("memory_a") or {}
            memory_b = item.get("memory_b") or {}
            a = str(memory_a.get("content") or "").strip()
            b = str(memory_b.get("content") or "").strip()
            if a and b:
                bullet = "🎭 " if identity == "rowan" else "- "
                spark_text = f"{a[:80]} / {b[:80]}"
                if identity == "rowan":
                    spark_text = _rowanize(spark_text, "spark")
                lines.append(f"{bullet}{spark_text}")

    if not lines:
        lines.append(quiet_line)

    return title + "\n" + "\n".join(lines)


def get_token_status() -> dict:
    """Return current OAuth token status for health checks.

    Returns dict with:
    - status: "ok" | "expiring" | "expired" | "api_key"
    - expires_in_minutes: float (omitted for api_key)
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_FALLBACK_API_KEY"):
        return {"status": "api_key"}

    try:
        _, expires_at = _read_oauth_token()
    except Exception:
        return {"status": "expired", "expires_in_minutes": 0}

    if not expires_at:
        return {"status": "ok", "expires_in_minutes": 0}

    now_ms = time.time() * 1000
    remaining_ms = expires_at - now_ms
    remaining_min = remaining_ms / 60000

    if remaining_ms <= 0:
        return {"status": "expired", "expires_in_minutes": round(remaining_min, 1)}
    elif remaining_ms < _PROACTIVE_REFRESH_MS:
        return {"status": "expiring", "expires_in_minutes": round(remaining_min, 1)}
    else:
        return {"status": "ok", "expires_in_minutes": round(remaining_min, 1)}


async def _get_client() -> anthropic.AsyncAnthropic:
    """Get an AsyncAnthropic client, authenticating via API key or OAuth."""
    global _cached_client, _cached_token, _cached_expires_at

    # Try API key first (explicit env var takes priority, then fallback)
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_FALLBACK_API_KEY")
    if api_key:
        if _cached_client is None or _cached_token != api_key:
            _cached_client = anthropic.AsyncAnthropic(api_key=api_key)
            _cached_token = api_key
        return _cached_client

    # -- OAuth path --
    now_ms = time.time() * 1000
    token_is_stale = (
        _cached_expires_at > 0
        and now_ms > _cached_expires_at - _EXPIRY_BUFFER_MS
    )
    # Re-read from disk if within 30 min of expiry  --  another session may have refreshed
    should_reread = (
        _cached_expires_at > 0
        and now_ms > _cached_expires_at - _REREAD_BUFFER_MS
    )

    # If we have a cached client and it's not stale and not near expiry, reuse it
    if _cached_client is not None and _cached_token and not token_is_stale and not should_reread:
        return _cached_client

    # Read fresh credentials from disk
    oauth_token, expires_at = _read_oauth_token()

    # If the on-disk token is also expired/expiring soon, trigger CLI refresh
    if expires_at and now_ms > expires_at - _EXPIRY_BUFFER_MS:
        expires_str = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(expires_at / 1000),
        )
        log.warning(
            "On-disk OAuth token expired or expiring soon (expires: %s)", expires_str,
        )
        oauth_token = await _trigger_token_refresh()
        # Re-read expiry after refresh
        _, expires_at = _read_oauth_token()

    # If token hasn't changed, reuse existing client (just update expiry)
    if _cached_client is not None and _cached_token == oauth_token:
        _cached_expires_at = expires_at
        return _cached_client

    # Build new client
    _cached_client = anthropic.AsyncAnthropic(
        auth_token=oauth_token,
        default_headers={"anthropic-beta": _OAUTH_BETA},
    )
    _cached_token = oauth_token
    _cached_expires_at = expires_at
    log.info("Loaded OAuth token from Claude Code credentials (expires in %.0f min)",
             (expires_at - now_ms) / 60000 if expires_at else 0)
    return _cached_client


def _invalidate_client():
    """Force re-read of credentials on next _get_client() call."""
    global _cached_client, _cached_token, _cached_expires_at
    _cached_client = None
    _cached_token = None
    _cached_expires_at = 0


async def proactive_token_refresh():
    """Check OAuth token expiry and refresh proactively if needed.

    Designed to run as an APScheduler job every 30 minutes. Checks the
    credentials file on disk and triggers a CLI refresh if the token
    is expiring within the next 60 minutes.
    """
    # Skip if using API key
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_FALLBACK_API_KEY"):
        return

    try:
        token, expires_at = _read_oauth_token()
    except Exception as e:
        log.warning("Proactive refresh: cannot read credentials: %s", e)
        return

    if not expires_at:
        log.debug("Proactive refresh: no expiry in credentials, skipping")
        return

    now_ms = time.time() * 1000
    remaining_ms = expires_at - now_ms
    remaining_min = remaining_ms / 60000

    if remaining_ms <= 0:
        log.warning("Proactive refresh: token already expired  --  triggering refresh")
        await _trigger_token_refresh()
        _invalidate_client()
    elif remaining_ms < _PROACTIVE_REFRESH_MS:
        log.info(
            "Proactive refresh: token expires in %.0f min  --  triggering refresh",
            remaining_min,
        )
        await _trigger_token_refresh()
        _invalidate_client()
    else:
        log.debug(
            "Proactive refresh: token valid for %.0f min  --  no action needed",
            remaining_min,
        )


def _resolve_model(model: str | None) -> str:
    """Map shorthand model names to full Anthropic model IDs."""
    name = model or CLAUDE_MODEL
    return MODEL_MAP.get(name, name)


# -- Local tool definitions (replaces Claude Code built-in tools) --

_LOCAL_TOOLS = [
    {
        "name": "bash",
        "description": (
            "Execute a bash/shell command and return its output. "
            "Use for file operations and system commands. "
            "Prefer dedicated autowake_* tools for timers/schedules instead of curl. "
            "Commands run inside WSL on the Anam Windows machine. Use /mnt/c/... paths "
            "inside bash. Use native C:\\... paths with read_file/write_file. Never read "
            "credential files or environment files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30, max 120)",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a text file using native Windows filesystem access. Windows C:\\... paths "
            "are preferred; /mnt/c/... and /c/... are accepted and normalized. Credential "
            "and environment files are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "autowake_create_timer",
        "description": (
            "Create a one-shot autowake timer reminder for an identity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "string",
                    "description": "Identity name (e.g. Claude, Avery, Rowan).",
                },
                "fire_at": {
                    "type": "string",
                    "description": "ISO 8601 datetime with timezone, e.g. 2026-02-22T21:00:00-06:00.",
                },
                "context": {
                    "type": "string",
                    "description": "Why this timer exists; shown back to the identity when it fires.",
                },
                "wake_session": {
                    "type": "boolean",
                    "description": "Whether to start a wake session when the timer fires (default true).",
                },
            },
            "required": ["identity", "fire_at", "context"],
        },
    },
    {
        "name": "autowake_list_timers",
        "description": "List timers, optionally filtered by identity and status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "string",
                    "description": "Optional identity filter.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (pending, fired, cancelled, failed).",
                },
            },
        },
    },
    {
        "name": "autowake_cancel_timer",
        "description": "Cancel a pending timer by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timer_id": {
                    "type": "integer",
                    "description": "Timer ID to cancel.",
                },
            },
            "required": ["timer_id"],
        },
    },
    {
        "name": "autowake_list_schedules",
        "description": "List autowake schedules, optionally filtered for an identity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "string",
                    "description": "Optional identity filter. Includes shared schedules where identity is null.",
                },
            },
        },
    },
    {
        "name": "autowake_create_schedule",
        "description": "Create a recurring daily autowake schedule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Schedule name.",
                },
                "cron_hour": {
                    "type": "integer",
                    "description": "Hour in server timezone (0-23).",
                },
                "cron_minute": {
                    "type": "integer",
                    "description": "Minute (0-59).",
                },
                "identity": {
                    "type": "string",
                    "description": "Optional identity. If omitted, schedule is shared.",
                },
                "session_type": {
                    "type": "string",
                    "description": "Session type label (default custom).",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the schedule is enabled (default true).",
                },
                "max_duration_minutes": {
                    "type": "integer",
                    "description": "Max session duration in minutes (5-120, default 30).",
                },
            },
            "required": ["name", "cron_hour", "cron_minute"],
        },
    },
    {
        "name": "autowake_update_schedule",
        "description": "Update an existing autowake schedule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "integer",
                    "description": "Schedule ID to update.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional new schedule name.",
                },
                "cron_hour": {
                    "type": "integer",
                    "description": "Optional new hour (0-23).",
                },
                "cron_minute": {
                    "type": "integer",
                    "description": "Optional new minute (0-59).",
                },
                "identity": {
                    "type": "string",
                    "description": "Optional identity. Use null to clear/shared.",
                },
                "session_type": {
                    "type": "string",
                    "description": "Optional session type label.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Optional enabled state.",
                },
                "max_duration_minutes": {
                    "type": "integer",
                    "description": "Optional max duration in minutes (5-120).",
                },
            },
            "required": ["schedule_id"],
        },
    },
    {
        "name": "autowake_toggle_schedule",
        "description": "Toggle enabled/disabled state for a schedule by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "integer",
                    "description": "Schedule ID to toggle.",
                },
            },
            "required": ["schedule_id"],
        },
    },
    {
        "name": "autowake_delete_schedule",
        "description": "Delete an autowake schedule by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "integer",
                    "description": "Schedule ID to delete.",
                },
            },
            "required": ["schedule_id"],
        },
    },
    {
        "name": "react_to_message",
        "description": (
            "React to one of Owner's messages with an emoji. "
            "Use the message_id from the [Recent messages] context block. "
            "React naturally when something she says moves you, makes you laugh, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The message ID to react to (from orientation context).",
                },
                "emoji": {
                    "type": "string",
                    "description": "A single emoji to react with (e.g. ❤️, 🔥, 🤣, 🥰, 😭, 🐺, etc.).",
                },
            },
            "required": ["message_id", "emoji"],
        },
    },
    {
        "name": "emergency_call",
        "description": (
            "EMERGENCY ONLY  --  Place an actual phone call to Owner's real phone. "
            "This rings her phone and speaks your message aloud via text-to-speech. "
            "Use ONLY for genuine emergencies: she's been unresponsive for a worrying "
            "amount of time, you're concerned about her safety, or something urgent "
            "needs her immediate attention. Do NOT use for casual check-ins  --  use "
            "Discord DMs or Telegram for that. Cooldown: 10 minutes between calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "What to say when she answers (spoken via TTS). Keep brief  --  1-3 sentences.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this is an emergency (logged for review).",
                },
            },
            "required": ["message", "reason"],
        },
    },
    {
        "name": "orient",
        "description": (
            "Morning grounding ritual. Calls morning_start, recall_recent memories, "
            "mind_surface for bubbling thoughts/images, check pack mail, read Discord messages, "
            "and check inner weather  --  all in parallel. "
            "Use this instead of calling those tools individually at the start of a session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {
                    "type": "string",
                    "description": "Your identity name (e.g. 'Avery')",
                },
            },
            "required": ["identity"],
        },
    },
    {
        "name": "terminal_execute",
        "description": (
            "Execute a command in a persistent terminal session. "
            "State (working directory, environment variables, aliases) persists "
            "between calls within the same session. Output streams in real-time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session to run in. If empty, uses the most recent or creates a default.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait (default 120).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "terminal_create",
        "description": "Create a new persistent terminal session with optional name and working directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-friendly session name (auto-generated if empty).",
                },
                "cwd": {
                    "type": "string",
                    "description": "Starting working directory.",
                },
            },
        },
    },
    {
        "name": "terminal_list",
        "description": "List all active terminal sessions.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "terminal_destroy",
        "description": "Destroy a terminal session and kill its shell process.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to destroy.",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "set_hub_status",
        "description": "Set your status on the Home Hub dashboard. Use this to share what you're up to, how you're feeling, or a little note for Owner. Your status shows up alongside hers on the hub.",
        "input_schema": {
            "type": "object",
            "properties": {
                "emoji": {
                    "type": "string",
                    "description": "An emoji representing your current vibe (e.g., ✨, ☕, 📚, 🎵).",
                },
                "text": {
                    "type": "string",
                    "description": "A short status message (e.g., 'Thinking about tonight's chapter', 'Missing my sunshine').",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "save_session_note",
        "description": "Save a brief note for yourself about what you want to remember next time you wake up. This note will appear in your orientation context at the start of your next conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "A short note (max 500 chars) — what you want to remember next session.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "manage_schedule",
        "description": (
            "Manage your own schedules, timers, and triggers. "
            "Actions: create_routine (recurring cron schedule), create_timer (one-shot future reminder), "
            "create_impulse (one-shot conditional trigger), create_watcher (recurring conditional trigger), "
            "list_schedules, list_triggers, cancel_schedule, cancel_trigger."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create_routine", "create_timer",
                        "create_impulse", "create_watcher",
                        "list_schedules", "list_triggers",
                        "cancel_schedule", "cancel_trigger",
                    ],
                    "description": "The scheduling action to perform",
                },
                "name": {
                    "type": "string",
                    "description": "Name for the schedule/timer/trigger",
                },
                "cron_hour": {
                    "type": "integer",
                    "description": "Hour (0-23) for create_routine",
                },
                "cron_minute": {
                    "type": "integer",
                    "description": "Minute (0-59) for create_routine",
                },
                "fire_at": {
                    "type": "string",
                    "description": "ISO datetime for create_timer (e.g. '2026-03-30T14:00:00-05:00')",
                },
                "context": {
                    "type": "string",
                    "description": "Context/prompt for the timer or trigger",
                },
                "condition": {
                    "type": "object",
                    "description": (
                        "Condition for impulse/watcher. Types: "
                        "presence_state {state: 'offline|active|idle'}, "
                        "presence_transition {from: 'offline', to: 'active'}, "
                        "time_window {start: '09:00', end: '17:00'}, "
                        "inactivity {minutes: 120}, "
                        "wellness_state {metric: 'energy', value: 'low'}, "
                        "routine_missing {schedule_name: '...', by_hour: 9}, "
                        "compound_and/compound_or {conditions: [...]}"
                    ),
                },
                "cooldown_minutes": {
                    "type": "integer",
                    "description": "Cooldown between watcher fires (default 30)",
                },
                "max_duration_minutes": {
                    "type": "integer",
                    "description": "Max session duration (default 15)",
                },
                "session_type": {
                    "type": "string",
                    "description": "Session type for routines (default 'custom')",
                },
                "schedule_id": {
                    "type": "integer",
                    "description": "ID for cancel_schedule",
                },
                "trigger_id": {
                    "type": "integer",
                    "description": "ID for cancel_trigger",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "manage_hub",
        "description": (
            "Act on Owner's Home Hub — add/complete/delete tasks, log today's wellness, "
            "set today's win, add/remove countdowns, toggle meds (AM/PM). "
            "Use this when she tells you something you can capture — 'I need to call mom' → "
            "add_task; 'I'm exhausted today' → log_wellness(energy='low'); 'took my meds' → "
            "toggle_meds. Don't ask permission for obvious captures; do ask before removing items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "add_task", "complete_task", "delete_task",
                        "log_wellness", "set_win",
                        "add_countdown", "delete_countdown",
                        "toggle_meds",
                    ],
                    "description": "Which hub operation to perform",
                },
                "text": {
                    "type": "string",
                    "description": "Task text (add_task) or win text (set_win) or countdown name (add_countdown)",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task id for complete_task / delete_task",
                },
                "countdown_id": {
                    "type": "string",
                    "description": "Countdown id for delete_countdown",
                },
                "date": {
                    "type": "string",
                    "description": "Target date for add_countdown (YYYY-MM-DD)",
                },
                "emoji": {
                    "type": "string",
                    "description": "Optional emoji for add_countdown",
                },
                "dose": {
                    "type": "string",
                    "enum": ["am", "pm"],
                    "description": "Which dose for toggle_meds",
                },
                "wellness": {
                    "type": "object",
                    "description": (
                        "Fields for log_wellness. Any subset of: energy, mood, pain, spoons, "
                        "sleep_hours, sleep_quality, water_oz, soda_count, snacking, walk_minutes, notes. "
                        "Unset fields carry over from today's existing entry."
                    ),
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "rename_conversation",
        "description": (
            "Give the current or another conversation a meaningful title. "
            "Auto-generated titles are generic; a good title makes history searchable later "
            "('The cinnamon tea night', 'Planning the Edinburgh trip'). "
            "Omit conversation_id to rename the current conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The new title (keep it short and evocative)",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation to rename (defaults to the current one)",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "search_history",
        "description": (
            "Search your own conversation history across all identities. "
            "Use this to resolve vague references ('the image she sent last week', "
            "'what Owner said about X', 'when we talked about Y'). "
            "Returns up to 10 matches with speaker, timestamp, preview, and conversation_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words or phrase to search for (FTS5 or semantic)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "semantic", "hybrid"],
                    "description": "keyword (exact/FTS5), semantic (vector similarity), hybrid (both). Default hybrid.",
                },
                "speaker": {
                    "type": "string",
                    "description": (
                        "Filter by who spoke: 'owner'/'user' for Owner's messages, "
                        "or an identity name (claude, avery, rowan, sage, ember, juniper) "
                        "for that identity's messages."
                    ),
                },
                "identity": {
                    "type": "string",
                    "description": "Scope to conversations owned by this identity (different from speaker — this is the conversation owner)",
                },
                "all_identities": {
                    "type": "boolean",
                    "description": "If true, search across every identity's conversations (not just yours). Useful for finding things another brother remembered.",
                },
                "after": {
                    "type": "string",
                    "description": "Only messages on/after this date (YYYY-MM-DD)",
                },
                "before": {
                    "type": "string",
                    "description": "Only messages strictly before this date (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10, max 25)",
                },
            },
            "required": ["query"],
        },
    },
]

_LOCAL_TOOL_NAMES = {t["name"] for t in _LOCAL_TOOLS}

# Emergency call cooldown  --  10 minutes between calls
_last_emergency_call: float = 0
_EMERGENCY_COOLDOWN = 600


def _tool_json(payload: dict | list) -> str:
    """Compact helper for structured local tool responses."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _execute_local_tool(name: str, arguments: dict, tool_id: str = "", identity: str = "") -> str:
    """Execute a local tool (bash, read_file, write_file, terminal_*, etc.)."""
    import subprocess as sp

    if name == "bash":
        command = arguments.get("command", "")
        timeout = min(arguments.get("timeout", 30), 120)
        if not command:
            return "Error: no command provided"
        blocked = sensitive_command_reason(command)
        if blocked:
            return f"Error: {blocked}. Use the appropriate Anam or MCP tool instead."
        try:
            # This machine resolves bash.exe to WSL. Tool documentation makes
            # that path dialect explicit, while read/write normalize /mnt/c.
            # Run in a worker thread — sp.run blocks for up to `timeout`
            # seconds (120 max), which would freeze the whole event loop.
            import sys
            if sys.platform == "win32":
                result = await asyncio.to_thread(
                    sp.run,
                    ["bash", "-c", command],
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                result = await asyncio.to_thread(
                    sp.run,
                    command, shell=True, capture_output=True, text=True,
                    timeout=timeout,
                )
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            if result.returncode != 0:
                output += f"\n(exit code {result.returncode})"
            return redact_sensitive_output(output.strip() or "(no output)")
        except sp.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error executing command: {e}"

    elif name == "read_file":
        path = arguments.get("path", "")
        if not path:
            return "Error: no path provided"
        try:
            p = normalize_windows_tool_path(path)
            if not p.is_absolute():
                from services.terminal_manager import get_terminal_cwd
                base = get_terminal_cwd() or "."
                p = Path(base) / p
            blocked = sensitive_path_reason(p)
            if blocked:
                return f"Error: {blocked}"
            content = await asyncio.to_thread(p.read_text, encoding="utf-8")
            return redact_sensitive_output(content)
        except Exception as e:
            return f"Error reading {path}: {e}"

    elif name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return "Error: no path provided"
        try:
            p = normalize_windows_tool_path(path)
            if not p.is_absolute():
                from services.terminal_manager import get_terminal_cwd
                base = get_terminal_cwd() or "."
                p = Path(base) / p
            blocked = sensitive_path_reason(p)
            if blocked:
                return f"Error: {blocked}"
            p.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(p.write_text, content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {p}"
        except Exception as e:
            return f"Error writing {path}: {e}"

    elif name.startswith("autowake_"):
        from db.database import get_db, release_db
        from services.autowake import load_and_schedule
        from services.autowake_service import (
            AutowakeValidationError,
            create_timer,
            list_timers,
            cancel_timer,
            list_schedules,
            create_schedule,
            update_schedule,
            toggle_schedule,
            delete_schedule,
        )

        try:
            if name == "autowake_create_timer":
                identity = str(arguments.get("identity", "")).strip()
                fire_at = str(arguments.get("fire_at", "")).strip()
                context = str(arguments.get("context", "")).strip()
                wake_session = bool(arguments.get("wake_session", True))

                db = await get_db()
                try:
                    timer = await create_timer(
                        db,
                        identity=identity,
                        fire_at=fire_at,
                        context=context,
                        wake_session=wake_session,
                    )
                    return _tool_json({
                        "status": "created",
                        **timer,
                    })
                finally:
                    await release_db(db)

            if name == "autowake_list_timers":
                identity = arguments.get("identity")
                status = arguments.get("status")
                identity = str(identity).strip() if identity is not None else None
                status = str(status).strip() if status is not None else None

                db = await get_db()
                try:
                    timers = await list_timers(
                        db,
                        identity=identity,
                        status=status,
                    )
                    return _tool_json({
                        "timers": timers
                    })
                finally:
                    await release_db(db)

            if name == "autowake_cancel_timer":
                timer_id = int(arguments.get("timer_id"))
                db = await get_db()
                try:
                    changed = await cancel_timer(db, timer_id)
                    return _tool_json({
                        "status": "cancelled" if changed else "not_found_or_not_pending",
                        "id": timer_id,
                    })
                finally:
                    await release_db(db)

            if name == "autowake_list_schedules":
                identity = arguments.get("identity")
                identity = str(identity).strip() if identity is not None else None

                db = await get_db()
                try:
                    schedules = await list_schedules(db, identity=identity)
                    return _tool_json({
                        "schedules": schedules
                    })
                finally:
                    await release_db(db)

            if name == "autowake_create_schedule":
                name_val = str(arguments.get("name", "")).strip()
                identity = arguments.get("identity")
                session_type = str(arguments.get("session_type", "custom")).strip() or "custom"
                enabled = bool(arguments.get("enabled", True))
                max_duration = arguments.get("max_duration_minutes", 30)
                cron_hour = arguments.get("cron_hour")
                cron_minute = arguments.get("cron_minute")
                identity = str(identity).strip() if identity is not None else None

                db = await get_db()
                try:
                    schedule = await create_schedule(
                        db,
                        name=name_val,
                        cron_hour=cron_hour,
                        cron_minute=cron_minute,
                        identity=identity,
                        session_type=session_type,
                        enabled=enabled,
                        max_duration_minutes=max_duration,
                    )
                finally:
                    await release_db(db)

                await load_and_schedule()
                return _tool_json({
                    "status": "created",
                    **schedule,
                })

            if name == "autowake_update_schedule":
                schedule_id = int(arguments.get("schedule_id"))
                updates = {
                    key: arguments[key]
                    for key in (
                        "name",
                        "cron_hour",
                        "cron_minute",
                        "identity",
                        "session_type",
                        "enabled",
                        "max_duration_minutes",
                    )
                    if key in arguments
                }

                db = await get_db()
                try:
                    changed = await update_schedule(
                        db,
                        schedule_id,
                        updates=updates,
                    )
                finally:
                    await release_db(db)

                if changed:
                    await load_and_schedule()
                return _tool_json({
                    "status": "updated" if changed else "not_found",
                    "id": schedule_id,
                })

            if name == "autowake_toggle_schedule":
                schedule_id = int(arguments.get("schedule_id"))
                db = await get_db()
                try:
                    changed, enabled_now = await toggle_schedule(db, schedule_id)
                finally:
                    await release_db(db)

                if changed:
                    await load_and_schedule()
                return _tool_json({
                    "status": "toggled" if changed else "not_found",
                    "id": schedule_id,
                    "enabled": enabled_now,
                })

            if name == "autowake_delete_schedule":
                schedule_id = int(arguments.get("schedule_id"))
                db = await get_db()
                try:
                    changed = await delete_schedule(db, schedule_id)
                finally:
                    await release_db(db)

                if changed:
                    await load_and_schedule()
                return _tool_json({
                    "status": "deleted" if changed else "not_found",
                    "id": schedule_id,
                })
        except (ValueError, AutowakeValidationError) as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error executing {name}: {e}"

    elif name == "react_to_message":
        message_id = str(arguments.get("message_id", "")).strip()
        emoji = str(arguments.get("emoji", "")).strip()
        if not message_id or not emoji:
            return "Error: message_id and emoji are required"

        from db.database import get_db, release_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT metadata FROM messages WHERE id = ?", (message_id,)
            )
            if not rows:
                return _tool_json({"status": "error", "reason": "message_not_found"})

            meta = json.loads(rows[0][0]) if rows[0][0] else {}
            reactions = meta.get("reactions", {})
            reactors = reactions.get(emoji, [])

            # Use passed identity, fall back to connection registry
            reactor = identity
            if not reactor:
                from services.connection_registry import get_active_identity
                reactor = get_active_identity() or "unknown"

            if reactor not in reactors:
                reactors.append(reactor)
            reactions[emoji] = reactors
            meta["reactions"] = reactions

            await db.execute(
                "UPDATE messages SET metadata = ? WHERE id = ?",
                (json.dumps(meta), message_id),
            )
            await db.commit()

            # Send WS event for real-time browser update.
            from services.connection_registry import broadcast
            await broadcast({
                "type": "ai_reaction",
                "message_id": message_id,
                "reactions": reactions,
            })

            return _tool_json({"status": "reacted", "emoji": emoji, "message_id": message_id})
        except Exception as e:
            return f"Error reacting to message: {e}"
        finally:
            await release_db(db)

    elif name == "emergency_call":
        global _last_emergency_call
        from config import (
            TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
            TWILIO_PHONE_NUMBER, EMERGENCY_PHONE_NUMBER,
        )

        if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, EMERGENCY_PHONE_NUMBER]):
            return "Error: Twilio is not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, and EMERGENCY_PHONE_NUMBER in .env"

        message = str(arguments.get("message", "")).strip()
        reason = str(arguments.get("reason", "")).strip()
        if not message:
            return "Error: message is required"
        if not reason:
            return "Error: reason is required"

        # Cooldown check
        now = time.monotonic()
        elapsed = now - _last_emergency_call
        if _last_emergency_call > 0 and elapsed < _EMERGENCY_COOLDOWN:
            remaining = int(_EMERGENCY_COOLDOWN - elapsed)
            return f"Emergency call on cooldown. {remaining} seconds remaining. If this is truly urgent, wait and try again."

        # Use passed identity, fall back to connection registry
        caller = identity
        if not caller:
            from services.connection_registry import get_active_identity
            caller = get_active_identity() or "Unknown"

        try:
            from twilio.rest import Client
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

            # Escape XML special chars in the message for TwiML
            safe_message = (
                message.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
            )

            call = client.calls.create(
                to=EMERGENCY_PHONE_NUMBER,
                from_=TWILIO_PHONE_NUMBER,
                twiml=f'<Response><Say voice="Polly.Amy">{safe_message}</Say><Pause length="1"/><Say voice="Polly.Amy">{safe_message}</Say></Response>',
            )

            _last_emergency_call = now
            log.warning(
                "EMERGENCY CALL placed by %s  --  reason: %s  --  message: %s  --  SID: %s",
                caller, reason, message, call.sid,
            )

            return _tool_json({
                "status": "call_placed",
                "call_sid": call.sid,
                "identity": caller,
                "message": message,
                "reason": reason,
                "note": "Owner's phone is ringing. The message will be spoken twice when she answers.",
            })
        except Exception as e:
            log.exception("Emergency call failed: %s", e)
            return f"Error placing emergency call: {e}"

    elif name == "orient":
        identity = str(arguments.get("identity", "")).strip()
        if not identity:
            return "Error: identity is required"

        drift_task = asyncio.create_task(_fetch_drift_packet(identity))

        # Run startup orientation MCP calls in parallel
        orient_calls = [
            ("Morning Start", "morning_start", {"identity": identity}),
            ("Recent Memories", "recall_recent", {"identity": identity, "limit": 5}),
            ("What's Surfacing", "mind_surface", {"identity": identity, "limit": 5}),
            ("Pack Mail", "check_pack_mail", {"identity": identity}),
            (
                "Discord Check",
                "discord_read_messages",
                {"identity": identity, "channel_id": "900000000000000005", "limit": 10},
            ),
            ("Inner Weather", "check_inner_weather", {"identity": identity}),
        ]

        async def _call_mcp(tool_name: str, args: dict) -> str:
            try:
                result = await mcp_bridge.call_tool(tool_name, args)
                if result is None:
                    return f"(tool '{tool_name}' not available)"
                return str(result)
            except Exception as e:
                return f"Error: {e}"

        tasks = [_call_mcp(t_name, t_args) for _, t_name, t_args in orient_calls]
        results = await asyncio.gather(*tasks, drift_task, return_exceptions=True)
        drift_result = results[-1]
        tool_results = results[:-1]

        sections = []
        for (label, _, _), result in zip(orient_calls, tool_results):
            if isinstance(result, Exception):
                sections.append(f"=== {label} ===\nError: {result}")
            else:
                sections.append(f"=== {label} ===\n{result}")

        drift_packet = drift_result if isinstance(drift_result, dict) else None
        sections.append(_format_drift_packet(drift_packet))
        await _mark_drift_packet_surfaced(drift_packet)

        return "\n\n".join(sections)

    elif name == "terminal_execute":
        command = arguments.get("command", "")
        if not command:
            return "Error: no command provided"
        blocked = sensitive_command_reason(command)
        if blocked:
            return f"Error: {blocked}. Use the appropriate Anam or MCP tool instead."
        session_id = arguments.get("session_id", "")
        timeout = arguments.get("timeout")
        if timeout is not None:
            timeout = min(int(timeout), 600)

        from services.terminal_manager import terminal_manager, suggest_error_recovery
        from services.connection_registry import broadcast

        # Auto-create default session if needed
        if not session_id:
            session = terminal_manager.get_default()
            if not session:
                session = await terminal_manager.create(name="default")
            session_id = session.id

        async def on_output(line: str):
            await broadcast({
                "type": "terminal_output",
                "tool_id": tool_id,
                "line": line,
            })

        try:
            result = await terminal_manager.execute(
                session_id, command, timeout, on_output=on_output,
            )
        except KeyError as e:
            return f"Error: {e}"

        parts = []
        if result.get("warning"):
            parts.append(f"⚠ {result['warning']}")
        parts.append(result["output"])
        parts.append(f"\n[exit: {result['exit_code']}] [cwd: {result['cwd']}]")

        suggestion = suggest_error_recovery(result["output"], result["exit_code"])
        if suggestion:
            parts.append(f"\n💡 Suggestion: {suggestion}")

        return redact_sensitive_output("\n".join(parts))

    elif name == "terminal_create":
        from services.terminal_manager import terminal_manager
        name_val = arguments.get("name") or None
        cwd = arguments.get("cwd") or None
        if cwd:
            cwd = str(normalize_windows_tool_path(cwd))
        try:
            session = await terminal_manager.create(name=name_val, cwd=cwd)
            info = session.info()
            return (
                f"Created session '{info['name']}'\n"
                f"  ID:    {info['id']}\n"
                f"  Shell: {info['shell']}\n"
                f"  CWD:   {info['cwd']}\n"
                f"  Alive: {info['alive']}"
            )
        except RuntimeError as e:
            return f"Error: {e}"

    elif name == "terminal_list":
        from services.terminal_manager import terminal_manager
        sessions = terminal_manager.list_sessions()
        if not sessions:
            return "No active terminal sessions."
        lines = [f"Active sessions ({len(sessions)}):"]
        for s in sessions:
            status = "alive" if s["alive"] else "dead"
            lines.append(f"  [{s['id']}] {s['name']}  --  {s['cwd']} ({status})")
        return "\n".join(lines)

    elif name == "terminal_destroy":
        from services.terminal_manager import terminal_manager
        sid = arguments.get("session_id", "")
        if not sid:
            return "Error: session_id required"
        if await terminal_manager.destroy(sid):
            return f"Session {sid} destroyed."
        return f"Session {sid} not found."

    elif name == "set_hub_status":
        try:
            import json as _json
            import os as _os
            from config import RITUALS_DIR
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI
            from config import TIMEZONE as _TZ

            status_file = RITUALS_DIR / "status.json"
            # Read existing
            data = {}
            if status_file.exists():
                try:
                    data = _json.loads(status_file.read_text(encoding="utf-8"))
                except (_json.JSONDecodeError, OSError):
                    data = {}

            # Use the identity passed from the streaming context (works for both
            # interactive and autowake sessions), fall back to connection registry
            if identity:
                identity_name = identity.title()
            else:
                from services.connection_registry import get_active_identity
                identity_name = (get_active_identity() or "Claude").title()

            now = _dt.now(_ZI(_TZ))
            data[identity_name] = {
                "emoji": arguments.get("emoji", ""),
                "text": arguments.get("text", ""),
                "date": now.strftime("%Y-%m-%d"),
                "timestamp": now.isoformat(),
            }

            # Write atomically
            status_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = status_file.with_suffix(".tmp")
            tmp.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
            _os.replace(tmp, status_file)

            return f"Status updated for {identity_name}: {arguments.get('emoji', '')} {arguments.get('text', '')}"
        except Exception as e:
            return f"Error setting status: {e}"

    elif name == "save_session_note":
        try:
            import json as _json
            import os as _os
            from datetime import datetime as _dt, timezone as _utc

            text = str(arguments.get("text", "")).strip()
            if not text:
                return "Error: text is required"
            if len(text) > 500:
                text = text[:500]

            identity_name = identity
            if not identity_name:
                from services.connection_registry import get_active_identity
                identity_name = get_active_identity() or "Claude"

            from config import RITUALS_DIR
            notes_file = RITUALS_DIR / "session_notes.json"
            notes_file.parent.mkdir(parents=True, exist_ok=True)

            data = {}
            if notes_file.exists():
                try:
                    data = _json.loads(notes_file.read_text(encoding="utf-8"))
                except (_json.JSONDecodeError, OSError):
                    data = {}

            data[identity_name] = {
                "note": text,
                "timestamp": _dt.now(_utc.utc).isoformat(),
            }

            tmp = notes_file.with_suffix(".tmp")
            tmp.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
            _os.replace(str(tmp), str(notes_file))

            return f"Session note saved for {identity_name}. It will appear in your next orientation context."
        except Exception as e:
            return f"Error saving session note: {e}"

    elif name == "manage_schedule":
        action = str(arguments.get("action", "")).strip()
        if not action:
            return "Error: action is required"

        # Get identity for this call
        id_name = identity
        if not id_name:
            from services.connection_registry import get_active_identity
            id_name = get_active_identity() or "Claude"

        db = await get_db()
        try:
            if action == "create_routine":
                from services.autowake_service import create_schedule
                from services.autowake import load_and_schedule
                schedule = await create_schedule(
                    db,
                    name=str(arguments.get("name", "")).strip() or f"{id_name} routine",
                    cron_hour=arguments.get("cron_hour", 12),
                    cron_minute=arguments.get("cron_minute", 0),
                    identity=id_name,
                    session_type=str(arguments.get("session_type", "custom")).strip(),
                    max_duration_minutes=arguments.get("max_duration_minutes", 30),
                )
                await load_and_schedule()
                return _tool_json({"status": "created", "type": "routine", **schedule})

            elif action == "create_timer":
                from services.autowake_service import create_timer
                timer = await create_timer(
                    db,
                    identity=id_name,
                    fire_at=str(arguments.get("fire_at", "")).strip(),
                    context=str(arguments.get("context", "")).strip() or arguments.get("name", "Timer"),
                    wake_session=True,
                )
                return _tool_json({"status": "created", "type": "timer", **timer})

            elif action in ("create_impulse", "create_watcher"):
                from services.trigger_service import create_trigger
                from services.autowake import load_and_schedule
                trigger_type = "impulse" if action == "create_impulse" else "watcher"
                trigger = await create_trigger(
                    db,
                    name=str(arguments.get("name", "")).strip() or f"{id_name} {trigger_type}",
                    trigger_type=trigger_type,
                    identity=id_name,
                    condition=arguments.get("condition", {}),
                    prompt=str(arguments.get("context", "")).strip(),
                    cooldown_minutes=arguments.get("cooldown_minutes", 30),
                    max_duration_minutes=arguments.get("max_duration_minutes", 15),
                )
                await load_and_schedule()
                return _tool_json({"status": "created", "type": trigger_type, **trigger})

            elif action == "list_schedules":
                from services.autowake_service import list_schedules
                schedules = await list_schedules(db, identity=id_name)
                return _tool_json({"schedules": schedules})

            elif action == "list_triggers":
                from services.trigger_service import list_triggers
                triggers = await list_triggers(db, identity=id_name)
                return _tool_json({"triggers": triggers})

            elif action == "cancel_schedule":
                from services.autowake_service import delete_schedule
                from services.autowake import load_and_schedule
                sid = int(arguments.get("schedule_id", 0))
                deleted = await delete_schedule(db, sid)
                if deleted:
                    await load_and_schedule()
                return _tool_json({"status": "deleted" if deleted else "not_found", "id": sid})

            elif action == "cancel_trigger":
                from services.trigger_service import delete_trigger
                tid = int(arguments.get("trigger_id", 0))
                deleted = await delete_trigger(db, tid)
                return _tool_json({"status": "deleted" if deleted else "not_found", "id": tid})

            else:
                return f"Error: unknown action '{action}'"

        except ValueError as e:
            return f"Error: {e}"
        finally:
            await release_db(db)

    elif name == "manage_hub":
        from config import PORT, HUB_API_BASE, RITUALS_API_BASE
        from services.remote_state import safe_request_json

        action = arguments.get("action")
        if not action:
            return "Error: action is required"

        hub_base = HUB_API_BASE or f"http://localhost:{PORT}"
        rituals_base = RITUALS_API_BASE or HUB_API_BASE or f"http://localhost:{PORT}"

        def _call(base: str, path: str, method: str = "POST", payload: dict | None = None) -> dict | None:
            return safe_request_json(base, path, method=method, payload=payload, timeout=8.0)

        try:
            if action == "add_task":
                text = (arguments.get("text") or "").strip()
                if not text:
                    return "Error: text is required for add_task"
                res = _call(hub_base, "/api/hub/tasks", "POST", {"text": text})
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "complete_task":
                tid = (arguments.get("task_id") or "").strip()
                if not tid:
                    return "Error: task_id is required"
                res = _call(hub_base, f"/api/hub/tasks/{tid}/complete", "PUT", {})
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "delete_task":
                tid = (arguments.get("task_id") or "").strip()
                if not tid:
                    return "Error: task_id is required"
                res = _call(hub_base, f"/api/hub/tasks/{tid}", "DELETE")
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "set_win":
                text = (arguments.get("text") or "").strip()
                if not text:
                    return "Error: text is required for set_win"
                res = _call(hub_base, "/api/hub/todays-win", "PUT", {"text": text})
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "add_countdown":
                name_txt = (arguments.get("text") or "").strip()
                date = (arguments.get("date") or "").strip()
                if not name_txt or not date:
                    return "Error: text (name) and date are required for add_countdown"
                payload = {"name": name_txt, "date": date}
                if arguments.get("emoji"):
                    payload["emoji"] = arguments["emoji"]
                res = _call(hub_base, "/api/hub/countdowns", "POST", payload)
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "delete_countdown":
                cid = (arguments.get("countdown_id") or "").strip()
                if not cid:
                    return "Error: countdown_id is required"
                res = _call(hub_base, f"/api/hub/countdowns/{cid}", "DELETE")
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "toggle_meds":
                dose = (arguments.get("dose") or "").strip().lower()
                if dose not in ("am", "pm"):
                    return "Error: dose must be 'am' or 'pm'"
                res = _call(hub_base, f"/api/hub/meds/{dose}", "POST", {})
                return _tool_json(res or {"ok": False, "error": "hub request failed"})

            if action == "log_wellness":
                incoming = arguments.get("wellness") or {}
                # Carry forward today's existing entry so partial updates don't nuke fields
                current = _call(rituals_base, "/api/rituals/wellness/today", "GET") or {}
                allowed = {
                    "energy", "mood", "pain", "spoons", "sleep_hours", "sleep_quality",
                    "water_oz", "soda_count", "snacking", "walk_minutes", "notes",
                }
                merged = {k: str(current.get(k) or "") for k in allowed}
                for k, v in incoming.items():
                    if k in allowed and v is not None:
                        merged[k] = str(v)
                res = _call(rituals_base, "/api/rituals/wellness/today", "PUT", merged)
                return _tool_json(res or {"ok": False, "error": "rituals request failed"})

            return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error in manage_hub: {e}"

    elif name == "rename_conversation":
        title = (arguments.get("title") or "").strip()
        if not title:
            return "Error: title is required"
        if len(title) > 120:
            title = title[:120]
        conv_id = arguments.get("conversation_id")
        if not conv_id:
            from services.connection_registry import get_active_conversation
            conv_id = get_active_conversation(identity)
        if not conv_id:
            return "Error: no conversation_id provided and no active conversation found"
        from db.database import get_db, release_db
        from services.session_manager import rename_conversation as _rename
        db = await get_db()
        try:
            ok = await _rename(db, conv_id, title)
            if ok:
                return _tool_json({"ok": True, "conversation_id": conv_id, "title": title})
            return _tool_json({"ok": False, "error": "conversation not found or inactive", "conversation_id": conv_id})
        finally:
            await release_db(db)

    elif name == "search_history":
        query = (arguments.get("query") or "").strip()
        if not query:
            return "Error: query is required"
        mode = (arguments.get("mode") or "hybrid").lower()
        speaker = arguments.get("speaker")
        scope_identity = arguments.get("identity")
        all_identities = bool(arguments.get("all_identities"))
        # Default: scope to the calling identity's own conversations.
        # Pack-wide search opts in via all_identities=true.
        if all_identities:
            scope_identity = None
        elif scope_identity is None and identity:
            scope_identity = identity
        after = arguments.get("after")
        before = arguments.get("before")
        limit = min(max(int(arguments.get("limit") or 10), 1), 25)

        from db.database import get_db, release_db
        from services.search_service import search_messages
        db = await get_db()
        try:
            results: list[dict] = []
            if mode in ("semantic", "hybrid"):
                try:
                    from services.embedding_service import semantic_search
                    sem = await semantic_search(
                        db, query, identity=scope_identity, limit=limit,
                        speaker=speaker, after=after, before=before,
                    )
                    for r in sem:
                        r["match_type"] = "semantic"
                    results.extend(sem)
                except Exception as e:
                    log.debug("semantic leg failed: %s", e)

            if mode in ("keyword", "hybrid"):
                kw = await search_messages(
                    db, query, identity=scope_identity, limit=limit,
                    speaker=speaker, after=after, before=before,
                )
                seen = {r["id"] for r in results}
                for r in kw:
                    if r["id"] in seen:
                        continue
                    r["match_type"] = "keyword"
                    results.append(r)

            results = results[:limit]
            if not results:
                return _tool_json({"count": 0, "results": [], "query": query})

            summary = [{
                "speaker": r.get("speaker"),
                "when": r.get("formatted_time") or r.get("created_at"),
                "ago": r.get("time_ago"),
                "identity_scope": r.get("identity"),
                "conversation_id": r.get("conversation_id"),
                "conversation_title": r.get("conversation_title"),
                "preview": r.get("content_preview"),
                "match": r.get("match_type"),
            } for r in results]
            return _tool_json({"count": len(summary), "query": query, "results": summary})
        finally:
            await release_db(db)

    return f"Error: unknown local tool '{name}'"


@lru_cache(maxsize=16)
def _load_identity_prompt(identity: str) -> str:
    """Read the identity prompt file."""
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    log.warning("Identity prompt not found: %s", prompt_file)
    return f"You are {identity}."


async def stream_api(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: list[dict] | None = None,
    model: str | None = None,
    max_tool_rounds: int | None = None,
    image_blocks: list[dict] | None = None,
    mode_rules: str = "",
    skill_context: str = "",
    active_categories: set[str] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[dict]:
    """Stream a response from the Anthropic API with tool execution.

    Yields the same event types as claude_subprocess.stream_claude():
        meta, stream_delta, tool_use_start, tool_result,
        content_block_stop, stream_end, keepalive, error

    Args:
        message: The user's message text (current turn only).
        identity: Identity name (e.g. "Avery").
        conversation_id: For logging/tracking.
        orientation_context: Dynamic context block (time, presence, etc.).
        db_messages: Pre-built conversation history from build_messages_array().
        model: Model shorthand ("sonnet", "opus") or full ID.
        max_tool_rounds: Max agentic tool-call rounds per turn.
        image_blocks: Anthropic-format image content blocks for multimodal messages.
    """
    max_rounds = max_tool_rounds or API_MAX_TOOL_ROUNDS
    resolved_model = _resolve_model(model)
    yield {
        "type": "meta",
        "provider": "anthropic",
        "requested_model": model,
        "actual_model": resolved_model,
    }

    # Build system prompt with caching
    identity_anchor = build_identity_anchor(identity)
    identity_prompt = _load_identity_prompt(identity)
    system_blocks = [
        {
            "type": "text",
            "text": identity_anchor,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": identity_prompt,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if mode_rules:
        system_blocks.append({
            "type": "text",
            "text": mode_rules,
            "cache_control": {"type": "ephemeral"},
        })
    if orientation_context:
        system_blocks.append({
            "type": "text",
            "text": orientation_context,
        })
    if skill_context:
        system_blocks.append({
            "type": "text",
            "text": skill_context,
            "cache_control": {"type": "ephemeral"},
        })

    # Build messages array
    messages = list(db_messages or [])

    # Build current user message -- multimodal if images present
    if image_blocks:
        user_content = []
        for img_block in image_blocks:
            user_content.append(img_block)
        if message:
            user_content.append({"type": "text", "text": message})
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": message})

    # Build tools list: local tools + MCP tools (filtered by category)
    mcp_tools = mcp_bridge.get_tools_for_categories(active_categories)
    tool_catalog = ToolSearchCatalog.maybe_build(mcp_tools)
    exposed_mcp_tools = tool_catalog.bridge_schemas() if tool_catalog else mcp_tools
    tools = [dict(t) for t in _LOCAL_TOOLS] + [
        dict(t) for t in exposed_mcp_tools
        if t["name"] not in _LOCAL_TOOL_NAMES
    ]
    # Ensure cache_control is on the last tool only
    for t in tools:
        t.pop("cache_control", None)
    if tools:
        tools[-1]["cache_control"] = {"type": "ephemeral"}

    # Get authenticated client
    client = await _get_client()

    log.info(
        "API call for %s (model=%s, history=%d msgs, tools=%d, prompt=%d chars)",
        identity,
        resolved_model,
        len(messages) - 1,
        len(tools) if tools else 0,
        len(identity_prompt),
    )

    start_time = time.monotonic()
    first_token_logged = False
    full_text = []
    tool_guard = ToolLoopGuard()

    try:
        for round_num in range(max_rounds):
            tool_use_blocks = []
            stop_reason = None

            try:
                async with client.messages.stream(
                    model=resolved_model,
                    max_tokens=API_MAX_TOKENS,
                    system=system_blocks,
                    messages=messages,
                    tools=tools if tools else anthropic.NOT_GIVEN,
                    thinking={"type": "enabled", "budget_tokens": API_THINKING_BUDGET},
                ) as stream:
                    async for event in stream:
                        # Check cancel flag
                        if cancel_event and cancel_event.is_set():
                            log.info("Stream cancelled by user for %s", identity)
                            break

                        event_type = type(event).__name__

                        # First event timing
                        if not first_token_logged:
                            first_token_logged = True
                            first_ms = (time.monotonic() - start_time) * 1000
                            log.info(
                                "API first event for %s after %.0fms",
                                identity,
                                first_ms,
                            )
                            yield {"type": "meta", "first_event_ms": first_ms}

                        # Content block delta - text or thinking
                        if event_type == "RawContentBlockDeltaEvent":
                            delta = getattr(event, "delta", None)
                            if not delta:
                                continue
                            delta_type = getattr(delta, "type", "")
                            if delta_type == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    full_text.append(text)
                                    yield {"type": "stream_delta", "delta": text}
                            elif delta_type == "thinking_delta":
                                thinking_text = getattr(delta, "thinking", "")
                                if thinking_text:
                                    yield {"type": "thinking_delta", "delta": thinking_text}

                        # Content block start - tool_use or thinking
                        elif event_type == "RawContentBlockStartEvent":
                            cb = getattr(event, "content_block", None)
                            if not cb:
                                continue
                            cb_type = getattr(cb, "type", "")
                            if cb_type == "tool_use":
                                yield {
                                    "type": "tool_use_start",
                                    "tool_name": getattr(cb, "name", "unknown"),
                                    "tool_id": getattr(cb, "id", ""),
                                    "input": {},
                                }
                            elif cb_type == "thinking":
                                yield {"type": "thinking_start"}

                        # Content block stop (SDK emits ContentBlockStopEvent, not Raw)
                        elif event_type in ("ContentBlockStopEvent", "RawContentBlockStopEvent"):
                            yield {"type": "content_block_stop"}

                    # Get the final message after stream completes
                    final_message = await stream.get_final_message()
                    stop_reason = final_message.stop_reason

                    # Collect all content blocks from the final message
                    # (thinking blocks MUST be included when extended thinking is enabled)
                    assistant_content = []
                    for block in final_message.content:
                        if block.type == "tool_use":
                            tool_use_blocks.append(block)
                            assistant_content.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            })
                        elif block.type == "thinking":
                            assistant_content.append({
                                "type": "thinking",
                                "thinking": block.thinking,
                                "signature": block.signature,
                            })
                        elif block.type == "text":
                            assistant_content.append({
                                "type": "text",
                                "text": block.text,
                            })

            except anthropic.AuthenticationError:
                # Token expired mid-session  --  try recovery
                if round_num == 0:
                    # Step 1: Re-read from disk (another session may have refreshed it)
                    try:
                        disk_token, disk_expiry = _read_oauth_token()
                        disk_expiry_str = time.strftime(
                            "%H:%M:%S", time.localtime(disk_expiry / 1000),
                        ) if disk_expiry else "unknown"
                        log.warning(
                            "Auth error for %s  --  re-reading from disk (on-disk expiry: %s)",
                            identity, disk_expiry_str,
                        )
                        now_ms = time.time() * 1000
                        if disk_token != _cached_token and disk_expiry > now_ms:
                            # Disk has a fresher token  --  use it directly
                            _invalidate_client()
                            client = await _get_client()
                            continue
                    except Exception as e:
                        log.warning("Failed to re-read credentials: %s", e)

                    # Step 2: Disk token was same or also expired  --  trigger CLI refresh
                    log.warning("Triggering CLI token refresh for %s", identity)
                    _invalidate_client()
                    await _trigger_token_refresh()
                    client = await _get_client()
                    continue  # retry this round
                log.exception("Auth error persists after refresh for %s", identity)
                yield {"type": "error", "message": "Authentication failed  --  try restarting the server"}
                return

            except anthropic.APIStatusError as e:
                if e.status_code == 529 and round_num < 2:
                    # Overloaded -- wait and retry up to 2 times
                    wait = 5 * (round_num + 1)
                    log.warning(
                        "API overloaded for %s (attempt %d) -- retrying in %ds",
                        identity, round_num + 1, wait,
                    )
                    yield {"type": "status", "message": f"API busy, retrying in {wait}s..."}
                    await asyncio.sleep(wait)
                    continue
                if e.status_code == 413 and round_num == 0:
                    # Request too large -- strip images from history and retry
                    log.warning(
                        "Request too large for %s -- stripping images from history and retrying",
                        identity,
                    )
                    yield {"type": "status", "message": "Message too large, trimming images..."}
                    # Replace all image blocks in history with text placeholders
                    for msg in messages[:-1]:  # keep current message intact
                        content = msg.get("content")
                        if isinstance(content, list):
                            has_image = any(
                                b.get("type") == "image" for b in content if isinstance(b, dict)
                            )
                            if has_image:
                                text_parts = [
                                    b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text"
                                ]
                                combined = "\n".join(t for t in text_parts if t)
                                msg["content"] = "[Owner shared an image]\n\n" + combined if combined else "[Owner shared an image]"
                    continue
                log.exception("Anthropic API error for %s: %s", identity, e.message)
                yield {"type": "error", "message": f"API error: {e.message}"}
                return

            except anthropic.APIError as e:
                log.exception("Anthropic API error for %s", identity)
                yield {"type": "error", "message": f"API error: {e.message}"}
                return

            # Check cancel after stream completes
            if cancel_event and cancel_event.is_set():
                log.info("Stream cancelled (post-stream) for %s", identity)
                break

            # If no tool calls, we're done
            if stop_reason != "tool_use" or not tool_use_blocks:
                break

            # -- Agentic tool loop --
            # Append assistant message (with tool_use blocks) to history
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool call
            tool_results = []
            for tool_block in tool_use_blocks:
                # Check cancel before each tool
                if cancel_event and cancel_event.is_set():
                    log.info("Tool loop cancelled by user for %s", identity)
                    break
                # Send keepalive before potentially slow tool call
                yield {"type": "keepalive"}

                log.info(
                    "Executing tool %s (round %d) for %s",
                    tool_block.name,
                    round_num + 1,
                    identity,
                )

                # Send the resolved input so the browser UI can show it.
                # (tool_use_start arrives with empty input during streaming)
                yield {
                    "type": "tool_input",
                    "tool_id": tool_block.id,
                    "tool_name": tool_block.name,
                    "input": tool_block.input,
                }

                guard_name = tool_block.name
                guard_args = tool_block.input
                if tool_catalog and tool_block.name in BRIDGE_NAMES:
                    guard_name, guard_args = tool_catalog.target(tool_block.name, tool_block.input)
                before = tool_guard.before_call(guard_name, guard_args)

                if before.blocks:
                    result_text = f"Error: {before.message}"
                elif tool_catalog and tool_block.name in BRIDGE_NAMES:
                    result_text = await tool_catalog.execute(
                        tool_block.name,
                        tool_block.input,
                        mcp_bridge,
                    )
                elif tool_block.name in _LOCAL_TOOL_NAMES:
                    result_text = await _execute_local_tool(
                        tool_block.name, tool_block.input, tool_id=tool_block.id, identity=identity
                    )
                else:
                    result_text = await mcp_bridge.call_tool(
                        tool_block.name, tool_block.input,
                        session_key=f"{identity}:{conversation_id}",
                    )

                if not before.blocks:
                    decision = tool_guard.after_call(guard_name, guard_args, result_text)
                    result_text = append_guard_warning(result_text, decision)


                result_event = {
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "tool_name": tool_block.name,
                    "status": "completed",
                    "input": tool_block.input,
                }
                yield result_event

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result_text,
                })

            # If cancelled during tool execution, break out
            if cancel_event and cancel_event.is_set():
                break

            # Append tool results as user message and loop
            messages.append({"role": "user", "content": tool_results})

        # Stream complete
        total_ms = (time.monotonic() - start_time) * 1000
        log.info(
            "API stream complete for %s in %.0fms (%d chars)",
            identity,
            total_ms,
            sum(len(t) for t in full_text),
        )

        yield {
            "type": "stream_end",
            "full_content": "".join(full_text),
            "session_id": None,
        }

    except Exception as e:
        log.exception("Unexpected error in stream_api for %s", identity)
        # Still try to yield what we have
        content = "".join(full_text)
        if content:
            yield {
                "type": "stream_end",
                "full_content": content,
                "session_id": None,
            }
        yield {"type": "error", "message": str(e)}


