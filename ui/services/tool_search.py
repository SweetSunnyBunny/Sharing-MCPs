"""Progressive disclosure for large MCP tool catalogs.

Direct API providers otherwise send every MCP schema on every model call.  In
auto mode this module replaces a large MCP catalog with three small bridge
tools.  Core Anam tools remain directly visible.
"""

# ANAM GUIDE: MCP TOOL CATALOG SHRINKER
# What: When the MCP tool list is huge, this hides it behind three small "search /
#       describe / call" bridge tools so every paid API call doesn't resend hundreds
#       of tool descriptions (big token savings).
# Called by: services/claude_api.py and services/openai_provider.py (direct-API
#            providers only — the Claude Code CLI path doesn't need it).
# Edit here when: You want to change when it kicks in (ANAM_MCP_TOOL_SEARCH env vars,
#                 minimum tool count/size) or how tool search ranking works.

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from typing import Any


BRIDGE_NAMES = {"anam_tool_search", "anam_tool_describe", "anam_tool_call"}
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall((value or "").lower())


def _load_taxonomy() -> dict:
    """Read toolTaxonomy from mcp-servers.json (slice 3 of the unified tool."""
    try:
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "mcp-servers.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        taxonomy = data.get("toolTaxonomy") or {}
        return {k: v for k, v in taxonomy.items()
                if not k.startswith("_") and isinstance(v, dict)}
    except Exception:
        return {}


class ToolSearchCatalog:
    def __init__(self, tools: list[dict]):
        self.tools = {str(tool.get("name")): dict(tool) for tool in tools if tool.get("name")}
        # Category boost map: keyword token -> set of server substrings.
        # A query token that matches a category keyword boosts every tool
        # belonging to that category's servers.
        self._keyword_servers: dict[str, set[str]] = {}
        for cat in _load_taxonomy().values():
            servers = {str(s).lower() for s in (cat.get("servers") or [])}
            if not servers:
                continue
            for keyword in cat.get("keywords") or []:
                for token in _tokens(str(keyword)):
                    self._keyword_servers.setdefault(token, set()).update(servers)
        self._docs: dict[str, Counter[str]] = {}
        document_frequency: Counter[str] = Counter()
        for name, tool in self.tools.items():
            schema = tool.get("input_schema") or {}
            properties = schema.get("properties") if isinstance(schema, dict) else {}
            haystack = " ".join(
                [name, str(tool.get("description") or ""), *map(str, (properties or {}).keys())]
            )
            counts = Counter(_tokens(haystack))
            self._docs[name] = counts
            document_frequency.update(counts.keys())
        size = max(1, len(self._docs))
        self._idf = {
            token: math.log(1 + (size - freq + 0.5) / (freq + 0.5))
            for token, freq in document_frequency.items()
        }

    @classmethod
    def maybe_build(cls, tools: list[dict]) -> "ToolSearchCatalog | None":
        mode = os.environ.get("ANAM_MCP_TOOL_SEARCH", "auto").strip().lower()
        if mode in {"0", "false", "off", "disabled"} or not tools:
            return None
        if mode not in {"1", "true", "on", "enabled"}:
            minimum_tools = max(1, int(os.environ.get("ANAM_MCP_TOOL_SEARCH_MIN_TOOLS", "20")))
            minimum_chars = max(1, int(os.environ.get("ANAM_MCP_TOOL_SEARCH_MIN_CHARS", "30000")))
            schema_chars = len(json.dumps(tools, ensure_ascii=False, default=str))
            if len(tools) < minimum_tools or schema_chars < minimum_chars:
                return None
        return cls(tools)

    def bridge_schemas(self) -> list[dict]:
        count = len(self.tools)
        return [
            {
                "name": "anam_tool_search",
                "description": f"Search {count} deferred MCP tools by capability before calling one.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Capability or task to search for."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "anam_tool_describe",
                "description": "Return the complete schema for one deferred MCP tool.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "anam_tool_call",
                "description": "Call a deferred MCP tool after finding or describing it.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["name", "arguments"],
                },
            },
        ]

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        scored: list[tuple[float, str]] = []
        for name, counts in self._docs.items():
            score = 0.0
            lower_name = name.lower()
            for token in query_tokens:
                score += counts.get(token, 0) * self._idf.get(token, 0.25)
                if token in lower_name:
                    score += 2.0
                # Taxonomy category boost: query token matches a category
                # keyword and this tool lives on one of that category's servers.
                for server in self._keyword_servers.get(token, ()):
                    if server in lower_name:
                        score += 3.0
                        break
            if query.strip().lower() in lower_name:
                score += 6.0
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        output = []
        for score, name in scored[: max(1, min(int(limit or 5), 20))]:
            tool = self.tools[name]
            output.append({
                "name": name,
                "description": str(tool.get("description") or "")[:400],
                "score": round(score, 3),
            })
        return output

    def describe(self, name: str) -> dict:
        tool = self.tools.get(str(name or ""))
        if not tool:
            return {"ok": False, "error": f"Unknown deferred tool: {name}"}
        return {"ok": True, "tool": tool}

    def target(self, bridge_name: str, arguments: dict) -> tuple[str, dict]:
        if bridge_name == "anam_tool_call":
            target = str(arguments.get("name") or "")
            raw_args = arguments.get("arguments") or {}
            return target, raw_args if isinstance(raw_args, dict) else {}
        return bridge_name, arguments

    async def execute(self, bridge_name: str, arguments: dict, mcp_bridge) -> str:
        if bridge_name == "anam_tool_search":
            return json.dumps(
                {"matches": self.search(str(arguments.get("query") or ""), arguments.get("limit") or 5)},
                ensure_ascii=False,
            )
        if bridge_name == "anam_tool_describe":
            return json.dumps(self.describe(str(arguments.get("name") or "")), ensure_ascii=False)
        if bridge_name == "anam_tool_call":
            target, target_args = self.target(bridge_name, arguments)
            if target not in self.tools:
                return f"Error: deferred tool '{target}' is not available in this session"
            return await mcp_bridge.call_tool(target, target_args)
        return f"Error: unknown tool-search bridge '{bridge_name}'"
