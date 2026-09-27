"""Is this timer a promise we have already made?."""

import re
from difflib import SequenceMatcher


DUPE_WINDOW_S = 20 * 60

# Below this, two briefings are about different things. Above it, they are the
# same sentence wearing different clock numbers.
DUPE_SIMILARITY = 0.75


def dupe_key(context: str) -> str:
    """The promise, stripped of everything that varies between two arms of it.

    Digits go first and hardest: the same promise armed twice carries different
    clock times, different row ids, and a different "armed at HH:MM" — all of
    which would otherwise drag the similarity below any sane threshold and hide
    the duplicate. What is left is the sentence itself.
    """
    head = (context or "").splitlines()[0].lower() if context else ""
    return re.sub(r"[^a-z ]+", " ", head).strip()[:60]


def is_same_promise(context_a: str, epoch_a: int, context_b: str, epoch_b: int) -> bool:
    """True when these two timers are one promise armed twice."""
    if epoch_a is None or epoch_b is None:
        return False
    if abs(int(epoch_a) - int(epoch_b)) > DUPE_WINDOW_S:
        return False
    ratio = SequenceMatcher(None, dupe_key(context_a), dupe_key(context_b)).ratio()
    return ratio >= DUPE_SIMILARITY
