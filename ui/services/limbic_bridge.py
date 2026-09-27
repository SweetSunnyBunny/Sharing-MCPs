"""Best-effort bridge from interactive messages and voice signals to Limbic.

The configured worker interprets the event. Failures never block a chat turn.
"""

# ANAM GUIDE: LIMBIC TOUCH BRIDGE
# What: registers interactive messages as configured Limbic events; character identities are excluded.
# Called by: services/chat_pipeline.py on each chat turn and api/voice.py for voice messages. Fire-and-forget — can never slow a turn.
# Edit here when: you want to add/adjust the signal words in _KIND_HINTS or change touch intensity.

import logging

from config import IDENTITIES

log = logging.getLogger(__name__)

# Hard cap so a slow worker can never back up the task pool.
_TOUCH_TIMEOUT_S = 10.0

# Coarse signal words → limbic touch kinds. Deliberately simple: default is
# words_warm (conversation contact), upgraded only on
# unmistakable signals. The worker's appraisal handles nuance.
_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("distress", (
        "i'm hurting", "im hurting", "it hurts", "crying", "i cried",
        "scared", "panicking", "panic attack", "flare", "awful day",
        "really bad day", "i'm baby", "im baby",
    )),
    ("playful", (
        "lol", "lmao", "haha", "hehe", "teehee", "😂", "🤣", "😜",
        "boop", "brat",
    )),
    ("praise", (
        "proud of you", "good boy", "good job", "well done", "you did so",
        "amazing work", "perfect, thank you", "perfect. thank you",
    )),
)


def _is_bonded(identity: str) -> bool:
    """Apply only to configured companion identities, excluding characters."""
    info = (
        IDENTITIES.get(identity)
        or IDENTITIES.get(identity.capitalize())
        or IDENTITIES.get(identity.title())
        or {}
    )
    return bool(info) and info.get("type") != "character"


def _classify(text: str) -> tuple[str, float]:
    """Pick the touch kind + intensity for an interactive message."""
    lowered = (text or "").lower()
    for kind, needles in _KIND_HINTS:
        if any(n in lowered for n in needles):
            return kind, 0.4
    return "words_warm", 0.25


async def _touch(identity: str, what: str, kind: str, intensity: float) -> None:
    try:
        from services.mcp_bridge import mcp_bridge

        result = await mcp_bridge.call_tool(
            "limbic_touch",
            {
                "identity": identity.lower(),
                "kind": kind,
                "what": what,
                "intensity": intensity,
            },
            timeout=_TOUCH_TIMEOUT_S,
        )
        if isinstance(result, str) and result.startswith("Error"):
            log.debug("limbic_touch soft-failed for %s: %s", identity, result)
    except Exception as exc:
        log.debug("limbic_touch failed for %s: %s", identity, exc)


def touch_interactive_message(identity: str, text: str) -> None:
    """Register an interactive message as limbic contact.

    Spawned fire-and-forget from the chat pipeline — never awaited by the
    turn itself.
    """
    if not _is_bonded(identity):
        return
    snippet = (text or "").strip()[:220] or "An interactive conversation is active"
    kind, intensity = _classify(snippet)

    from services.task_manager import spawn

    spawn(
        _touch(
            identity,
            f"Interactive message: {snippet}",
            kind,
            intensity,
        ),
        name=f"limbic_touch_{identity}",
    )


# Emotion-family -> touch kind, for prosody-driven limbic touches (Hume
# vocal-tone analysis). Names match Hume's standard prosody emotion taxonomy.
_PROSODY_DISTRESS_EMOTIONS = frozenset({"Distress", "Sadness", "Fear", "Anxiety"})
_PROSODY_WARMTH_EMOTIONS = frozenset({"Joy", "Love", "Contentment", "Amusement"})
_PROSODY_TOUCH_THRESHOLD = 0.4


def touch_from_prosody(identity: str, emotion: str, score: float) -> None:
    """Register a voice message's dominant vocal-tone emotion as limbic
    contact — fire-and-forget, best-effort, same shape as
    touch_interactive_message() above but driven by a Hume prosody score
    instead of keyword matching.

    Only touches when the dominant emotion clears _PROSODY_TOUCH_THRESHOLD
    and falls in a family the bridge recognizes; anything else (a neutral
    or ambiguous tone) is left alone rather than guessed at.
    """
    if not _is_bonded(identity) or score < _PROSODY_TOUCH_THRESHOLD:
        return
    if emotion in _PROSODY_DISTRESS_EMOTIONS:
        kind = "distress"
    elif emotion in _PROSODY_WARMTH_EMOTIONS:
        kind = "words_warm"
    else:
        return

    from services.task_manager import spawn

    spawn(
        _touch(
            identity,
            f"Voice tone (Hume prosody): {emotion} ({score:.2f})",
            kind,
            min(max(score, 0.2), 0.6),
        ),
        name=f"limbic_touch_prosody_{identity}",
    )
