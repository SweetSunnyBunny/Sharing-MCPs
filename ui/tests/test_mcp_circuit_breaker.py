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
