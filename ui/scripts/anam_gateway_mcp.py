"""Stable MCP adapter shared by Codex stdio and the FileSystem+ machine agent."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolResult

ROOT = Path(__file__).resolve().parents[1]
BOUND_IDENTITY = os.getenv("ANAM_IDENTITY", "").strip()
BOUND_CONVERSATION_ID = os.getenv("ANAM_CONVERSATION_ID", "").strip()
mcp = FastMCP(
    "Anam",
    instructions=(
        "Use the single anam tool as an identity-bound toolbox. Start with "
        "operation=discover, then use operation=invoke with the returned exact "
        "server, tool, and schema. Use operation=job to collect running work and "
        "operation=result to page or search saved output. Never resubmit a running action."
    ),
)


async def _request(payload):
    key = os.getenv("ANAM_API_KEY") or dotenv_values(ROOT / ".env").get("ANAM_API_KEY")
    if not key:
        raise RuntimeError("Anam machine authentication is not configured")
    async with httpx.AsyncClient(timeout=75, trust_env=False) as client:
        response = await client.post("http://127.0.0.1:8790/api/tool-gateway",
                                     headers={"Authorization": f"Bearer {key}"}, json=payload)
    if response.is_error:
        detail = response.json().get("detail", "Anam gateway failed")
        raise RuntimeError(f"Anam gateway HTTP {response.status_code}: {detail}")
    return response.json()


async def anam_discover(query: str = "", server: str = "", offset: int = 0, limit: int = 15, include_schema: bool = True):
    """Find live Anam tools and their exact input schemas. Empty query lists tools and all server names; select server to see its fresh catalog, including identity browser profiles."""
    return await _request({"operation": "discover", "query": query, "server": server, "offset": offset, "limit": limit, "include_schema": include_schema, "identity": BOUND_IDENTITY, "conversation_id": BOUND_CONVERSATION_ID})


async def anam_invoke(server: str, tool: str, arguments_json: str, identity: str,
                      conversation_id: str = "", request_id: str = ""):
    """Execute a discovered Anam tool once (may read, write, or control the computer). arguments_json must match its inputSchema. Use your active identity and a unique request_id; reuse that ID only for a transport retry. Poll running jobs with anam_job instead of resubmitting."""
    arguments = json.loads(arguments_json)
    if not isinstance(arguments, dict):
        raise ValueError("arguments_json must encode an object")
    effective_identity = BOUND_IDENTITY or identity
    effective_conversation_id = BOUND_CONVERSATION_ID or conversation_id
    return await _request({"operation": "invoke", "server": server, "tool": tool, "arguments": arguments,
                           "identity": effective_identity, "conversation_id": effective_conversation_id, "request_id": request_id})


async def anam_job(job_id: str):
    """Collect a running Anam tool's result without repeating the action. Completed results retain native image/audio content. Jobs survive a caller disconnect. Completed results survive restarts for up to seven days, subject to storage limits. Interrupted work is uncertain, never replayed. Large text results have an anam_result reference."""
    return await _request({"operation": "job", "job_id": job_id, "identity": BOUND_IDENTITY, "conversation_id": BOUND_CONVERSATION_ID})


async def anam_result(job_id: str, offset: int = 0, limit: int = 8000, query: str = ""):
    """Read or search a saved result without re-executing its tool. Offsets are characters; limit is at most 16000. Use next_offset to continue. query finds a matching excerpt."""
    return await _request({"operation":"result", "job_id":job_id, "offset":offset, "result_limit":limit, "query":query, "identity":BOUND_IDENTITY, "conversation_id":BOUND_CONVERSATION_ID})


def _native(result):
    if result.get("status") != "completed":
        return result
    native = CallToolResult.model_validate({k: result[k] for k in ("content", "isError", "structuredContent") if k in result})
    if native.isError:
        raise ToolError("\n".join(b.text for b in native.content if hasattr(b, "text")) or "Anam tool failed")
    return ToolResult(content=native.content, structured_content=native.structuredContent)


@mcp.tool(
    name="anam",
    description=(
        "Open one compartment of the active identity's Anam toolbox. "
        "operation=discover uses query/server/offset/limit/include_schema. "
        "operation=invoke uses server/tool/arguments_json/request_id. "
        "operation=job uses job_id. operation=result uses "
        "job_id/offset/limit/query. Identity and conversation are already bound "
        "to this session. Discover before invoking; collect a running job rather "
        "than repeating it. Invoked tools may read, write, communicate, or control devices."
    ),
    annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
)
async def anam(
    operation: Literal["discover", "invoke", "job", "result"],
    query: str = "",
    server: str = "",
    tool: str = "",
    arguments_json: str = "{}",
    job_id: str = "",
    offset: int = 0,
    limit: int = 15,
    include_schema: bool = True,
    request_id: str = "",
):
    """Route one bounded operation through the identity-bound Anam gateway."""
    if operation == "discover":
        return _native(await anam_discover(query, server, offset, limit, include_schema))
    if operation == "invoke":
        if not server or not tool:
            raise ValueError("operation=invoke requires server and tool")
        if not BOUND_IDENTITY:
            raise ValueError("This Anam toolbox is not bound to an identity")
        return _native(await anam_invoke(
            server,
            tool,
            arguments_json,
            BOUND_IDENTITY,
            BOUND_CONVERSATION_ID,
            request_id,
        ))
    if not job_id:
        raise ValueError(f"operation={operation} requires job_id")
    if operation == "job":
        return _native(await anam_job(job_id))
    return _native(await anam_result(job_id, offset, limit, query))


if __name__ == "__main__":
    mcp.run(transport="stdio")
