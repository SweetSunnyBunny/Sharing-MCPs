from unittest.mock import patch

from services.tool_loop_guard import ToolLoopGuard


def test_repeated_identical_failure_warns():
    guard = ToolLoopGuard()
    first = guard.after_call("broken_tool", {"x": 1}, "Error: unavailable")
    second = guard.after_call("broken_tool", {"x": 1}, "Error: unavailable")
    assert first.action == "allow"
    assert second.action == "warn"
    assert "identical" in second.message


def test_repeated_read_only_result_warns():
    guard = ToolLoopGuard()
    guard.after_call("search_history", {"query": "same"}, '{"results": []}')
    decision = guard.after_call("search_history", {"query": "same"}, '{"results": []}')
    assert decision.action == "warn"
    assert "same result" in decision.message


def test_hard_stop_blocks_after_threshold():
    with patch.dict(
        "os.environ",
        {
            "ANAM_TOOL_LOOP_HARD_STOP": "true",
            "ANAM_TOOL_LOOP_EXACT_BLOCK": "3",
        },
    ):
        guard = ToolLoopGuard()
    for _ in range(3):
        guard.after_call("broken_tool", {"x": 1}, "Error: unavailable")
    assert guard.before_call("broken_tool", {"x": 1}).blocks
