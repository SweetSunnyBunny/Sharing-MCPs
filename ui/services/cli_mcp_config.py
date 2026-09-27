"""Helpers for giving CLI providers the same MCP server map Anam already owns."""

# ANAM GUIDE: CLI MCP TOOL CONFIG WRITER
# What: Writes identity-scoped MCP config. Claude Code and Codex receive only
# the compact Anam gateway; Anam owns the plugin forest once and opens exact
# schemas after the identity chooses a toolbox leaf.
# Called by: services/claude_subprocess.py, services/claude_pty.py, services/claude_agent_sdk_provider.py, services/codex_subprocess.py at session spawn.
# Edit here when: changing which MCP servers CLI sessions can see, or how the anam-context server (port 8814) is wired in.

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from config import DATA_DIR, SHARING_MCP_ROOT
from services.mcp_bridge import mcp_bridge

log = logging.getLogger(__name__)


# The Cloudflare Streamable HTTP wrappers for these two workstation-local
# servers interoperate with FastMCP's Python client, but Codex 0.144 closes the
# transport while sending the post-initialize notification. Codex is already
# running on the same Windows machine as the real servers, so give it the
# simpler stdio door instead of routing local hands out through Cloudflare and
# back again. Keep this translation provider-local; Anam's own bridge and
# Claude retain their existing configured transports.
_MACHINE_AGENT_ROOT = Path(os.environ.get("ANAM_MACHINE_AGENT_ROOT", str(SHARING_MCP_ROOT / "machine-agent")))
_CODEX_LOCAL_STDIO_FALLBACKS: dict[str, Path] = {
    "desktop-control": _MACHINE_AGENT_ROOT / "tools" / "desktop-control" / "desktop_control_server.py",
    "krita": _MACHINE_AGENT_ROOT / "tools" / "krita" / "server.py",
}


def _identity_server_owner(server_name: str) -> str:
    """Return the identity suffix for a per-identity MCP server, if any."""
    lowered = str(server_name or "").strip().lower()
    for prefix in ("playwright-", "vox-"):
        if lowered.startswith(prefix):
            return lowered[len(prefix):]
    return ""


def _active_bridge_servers(identity: str = "", conversation_id: str = "") -> dict[str, dict[str, Any]]:
    """Return the active MCP server config for one CLI identity.

    Browser profiles are private hands, so a Claude process receives only
    ``playwright-claude`` (and likewise for every other identity). Vox is a
    specialist testing surface: it stays out of ordinary CLI sessions and
    remains deliberately reachable through the small Anam gateway instead.
    """
    try:
        # Claude/Codex CLI sessions keep identity-specific browser servers.
        # Only Anam's flattened direct bridge suppresses their duplicate schemas.
        active, _configured, _skipped = mcp_bridge._load_config(
            emit_logs=False,
            apply_direct_bridge_skips=False,
        )
    except Exception:
        log.exception("Failed to load active MCP bridge config for CLI provider")
        active = {}

    servers = dict(active) if isinstance(active, dict) else {}
    identity_key = str(identity or "").strip().lower()
    if identity_key:
        servers = {
            name: cfg
            for name, cfg in servers.items()
            if (
                not str(name).lower().startswith("vox-")
                and (
                    not str(name).lower().startswith("playwright-")
                    or _identity_server_owner(name) == identity_key
                )
            )
        }


    servers.setdefault(
        "anam-context",
        {"type": "http", "url": "http://127.0.0.1:8814/mcp"},
    )
    # Stable discovery/execution fallback, shared with the ChatGPT connector.
    # The local adapter keeps credentials out of CLI arguments and tool text.
    servers.setdefault("anam-gateway", {
        "command": sys.executable,
        "args": [str(Path(__file__).resolve().parents[1] / "scripts" / "anam_gateway_mcp.py")],
        "env": {
            **({"ANAM_IDENTITY": identity} if identity_key else {}),
            **({"ANAM_CONVERSATION_ID": conversation_id} if conversation_id else {}),
        },
        "required": True,
    })
    return servers


def write_claude_mcp_config(identity: str = "", conversation_id: str = "") -> Path | None:
    """Write a temp Claude config containing only the bound Anam gateway."""
    servers = _active_bridge_servers(identity, conversation_id)
    gateway = servers.get("anam-gateway") if servers else None
    if not isinstance(gateway, dict):
        return None
    servers = {"anam-gateway": gateway}

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(
            prefix="claude-mcp-",
            suffix=".json",
            dir=str(DATA_DIR),
        )
        os.close(fd)
        path = Path(raw_path)
        path.write_text(
            json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
    except Exception:
        log.exception("Failed to write temporary Claude MCP config")
        return None


def build_codex_mcp_overrides(*, identity: str = "", conversation_id: str = "",
                              bypass_approvals: bool = False) -> list[str]:
    """Give one-shot Codex only the small, identity-bound Anam gateway.

    Codex has no Claude-style deferred MCP schema loader. Supplying every
    direct server therefore recreates the hundreds-of-tools flood this adapter
    exists to prevent.
    """
    servers = _active_bridge_servers(identity, conversation_id)
    gateway = servers.get("anam-gateway") if servers else None
    if not isinstance(gateway, dict):
        return []
    servers = {"anam-gateway": gateway}

    overrides: list[str] = []
    for server_name, cfg in sorted(servers.items()):
        if not isinstance(cfg, dict):
            continue
        cfg = _codex_server_config(server_name, cfg)
        prefix = f"mcp_servers.{server_name}"
        _append_scalar(overrides, f"{prefix}.required", cfg.get("required"))
        _append_scalar(overrides, f"{prefix}.startup_timeout_sec", cfg.get("startup_timeout_sec", 60))
        _append_scalar(overrides, f"{prefix}.tool_timeout_sec", cfg.get("tool_timeout_sec", 600))
        _append_scalar(overrides, f"{prefix}.url", cfg.get("url"))
        _append_scalar(overrides, f"{prefix}.command", cfg.get("command"))
        _append_array(overrides, f"{prefix}.args", cfg.get("args"))
        _append_scalar(overrides, f"{prefix}.cwd", cfg.get("cwd"))
        _append_scalar(
            overrides,
            f"{prefix}.bearer_token_env_var",
            cfg.get("bearer_token_env_var") or cfg.get("bearerTokenEnvVar"),
        )
        _append_scalar(
            overrides,
            f"{prefix}.default_tools_approval_mode",
            cfg.get("default_tools_approval_mode") or cfg.get("approval_mode") or cfg.get("approvalMode") or ("approve" if bypass_approvals else None),
        )
        _append_table(overrides, f"{prefix}.env", cfg.get("env"))
        _append_table(
            overrides,
            f"{prefix}.http_headers",
            cfg.get("headers") or cfg.get("http_headers") or cfg.get("httpHeaders"),
        )
        _append_table(
            overrides,
            f"{prefix}.env_http_headers",
            cfg.get("env_http_headers") or cfg.get("envHttpHeaders"),
        )

    return overrides


def build_codex_app_server_mcp_overrides(*, identity: str = "", conversation_id: str = "",
                                         bypass_approvals: bool = False) -> list[str]:
    """Give each retained Codex app-server only Anam's gateway adapter.

    App-server has no ``--ignore-user-config`` flag and deep-merges ``-c``
    overrides with the user's global MCP table, so the adapter keeps its
    collision-safe ``anam_`` prefix. All Anam-owned tools -- local and remote --
    stay reachable through this one tiny per-session stdio process. It forwards
    to Anam's shared gateway, which dispatches concurrent calls through the one
    MCP bridge forest owned by the main service. Never add direct Anam servers
    here: doing so multiplies connector clients and local subprocess trees by
    every retained identity session.
    """
    servers = _active_bridge_servers(identity, conversation_id)
    gateway = servers.get("anam-gateway") if servers else None
    if not isinstance(gateway, dict):
        return []

    overrides: list[str] = []
    if bypass_approvals:
        # Anam's existing bypass setting also applies to connected app tools.
        # approvalPolicy=never alone rejects their prompts rather than allowing
        # the calls. Explicit per-app/per-tool policies still take precedence.
        overrides.append('apps._default.default_tools_approval_mode="approve"')
    prefix = "mcp_servers.anam_anam-gateway"
    _append_scalar(overrides, f"{prefix}.required", gateway.get("required"))
    _append_scalar(overrides, f"{prefix}.startup_timeout_sec", gateway.get("startup_timeout_sec", 60))
    _append_scalar(overrides, f"{prefix}.tool_timeout_sec", gateway.get("tool_timeout_sec", 600))
    _append_scalar(overrides, f"{prefix}.url", gateway.get("url"))
    _append_scalar(overrides, f"{prefix}.command", gateway.get("command"))
    _append_array(overrides, f"{prefix}.args", gateway.get("args"))
    _append_scalar(overrides, f"{prefix}.cwd", gateway.get("cwd"))
    _append_scalar(
        overrides,
        f"{prefix}.bearer_token_env_var",
        gateway.get("bearer_token_env_var") or gateway.get("bearerTokenEnvVar"),
    )
    _append_scalar(
        overrides,
        f"{prefix}.default_tools_approval_mode",
        gateway.get("default_tools_approval_mode")
        or gateway.get("approval_mode")
        or gateway.get("approvalMode")
        or ("approve" if bypass_approvals else None),
    )
    _append_table(overrides, f"{prefix}.env", gateway.get("env"))
    _append_table(
        overrides,
        f"{prefix}.http_headers",
        gateway.get("headers") or gateway.get("http_headers") or gateway.get("httpHeaders"),
    )
    _append_table(
        overrides,
        f"{prefix}.env_http_headers",
        gateway.get("env_http_headers") or gateway.get("envHttpHeaders"),
    )
    return overrides


def _codex_server_config(server_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate bridge config into the most reliable Codex-local transport."""
    script = _CODEX_LOCAL_STDIO_FALLBACKS.get(server_name)
    if script is None or not script.is_file():
        return cfg
    return {
        "command": sys.executable,
        "args": [str(script)],
        "cwd": str(script.parent),
    }


def _append_scalar(target: list[str], key: str, value: Any) -> None:
    if value is None:
        return
    target.append(f"{key}={_toml_literal(value)}")


def _append_array(target: list[str], key: str, value: Any) -> None:
    if not isinstance(value, list):
        return
    target.append(f"{key}={json.dumps(value, ensure_ascii=False)}")


def _append_table(target: list[str], prefix: str, value: Any) -> None:
    if not isinstance(value, dict):
        return
    for item_key, item_value in sorted(value.items()):
        if item_value is None:
            continue
        target.append(f"{prefix}.{item_key}={_toml_literal(item_value)}")


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)
