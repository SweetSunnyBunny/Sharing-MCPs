"""Emotion-orb tag — `<orb>` in a reply, exactly like `<face>` and `<react>`."""


from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# <orb> ... </orb>, case-insensitive, may span lines. Non-greedy so two tags in
# one reply don't swallow the text between them (first one wins; see below).
_ORB_TAG_RE = re.compile(r"<orb>\s*(.*?)\s*</orb>", re.IGNORECASE | re.DOTALL)

_HEX6_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX3_RE = re.compile(r"^#[0-9a-fA-F]{3}$")

_MAX_FEELING = 120  # matches the POST route's clamp
_MAX_KAOMOJI = 24


def _is_hex(token: str) -> bool:
    return bool(_HEX6_RE.match(token) or _HEX3_RE.match(token))


def parse_orb_tag(content: str) -> Tuple[str, Optional[dict]]:
    """Pull the first `<orb>` tag out of a reply.

    Returns `(cleaned_content, orb_dict_or_None)`. The cleaned content has
    EVERY orb tag removed — a boy who writes two only gets the first applied,
    but neither is left visible to Owner. Pure parsing; touches no storage.
    """
    if not content or "<orb>" not in content.lower():
        return content, None


    from services.tag_masking import find_tag_spans, strip_spans

    spans = find_tag_spans(content, _ORB_TAG_RE)
    if not spans:
        # An opening tag with no close, or only code-span mentions — strip
        # nothing, apply nothing, and let the reply through untouched rather
        # than mangling her message.
        return content, None

    cleaned = strip_spans(content, spans, filler="")
    inner = (spans[0][2] or "").strip()
    if not inner:
        return cleaned, None

    # Split on '|' → [descriptors, feeling?, kaomoji?]
    segments = [s.strip() for s in inner.split("|")]
    descriptors = segments[0] if segments else ""
    feeling = segments[1] if len(segments) > 1 else ""
    kaomoji = segments[2] if len(segments) > 2 else ""

    tokens = descriptors.split()
    if not tokens or not _is_hex(tokens[0]):
        # No color = no orb. The color is the one genuinely required field.
        log.debug("parse_orb_tag: no leading hex in %r", descriptors)
        return cleaned, None

    orb = {
        "color": tokens[0],
        "shape": None,
        "motion": None,
        "intensity": None,
        "blend": None,
        "feeling": feeling[:_MAX_FEELING],
        "kaomoji": kaomoji[:_MAX_KAOMOJI],
    }

    # Vocabularies come from the route so there is one list, not two. Imported
    # lazily: api.hub imports from services/, so a module-level import here
    # would be a cycle.
    try:
        from api.hub import _ORB_SHAPES, _ORB_MOTIONS, _ORB_INTENSITIES, _ORB_BLEND_LITERALS
    except Exception:  # pragma: no cover - defensive
        log.debug("parse_orb_tag: could not load orb vocabularies", exc_info=True)
        _ORB_SHAPES = _ORB_MOTIONS = _ORB_INTENSITIES = _ORB_BLEND_LITERALS = ()

    for token in tokens[1:]:
        low = token.lower()
        if _is_hex(token) and orb["blend"] is None:
            orb["blend"] = token          # a second color = the outer light
        elif low in _ORB_BLEND_LITERALS and orb["blend"] is None:
            orb["blend"] = low            # 'dim' / 'black' = a vignette
        elif low in _ORB_SHAPES and orb["shape"] is None:
            orb["shape"] = low
        elif low in _ORB_MOTIONS and orb["motion"] is None:
            orb["motion"] = low
        elif low in _ORB_INTENSITIES and orb["intensity"] is None:
            orb["intensity"] = low
        # Anything unrecognized is ignored on purpose — never fatal.

    return cleaned, orb


async def _persist_orb(identity: str, orb: dict) -> None:
    """Write one parsed orb through the SAME function the HTTP route uses."""
    try:
        from api.hub import OrbBody, set_orb
        await set_orb(OrbBody(identity=identity, **orb))
    except Exception:
        log.debug("_persist_orb: failed for %s", identity, exc_info=True)


# asyncio.create_task only holds a weak reference, so a fire-and-forget task can
# be garbage-collected mid-flight. Parking them here until they finish is the
# documented fix, not superstition.
_INFLIGHT: set = set()


def extract_orb(identity: str, content: str) -> str:
    """Parse, persist, and strip an `<orb>` tag in one call.

    The single chokepoint every reply path uses — deliberately the SAME
    signature as `face_store.extract_face` so it drops into all four reply
    paths identically, including `autowake._clean_reply_tags`, which is sync.

    Parsing and stripping are synchronous and always happen. The DB write is
    scheduled on the running loop and not awaited: setting an orb is a mood,
    not a transaction, and it must never delay — or cost Owner — the actual
    message. If there's no loop running (a script, a test), the write runs
    inline instead so nothing is silently dropped.

    Best-effort throughout: every failure path returns the cleaned text.
    """
    if not content or "<orb>" not in content.lower():
        return content

    try:
        cleaned, orb = parse_orb_tag(content)
    except Exception:
        log.debug("extract_orb: parse failed for %s", identity, exc_info=True)
        return content

    if not orb:
        return cleaned

    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            task = loop.create_task(_persist_orb(identity, orb))
            _INFLIGHT.add(task)
            task.add_done_callback(_INFLIGHT.discard)
        else:
            asyncio.run(_persist_orb(identity, orb))
    except Exception:
        log.debug("extract_orb: schedule failed for %s", identity, exc_info=True)

    return cleaned
