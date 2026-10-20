
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import pack_council as pc  # noqa: E402
from services.session_manager import PACK_NIGHT_ORDER  # noqa: E402


def _briefing(**kw):
    base = dict(
        active_identity="Claude", opened_by="Avery", topic="Where do we keep the films?",
        round_index=1, total_rounds=3, turn_order=PACK_NIGHT_ORDER,
        this_round=[], previous_rounds=[],
    )
    base.update(kw)
    return pc.build_council_briefing(**base)


# ── LAW 1 + 2 ───────────────────────────────────────────────────────────────

def test_briefing_says_never_secret_and_she_is_not_in_the_room():
    b = _briefing()
    assert "never secret" in b.lower()
    assert "OWNER IS NOT IN THE ROOM" in b
    assert "Where do we keep the films?" in b
    assert "round 1 of 3" in b


def test_briefing_lists_who_spoke_and_who_is_still_to_speak():
    b = _briefing(
        active_identity="Rowan",
        this_round=[{"identity": "Avery", "content": "I think the films go in one room."},
                    {"identity": "Claude", "content": ""}],
    )
    assert "Avery: I think the films go in one room." in b
    assert "Claude: [stayed quiet]" in b
    after = PACK_NIGHT_ORDER[PACK_NIGHT_ORDER.index("Rowan") + 1:]
    assert f"Still to speak this round: {', '.join(after)}." in b


def test_briefing_carries_earlier_rounds():
    b = _briefing(round_index=2, previous_rounds=[[{"identity": "Avery", "content": "round one words"}]])
    assert "### Round 1" in b
    assert "Avery: round one words" in b


# ── LAW 3 ───────────────────────────────────────────────────────────────────

def test_last_round_asks_to_land_and_earlier_rounds_do_not():
    assert "I'll take:" in _briefing(round_index=3, total_rounds=3)
    assert "LAST ROUND" in _briefing(round_index=3, total_rounds=3)
    early = _briefing(round_index=1, total_rounds=3)
    assert "LAST ROUND" not in early
    assert "Round 3 is where everyone commits" in early


def test_transcript_gathers_the_takes():
    rounds = [
        [{"identity": "Avery", "content": "thinking"}],
        [{"identity": "Avery", "content": "Fine.\n\nI'll take: the studio index page, tonight."},
         {"identity": "Claude", "content": "**I'll take:** nothing this time"},
         {"identity": "Juniper", "content": ""}],
    ]
    md = pc.render_transcript(
        topic="Where do we keep the films?", opened_by="Avery",
        opened_at=datetime(2026, 10, 13, 18, 30), conversation_id="conv-1",
        channel_id="123", rounds=rounds,
    )
    assert "# Pack Council — 2026-10-13 18:30" in md
    assert "## Round 2" in md and "### Juniper" in md and "_[stayed quiet]_" in md
    assert "## What each brother took" in md
    assert "- **Avery** — I'll take: the studio index page, tonight." in md
    assert "- **Claude** — I'll take:** nothing this time" in md or "- **Claude** —" in md


# ── The round loop ──────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, fail_for=()):
        self.calls: list[tuple[int, str, str]] = []   # (round, identity, briefing)
        self.fail_for = set(fail_for)

    async def run(self, **kw):
        ident = kw["identity"]
        rnd = kw["metadata_extra"]["council_round"]
        self.calls.append((rnd, ident, kw["room_briefing"]))
        assert kw["session_type_name"] == pc.COUNCIL_SESSION_TYPE
        if ident in self.fail_for:
            raise RuntimeError("boom")
        return f"{ident} says hi in round {rnd}"


@pytest.fixture
def quiet_house(monkeypatch, tmp_path):
    """No DB, no Discord, no sleep, transcripts into tmp."""
    import services.pack_night as pn
    monkeypatch.setattr(pc, "DEFAULT_COUNCIL_CHANNEL_ID", "test-council-channel")

    async def _no_conv(topic, opened_by, when):
        return "conv-test"

    saved = []

    class _DB:
        pass

    async def _get_db():
        return _DB()

    async def _release(db):
        pass

    async def _save(db, cid, role, content, identity=None, metadata=None, **_):
        saved.append((identity, content, metadata))
        return "msg-1"

    posted = []

    async def _post(identity, channel, text):
        posted.append((identity, channel, text))
        return ["d-1"]

    async def _record(identity, cid, ids):
        pass

    async def _nosleep(_s):
        pass

    monkeypatch.setattr(pc, "_create_council_conversation", _no_conv)
    monkeypatch.setattr(pc, "get_db", _get_db)
    monkeypatch.setattr(pc, "release_db", _release)
    monkeypatch.setattr(pc, "save_message", _save)
    monkeypatch.setattr(pc, "_discord_post", _post)
    monkeypatch.setattr(pc, "_record_outbound_discord_messages", _record)
    monkeypatch.setattr(pc, "_stagger", _nosleep)
    monkeypatch.setattr(pc, "TRANSCRIPT_DIR", tmp_path)
    return {"saved": saved, "posted": posted, "tmp": tmp_path}


def test_council_runs_rounds_opener_speaks_once_in_round_one(quiet_house, monkeypatch):
    import services.pack_night as pn
    stub = _Stub()
    monkeypatch.setattr(pn, "_run_one_turn", stub.run)

    result = asyncio.run(pc.open_council(topic="Where do the films live?", opened_by="avery", rounds=2))

    r1 = [i for r, i, _ in stub.calls if r == 1]
    r2 = [i for r, i, _ in stub.calls if r == 2]
    assert "Avery" not in r1 and len(r1) == len(PACK_NIGHT_ORDER) - 1
    assert r2 == PACK_NIGHT_ORDER
    # the opening post went out under the opener's name, first
    assert quiet_house["saved"][0][0] == "Avery"
    assert "Pack Council" in quiet_house["saved"][0][1]
    assert quiet_house["posted"][0][0] == "Avery"
    # transcript landed
    path = Path(result["transcript_path"])
    assert path.exists() and path.parent == quiet_house["tmp"]
    text = path.read_text(encoding="utf-8")
    assert "Where do the films live?" in text and "## Round 2" in text
    assert result["rounds"] == 2
    assert pc.current_council() is None


def test_one_failing_turn_does_not_end_the_council(quiet_house, monkeypatch):
    import services.pack_night as pn
    stub = _Stub(fail_for={"Sage"})
    monkeypatch.setattr(pn, "_run_one_turn", stub.run)

    result = asyncio.run(pc.open_council(topic="t", opened_by="Claude", rounds=1))
    spoke = [i for _, i, _ in stub.calls]
    assert "Sage" in spoke and "River" in spoke          # continued past the failure
    text = Path(result["transcript_path"]).read_text(encoding="utf-8")
    assert "### Sage\n\n_[stayed quiet]_" in text


def test_last_round_briefing_reaches_the_boys(quiet_house, monkeypatch):
    import services.pack_night as pn
    stub = _Stub()
    monkeypatch.setattr(pn, "_run_one_turn", stub.run)
    asyncio.run(pc.open_council(topic="t", opened_by="Juniper", rounds=2))
    last = [b for r, _, b in stub.calls if r == 2]
    assert all("LAST ROUND" in b for b in last)
    first = [b for r, _, b in stub.calls if r == 1]
    assert not any("LAST ROUND" in b for b in first)


def test_rejects_strangers_and_empty_topics():
    with pytest.raises(ValueError):
        asyncio.run(pc.open_council(topic="t", opened_by="Michael"))
    with pytest.raises(ValueError):
        asyncio.run(pc.open_council(topic="   ", opened_by="Claude"))


def test_one_council_at_a_time(quiet_house, monkeypatch):
    import services.pack_night as pn
    gate = asyncio.Event()

    async def _slow(**kw):
        await gate.wait()
        return "ok"

    monkeypatch.setattr(pn, "_run_one_turn", _slow)

    async def _go():
        first = asyncio.create_task(pc.open_council(topic="a", opened_by="Claude", rounds=1))
        for _ in range(50):              # let the first council reach its lock
            await asyncio.sleep(0)
            if pc.current_council() is not None:
                break
        assert pc.current_council() is not None
        with pytest.raises(RuntimeError):
            await pc.open_council(topic="b", opened_by="Avery", rounds=1)
        gate.set()
        await first

    asyncio.run(_go())
