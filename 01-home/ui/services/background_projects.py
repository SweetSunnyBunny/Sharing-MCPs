"""Bounded completion contracts for Hub background projects."""


from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any


_CONTRACT_RE = re.compile(
    r"\n\[Project Contract\]\s*(\{.*\})\s*\n\[/Project Contract\]",
    re.DOTALL,
)
_STATUS_RE = re.compile(
    r"\s*<project_status\s+state=[\"'](complete|continue|blocked)[\"']\s*>"
    r"(.*?)</project_status>\s*",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ProjectDecision:
    state: str
    reason: str
    should_continue: bool
    turn: int
    max_turns: int


def default_project_squad(identity: str) -> list[dict[str, str]]:
    """The small, honest crew available to a background-project lead.

    These are seats at the workbench, not claims that every agent has acted.
    The bonded identity remains the lead and decides which specialist actually
    fits the card instead of blindly marching every task through four models.
    """
    lead = str(identity or "Lead").strip()[:64] or "Lead"
    return [
        {"role": "Scout", "agent": "research-runner"},
        {"role": "Lead", "agent": lead},
        {"role": "Review", "agent": "code-reviewer"},
        {"role": "Handoff", "agent": "state-keeper"},
    ]


def normalize_squad(squad: Any) -> list[dict[str, str]]:
    """Validate the tiny role/agent pairs carried inside a project contract."""
    if not isinstance(squad, list):
        return []
    clean: list[dict[str, str]] = []
    for seat in squad[:6]:
        if not isinstance(seat, dict):
            continue
        role = str(seat.get("role") or "").strip()[:32]
        agent = str(seat.get("agent") or "").strip()[:64]
        if role and agent:
            clean.append({"role": role, "agent": agent})
    return clean


def normalize_contract(
    *,
    outcome: str = "",
    verification: str = "",
    boundaries: str = "",
    stop_when: str = "",
    max_turns: int = 4,
    project_id: str | None = None,
    turn: int = 1,
    squad: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        parsed_turn = int(turn)
    except (TypeError, ValueError):
        parsed_turn = 1
    try:
        parsed_max_turns = int(max_turns)
    except (TypeError, ValueError):
        parsed_max_turns = 4
    return {
        "project_id": project_id or str(uuid.uuid4()),
        "outcome": str(outcome or "Complete the requested project and leave a usable result.").strip(),
        "verification": str(verification or "Run the most relevant safe checks and report evidence.").strip(),
        "boundaries": str(boundaries or "Stay within the requested scope; do not deploy or contact people unless explicitly authorized.").strip(),
        "stop_when": str(stop_when or "The outcome is achieved and verification passes, or progress is genuinely blocked.").strip(),
        "turn": max(1, parsed_turn),
        "max_turns": min(8, max(1, parsed_max_turns)),
        "squad": normalize_squad(squad),
    }


def build_project_context(prompt: str, contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
    squad = normalize_squad(contract.get("squad"))
    squad_line = ""
    if squad:
        seats = ", ".join(f"{s['role']}={s['agent']}" for s in squad)
        squad_line = (
            f"Your available squad is {seats}. These are helpers, not mandatory gates: "
            "delegate only when a seat genuinely improves the work, and never claim a "
            "specialist acted unless you actually called it. "
        )
    return (
        "[Background Project] Owner asked you to work on this while she's away:\n\n"
        f"{prompt.strip()}\n\n"
        f"[Project Contract]\n{payload}\n[/Project Contract]\n\n"
        "Use your real tools and agents to make concrete progress. "
        + squad_line
        + "Work against the outcome, "
        "verification, boundaries, and stop condition above. Do not merely plan. At the very end "
        "of your briefing emit exactly one machine-readable status marker: "
        "<project_status state=\"complete\">why verification passed</project_status>, "
        "<project_status state=\"continue\">what remains</project_status>, or "
        "<project_status state=\"blocked\">the concrete blocker</project_status>. "
        "Anam may schedule another bounded pass when you choose continue. On the final pass, "
        "leave Owner a clear, warm briefing and post it in your Discord den when available."
    )


def parse_contract(context: str) -> dict[str, Any] | None:
    match = _CONTRACT_RE.search(str(context or ""))
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_contract(
        outcome=payload.get("outcome", ""),
        verification=payload.get("verification", ""),
        boundaries=payload.get("boundaries", ""),
        stop_when=payload.get("stop_when", ""),
        max_turns=payload.get("max_turns", 4),
        project_id=payload.get("project_id"),
        turn=payload.get("turn", 1),
        squad=payload.get("squad"),
    )


def evaluate_project_response(context: str, content: str) -> tuple[str, ProjectDecision]:
    contract = parse_contract(context)
    if not contract:
        return content, ProjectDecision("complete", "legacy project", False, 1, 1)

    matches = list(_STATUS_RE.finditer(str(content or "")))
    state = matches[-1].group(1).lower() if matches else "continue"
    reason = matches[-1].group(2).strip() if matches else "No completion marker was returned."
    cleaned = _STATUS_RE.sub("\n", str(content or "")).strip()
    turn = int(contract["turn"])
    max_turns = int(contract["max_turns"])

    if state == "continue" and turn >= max_turns:
        return cleaned, ProjectDecision(
            "blocked",
            f"Bounded continuation stopped at the {max_turns}-turn limit: {reason}",
            False,
            turn,
            max_turns,
        )
    return cleaned, ProjectDecision(state, reason, state == "continue", turn, max_turns)


def advance_project_context(context: str) -> str:
    contract = parse_contract(context)
    if not contract:
        return context
    contract["turn"] = min(int(contract["max_turns"]), int(contract["turn"]) + 1)
    replacement = (
        "\n[Project Contract]\n"
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
        + "\n[/Project Contract]"
    )
    return _CONTRACT_RE.sub(replacement, context, count=1)
