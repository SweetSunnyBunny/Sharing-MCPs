"""Jev (TypeSafe System One) adapter — typed judgment questions, privacy-gated."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-latest"
JEV_TIMEOUT_SECONDS = 8.0

_QUESTION_TYPES = {"noul", "choice", "score"}
_MAX_CHOICE_OPTIONS = 255
_MAX_SCORE_LEVELS = 10


class JevPrivacyRefused(Exception):
    """Raised (and caught internally) when non-synthetic state would leave the house."""


@dataclass
class JevAnswer:
    qid: str
    type: str
    value: Any                      # noul: float 0..1 · choice: str · score: float
    confidence: float | None = None  # choice/score only
    probabilities: dict[str, float] = field(default_factory=dict)


# ── config ────────────────────────────────────────────────────────────────

def _api_key() -> str:
    return (os.environ.get("TYPESAFE_API_KEY") or "").strip()


def _private_allowed() -> bool:
    return (os.environ.get("ANAM_JEV_ALLOW_PRIVATE") or "").strip().lower() == "true"


def is_enabled() -> bool:
    return bool(_api_key())


# ── question builders ─────────────────────────────────────────────────────

def noul(instructions: Any, true: Any = None, false: Any = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true is not None or false is not None:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    return q


def choice(instructions: Any, options: dict[str, Any]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions: Any, levels: list[Any]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def _validate_questions(questions: dict[str, dict]) -> None:
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a non-empty map")
    for qid, q in questions.items():
        t = q.get("type")
        if t not in _QUESTION_TYPES:
            raise ValueError(f"question {qid!r}: unknown type {t!r}")
        if q.get("instructions") in (None, ""):
            raise ValueError(f"question {qid!r}: instructions required")
        crit = q.get("criteria")
        if t == "choice":
            if not isinstance(crit, dict) or not (1 <= len(crit) <= _MAX_CHOICE_OPTIONS):
                raise ValueError(f"question {qid!r}: choice needs 1..{_MAX_CHOICE_OPTIONS} options")
        elif t == "score":
            if not isinstance(crit, list) or not (2 <= len(crit) <= _MAX_SCORE_LEVELS):
                raise ValueError(f"question {qid!r}: score needs 2..{_MAX_SCORE_LEVELS} levels")


# ── answer validation ─────────────────────────────────────────────────────

def _is_prob(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def _parse_answer(qid: str, question: dict, raw: Any) -> JevAnswer | None:
    if not isinstance(raw, dict) or raw.get("type") != question["type"]:
        return None
    t = question["type"]
    if t == "noul":
        v = raw.get("noul")
        return JevAnswer(qid, t, float(v)) if _is_prob(v) else None

    probs = raw.get("probabilities")
    conf = raw.get("confidence")
    if not isinstance(probs, dict) or not _is_prob(conf):
        return None
    if not all(isinstance(k, str) and _is_prob(v) for k, v in probs.items()):
        return None

    if t == "choice":
        picked = raw.get("choice")
        if picked not in question["criteria"] or set(probs) - set(question["criteria"]):
            return None
        return JevAnswer(qid, t, picked, float(conf), {k: float(v) for k, v in probs.items()})

    # score
    v = raw.get("score")
    top = len(question["criteria"]) - 1
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not (0 <= v <= top):
        return None
    return JevAnswer(qid, t, float(v), float(conf), {k: float(p) for k, p in probs.items()})


# ── the call ──────────────────────────────────────────────────────────────

async def ask(
    state: Any,
    questions: dict[str, dict],
    *,
    synthetic: bool = False,
    model: str = JEV_DEFAULT_MODEL,
    client: httpx.AsyncClient | None = None,
) -> dict[str, JevAnswer] | None:
    """Ask Jev typed questions about `state`. Returns validated answers, or None.

    None means "no opinion" — disabled, refused, failed, or malformed.
    Individual answers that fail validation are dropped from the dict.
    Never raises.
    """
    try:
        _validate_questions(questions)
    except ValueError as e:
        logger.warning("jev: bad questions: %s", e)
        return None

    key = _api_key()
    if not key:
        return None

    # Privacy gate — BEFORE any request object exists.
    if not synthetic and not _private_allowed():
        logger.info("jev: refused non-synthetic state (privacy gate)")
        return None

    payload = {"state": state, "model": model, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    own_client = client is None
    c = client or httpx.AsyncClient(timeout=JEV_TIMEOUT_SECONDS)
    try:
        resp = await c.post(JEV_ENDPOINT, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning("jev: HTTP %s", resp.status_code)
            return None
        body = resp.json()
    except Exception as e:  # timeout, network, bad JSON — fail open
        logger.warning("jev: request failed open: %s", type(e).__name__)
        return None
    finally:
        if own_client:
            await c.aclose()

    answers_raw = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers_raw, dict):
        return None

    out: dict[str, JevAnswer] = {}
    for qid, q in questions.items():
        parsed = _parse_answer(qid, q, answers_raw.get(qid))
        if parsed is not None:
            out[qid] = parsed
        else:
            logger.warning("jev: dropped malformed answer for %r", qid)
    return out
