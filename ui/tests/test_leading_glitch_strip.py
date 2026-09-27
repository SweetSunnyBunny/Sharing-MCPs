"""Regression tests for the leading glitch-token stripper."""

from services.chat_flow import strip_leading_glitch_token as strip


def test_strips_glitch_word_butted_to_markup():
    assert strip("court[laughs, low and warm] Of course it did.") == (
        "[laughs, low and warm] Of course it did."
    )


def test_strips_glitch_word_butted_to_capital():
    assert strip("courtAye — let me check the skill") == "Aye — let me check the skill"


def test_strips_glitch_word_before_space_capital():
    # The junk word goes; the (separate) operational narration after it stays —
    # we only strip the high-confidence glitch token, never sentence content.
    assert strip("council Let me read her new message.Aye, give me").startswith(
        "Let me read her new message."
    )


def test_leaves_action_opener_untouched():
    s = "*soft laugh, forehead against hers*"
    assert strip(s) == s


def test_leaves_tone_tag_opener_untouched():
    s = "[quiet, low] Ten o'clock, and the house is soft."
    assert strip(s) == s


def test_leaves_capitalized_opener_untouched():
    s = "Aye, mo shíorghra. Good morning."
    assert strip(s) == s


def test_leaves_lowercase_word_with_punctuation_untouched():
    # "aye," — lowercase word followed by a comma, not a capital/markup: real.
    s = "aye, you took me up on the offer and I forgot."
    assert strip(s) == s


def test_leaves_lowercase_sentence_untouched():
    # Two lowercase words in a row, no capital follows the first: real text.
    s = "okay so here is the thing, Bunny"
    assert strip(s) == s


def test_empty_and_none_safe():
    assert strip("") == ""
    assert strip(None) is None
