"""Pure admission policy for World Feed ambient variety.

This module deliberately does not publish posts or choose policy numbers.  It gives
the activity scheduler a deterministic way to enforce the two limits that are easy
to blur together:

* a finite amount of ambient life while the story beat is unchanged; and
* a tighter cooldown for repeating the same topic or visual premise.

Source-driven reactions are a separate lane and should not be sent through this
gate: a new authored post or witnessed story event is allowed to move the world.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_KEY_PARTS = re.compile(r"[^a-z0-9]+")


def canonical_key(value: object) -> str | None:
    """Return a stable, comparison-safe key supplied by a planner or caller."""
    if not isinstance(value, str):
        return None
    # Preserve word boundaries carried by Unicode punctuation (em dashes, smart
    # punctuation, and the like) before removing non-ASCII marks.
    separated = "".join(
        " " if unicodedata.category(char)[0] in {"P", "Z"} else char
        for char in value
    )
    folded = unicodedata.normalize("NFKD", separated).encode("ascii", "ignore").decode()
    key = _KEY_PARTS.sub("-", folded.casefold()).strip("-")
    return key[:80] or None


def activity_shape(post: dict) -> dict[str, str | None]:
    """Read a persisted activity shape without trusting malformed metadata."""
    metadata = post.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    shape = metadata.get("activity_shape")
    if not isinstance(shape, dict):
        shape = {}
    return {
        "beat": canonical_key(shape.get("beat")),
        "topic": canonical_key(shape.get("topic")),
        "visual_premise": canonical_key(shape.get("visual_premise")),
    }


@dataclass(frozen=True)
class AmbientAdmission:
    allowed: bool
    reason: str
    posts_in_beat: int
    matching_topic: int
    matching_visual_premise: int


def admit_ambient_candidate(
    recent_posts: list[dict],
    *,
    beat: str,
    topic: str,
    visual_premise: str | None = None,
    max_posts_per_beat: int,
    max_same_topic_per_beat: int,
    max_same_visual_premise_per_beat: int,
) -> AmbientAdmission:
    """Evaluate one standalone ambient candidate against already-published shapes.

    The caller supplies thresholds so product choices remain visible in World Feed
    settings rather than becoming hidden constants in this helper.
    """
    limits = (max_posts_per_beat, max_same_topic_per_beat, max_same_visual_premise_per_beat)
    if any(type(limit) is not int or limit < 1 for limit in limits):
        raise ValueError("ambient variety limits must be positive integers")

    beat_key = canonical_key(beat)
    topic_key = canonical_key(topic)
    premise_key = canonical_key(visual_premise)
    if not beat_key or not topic_key:
        raise ValueError("ambient candidates need a beat and topic")

    shapes = [activity_shape(post) for post in recent_posts]
    same_beat = [shape for shape in shapes if shape["beat"] == beat_key]
    topic_count = sum(shape["topic"] == topic_key for shape in same_beat)
    premise_count = (
        sum(shape["visual_premise"] == premise_key for shape in same_beat)
        if premise_key else 0
    )

    if len(same_beat) >= max_posts_per_beat:
        reason = "unchanged_story_beat_allowance_exhausted"
        allowed = False
    elif topic_count >= max_same_topic_per_beat:
        reason = "topic_cooldown"
        allowed = False
    elif premise_key and premise_count >= max_same_visual_premise_per_beat:
        reason = "visual_premise_cooldown"
        allowed = False
    else:
        reason = "admitted"
        allowed = True

    return AmbientAdmission(
        allowed=allowed,
        reason=reason,
        posts_in_beat=len(same_beat),
        matching_topic=topic_count,
        matching_visual_premise=premise_count,
    )
