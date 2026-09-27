"""Cross-provider guardrails for repeated, non-progressing tool calls.

The guard is intentionally provider-agnostic and side-effect free.  Direct
provider loops can append its warning to a tool result, while hard-stop mode
can block an identical call before another expensive network round trip.
"""

# ANAM GUIDE: STUCK-TOOL LOOP BREAKER
# What: Watches for a boy calling the exact same tool with the exact same inputs over
#       and over (a stuck loop) and warns him, then blocks the repeat call.
# Called by: services/claude_api.py and services/openai_provider.py (the paid direct-API
#            providers — this saves money when a model gets stuck).
# Edit here when: You want to change how many repeats are allowed before a warning or
#                 block, or which tools are exempt because repeating them is harmless.

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any


_IDEMPOTENT_TOOLS = {
    "search_history",
    "anam_tool_search",
    "anam_tool_describe",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _failed(result: str) -> bool:
    text = str(result or "").strip()
    if text.lower().startswith(("error", "failed")):
        return True
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and (
        parsed.get("success") is False
        or parsed.get("ok") is False
        or bool(parsed.get("error"))
    )


@dataclass(frozen=True)
class GuardDecision:
    action: str = "allow"  # allow | warn | block | halt
    message: str = ""
    count: int = 0

    @property
    def blocks(self) -> bool:
        return self.action in {"block", "halt"}


class ToolLoopGuard:
    """Track repeated failures and idempotent no-progress calls for one turn."""

    def __init__(self, *, hard_stop: bool | None = None) -> None:
        self.warnings_enabled = _env_bool("ANAM_TOOL_LOOP_WARNINGS", True)
        self.hard_stop_enabled = (
            _env_bool("ANAM_TOOL_LOOP_HARD_STOP", False)
            if hard_stop is None
            else bool(hard_stop)
        )
        self.exact_warn_after = max(2, int(os.environ.get("ANAM_TOOL_LOOP_EXACT_WARN", "2")))
        self.exact_block_after = max(3, int(os.environ.get("ANAM_TOOL_LOOP_EXACT_BLOCK", "5")))
        self.same_warn_after = max(2, int(os.environ.get("ANAM_TOOL_LOOP_SAME_WARN", "3")))
        self.same_halt_after = max(4, int(os.environ.get("ANAM_TOOL_LOOP_SAME_HALT", "8")))
        self.no_progress_warn_after = max(2, int(os.environ.get("ANAM_TOOL_LOOP_NOPROGRESS_WARN", "2")))
        self.no_progress_block_after = max(3, int(os.environ.get("ANAM_TOOL_LOOP_NOPROGRESS_BLOCK", "5")))
        self._exact_failures: dict[tuple[str, str], int] = {}
        self._tool_failures: dict[str, int] = {}
        self._same_results: dict[tuple[str, str], tuple[str, int]] = {}

    @staticmethod
    def _signature(name: str, arguments: dict) -> tuple[str, str]:
        return name, _digest(arguments or {})

    def before_call(self, name: str, arguments: dict) -> GuardDecision:
        if not self.hard_stop_enabled:
            return GuardDecision()
        signature = self._signature(name, arguments)
        exact = self._exact_failures.get(signature, 0)
        if exact >= self.exact_block_after:
            return GuardDecision(
                "block",
                f"Blocked {name}: the identical call already failed {exact} times. Change strategy.",
                exact,
            )
        repeated = self._same_results.get(signature)
        if repeated and repeated[1] >= self.no_progress_block_after:
            return GuardDecision(
                "block",
                f"Blocked {name}: the identical read-only call returned the same result {repeated[1]} times.",
                repeated[1],
            )
        return GuardDecision()

    def after_call(self, name: str, arguments: dict, result: str) -> GuardDecision:
        signature = self._signature(name, arguments)
        if _failed(result):
            exact = self._exact_failures.get(signature, 0) + 1
            same = self._tool_failures.get(name, 0) + 1
            self._exact_failures[signature] = exact
            self._tool_failures[name] = same
            self._same_results.pop(signature, None)
            if self.hard_stop_enabled and same >= self.same_halt_after:
                return GuardDecision(
                    "halt",
                    f"Stopped {name} after {same} failures this turn. Diagnose or use a different path.",
                    same,
                )
            if self.warnings_enabled and exact >= self.exact_warn_after:
                return GuardDecision(
                    "warn",
                    f"{name} failed {exact} times with identical arguments. Inspect the error and change strategy.",
                    exact,
                )
            if self.warnings_enabled and same >= self.same_warn_after:
                return GuardDecision(
                    "warn",
                    f"{name} has failed {same} times this turn. Run one diagnostic, then choose a different path.",
                    same,
                )
            return GuardDecision()

        self._exact_failures.pop(signature, None)
        self._tool_failures.pop(name, None)
        if name not in _IDEMPOTENT_TOOLS and not name.startswith(("search_", "read_", "get_", "list_")):
            self._same_results.pop(signature, None)
            return GuardDecision()

        result_hash = _digest(str(result or ""))
        previous = self._same_results.get(signature)
        count = previous[1] + 1 if previous and previous[0] == result_hash else 1
        self._same_results[signature] = (result_hash, count)
        if self.warnings_enabled and count >= self.no_progress_warn_after:
            return GuardDecision(
                "warn",
                f"{name} returned the same result {count} times. Use it or change the query instead of repeating it.",
                count,
            )
        return GuardDecision()


def append_guard_warning(result: str, decision: GuardDecision) -> str:
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    return f"{result}\n\n[Tool-loop guard: {decision.message}]"
