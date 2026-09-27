"""MCP Bridge - manages long-lived connections to MCP servers for direct API mode.

Connects to MCP servers defined in mcp-servers.json using fastmcp Client,
discovers tool schemas, and routes tool calls to the correct server.
"""

# ANAM GUIDE: MCP TOOL BRIDGE (NON-CLI PROVIDERS)
# What: Anam's own connection to the MCP tool servers (Discord, Google, Home Assistant, ...) — discovers their tools and routes calls, for providers that are NOT the Claude Code CLI (the CLI brings its own from .claude.json).
# Called by: services/claude_api.py and services/openai_provider.py for tool calls; core/lifespan.py/server.py start it; house_snapshot, identity_context, limbic_bridge, autowake, and api/hub.py borrow it for one-off tool calls.
# Edit here when: adding/removing an MCP server or tool whitelist — but the actual server list lives in mcp-servers.json; edit here only for connection/routing behavior.

import asyncio
import hashlib
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from config import MCP_SERVERS_FILE, DOCUMENTS_DIR

log = logging.getLogger(__name__)

_REQUIRED_TOOL_GROUPS: dict[str, dict[str, Any]] = {
    "calendar": {
        "server": "google",
        "accepted_servers": ["google", "gdrive-multibot"],
        "tools": [
            "gcal_today_events",
            "gcal_create_event",
            "gcal_update_event",
            "gcal_delete_event",
        ],
    }
}


def _parse_server_list(raw: str) -> set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def _normalize_name_list(value) -> set[str]:
    if isinstance(value, str):
        return _parse_server_list(value)
    if isinstance(value, list):
        out = set()
        for item in value:
            name = str(item).strip()
            if name:
                out.add(name)
        return out
    return set()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_string_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _strip_tool_marker_prefixes(raw: str) -> str:
    text = (raw or "").strip()
    prefixes = ("##nolongerused", "#nolongerused", "nolongerused", "@mcptool", "mcptool")
    changed = True
    while changed and text:
        changed = False
        lower = text.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                text = text[len(prefix):].lstrip(" :")
                changed = True
                break
    return text.strip()


def _global_mcp_config_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("ANAM_MCP_GLOBAL_CONFIG_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.home() / ".claude.json")
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")

    # Preserve order while removing duplicates.
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _discover_codex_sites_design_picker() -> dict[str, Any] | None:
    """Expose the newest installed Codex Sites design helper to Anam.

    Codex registers this MCP dynamically from its versioned plugin cache, so
    it is not present in the config files Anam normally reads. Resolve the
    current plugin version at startup instead of pinning a cache directory.
    """
    sites_root = Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / "sites"
    candidates = list(sites_root.glob("*/mcp/server.mjs"))
    if not candidates:
        return None

    def version_key(server_path: Path) -> tuple[int, ...]:
        version = server_path.parents[1].name
        parts: list[int] = []
        for piece in version.split("."):
            digits = "".join(char for char in piece if char.isdigit())
            parts.append(int(digits) if digits else -1)
        return tuple(parts)

    server_path = max(candidates, key=version_key)
    return {"command": "node", "args": [str(server_path)]}


def _discover_unreal_engine_mcp() -> dict[str, Any] | None:
    """Return Epic's editor-embedded MCP only while the editor server is up."""
    configured_url = os.environ.get("ANAM_UNREAL_MCP_URL", "").strip()
    url = configured_url or "http://127.0.0.1:8000/mcp"
    if configured_url:
        return {"type": "http", "url": url}

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000
    try:
        connection = socket.create_connection((host, port), timeout=0.15)
    except OSError:
        return None
    connection.close()
    return {"type": "http", "url": url}


class MCPBridge:
    """Singleton manager for MCP server connections and tool routing."""

    def __init__(self):
        self._process_started_at = time.time()
        self._clients: dict[str, Client] = {}
        self._contexts: dict[str, object] = {}
        self._http_servers: set[str] = set()
        self._tool_schemas: list[dict] = []
        self._retry_safe_tools: set[tuple[str, str]] = set()
        self._tool_server_map: dict[str, str] = {}
        self._configured_servers: set[str] = set()
        self._skipped_servers: dict[str, str] = {}
        self._failed_servers: dict[str, str] = {}
        self._critical_servers: set[str] = set()
        self._optional_servers: set[str] = set()
        self._declared_critical_servers: set[str] = set()
        self._declared_optional_servers: set[str] = set()
        self._pending_optional_servers: set[str] = set()
        self._duplicate_collisions: dict[str, list[str]] = {}
        self._preferred_tool_servers: dict[str, str] = {}
        self._disabled_tools_global: set[str] = set()
        self._disabled_tools_by_server: set[tuple[str, str]] = set()
        self._tool_categories: dict[str, list[str]] = {}
        self._category_keywords: dict[str, list[str]] = {}
        self._server_category_map: dict[str, str] = {}
        self._server_error_counts: dict[str, int] = {}
        self._server_circuit_opened_at: dict[str, float] = {}
        self._circuit_threshold = max(
            int(os.environ.get("ANAM_MCP_CIRCUIT_FAILURES", "3")),
            1,
        )
        self._circuit_cooldown_seconds = max(
            float(os.environ.get("ANAM_MCP_CIRCUIT_COOLDOWN_SECONDS", "60")),
            1.0,
        )
        self._optional_connect_task: asyncio.Task | None = None
        self._started = False
        self._lock = asyncio.Lock()
        self._connect_timeout_seconds = max(
            float(os.environ.get("ANAM_MCP_CONNECT_TIMEOUT_SECONDS", "30")),
            1.0,
        )
        self._connect_optional_in_background = _env_bool(
            "ANAM_MCP_CONNECT_OPTIONAL_IN_BACKGROUND",
            True,
        )
        # Tool-loop guard rings: session_key -> list of the last N call hashes.
        # Instance-level (not class-level) so separate bridges never share rings.
        self._loop_rings: dict[str, list[str]] = {}

    async def start(self):
        """Connect to all MCP servers, discover tools, cache schemas."""
        async with self._lock:
            if self._started:
                return

            if self._optional_connect_task and not self._optional_connect_task.done():
                self._optional_connect_task.cancel()
            self._optional_connect_task = None

            config, configured_servers, skipped_servers = self._load_config(emit_logs=True)
            self._configured_servers = configured_servers
            self._skipped_servers = dict(skipped_servers)
            self._failed_servers.clear()
            self._server_error_counts.clear()
            self._server_circuit_opened_at.clear()
            self._pending_optional_servers.clear()
            self._duplicate_collisions = {}

            if not config:
                if configured_servers:
                    log.warning("All MCP servers are filtered out or skipped")
                else:
                    log.warning("No MCP servers configured - tools will be unavailable")
                self._started = True
                return

            critical = sorted(name for name in config if name in self._critical_servers)
            optional = sorted(
                name for name in config if name in self._optional_servers and name not in self._critical_servers
            )
            if not critical:
                critical = sorted(config.keys())

            connect_tasks = [
                self._connect_server(name, config[name])
                for name in critical
            ]
            await asyncio.gather(*connect_tasks)

            await self._discover_tools()

            self._started = True
            optional_started_in_background = bool(optional and self._connect_optional_in_background)
            if optional_started_in_background:
                self._pending_optional_servers = set(optional)
                self._optional_connect_task = asyncio.create_task(
                    self._connect_optional_servers_background(config, optional),
                    name="mcp_optional_connect",
                )
            elif optional:
                optional_tasks = [self._connect_server(name, config[name]) for name in optional]
                await asyncio.gather(*optional_tasks)
                await self._discover_tools()
            log.info(
                "MCP bridge started: %d connected, %d failed, %d skipped, %d tools (critical=%d, optional=%d, optional_bg=%s)",
                len(self._clients),
                len(self._failed_servers),
                len(self._skipped_servers),
                len(self._tool_schemas),
                len(critical),
                len(optional),
                str(optional_started_in_background).lower(),
            )

    async def stop(self):
        """Close all MCP server connections."""
        async with self._lock:
            if self._optional_connect_task and not self._optional_connect_task.done():
                self._optional_connect_task.cancel()
            self._optional_connect_task = None
            for name, client in self._clients.items():
                try:
                    await client.__aexit__(None, None, None)
                    transport_type = "HTTP" if name in self._http_servers else "stdio"
                    log.info("Disconnected MCP server (%s): %s", transport_type, name)
                except Exception as exc:
                    log.exception("Error disconnecting MCP server %s: %s", name, exc)
            self._clients.clear()
            self._contexts.clear()
            self._http_servers.clear()
            self._tool_schemas.clear()
            self._tool_server_map.clear()
            self._configured_servers.clear()
            self._skipped_servers.clear()
            self._failed_servers.clear()
            self._critical_servers.clear()
            self._optional_servers.clear()
            self._declared_critical_servers.clear()
            self._declared_optional_servers.clear()
            self._pending_optional_servers.clear()
            self._duplicate_collisions = {}
            self._tool_categories.clear()
            self._category_keywords.clear()
            self._server_category_map.clear()
            self._started = False

    def get_tools(self) -> list[dict]:
        """Return Anthropic-format tool definitions.

        The last tool gets cache_control for prompt caching.
        """
        return self._tool_schemas

    def get_status(self) -> dict:
        """Return runtime MCP connection/tool status for UI and diagnostics."""
        tool_counts: dict[str, int] = {}
        for _tool_name, server_name in self._tool_server_map.items():
            tool_counts[server_name] = tool_counts.get(server_name, 0) + 1

        if self._started:
            configured = set(self._configured_servers)
            skipped = dict(self._skipped_servers)
            failed = dict(self._failed_servers)
            pending_optional = set(self._pending_optional_servers)
            critical = set(self._critical_servers)
            optional = set(self._optional_servers)
            declared_critical = set(self._declared_critical_servers)
            declared_optional = set(self._declared_optional_servers)
            duplicates = dict(self._duplicate_collisions)
        else:
            _active, configured, skipped = self._load_config(emit_logs=False)
            failed = {}
            pending_optional = set()
            critical = set(self._critical_servers)
            optional = set(self._optional_servers)
            declared_critical = set(self._declared_critical_servers)
            declared_optional = set(self._declared_optional_servers)
            duplicates = {}

        connected = set(self._clients.keys())
        all_servers = sorted(configured)

        servers = []
        critical_failed: list[str] = []
        optional_failed: list[str] = []
        critical_connected = 0
        optional_connected = 0
        for name in all_servers:
            reason = None
            if name in critical or name in declared_critical:
                tier = "critical"
            elif name in optional or name in declared_optional:
                tier = "optional"
            else:
                tier = "critical"

            if name in connected:
                status = "connected"
                if tier == "critical":
                    critical_connected += 1
                else:
                    optional_connected += 1
            elif name in pending_optional:
                status = "connecting"
            elif name in skipped:
                status = "skipped"
                reason = skipped.get(name)
            elif self._started:
                status = "failed"
                reason = failed.get(name, "connection failed")
                if tier == "critical":
                    critical_failed.append(name)
                else:
                    optional_failed.append(name)
            else:
                status = "configured"

            item = {
                "name": name,
                "tools": int(tool_counts.get(name, 0)),
                "status": status,
                "tier": tier,
            }
            if reason:
                item["reason"] = reason
            servers.append(item)

        required_tools: dict[str, dict[str, Any]] = {}
        for group_name, spec in _REQUIRED_TOOL_GROUPS.items():
            expected_server = str(spec.get("server", "")).strip()
            accepted_servers = {
                str(name).strip()
                for name in spec.get("accepted_servers", []) or []
                if str(name).strip()
            }
            if expected_server:
                accepted_servers.add(expected_server)
            tools = [str(name).strip() for name in spec.get("tools", []) if str(name).strip()]
            missing = [name for name in tools if name not in self._tool_server_map]
            misplaced = [
                name for name in tools
                if name in self._tool_server_map
                and accepted_servers
                and self._tool_server_map[name] not in accepted_servers
            ]
            required_tools[group_name] = {
                "server": expected_server,
                "server_connected": bool(accepted_servers & connected),
                "required": tools,
                "present": [name for name in tools if name in self._tool_server_map],
                "missing": missing,
                "misplaced": misplaced,
                "ok": not missing and not misplaced,
            }

        config_path = MCP_SERVERS_FILE
        config_mtime = None
        config_changed_since_start = False
        restart_recommended = False
        if config_path.exists():
            try:
                config_mtime = config_path.stat().st_mtime
                config_changed_since_start = config_mtime > (self._process_started_at + 0.001)
                restart_recommended = config_changed_since_start
            except OSError:
                config_mtime = None

        return {
            "started": bool(self._started),
            "startup_phase": "warming_optional" if pending_optional else "ready",
            "server_count": len(servers),
            "tool_count": len(self._tool_schemas),
            "connect_timeout_seconds": self._connect_timeout_seconds,
            "critical_server_count": len(critical or declared_critical),
            "optional_server_count": len(optional or declared_optional),
            "critical_connected": critical_connected,
            "optional_connected": optional_connected,
            "critical_failed": sorted(critical_failed),
            "optional_failed": sorted(optional_failed),
            "optional_pending": sorted(pending_optional),
            "duplicate_tool_count": len(duplicates),
            "duplicate_tools": duplicates,
            "open_circuits": {
                name: {
                    "failure_count": self._server_error_counts.get(name, 0),
                    "opened_at_epoch": time.time() - (time.monotonic() - opened),
                    "cooldown_seconds": self._circuit_cooldown_seconds,
                }
                for name, opened in self._server_circuit_opened_at.items()
                if (time.monotonic() - opened) < self._circuit_cooldown_seconds
            },
            "required_tools": required_tools,
            "config_path": str(config_path),
            "config_mtime": config_mtime,
            "config_changed_since_start": config_changed_since_start,
            "restart_recommended": restart_recommended,
            "servers": servers,
        }

    def get_tools_for_categories(self, active_categories: set[str] | None = None) -> list[dict]:
        """Return all tool schemas. The active_categories parameter is kept for backward compat but ignored."""
        return self._tool_schemas

    def _is_tool_disabled(self, server_name: str, tool_name: str) -> bool:
        if tool_name in self._disabled_tools_global:
            return True
        return (server_name, tool_name) in self._disabled_tools_by_server

    async def reconnect_failed(self):
        """Attempt to reconnect MCP servers that are not currently connected."""
        async with self._lock:
            if not self._started:
                return

            active_config, configured_servers, skipped_servers = self._load_config(
                emit_logs=False
            )
            self._configured_servers = configured_servers
            self._skipped_servers = skipped_servers

            reconnect_targets = [
                name
                for name in active_config.keys()
                if name not in self._clients
                and name not in self._pending_optional_servers
            ]
            if not reconnect_targets:
                return

            log.info(
                "MCP reconnect check: %d target(s): %s",
                len(reconnect_targets),
                ", ".join(sorted(reconnect_targets)),
            )

            # Preserve only failures still relevant for this reconnect pass.
            self._failed_servers = {
                name: reason
                for name, reason in self._failed_servers.items()
                if name in reconnect_targets
            }

            tasks = [
                self._connect_server(name, active_config[name])
                for name in reconnect_targets
            ]
            await asyncio.gather(*tasks)
            await self._discover_tools()

    async def _connect_optional_servers_background(self, full_config: dict[str, dict], names: list[str]):
        try:
            should_discover = False
            for name in names:
                async with self._lock:
                    if not self._started:
                        return
                    if name not in full_config:
                        self._pending_optional_servers.discard(name)
                        continue
                    if name not in self._clients:
                        await self._connect_server(name, full_config[name])
                    self._pending_optional_servers.discard(name)
                    should_discover = True
            if should_discover:
                async with self._lock:
                    if self._started:
                        await self._discover_tools()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Optional MCP background connect failed: %s", exc)
        finally:
            async with self._lock:
                if self._optional_connect_task is asyncio.current_task():
                    self._optional_connect_task = None

    async def log_duplicate_audit(self):
        """Periodic duplicate audit for routine operations."""
        async with self._lock:
            if not self._started:
                return
            collisions = dict(self._duplicate_collisions)
        if collisions:
            log.warning("MCP duplicate audit: %d duplicate tool name(s): %s", len(collisions), collisions)
        else:
            log.info("MCP duplicate audit: no duplicate tool names")

    # Tool-loop guard — repeated-identical-call breaker (ported from Friend's
    # resonant PreToolUse hook). Tracks the last 6 calls per session as
    # sha1(tool name + stable-stringified input). Exact-identical only: any
    # change in arguments resets the run, so legitimately-repeating tools
    # never trigger. The 3rd identical consecutive call (and 4th/5th) gets
    # a warning appended to its real result; the 6th is denied outright
    # without executing.
    _LOOP_RING_SIZE = 6
    _LOOP_WARN_AT = 3
    _LOOP_DENY_AT = 6
    _LOOP_MAX_SESSIONS = 50

    @staticmethod
    def _stable_call_hash(name: str, arguments: dict) -> str:
        """Deterministic identity for a tool call regardless of key order."""
        canonical = json.dumps(
            arguments or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha1(f"{name}\x00{canonical}".encode("utf-8")).hexdigest()

    def _track_loop_call(self, session_key: str, name: str, arguments: dict) -> int:
        """Record this call in the session's ring and return the consecutive
        identical run length INCLUDING the incoming call itself."""
        call_hash = self._stable_call_hash(name, arguments)
        ring = self._loop_rings.pop(session_key, [])
        run = 1
        for prior in reversed(ring):
            if prior != call_hash:
                break
            run += 1
        ring.append(call_hash)
        del ring[:-self._LOOP_RING_SIZE]
        # Pop+reinsert keeps dict insertion order = recency, so pruning drops
        # the least-recently-touched session.
        self._loop_rings[session_key] = ring
        while len(self._loop_rings) > self._LOOP_MAX_SESSIONS:
            oldest = next(iter(self._loop_rings))
            self._loop_rings.pop(oldest, None)
        return run

    # Tools that need longer than the default 30s timeout
    _SLOW_TOOLS: dict[str, int] = {
        "morning_start": 90,
        "go_to_sleep": 90,
        "mind_consolidate": 60,
        "build_context": 60,
        "orient": 60,
    }

    # Cache for document/file reads — keyed by (tool_name, file_path), stores (result, timestamp).
    # Documents and prompts don't change once written, so cache hits avoid repeated disk I/O.
    _CACHEABLE_READ_TOOLS = {"fs_read_file", "read_note"}
    _CACHEABLE_PATH_PREFIXES = (str(DOCUMENTS_DIR.resolve()) + "/", str(DOCUMENTS_DIR.resolve()) + "\\")
    _file_read_cache: dict[str, tuple[str, float]] = {}
    _FILE_CACHE_TTL = 86400  # 24 hours — documents don't change once uploaded

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        timeout: float | None = None,
        session_key: str | None = None,
    ) -> str:
        """Execute a tool call via the appropriate MCP server.

        Returns the text content of the tool result, or an error message.

        timeout: optional hard cap in seconds. When a caller pins this — e.g. a
        best-effort context-building hook that must never stall a session — the
        1.5x auto-extend-and-retry on timeout is DISABLED so the call fails fast
        instead of riding the default 45s -> ~112s cascade. When None (default),
        the per-tool default (_SLOW_TOOLS or 45s) applies with auto-extend, so
        existing callers are unchanged.

        session_key: identity/session scope for the tool-loop guard. The guard
        only arms when a session_key is passed — model-driven providers pass
        their conversation id; infrastructure callers (hub polling, context
        hooks, limbic bridge) pass nothing and are exempt, because they repeat
        identical calls BY DESIGN and machine-parse the results (a "[loop-guard]"
        suffix would corrupt their JSON, and a 60s poll would hit permanent
        denial overnight).
        """
        server_name = self._tool_server_map.get(name)
        if not server_name:
            return f"Error: Unknown tool '{name}'"

        # Tool-loop guard: identical call repeated 3x -> warn, 6th -> deny.
        # Armed only for model-driven callers (session_key present).
        loop_run = 0
        if session_key is not None:
            loop_run = self._track_loop_call(session_key, name, arguments)
        if loop_run >= self._LOOP_DENY_AT:
            log.warning(
                "Tool-loop guard DENY: %s x%d identical (session %s)",
                name, loop_run, session_key,
            )
            return (
                f"You've called {name} with identical input {loop_run} times; "
                "the result will not change. Change approach or stop."
            )
        loop_warning = ""
        if loop_run >= self._LOOP_WARN_AT:
            log.warning(
                "Tool-loop guard warning: %s x%d identical (session %s)",
                name, loop_run, session_key,
            )
            loop_warning = (
                f"\n\n[loop-guard] You've called {name} with identical input "
                f"{loop_run} times in a row — the result will not change. "
                "Change approach or stop."
            )

        circuit_error = self._circuit_error(server_name)
        if circuit_error:
            return circuit_error + loop_warning

        client = self._clients.get(server_name)
        if not client:
            # The client may have been evicted by a prior disconnect. Try to heal before giving up.
            if await self._reconnect_client(server_name):
                client = self._clients.get(server_name)
            if not client:
                self._record_server_failure(server_name)
                return f"Error: MCP server '{server_name}' is not connected" + loop_warning

        # Check document read cache
        cache_key = None
        if name in self._CACHEABLE_READ_TOOLS:
            file_path = arguments.get("path") or arguments.get("file_path") or ""
            if any(file_path.startswith(p) for p in self._CACHEABLE_PATH_PREFIXES):
                cache_key = f"{name}:{file_path}"
                cached = self._file_read_cache.get(cache_key)
                if cached and (time.monotonic() - cached[1]) < self._FILE_CACHE_TTL:
                    log.debug("Document cache hit: %s", file_path)
                    return cached[0] + loop_warning

        hard_cap = timeout is not None
        timeout_s = float(timeout) if hard_cap else self._SLOW_TOOLS.get(name, 45)
        last_exc = None
        reconnected = False
        for attempt in range(3):
            client = self._clients.get(server_name)
            if not client:
                return f"Error: MCP server '{server_name}' is not connected" + loop_warning
            try:
                result = await client.call_tool(name, arguments, timeout=timeout_s)
                parts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    elif hasattr(block, "data"):
                        parts.append(f"[binary data: {getattr(block, 'mimeType', 'unknown')}]")
                text_result = "\n".join(parts) if parts else "(empty result)"
                self._record_server_success(server_name)
                if server_name == 'qualia-backend' and name in {
                    'mind_store', 'mind_feel', 'mind_notice', 'mind_small_joy',
                    'mind_edit', 'mind_delete', 'mind_go_to_sleep',
                } and not getattr(result, 'isError', False) and not text_result.startswith('Error'):
                    from services.cloud_state import invalidate_cloud_cache
                    invalidate_cloud_cache()


                # Cache document reads
                if cache_key and not text_result.startswith("Error"):
                    self._file_read_cache[cache_key] = (text_result, time.monotonic())
                    # Prune on insert: drop expired entries, then bound to the
                    # 128 newest — TTL was only ever checked on lookup, so
                    # document text accumulated for the life of the process.
                    now = time.monotonic()
                    for key in [
                        k for k, (_, ts) in self._file_read_cache.items()
                        if now - ts >= self._FILE_CACHE_TTL
                    ]:
                        self._file_read_cache.pop(key, None)
                    if len(self._file_read_cache) > 128:
                        overflow = len(self._file_read_cache) - 128
                        for key in sorted(
                            self._file_read_cache,
                            key=lambda k: self._file_read_cache[k][1],
                        )[:overflow]:
                            self._file_read_cache.pop(key, None)

                return text_result + loop_warning
            except (TimeoutError, Exception) as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_timeout = isinstance(exc, TimeoutError) or "timeout" in msg
                is_disconnect = any(marker in msg for marker in self._DISCONNECT_MARKERS)
                retry_safe = (server_name, name) in self._retry_safe_tools
                if (is_timeout or is_disconnect) and not retry_safe:
                    if is_disconnect:
                        # Repair the connection for future calls, without replaying this action.
                        await self._reconnect_client(server_name)
                    if not (hard_cap and is_timeout):
                        self._record_server_failure(server_name)
                    return "Error: tool delivery uncertain. The action was not retried; inspect its target before repeating a write." + loop_warning
                if is_timeout and attempt == 0 and not hard_cap:
                    log.warning("Tool call timeout, retrying with extended timeout: %s.%s", server_name, name)
                    timeout_s = int(timeout_s * 1.5)
                    continue
                # Stale long-lived HTTP session (e.g. qualia worker redeploy / session eviction):
                # the client looks open but the transport is dead. Evict + reconnect, then retry once.
                if is_disconnect and not reconnected:
                    reconnected = True
                    log.warning(
                        "Tool call hit disconnect-class error on %s.%s (%s) - reconnecting and retrying",
                        server_name, name, exc,
                    )
                    await self._reconnect_client(server_name)
                    continue


                if hard_cap and is_timeout:
                    log.warning(
                        "Tool call hit caller-pinned %.0fs cap: %s.%s "
                        "(best-effort — NOT counted against the circuit)",
                        timeout_s, server_name, name,
                    )
                    return f"Error calling {name}: {exc}" + loop_warning
                log.exception("Tool call failed: %s.%s", server_name, name)
                self._record_server_failure(server_name)
                return f"Error calling {name}: {exc}" + loop_warning
        self._record_server_failure(server_name)
        return f"Error calling {name}: {last_exc}" + loop_warning

    def _circuit_error(self, server_name: str) -> str | None:
        """Short-circuit repeatedly failing MCP servers until one half-open probe."""
        failures = self._server_error_counts.get(server_name, 0)
        opened_at = self._server_circuit_opened_at.get(server_name)
        if failures < self._circuit_threshold or opened_at is None:
            return None
        age = time.monotonic() - opened_at
        if age >= self._circuit_cooldown_seconds:
            # Half-open: the next caller gets one real probe. Re-arm the clock
            # immediately so concurrent callers do not all probe together.
            self._server_circuit_opened_at[server_name] = time.monotonic()
            return None
        remaining = max(1, int(self._circuit_cooldown_seconds - age))
        return (
            f"Error: MCP server '{server_name}' circuit is open after {failures} "
            f"consecutive failures; retry in about {remaining}s"
        )

    def _record_server_failure(self, server_name: str) -> None:
        failures = self._server_error_counts.get(server_name, 0) + 1
        self._server_error_counts[server_name] = failures
        if failures >= self._circuit_threshold:
            self._server_circuit_opened_at[server_name] = time.monotonic()
            log.warning(
                "MCP circuit opened for %s after %d consecutive failed call(s)",
                server_name,
                failures,
            )

    def _record_server_success(self, server_name: str) -> None:
        if server_name in self._server_error_counts:
            log.info("MCP circuit closed for %s after successful call", server_name)
        self._server_error_counts.pop(server_name, None)
        self._server_circuit_opened_at.pop(server_name, None)

    def _load_config(
        self,
        emit_logs: bool = True,
        *,
        apply_direct_bridge_skips: bool = True,
    ) -> tuple[dict, set[str], dict[str, str]]:
        """Load MCP server config from Claude Code config + local overrides.

        Sources (merged in order, later wins):
        1. Global MCP config candidates (first wins per server name):
           - ANAM_MCP_GLOBAL_CONFIG_PATH (if set)
           - ~/.claude.json
           - %APPDATA%/Claude/claude_desktop_config.json
        2. Local mcp-servers.json overrides

        Filtering order:
        1. enabledServers from local mcp-servers.json (if present)
        2. skipServers from local mcp-servers.json (if present)
        3. ANAM_MCP_SKIP_SERVERS env var (comma-separated)

        Returns:
            (active_servers, configured_servers_before_filtering, skipped_reason_map)
        """
        servers: dict[str, dict] = {}
        enabled_filter: set[str] | None = None
        local_skip: set[str] = set()
        direct_bridge_skip: set[str] = set()
        preferred_tool_servers: dict[str, str] = {}
        disabled_tools_entries: list[str] = []
        configured_critical: set[str] = set()
        configured_optional: set[str] = set()

        global_sources_loaded: list[tuple[str, int]] = []
        for global_cfg in _global_mcp_config_candidates():
            if not global_cfg.exists():
                continue
            try:
                data = json.loads(global_cfg.read_text(encoding="utf-8-sig"))
                source_servers = data.get("mcpServers", {})
                if not isinstance(source_servers, dict):
                    continue
                added = 0
                for name, cfg in source_servers.items():
                    if name in servers:
                        continue
                    servers[name] = cfg
                    added += 1
                global_sources_loaded.append((str(global_cfg), added))
            except Exception as exc:
                log.exception("Failed to parse MCP global config %s: %s", global_cfg, exc)

        if emit_logs and global_sources_loaded:
            loaded_details = ", ".join(f"{path} (+{count})" for path, count in global_sources_loaded)
            log.info(
                "Loaded %d MCP servers from %d global config source(s): %s",
                len(servers),
                len(global_sources_loaded),
                loaded_details,
            )

        sites_picker = _discover_codex_sites_design_picker()
        if sites_picker and "sites-design-picker" not in servers:
            servers["sites-design-picker"] = sites_picker
            if emit_logs:
                log.info("Added Codex Sites design helper to Anam's MCP registry")

        local_config_data = None
        if MCP_SERVERS_FILE.exists():
            try:
                data = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8-sig"))
                local_config_data = data
                local = data.get("mcpServers", {})
                if local:
                    for name, override in local.items():
                        existing = servers.get(name, {})
                        if isinstance(existing, dict) and isinstance(override, dict):
                            merged = _deep_merge_dict(existing, override)
                            unset_env = _normalize_string_list(override.get("unsetEnv", []))
                            if unset_env:
                                env = merged.get("env")
                                if isinstance(env, dict):
                                    for key in unset_env:
                                        env.pop(key, None)
                            merged.pop("unsetEnv", None)
                            servers[name] = merged
                        else:
                            servers[name] = override
                    if emit_logs:
                        log.info("Applied %d local MCP server overrides", len(local))
                if "enabledServers" in data:
                    enabled_filter = _normalize_name_list(data.get("enabledServers"))
                if "skipServers" in data:
                    local_skip = _normalize_name_list(data.get("skipServers"))
                if "directBridgeSkipServers" in data:
                    direct_bridge_skip = _normalize_name_list(data.get("directBridgeSkipServers"))
                configured_critical = _normalize_name_list(data.get("criticalServers"))
                configured_optional = _normalize_name_list(data.get("optionalServers"))
                preferred_cfg = data.get("preferredToolServers")
                if isinstance(preferred_cfg, dict):
                    for tool_name, server_name in preferred_cfg.items():
                        tool = str(tool_name).strip()
                        server = str(server_name).strip()
                        if tool and server:
                            preferred_tool_servers[tool] = server
                disabled_tools_entries.extend(_normalize_string_list(data.get("disabledTools", [])))
                disabled_tools_entries.extend(_normalize_string_list(data.get("noLongerUsedTools", [])))

                if data.get("discoverUnrealEngine"):
                    unreal_mcp = _discover_unreal_engine_mcp()
                    if unreal_mcp and "unreal-engine" not in servers:
                        servers["unreal-engine"] = unreal_mcp
                        if emit_logs:
                            log.info("Discovered Epic Unreal MCP at %s", unreal_mcp["url"])
            except Exception as exc:
                log.exception("Failed to parse local MCP config %s: %s", MCP_SERVERS_FILE, exc)

        configured_servers = set(servers.keys())
        skipped_reasons: dict[str, str] = {}

        if enabled_filter is not None:
            for name in configured_servers:
                if name not in enabled_filter:
                    skipped_reasons[name] = "disabled_by_enabledServers"
            servers = {k: v for k, v in servers.items() if k in enabled_filter}
            if emit_logs and skipped_reasons:
                log.info(
                    "Filtered to %d enabled servers (disabled by enabledServers: %s)",
                    len(servers),
                    ", ".join(sorted(skipped_reasons.keys())),
                )

        for name in local_skip:
            if name in servers:
                skipped_reasons[name] = "disabled_by_skipServers"
                servers.pop(name, None)

        if apply_direct_bridge_skips:
            for name in direct_bridge_skip:
                if name in servers:
                    skipped_reasons[name] = "disabled_for_direct_bridge"
                    servers.pop(name, None)

        env_skip = _parse_server_list(os.environ.get("ANAM_MCP_SKIP_SERVERS", ""))
        for name in env_skip:
            if name in servers:
                skipped_reasons[name] = "disabled_by_env"
                servers.pop(name, None)

        if emit_logs and local_skip:
            disabled = sorted(name for name, reason in skipped_reasons.items() if reason == "disabled_by_skipServers")
            if disabled:
                log.info("Skipped %d MCP servers via skipServers: %s", len(disabled), ", ".join(disabled))

        if emit_logs and apply_direct_bridge_skips and direct_bridge_skip:
            disabled = sorted(
                name for name, reason in skipped_reasons.items()
                if reason == "disabled_for_direct_bridge"
            )
            if disabled:
                log.info(
                    "Skipped %d Claude-only MCP servers in Anam's direct bridge: %s",
                    len(disabled),
                    ", ".join(disabled),
                )

        if emit_logs and env_skip:
            disabled = sorted(name for name, reason in skipped_reasons.items() if reason == "disabled_by_env")
            if disabled:
                log.info("Skipped %d MCP servers via ANAM_MCP_SKIP_SERVERS: %s", len(disabled), ", ".join(disabled))

        env_critical = _parse_server_list(os.environ.get("ANAM_MCP_CRITICAL_SERVERS", ""))
        env_optional = _parse_server_list(os.environ.get("ANAM_MCP_OPTIONAL_SERVERS", ""))
        configured_critical |= env_critical
        configured_optional |= env_optional
        active_names = set(servers.keys())
        declared_critical = configured_critical & configured_servers
        declared_optional = configured_optional & configured_servers
        critical_active = active_names & configured_critical
        optional_active = active_names & configured_optional
        if configured_critical or configured_optional:
            if not critical_active:
                critical_active = active_names - optional_active
        else:
            critical_active = set(active_names)
        optional_active -= critical_active
        if not critical_active and active_names:
            critical_active = set(active_names)
        # Any configured server that landed in neither tier defaults to
        # optional (background connect) rather than being silently dropped.
        # This guarantees every server from .claude.json actually connects,
        # so a newly added connector doesn't need a matching mcp-servers.json
        # edit to come online for non-Claude-Code providers.
        orphan_active = active_names - critical_active - optional_active
        optional_active |= orphan_active
        self._critical_servers = critical_active
        self._optional_servers = optional_active
        self._declared_critical_servers = declared_critical
        self._declared_optional_servers = declared_optional

        env_disabled_tools = _normalize_string_list(os.environ.get("ANAM_MCP_DISABLED_TOOLS", ""))
        all_disabled_entries = disabled_tools_entries + env_disabled_tools
        disabled_global: set[str] = set()
        disabled_by_server: set[tuple[str, str]] = set()
        for entry in all_disabled_entries:
            cleaned = _strip_tool_marker_prefixes(entry)
            if not cleaned:
                continue
            if ":" in cleaned:
                server_name, tool_name = cleaned.split(":", 1)
                server = server_name.strip()
                tool = tool_name.strip()
                if server and tool:
                    disabled_by_server.add((server, tool))
                continue
            disabled_global.add(cleaned)

        self._preferred_tool_servers = preferred_tool_servers
        self._disabled_tools_global = disabled_global
        self._disabled_tools_by_server = disabled_by_server

        if emit_logs and preferred_tool_servers:
            log.info(
                "Applied %d preferred MCP tool mapping(s)",
                len(preferred_tool_servers),
            )
        if emit_logs and (configured_critical or configured_optional):
            log.info(
                "Applied MCP tiering (critical=%d optional=%d active_critical=%d active_optional=%d)",
                len(configured_critical),
                len(configured_optional),
                len(self._critical_servers),
                len(self._optional_servers),
            )
        if emit_logs and (disabled_global or disabled_by_server):
            log.info(
                "Applied %d disabled MCP tool rule(s)",
                len(disabled_global) + len(disabled_by_server),
            )

        # Tool categories for lazy-loading (reuses the parse from the overrides block above)
        if local_config_data is not None:
            try:
                cat_data = local_config_data


                raw_taxonomy = cat_data.get("toolTaxonomy", {})
                if isinstance(raw_taxonomy, dict):
                    for cat, spec in raw_taxonomy.items():
                        if cat.startswith("_") or not isinstance(spec, dict):
                            continue
                        servers_list = spec.get("servers")
                        if isinstance(servers_list, list) and servers_list:
                            self._tool_categories[cat] = list(servers_list)
                        keywords_list = spec.get("keywords")
                        if isinstance(keywords_list, list) and keywords_list:
                            self._category_keywords[cat] = list(keywords_list)
                raw_categories = cat_data.get("toolCategories", {})
                if isinstance(raw_categories, dict):
                    for k, v in raw_categories.items():
                        if isinstance(v, list) and k not in self._tool_categories:
                            self._tool_categories[k] = list(v)
                raw_keywords = cat_data.get("categoryKeywords", {})
                if isinstance(raw_keywords, dict):
                    for k, v in raw_keywords.items():
                        if isinstance(v, list) and k not in self._category_keywords:
                            self._category_keywords[k] = list(v)
                # Build reverse map: server → category
                self._server_category_map = {}
                for cat, cat_servers in self._tool_categories.items():
                    for srv in cat_servers:
                        self._server_category_map[srv] = cat
                if emit_logs and self._tool_categories:
                    log.info(
                        "Loaded %d tool categories (%s)",
                        len(self._tool_categories),
                        ", ".join(f"{k}={len(v)}" for k, v in self._tool_categories.items()),
                    )
            except Exception as exc:
                log.exception("Failed to load tool categories: %s", exc)

        if not servers and emit_logs:
            log.warning("No active MCP servers after filtering")

        return servers, configured_servers, skipped_reasons

    async def _cleanup_partial_client(self, name: str):
        client = self._clients.pop(name, None)
        self._http_servers.discard(name)
        if client:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass

    # Substrings that signal a dead/stale transport rather than a tool-logic error.
    # When these appear, the client corpse must be evicted and re-established, not retried in place.
    _DISCONNECT_MARKERS = (
        "not connected",
        "closed",
        "disconnect",
        "connection",
        "session",
        "transport",
        "broken pipe",
        "eof",
    )

    async def _reconnect_client(self, server_name: str) -> bool:
        """Evict a stale client and re-establish its connection. Returns True on success.

        This is the self-heal path for long-lived HTTP sessions (e.g. Cloudflare-hosted
        backends like qualia) whose session dies server-side while the bridge still holds
        an open-looking client. Without this, a dead session stays wedged in self._clients
        forever — reconnect_failed() only targets *absent* servers, so it never refreshes
        a present-but-dead one, and the only recovery was a full Anam restart.
        """
        async with self._lock:
            active_config, configured_servers, skipped_servers = self._load_config(emit_logs=False)
            self._configured_servers = configured_servers
            self._skipped_servers = skipped_servers
            cfg = active_config.get(server_name)
            if not cfg:
                return False
            await self._cleanup_partial_client(server_name)
            self._failed_servers.pop(server_name, None)
            log.warning("Self-healing MCP connection: reconnecting %s", server_name)
            await self._connect_server(server_name, cfg)
            return server_name in self._clients

    async def _connect_server(self, name: str, config: dict):
        """Connect to a single MCP server (HTTP URL or stdio subprocess)."""
        url = config.get("url")

        try:
            if url:
                await asyncio.wait_for(
                    self._connect_http(name, url, config),
                    timeout=self._connect_timeout_seconds,
                )
            else:
                await asyncio.wait_for(
                    self._connect_stdio(name, config),
                    timeout=self._connect_timeout_seconds,
                )
        except asyncio.TimeoutError:
            await self._cleanup_partial_client(name)
            reason = f"connect timeout after {self._connect_timeout_seconds:.1f}s"
            self._failed_servers[name] = reason
            log.error("Failed to connect MCP server %s: %s", name, reason)
        except Exception as exc:
            await self._cleanup_partial_client(name)
            reason = str(exc) or exc.__class__.__name__
            self._failed_servers[name] = reason
            log.exception("Failed to connect MCP server %s: %s", name, exc)

    async def _connect_http(self, name: str, url: str, config: dict | None = None):
        """Connect to an MCP server via StreamableHTTP with retry/backoff."""
        config = config or {}
        headers = config.get("headers") or config.get("http_headers") or config.get("httpHeaders")
        if not isinstance(headers, dict):
            headers = None


        from services.cc_mcp_oauth import auth_for
        auth = auth_for(name, url, headers)
        max_retries = 3
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                transport = StreamableHttpTransport(url=url, headers=headers, auth=auth)
                client = Client(transport, timeout=self._connect_timeout_seconds)
                await client.__aenter__()
                self._clients[name] = client
                self._http_servers.add(name)
                log.info("Connected MCP server (HTTP): %s -> %s", name, url)
                return
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    log.warning(
                        "MCP HTTP connect failed for %s (attempt %d/%d): %s - retrying in %ds",
                        name,
                        attempt + 1,
                        max_retries,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)

        if last_error:
            raise RuntimeError(f"HTTP connect failed after {max_retries} attempts: {last_error}")

    async def _connect_stdio(self, name: str, config: dict):
        """Connect to an MCP server via stdio subprocess."""
        command = config.get("command", "")
        args = config.get("args", [])
        env = config.get("env")

        if not command:
            raise RuntimeError("missing command and url")

        transport = StdioTransport(
            command=command,
            args=args,
            env=env,
        )
        client = Client(transport, timeout=self._connect_timeout_seconds)
        await client.__aenter__()
        self._clients[name] = client
        log.info("Connected MCP server (stdio): %s (%s %s)", name, command, " ".join(args))

    async def _discover_tools(self):
        """Discover tools from all connected servers and build Anthropic-format schemas."""
        self._tool_schemas.clear()
        self._tool_server_map.clear()
        self._retry_safe_tools.clear()
        tool_index: dict[str, int] = {}
        collisions: dict[str, set[str]] = {}

        for server_name in sorted(self._clients.keys()):
            client = self._clients[server_name]
            try:
                tools = await client.list_tools()
                added = 0
                for tool in tools:
                    tool_name = tool.name
                    annotations = getattr(tool, 'annotations', None)
                    if annotations and (getattr(annotations, 'readOnlyHint', False) is True or
                                        getattr(annotations, 'idempotentHint', False) is True):
                        self._retry_safe_tools.add((server_name, tool_name))
                    if self._is_tool_disabled(server_name, tool_name):
                        log.info("Skipping disabled tool '%s' from %s", tool_name, server_name)
                        continue
                    anthropic_tool = {
                        "name": tool_name,
                        "description": tool.description or f"Tool from {server_name}",
                        "input_schema": tool.inputSchema,
                    }
                    existing_server = self._tool_server_map.get(tool_name)
                    if existing_server:
                        collisions.setdefault(tool_name, set()).update({existing_server, server_name})
                        preferred_server = self._preferred_tool_servers.get(tool_name)
                        if preferred_server and server_name == preferred_server and existing_server != preferred_server:
                            idx = tool_index[tool_name]
                            self._tool_schemas[idx] = anthropic_tool
                            self._tool_server_map[tool_name] = server_name
                            log.info(
                                "Resolved duplicate tool '%s' to preferred server %s (replaced %s)",
                                tool_name,
                                server_name,
                                existing_server,
                            )
                        else:
                            log.warning(
                                "Skipping duplicate tool '%s' from %s (kept from %s)",
                                tool_name,
                                server_name,
                                existing_server,
                            )
                        continue

                    self._tool_schemas.append(anthropic_tool)
                    self._tool_server_map[tool_name] = server_name
                    tool_index[tool_name] = len(self._tool_schemas) - 1
                    added += 1
                log.info("Discovered %d tools from %s", added, server_name)
            except Exception as exc:
                log.exception("Failed to discover tools from %s: %s", server_name, exc)

        self._duplicate_collisions = {
            name: sorted(list(servers))
            for name, servers in sorted(collisions.items())
        }

        if self._tool_schemas:
            self._tool_schemas[-1]["cache_control"] = {"type": "ephemeral"}


# Singleton instance
mcp_bridge = MCPBridge()
