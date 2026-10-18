'Application module.'

from services.mcp_bridge import MCPBridge


def _bridge(global_=(), by_server=()):
    b = MCPBridge()
    b._disabled_tools_global = set(global_)
    b._disabled_tools_by_server = set(by_server)
    return b


def test_server_scoped_prefix_hides_family_only_on_that_server():
    b = _bridge(by_server={("elevenlabs-creative", "agents_*")})
    assert b._is_tool_disabled("elevenlabs-creative", "agents_create")
    assert b._is_tool_disabled("elevenlabs-creative", "agents_update_agent_behavior")
    assert not b._is_tool_disabled("elevenlabs-creative", "creative_design_voice")
    assert not b._is_tool_disabled("other-server", "agents_create")


def test_exact_names_still_work():
    b = _bridge(global_={"phone_speak"}, by_server={("krita", "krita_request_save")})
    assert b._is_tool_disabled("phone", "phone_speak")
    assert b._is_tool_disabled("krita", "krita_request_save")
    assert not b._is_tool_disabled("krita", "krita_save_as")


def test_global_prefix():
    b = _bridge(global_={"legacy_*"})
    assert b._is_tool_disabled("any", "legacy_thing")
    assert not b._is_tool_disabled("any", "legac")
