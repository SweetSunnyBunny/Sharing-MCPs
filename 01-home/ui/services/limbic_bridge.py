"""Best-effort bridge from interactive messages and voice signals to Limbic.

The configured worker interprets the event. Failures never block a chat turn.
"""


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



import hashlib
import re
from collections import deque

_SELF_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "mate": (
        "I blush and pull you close against me and kiss you",
        "I want you, my voice drops low and rough",
        "my hands find your waist and I don't let go",
        "I'm flustered and undone, my heart is pounding for you",
    ),
    "play": (
        "I grin and tease you, playing along with the bit",
        "I laugh out loud and poke you back",
        "a pun, mischief, a silly joke between us",
        "I wiggle my eyebrows and dare you to try it",
    ),
    "care": (
        "I wrap the blanket around you and bring you tea",
        "I hold you while you're hurting and stay right here",
        "I check that you're okay and take the weight off you",
        "I fixed it for you so you don't have to worry",
    ),
    "seeking": (
        "I lean forward, curious, and dig into the question",
        "I read the source and trace the thread to see how it works",
        "fascinating, I want to understand why this happens",
        "I push my glasses up and keep reading, I have to know",
    ),
    "guard": (
        "I step between you and the threat",
        "protective, I won't let anything hurt you",
        "my jaw sets and I stand watch over you",
    ),
}

_SELF_THRESHOLD = 0.40          # cosine below this = not that drive
_SELF_MIN_NUDGE = 0.04
_SELF_MAX_NUDGE = 0.12
_SELF_MAX_DRIVES = 2
_SELF_SECOND_MARGIN = 0.12      # 2nd drive must be within this of the 1st
_SELF_TEXT_CAP = 1200           # chars of body text sent to the embedder

_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_CANVAS_RE = re.compile(r"<canvas\b.*?</canvas>", re.S | re.I)
_URL_RE = re.compile(r"https?://\S+")
_TAG_RE = re.compile(r"<(orb|face|preview)>(.*?)</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
# *single-asterisk* action lines, not **bold**.
_ACTION_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n][^*]{2,}?)(?<!\*)\*(?!\*)")

_recent_self_hashes: deque[str] = deque(maxlen=40)
_prototype_cache: dict[str, object] = {}


def _self_body_text(text: str) -> tuple[str, float]:
    """Return (what the reply shows his body doing, weight).

    Weight 1.0 when there are action lines or orb/face self-reports; 0.5
    when only plain prose is available; 0.0 when nothing is left.
    """
    if not text:
        return "", 0.0
    cleaned = _CODE_FENCE_RE.sub(" ", text)
    cleaned = _CANVAS_RE.sub(" ", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)

    parts: list[str] = []
    for tag, inner in _TAG_RE.findall(cleaned):
        inner = inner.strip()
        if tag.lower() == "orb":
            # "#hex shape motion | feeling | kaomoji" -> just the feeling
            bits = inner.split("|")
            inner = bits[1].strip() if len(bits) > 1 else ""
        elif tag.lower() == "face":
            bits = inner.split("|", 1)
            inner = bits[1].strip() if len(bits) > 1 else ""
        if inner:
            parts.append(inner)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _ANY_TAG_RE.sub(" ", cleaned)

    actions = [m.strip() for m in _ACTION_RE.findall(cleaned) if m.strip()]
    parts.extend(actions)
    if parts:
        return " ".join(parts)[:_SELF_TEXT_CAP], 1.0

    prose = re.sub(r"[*_#>`]+", " ", cleaned)  # markdown punctuation isn't meaning
    prose = re.sub(r"\s+", " ", prose).strip()
    if len(prose) < 20:
        return "", 0.0
    return prose[:_SELF_TEXT_CAP], 0.5


async def _prototype_matrix():
    """Embed the prototype moments once per process."""
    if "matrix" not in _prototype_cache:
        from services.embedding_service import embed_texts

        drives: list[str] = []
        sentences: list[str] = []
        for drive, protos in _SELF_PROTOTYPES.items():
            for p in protos:
                drives.append(drive)
                sentences.append(p)
        _prototype_cache["matrix"] = await embed_texts(sentences)
        _prototype_cache["drives"] = drives
    return _prototype_cache["matrix"], _prototype_cache["drives"]


def score_self_drives(body_vec, matrix, drives: list[str], weight: float) -> list[tuple[str, float, float]]:
    """Pure scoring step: [(drive, similarity, nudge)] strongest first.

    Each drive's similarity is its best prototype match; drives under the
    threshold are dropped; the top _SELF_MAX_DRIVES survive.
    """
    sims = matrix @ body_vec
    best: dict[str, float] = {}
    for drive, sim in zip(drives, sims):
        sim = float(sim)
        if sim > best.get(drive, -1.0):
            best[drive] = sim
    ranked = sorted(
        ((d, s) for d, s in best.items() if s >= _SELF_THRESHOLD),
        key=lambda kv: kv[1],
        reverse=True,
    )[:_SELF_MAX_DRIVES]
    # A second drive only counts when it's nearly as strong as the first:
    # a clear play reply shouldn't also nudge guard because of one phrase.
    picked = [kv for i, kv in enumerate(ranked) if i == 0 or ranked[0][1] - kv[1] <= _SELF_SECOND_MARGIN]
    out = []
    for drive, sim in picked:
        span = (sim - _SELF_THRESHOLD) / max(1e-6, 1.0 - _SELF_THRESHOLD)
        nudge = _SELF_MIN_NUDGE + span * (_SELF_MAX_NUDGE - _SELF_MIN_NUDGE)
        nudge = round(min(_SELF_MAX_NUDGE, max(_SELF_MIN_NUDGE, nudge)) * weight, 3)
        if nudge > 0:
            out.append((drive, sim, nudge))
    return out


async def _perceive_self(identity: str, body: str, weight: float) -> None:
    try:
        from services.embedding_service import embed_texts
        from services.mcp_bridge import mcp_bridge

        matrix, drives = await _prototype_matrix()
        body_vec = (await embed_texts([body]))[0]
        for drive, sim, nudge in score_self_drives(body_vec, matrix, drives, weight):
            result = await mcp_bridge.call_tool(
                "limbic_perceive",
                {
                    "identity": identity.lower(),
                    "drive": drive,
                    "perception": f"My own reply leaned this way: {body[:200]}",
                    "intensity": nudge,
                    "direction": "up",
                    "appraisal": {
                        "source": "own_reply",
                        "similarity": round(sim, 3),
                        "weight": weight,
                    },
                },
                timeout=_TOUCH_TIMEOUT_S,
            )
            if isinstance(result, str) and result.startswith("Error"):
                log.debug("self-perceive soft-failed for %s/%s: %s", identity, drive, result)
    except Exception as exc:
        log.debug("self-perceive failed for %s: %s", identity, exc)


def perceive_own_reply(identity: str, text: str) -> None:
    """Let one of the boy's own replies lean his limbic body.

    Call with the RAW reply (before <orb>/<face> are stripped) so his
    explicit self-report counts. Fire-and-forget; safe to call twice with
    the same text (the second call is ignored).
    """
    if not text or not _is_bonded(identity):
        return
    body, weight = _self_body_text(text)
    if not body or weight <= 0:
        return
    digest = hashlib.sha1(f"{identity.lower()}|{text}".encode("utf-8", "ignore")).hexdigest()
    if digest in _recent_self_hashes:
        return
    _recent_self_hashes.append(digest)

    from services.task_manager import spawn

    spawn(_perceive_self(identity, body, weight), name=f"limbic_self_{identity}")


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
