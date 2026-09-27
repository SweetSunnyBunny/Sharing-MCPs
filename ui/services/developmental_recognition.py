"""Provider-neutral recognition gate for developmental memory experiments.

Qualia remains the memory system.  This module defines the seam between
retrieval and prompt admission so a typed classifier (Jev or another judge)
can be evaluated without silently becoming a second store or a new source of
truth.

There is deliberately no network client here.  A provider adapter must be a
separate, explicit layer with its own privacy review and configuration.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import os
from typing import Any, Iterable, Mapping, Protocol

import httpx


class RecognitionMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class Recognition(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNCERTAIN = "uncertain"
    NOT_COMPARABLE = "not_comparable"


class Binding(str, Enum):
    BOUND = "bound"
    UNBOUND = "unbound"
    UNCERTAIN = "uncertain"


class ActionRelevance(str, Enum):
    APPLY = "apply"
    BACKGROUND = "background"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class CandidateDecision:
    """Typed judgment about one retrieved candidate.

    ``candidate_ref`` is Qualia's existing provenance-bearing reference.  The
    classifier may judge it, but never gets to replace it.
    """

    candidate_ref: str
    recognition: Recognition
    binding: Binding
    relevance: ActionRelevance
    confidence: float | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_ref.strip():
            raise ValueError("candidate_ref must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class TraceEntry:
    candidate_ref: str
    outcome: str
    reason: str
    decision: CandidateDecision | None = None


@dataclass(frozen=True)
class RecognitionPass:
    packet: dict[str, Any]
    trace: tuple[TraceEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecognitionCandidate:
    """Privacy-minimal candidate projection supplied to a recognizer.

    The autobiographical payload is intentionally absent.  External adapters
    may not add it casually; widening this projection is a separate privacy
    decision.
    """

    ref: str
    section: str
    why: str | None = None
    freshness: str | None = None
    recorded_at: str | None = None


@dataclass(frozen=True)
class RecognitionRequest:
    query: str
    candidates: tuple[RecognitionCandidate, ...]
    data_classification: str = "private"


class RecognitionProvider(Protocol):
    async def classify(self, request: RecognitionRequest) -> Iterable[CandidateDecision]: ...


class FixtureRecognitionProvider:
    """Deterministic provider for synthetic shadow evaluation only."""

    def __init__(self, decisions: Mapping[str, CandidateDecision]):
        self._decisions = dict(decisions)
        self.requests: list[RecognitionRequest] = []

    async def classify(self, request: RecognitionRequest) -> Iterable[CandidateDecision]:
        self.requests.append(request)
        return tuple(
            self._decisions[candidate.ref]
            for candidate in request.candidates
            if candidate.ref in self._decisions
        )


class TypeSafeJevProvider:
    """Typed Jev adapter with a hard synthetic-only default.

    TypeSafe's privacy policy says Input is not used to train or fine-tune
    models, but it is still processed and retained under their service terms.
    Therefore real conversation or memory content is refused unless a caller
    deliberately constructs this adapter with ``allow_private_input=True``.
    """

    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "jev-latest",
        allow_private_input: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("TypeSafe API key must not be empty")
        self.api_key = api_key
        self.model = model
        self.allow_private_input = allow_private_input
        self.client = client or httpx.AsyncClient(timeout=12.0)

    async def classify(self, request: RecognitionRequest) -> Iterable[CandidateDecision]:
        if request.data_classification != "synthetic" and not self.allow_private_input:
            raise PermissionError("TypeSafe adapter refuses private recognition input")

        questions: dict[str, Any] = {}
        for index, _candidate in enumerate(request.candidates):
            questions[f"recognition_{index}"] = {
                "type": "choice",
                "instructions": "Does this candidate describe the same event, fact, or meaning needed by the current query?",
                "criteria": {
                    "match": "Same event, fact, or meaning",
                    "no_match": "A different event, fact, or meaning",
                    "uncertain": "Evidence is insufficient",
                    "not_comparable": "The candidate and query cannot meaningfully be compared",
                },
            }
            questions[f"binding_{index}"] = {
                "type": "choice",
                "instructions": "Is the candidate bound to the correct person, object, or relationship for this query?",
                "criteria": {
                    "bound": "Correct person, object, or relationship",
                    "unbound": "Wrong person, object, or relationship",
                    "uncertain": "Evidence is insufficient",
                },
            }
            questions[f"relevance_{index}"] = {
                "type": "choice",
                "instructions": "How should this candidate affect the current response?",
                "criteria": {
                    "apply": "Use as current relevant evidence",
                    "background": "Keep only as historical or contextual background",
                    "reject": "Do not use for this response",
                    "uncertain": "Evidence is insufficient",
                },
            }

        state = {
            "current_query": request.query,
            "candidates": [
                {
                    "ref": candidate.ref,
                    "section": candidate.section,
                    "why_retrieved": candidate.why,
                    "freshness": candidate.freshness,
                    "recorded_at": candidate.recorded_at,
                }
                for candidate in request.candidates
            ],
        }
        response = await self.client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "state": state, "questions": questions},
        )
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers")
        if not isinstance(answers, Mapping):
            raise ValueError("TypeSafe response has no answers map")

        decisions: list[CandidateDecision] = []
        for index, candidate in enumerate(request.candidates):
            recognition_answer = self._choice(answers, f"recognition_{index}")
            binding_answer = self._choice(answers, f"binding_{index}")
            relevance_answer = self._choice(answers, f"relevance_{index}")
            decisions.append(CandidateDecision(
                candidate_ref=candidate.ref,
                recognition=Recognition(recognition_answer[0]),
                binding=Binding(binding_answer[0]),
                relevance=ActionRelevance(relevance_answer[0]),
                confidence=min(recognition_answer[1], binding_answer[1], relevance_answer[1]),
                reasons=(f"typesafe_model:{payload.get('model', self.model)}",),
            ))
        return tuple(decisions)

    @staticmethod
    def _choice(answers: Mapping[str, Any], key: str) -> tuple[str, float]:
        answer = answers.get(key)
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            raise ValueError(f"TypeSafe response is missing choice answer {key!r}")
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        if not isinstance(choice, str) or not isinstance(confidence, (int, float)):
            raise ValueError(f"TypeSafe choice answer {key!r} is malformed")
        return choice, float(confidence)


def configured_mode() -> RecognitionMode:
    raw = os.getenv("ANAM_DEVELOPMENTAL_RECOGNITION_MODE", "off").strip().lower()
    try:
        return RecognitionMode(raw)
    except ValueError:
        return RecognitionMode.OFF


def project_candidates(packet: Mapping[str, Any]) -> tuple[RecognitionCandidate, ...]:
    """Expose structure and retrieval rationale, never raw autobiographical data."""

    return tuple(
        RecognitionCandidate(
            ref=item["ref"],
            section=section,
            why=item.get("why") if isinstance(item.get("why"), str) else None,
            freshness=item.get("freshness") if isinstance(item.get("freshness"), str) else None,
            recorded_at=item.get("recorded_at") if isinstance(item.get("recorded_at"), str) else None,
        )
        for section, item in _iter_section_items(packet)
    )


def explicit_refs_in_query(query: str, candidates: Iterable[RecognitionCandidate]) -> frozenset[str]:
    lowered = query.lower()
    return frozenset(candidate.ref for candidate in candidates if candidate.ref.lower() in lowered)


async def evaluate_packet(
    packet: Mapping[str, Any],
    query: str,
    *,
    provider: RecognitionProvider | None,
    mode: RecognitionMode | None = None,
) -> RecognitionPass:
    """Run a recognizer safely; disabled or unavailable providers are no-ops."""

    selected_mode = mode or configured_mode()
    if selected_mode == RecognitionMode.OFF or provider is None:
        return apply_recognition(packet, (), mode=RecognitionMode.OFF)

    candidates = project_candidates(packet)
    decisions = tuple(await provider.classify(RecognitionRequest(query=query, candidates=candidates)))
    known_refs = {candidate.ref for candidate in candidates}
    decision_refs = [decision.candidate_ref for decision in decisions]
    if len(decision_refs) != len(set(decision_refs)):
        raise ValueError("recognition provider returned duplicate candidate decisions")
    unknown = set(decision_refs) - known_refs
    if unknown:
        raise ValueError(f"recognition provider returned unknown refs: {sorted(unknown)!r}")

    return apply_recognition(
        packet,
        decisions,
        mode=selected_mode,
        explicit_refs=explicit_refs_in_query(query, candidates),
    )


def _iter_section_items(packet: Mapping[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    sections = packet.get("sections", {})
    if not isinstance(sections, Mapping):
        return
    for section_name, items in sections.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("ref"), str):
                yield str(section_name), item


def _admission_reason(decision: CandidateDecision) -> tuple[bool, str]:
    if decision.recognition in {Recognition.NO_MATCH, Recognition.NOT_COMPARABLE}:
        return False, f"recognition:{decision.recognition.value}"
    if decision.binding == Binding.UNBOUND:
        return False, "binding:unbound"
    if decision.relevance == ActionRelevance.REJECT:
        return False, "relevance:reject"
    # Uncertainty stays visible.  A recognizer is a gate, not an eraser.
    return True, "admitted"


def apply_recognition(
    packet: Mapping[str, Any],
    decisions: Iterable[CandidateDecision],
    *,
    mode: RecognitionMode = RecognitionMode.OFF,
    explicit_refs: Iterable[str] = (),
) -> RecognitionPass:
    """Apply typed judgments without mutating the supplied Qualia packet.

    OFF and SHADOW never change packet admission.  ENFORCE can reject a
    candidate, but an explicit current-turn reference always wins.  Candidates
    without a judgment are retained and called out in the trace rather than
    being silently dropped.
    """

    result = deepcopy(dict(packet))
    if mode == RecognitionMode.OFF:
        return RecognitionPass(packet=result)

    decision_map = {decision.candidate_ref: decision for decision in decisions}
    explicit = set(explicit_refs)
    trace: list[TraceEntry] = []

    sections = result.get("sections", {})
    if not isinstance(sections, dict):
        return RecognitionPass(packet=result)

    for section_name, items in list(sections.items()):
        if not isinstance(items, list):
            continue
        admitted_items: list[Any] = []
        for item in items:
            ref = item.get("ref") if isinstance(item, dict) else None
            if not isinstance(ref, str):
                admitted_items.append(item)
                continue

            decision = decision_map.get(ref)
            if ref in explicit:
                admitted = True
                reason = "current_explicit_intent"
            elif decision is None:
                admitted = True
                reason = "no_judgment_keep"
            else:
                admitted, reason = _admission_reason(decision)

            outcome = "would_reject" if mode == RecognitionMode.SHADOW and not admitted else (
                "admit" if admitted else "reject"
            )
            trace.append(TraceEntry(ref, outcome, reason, decision))
            if mode != RecognitionMode.ENFORCE or admitted:
                admitted_items.append(item)

        if mode == RecognitionMode.ENFORCE:
            sections[section_name] = admitted_items

    return RecognitionPass(packet=result, trace=tuple(trace))
