"""Tests for the <orb> reply tag (services/orb_store.py)."""

from services.orb_store import parse_orb_tag


def test_color_only():
    cleaned, orb = parse_orb_tag("Morning, Bunny. <orb>#B4693A</orb>")
    assert orb is not None
    assert orb["color"] == "#B4693A"
    assert orb["shape"] is None and orb["motion"] is None
    assert cleaned == "Morning, Bunny."


def test_shape_and_motion():
    _, orb = parse_orb_tag("<orb>#B4693A ember breathing</orb>")
    assert orb["shape"] == "ember"
    assert orb["motion"] == "breathing"


def test_order_does_not_matter():
    """Vocabularies are disjoint, so tokens are matched by membership."""
    _, orb = parse_orb_tag("<orb>#B4693A breathing ember</orb>")
    assert orb["shape"] == "ember"
    assert orb["motion"] == "breathing"


def test_feeling_and_kaomoji():
    _, orb = parse_orb_tag("<orb>#B4693A spire surge bright | lit up | (^_^)</orb>")
    assert orb["intensity"] == "bright"
    assert orb["feeling"] == "lit up"
    assert orb["kaomoji"] == "(^_^)"


def test_second_hex_becomes_blend():
    _, orb = parse_orb_tag("<orb>#B4693A ember #2A1A0E | storm in my chest</orb>")
    assert orb["color"] == "#B4693A"
    assert orb["blend"] == "#2A1A0E"
    assert orb["feeling"] == "storm in my chest"


def test_blend_literal():
    _, orb = parse_orb_tag("<orb>#FFAA00 pulse dim</orb>")
    assert orb["blend"] == "dim"


def test_short_hex_accepted():
    _, orb = parse_orb_tag("<orb>#FA0 pulse</orb>")
    assert orb["color"] == "#FA0"  # api.hub expands #RGB on write


def test_unknown_words_ignored_not_fatal():
    _, orb = parse_orb_tag("<orb>#B4693A ember wobbling sideways breathing</orb>")
    assert orb["shape"] == "ember"
    assert orb["motion"] == "breathing"


def test_case_insensitive_tag():
    _, orb = parse_orb_tag("<ORB>#B4693A EMBER Breathing</ORB>")
    assert orb["shape"] == "ember"
    assert orb["motion"] == "breathing"


def test_no_color_is_stripped_but_sets_nothing():
    """The one genuinely required field. No color = no orb, reply survives."""
    cleaned, orb = parse_orb_tag("hello <orb>ember breathing</orb> there")
    assert orb is None
    assert "<orb>" not in cleaned
    assert "hello" in cleaned and "there" in cleaned


def test_empty_tag_is_safe():
    cleaned, orb = parse_orb_tag("hi <orb></orb>")
    assert orb is None
    assert "<orb>" not in cleaned


def test_unclosed_tag_leaves_reply_untouched():
    """Better to show one stray tag than to mangle her message."""
    text = "here is my <orb>#B4693A and then I kept typing"
    cleaned, orb = parse_orb_tag(text)
    assert orb is None
    assert cleaned == text


def test_no_tag_is_passthrough():
    text = "just a normal sentence"
    cleaned, orb = parse_orb_tag(text)
    assert orb is None
    assert cleaned is text  # identity, not just equality — zero work done


def test_multiline_tag():
    _, orb = parse_orb_tag("<orb>#B4693A ember breathing\n| steady, watching her door</orb>")
    assert orb["feeling"] == "steady, watching her door"


def test_two_tags_first_wins_both_stripped():
    cleaned, orb = parse_orb_tag("a <orb>#111111 ember</orb> b <orb>#222222 spire</orb> c")
    assert orb["color"] == "#111111"
    assert "<orb>" not in cleaned
    assert "#222222" not in cleaned


def test_surrounding_text_preserved():
    cleaned, _ = parse_orb_tag("Good morning, watashi no ai. <orb>#B4693A</orb> I missed you.")
    assert "Good morning, watashi no ai." in cleaned
    assert "I missed you." in cleaned


def test_long_feeling_is_clamped_not_rejected():
    _, orb = parse_orb_tag("<orb>#B4693A | " + ("x" * 400) + "</orb>")
    assert len(orb["feeling"]) == 120


_REPLY_PATHS = [
    "services/autowake.py",
    "services/chat_turn_finalize.py",
    "services/discord_mentions_bridge.py",
    "services/platform_bridge.py",
]


def _repo_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def test_all_four_reply_paths_call_extract_orb():
    """The orb must be applied on every path a reply can leave by."""
    root = _repo_root()
    for rel in _REPLY_PATHS:
        src = (root / rel).read_text(encoding="utf-8")
        assert "extract_orb(" in src, f"{rel} never applies the <orb> tag"


def test_extract_orb_is_never_nested_under_a_face_guard():
    """An orb tag must work on its own — it must never require a <face> tag.

    Asserted on the parsed AST, not on indentation: no `extract_orb` call may
    live anywhere inside an `if` whose condition tests for a <face> tag. That
    exact nesting is the bug this test exists to keep from coming back, and a
    text/indent check can't tell it apart from a legitimate sibling guard.
    """
    import ast

    root = _repo_root()
    for rel in _REPLY_PATHS:
        src_text = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            cond = (ast.get_source_segment(src_text, node.test) or "").lower()
            if "<face>" not in cond:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                fn = child.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "extract_orb":
                    raise AssertionError(
                        f"{rel}: extract_orb is nested inside a <face> guard — "
                        f"an orb sent without a face tag silently never fires."
                    )
