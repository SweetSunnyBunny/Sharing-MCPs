"""Tests for the repeated-identical-call loop guard.

Covers the native ring in services.mcp_bridge.MCPBridge.call_tool and the
Claude Code PreToolUse hook script at tools/tool_loop_guard.py.
"""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.mcp_bridge import MCPBridge


def test_loop_ring_counts_consecutive_identical_calls():
    bridge = MCPBridge()
    assert bridge._track_loop_call("s", "tool_a", {"x": 1}) == 1
    assert bridge._track_loop_call("s", "tool_a", {"x": 1}) == 2
    assert bridge._track_loop_call("s", "tool_a", {"x": 2}) == 1  # arg change resets
    assert bridge._track_loop_call("s", "tool_a", {"x": 2}) == 2
    assert bridge._track_loop_call("s", "tool_b", {"x": 2}) == 1  # name change resets


def test_loop_ring_hash_is_stable_across_key_order():
    bridge = MCPBridge()
    bridge._track_loop_call("s", "t", {"a": 1, "b": [1, {"c": 2}]})
    assert bridge._track_loop_call("s", "t", {"b": [1, {"c": 2}], "a": 1}) == 2


def test_loop_ring_sessions_are_isolated():
    bridge = MCPBridge()
    assert bridge._track_loop_call("s1", "t", {}) == 1
    assert bridge._track_loop_call("s2", "t", {}) == 1
    assert bridge._track_loop_call("s1", "t", {}) == 2


def test_loop_ring_session_map_is_pruned():
    bridge = MCPBridge()
    for i in range(bridge._LOOP_MAX_SESSIONS + 5):
        bridge._track_loop_call(f"session-{i}", "t", {})
    assert len(bridge._loop_rings) == bridge._LOOP_MAX_SESSIONS
    assert "session-0" not in bridge._loop_rings


def test_call_tool_warns_on_third_and_denies_on_sixth():
    bridge = MCPBridge()
    bridge._tool_server_map["t"] = "srv"

    async def run_calls():
        with patch.object(
            MCPBridge, "_reconnect_client", new=AsyncMock(return_value=False)
        ):
            return [
                await bridge.call_tool("t", {"x": 1}, session_key="sess")
                for _ in range(7)
            ]

    results = asyncio.run(run_calls())

    assert "[loop-guard]" not in results[0]
    assert "[loop-guard]" not in results[1]
    for i in (2, 3, 4):
        assert "[loop-guard]" in results[i]
        assert f"{i + 1} times" in results[i]
    # 6th identical call is denied without executing
    assert "the result will not change" in results[5]
    assert "Error" not in results[5]
    # continued attempts stay denied
    assert "the result will not change" in results[6]


def test_call_tool_changed_arguments_reset_the_run():
    bridge = MCPBridge()
    bridge._tool_server_map["t"] = "srv"

    async def run_calls():
        with patch.object(
            MCPBridge, "_reconnect_client", new=AsyncMock(return_value=False)
        ):
            results = [
                await bridge.call_tool("t", {"x": 1}, session_key="sess")
                for _ in range(2)
            ]
            results.append(await bridge.call_tool("t", {"x": 2}, session_key="sess"))
            return results

    results = asyncio.run(run_calls())
    assert all("[loop-guard]" not in r for r in results)


def test_call_tool_without_session_key_is_exempt():
    """Infrastructure callers (hub polling, context hooks) pass no session_key
    and repeat identical calls by design — the guard must never arm for them."""
    bridge = MCPBridge()
    bridge._tool_server_map["t"] = "srv"

    async def run_calls():
        with patch.object(
            MCPBridge, "_reconnect_client", new=AsyncMock(return_value=False)
        ):
            return [await bridge.call_tool("t", {"x": 1}) for _ in range(8)]

    results = asyncio.run(run_calls())
    assert all("[loop-guard]" not in r for r in results)
    assert all("the result will not change" not in r for r in results)


def _load_hook_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "tool_loop_guard.py"
    spec = importlib.util.spec_from_file_location("tool_loop_guard_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_script_run_counting(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(mod, "STATE_DIR", str(tmp_path))
    runs = [mod.record_call("sess-1", "Read", {"file_path": "x"}) for _ in range(6)]
    assert runs == [1, 2, 3, 4, 5, 6]
    assert mod.record_call("sess-1", "Read", {"file_path": "y"}) == 1
    # sessions are isolated by state file
    assert mod.record_call("sess-2", "Read", {"file_path": "y"}) == 1


def test_hook_script_ring_is_bounded(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(mod, "STATE_DIR", str(tmp_path))
    for _ in range(20):
        mod.record_call("sess-ring", "Bash", {"command": "ls"})
    ring = mod._load_ring(mod._state_path("sess-ring"))
    assert len(ring) == mod.RING_SIZE


def test_hook_script_hash_is_stable_across_key_order(tmp_path, monkeypatch):
    mod = _load_hook_module()
    monkeypatch.setattr(mod, "STATE_DIR", str(tmp_path))
    assert mod.stable_call_hash("T", {"a": 1, "b": 2}) == mod.stable_call_hash(
        "T", {"b": 2, "a": 1}
    )
    assert mod.stable_call_hash("T", {"a": 1}) != mod.stable_call_hash("U", {"a": 1})
