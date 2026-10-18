from unittest.mock import patch

from services.mcp_bridge import MCPBridge


def test_mcp_circuit_opens_and_success_closes_it():
    with patch.dict(
        "os.environ",
        {"ANAM_MCP_CIRCUIT_FAILURES": "2", "ANAM_MCP_CIRCUIT_COOLDOWN_SECONDS": "60"},
    ):
        bridge = MCPBridge()
    bridge._record_server_failure("example")
    assert bridge._circuit_error("example") is None
    bridge._record_server_failure("example")
    assert "circuit is open" in bridge._circuit_error("example")
    bridge._record_server_success("example")
    assert bridge._circuit_error("example") is None


import asyncio

from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData


class _FakeClient:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    async def call_tool(self, name, arguments, timeout=None):
        self.calls += 1
        raise self.exc


def _bridge_with(exc):
    bridge = MCPBridge()
    bridge._tool_server_map["probe_tool"] = "example"
    bridge._clients["example"] = _FakeClient(exc)
    reconnects = []

    async def _no_reconnect(server_name):
        reconnects.append(server_name)
        return False

    bridge._reconnect_client = _no_reconnect
    return bridge, reconnects


def test_tool_error_keeps_its_words_and_spares_the_circuit():
    # "session" and "closed" are disconnect markers; before the fix this
    # healthy answer tore the connection down and counted as a failure.
    bridge, reconnects = _bridge_with(ToolError("no active session; the door is closed"))
    result = asyncio.run(bridge.call_tool("probe_tool", {}))
    assert "no active session" in result
    assert "delivery uncertain" not in result
    assert reconnects == []
    assert bridge._server_error_counts.get("example", 0) == 0


def test_sdk_timeout_is_recognised_as_timeout_and_pinned_cap_spares_circuit():
    exc = McpError(ErrorData(code=408, message="Timed out while waiting for response to CallToolRequest"))
    bridge, _ = _bridge_with(exc)
    bridge._retry_safe_tools.add(("example", "probe_tool"))
    result = asyncio.run(bridge.call_tool("probe_tool", {}, timeout=1))
    assert "Timed out" in result
    # A caller-pinned timeout is about the caller's patience, not server health.
    assert bridge._server_error_counts.get("example", 0) == 0
