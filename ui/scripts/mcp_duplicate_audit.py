#!/usr/bin/env python3
"""Audit MCP duplicate tool names and optionally apply resolution rules.

Usage examples:
  python scripts/mcp_duplicate_audit.py
  python scripts/mcp_duplicate_audit.py --keep add_journal_entry=memory-core --apply
  python scripts/mcp_duplicate_audit.py --apply --fallback first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import MCP_SERVERS_FILE
from services.mcp_bridge import MCPBridge, _strip_tool_marker_prefixes


def _parse_keep_overrides(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"Invalid --keep value '{item}'. Expected TOOL=SERVER.")
        tool_name, server_name = item.split("=", 1)
        tool = tool_name.strip()
        server = server_name.strip()
        if not tool or not server:
            raise ValueError(f"Invalid --keep value '{item}'. Expected TOOL=SERVER.")
        out[tool] = server
    return out


def _load_local_config() -> dict:
    if not MCP_SERVERS_FILE.exists():
        return {}
    return json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))


def _extract_existing_disabled_pairs(data: dict) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for raw in data.get("disabledTools", []):
        cleaned = _strip_tool_marker_prefixes(str(raw))
        if ":" not in cleaned:
            continue
        server_name, tool_name = cleaned.split(":", 1)
        server = server_name.strip()
        tool = tool_name.strip()
        if server and tool:
            pairs.add((server, tool))
    return pairs


def _apply_resolutions(data: dict, resolutions: dict[str, str], duplicates: dict[str, list[str]]):
    preferred = data.get("preferredToolServers")
    if not isinstance(preferred, dict):
        preferred = {}
    for tool_name, keep_server in resolutions.items():
        preferred[tool_name] = keep_server
    data["preferredToolServers"] = preferred

    disabled = data.get("disabledTools")
    if not isinstance(disabled, list):
        disabled = []
    existing_pairs = _extract_existing_disabled_pairs(data)

    for tool_name, servers in duplicates.items():
        keep_server = resolutions.get(tool_name)
        if not keep_server:
            continue
        for server_name in servers:
            if server_name == keep_server:
                continue
            pair = (server_name, tool_name)
            if pair in existing_pairs:
                continue
            disabled.append(f"##nolongerused @mcptool {server_name}:{tool_name}")
            existing_pairs.add(pair)

    data["disabledTools"] = disabled


async def _collect_duplicates(bridge: MCPBridge) -> tuple[dict[str, list[str]], dict[str, int]]:
    config, _configured, _skipped = bridge._load_config(emit_logs=False)
    await asyncio.gather(*[bridge._connect_server(name, cfg) for name, cfg in config.items()])

    tool_to_servers: dict[str, set[str]] = defaultdict(set)
    per_server_counts: dict[str, int] = {}

    for server_name in sorted(bridge._clients.keys()):
        client = bridge._clients[server_name]
        tools = await client.list_tools()
        per_server_counts[server_name] = len(tools)
        for tool in tools:
            tool_name = tool.name
            if bridge._is_tool_disabled(server_name, tool_name):
                continue
            tool_to_servers[tool_name].add(server_name)

    duplicates = {
        tool_name: sorted(servers)
        for tool_name, servers in tool_to_servers.items()
        if len(servers) > 1
    }
    return dict(sorted(duplicates.items())), dict(sorted(per_server_counts.items()))


def _resolve_duplicates(
    duplicates: dict[str, list[str]],
    keep_overrides: dict[str, str],
    preferred: dict[str, str],
    fallback: str,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    resolved: dict[str, str] = {}
    unresolved: dict[str, list[str]] = {}

    for tool_name, servers in duplicates.items():
        chosen = None
        if tool_name in keep_overrides and keep_overrides[tool_name] in servers:
            chosen = keep_overrides[tool_name]
        elif tool_name in preferred and preferred[tool_name] in servers:
            chosen = preferred[tool_name]
        elif fallback == "first":
            chosen = servers[0]

        if chosen:
            resolved[tool_name] = chosen
        else:
            unresolved[tool_name] = servers

    return resolved, unresolved


async def _async_main(args: argparse.Namespace) -> int:
    local_config = _load_local_config()
    preferred = local_config.get("preferredToolServers")
    preferred_map = preferred if isinstance(preferred, dict) else {}
    keep_overrides = _parse_keep_overrides(args.keep)

    bridge = MCPBridge()
    duplicates: dict[str, list[str]]
    per_server_counts: dict[str, int]
    try:
        duplicates, per_server_counts = await _collect_duplicates(bridge)
    finally:
        await bridge.stop()

    resolved, unresolved = _resolve_duplicates(
        duplicates=duplicates,
        keep_overrides=keep_overrides,
        preferred=preferred_map,
        fallback=args.fallback,
    )

    report = {
        "tool_count_before_dedupe": sum(per_server_counts.values()),
        "connected_server_count": len(per_server_counts),
        "duplicate_tool_count": len(duplicates),
        "duplicates": duplicates,
        "resolved": resolved,
        "unresolved": unresolved,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Connected servers: {len(per_server_counts)}")
        print(f"Duplicate tool names: {len(duplicates)}")
        if duplicates:
            print("")
            for tool_name, servers in duplicates.items():
                marker = ""
                if tool_name in resolved:
                    marker = f" -> keep {resolved[tool_name]}"
                print(f"- {tool_name}: {', '.join(servers)}{marker}")
        if unresolved:
            print("\nUnresolved duplicates:")
            for tool_name, servers in unresolved.items():
                print(f"- {tool_name}: choose one of {', '.join(servers)}")

    if not args.apply:
        return 0

    if unresolved:
        print("\nRefusing to apply while unresolved duplicates remain.")
        return 2

    _apply_resolutions(local_config, resolved, duplicates)
    MCP_SERVERS_FILE.write_text(json.dumps(local_config, indent=2) + "\n", encoding="utf-8")
    print(f"\nApplied duplicate resolution rules to {MCP_SERVERS_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and resolve duplicate MCP tool names.")
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        help="Explicit winner mapping TOOL=SERVER. Can be repeated.",
    )
    parser.add_argument(
        "--fallback",
        choices=("none", "first"),
        default="none",
        help="Auto-pick strategy when no explicit or preferred winner exists.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write resolution rules into mcp-servers.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report.",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
