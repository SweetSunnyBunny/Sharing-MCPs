"""Reply tags must survive a boy WRITING ABOUT reply tags."""

import re

from services.face_store import parse_face_tag
from services.orb_store import parse_orb_tag
from services.tag_masking import mask_code, find_tag_spans


# The real message shape that broke it, preserved verbatim in structure.
POISONED = (
    "Let me check whether the other paths have the same rot: comparing every "
    "`<face>` call site to every `<orb>` one. Three of four paths are clean.\n\n"
    "```\n<face>NOT_A_REAL_FACE</face>\n<orb>#000000 solid still | nope</orb>\n```\n\n"
    "<face>(◕‿◕) | ears up, thinking about goats</face>\n"
    "<orb>#BF4A2F ember breathing | back in my own gait</orb>"
)


def test_mask_code_preserves_length_exactly():
    """Callers slice the ORIGINAL by offsets found in the mask — same length
    is the invariant that makes that safe."""
    for sample in (POISONED, "", "no code here", "`a`", "```\nx\n```", "`unclosed"):
        assert len(mask_code(sample)) == len(sample)


def test_inline_code_mention_cannot_open_a_face_tag():
    _, face, note = parse_face_tag(POISONED)
    assert face == "(◕‿◕)"
    assert note == "ears up, thinking about goats"


def test_fenced_block_decoy_is_not_applied():
    _, face, _ = parse_face_tag(POISONED)
    assert face != "NOT_A_REAL_FACE"
    _, orb = parse_orb_tag(POISONED)
    assert orb and orb["color"] == "#BF4A2F"


def test_orb_survives_the_same_poisoning():
    _, orb = parse_orb_tag(POISONED)
    assert orb is not None
    assert orb["color"] == "#BF4A2F"
    assert orb["feeling"] == "back in my own gait"


def test_her_backticked_prose_is_left_visible():
    """The fix must not eat what he actually wrote — only the real tags go."""
    cleaned, _, _ = parse_face_tag(POISONED)
    assert "`<face>` call site" in cleaned
    assert "```" in cleaned
    assert "ears up, thinking" not in cleaned


def test_poisoned_face_is_dropped_not_stored():
    """Belt and braces: markup in a face means poisoned, so store nothing."""
    _, face, _ = parse_face_tag("<face>`code` | note</face>")
    assert face is None


def test_a_plain_tag_with_no_code_still_works():
    """The ordinary case must be untouched by all of this."""
    cleaned, face, note = parse_face_tag("hello <face>^_^ | warm</face> there")
    assert face == "^_^" and note == "warm"
    assert "^_^" not in cleaned and "hello" in cleaned and "there" in cleaned


def test_find_tag_spans_returns_original_text_not_masked():
    pat = re.compile(r"<face>\s*(.+?)\s*</face>", re.IGNORECASE | re.DOTALL)
    spans = find_tag_spans("x `<face>fake</face>` y <face>real</face>", pat)
    assert len(spans) == 1
    assert spans[0][2] == "real"


VOICE_POISONED = (
    "The bug is that `<voice>` opens a tag even inside backticks. I traced it "
    "through chat_turn_finalize, then discord_mentions_bridge, then "
    "platform_bridge — three surfaces, same strict pair, none masking first.\n\n"
    "<voice>[softly] I love you, Bunny.</voice>"
)


def test_voice_tag_in_backticks_does_not_bill_her():
    """A prose mention must not drag the whole explanation into ElevenLabs.

    Pre-fix this returned 300 characters instead of 30 — a 10x spend that
    would have read her the source filenames aloud.
    """
    from services.chat_turn_finalize import extract_voice_text

    got = extract_voice_text(VOICE_POISONED)
    assert got == "[softly] I love you, Bunny."
    assert "chat_turn_finalize" not in got
    assert len(got) < 40


def test_unclosed_voice_still_fails_closed():
    """The fail-safe Ember verified must survive the fix: no pair, no spend."""
    from services.chat_turn_finalize import extract_voice_text

    assert extract_voice_text("I meant to say <voice>[softly] hey Bunny") is None


def test_ordinary_voice_tag_untouched():
    from services.chat_turn_finalize import extract_voice_text

    assert extract_voice_text("hi <voice>[warmly] hey you</voice> x") == "[warmly] hey you"


def test_two_real_voice_tags_both_captured():
    from services.chat_turn_finalize import extract_voice_text

    got = extract_voice_text("<voice>one</voice> mid <voice>two</voice>")
    assert got == "one two" and "mid" not in got
