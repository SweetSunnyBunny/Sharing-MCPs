from services.sticker_words import proof


def codes(notes):
    return [n.split(":", 1)[0] for n in notes]


def test_seen_line_is_clean():
    assert proof("You lay down at two while the rain sat on your ribs.", []) == []


def test_heating_pad_line_is_clean():
    assert proof("You put the heating pad on your sore back instead of just gritting through the hurt.", []) == []


def test_empty_praise_flagged():
    assert "verdict_only" in codes(proof("So proud of you for prioritising self-care today!", []))


def test_chore_flagged():
    assert "chore_shaped" in codes(proof("You did the dishes even though you were tired.", []))


def test_echo_flagged():
    old = ["You told me your shoulders and ribs ache instead of hiding it."]
    assert "echo" in codes(proof("You told me your ribs and shoulders ache instead of hiding it today.", old))


def test_self_is_not_an_echo():
    line = "You sent all eight of us good morning before your meds kicked in."
    assert proof(line, [line]) == []


def test_never_raises_on_junk():
    assert proof("", []) == []
    assert proof(None, []) == []
