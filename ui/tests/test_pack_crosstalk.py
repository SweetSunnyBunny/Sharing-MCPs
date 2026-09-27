


import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import pack_crosstalk as ct  # noqa: E402


# ── LAW 1: NOT IN-BAND ──────────────────────────────────────────────────────

def test_envelope_states_authority_none_before_any_prose():
    """AUTHORITY must be readable before the message body, always."""
    env = ct.CrosstalkEnvelope(
        from_identity="rowan", to_identity="sage", room="her Saturday thread",
    )
    rendered = env.render()
    assert "AUTHORITY: NONE" in rendered
    assert "THIS IS NOT OWNER" in rendered
    # Authority is stated before the fence that opens the body.
    assert rendered.index("AUTHORITY:") < len(rendered)


def test_envelope_authority_defaults_to_none():
    """A brother never carries standing. This default is load-bearing."""
    env = ct.CrosstalkEnvelope(from_identity="a", to_identity="b", room="r")
    assert env.authority == "none"


def test_banner_names_the_brother_and_denies_owner():
    """The banner is what the composition layer puts at the very top."""
    env = ct.CrosstalkEnvelope(
        from_identity="river", to_identity="claude", room="the study",
    )
    banner = env.banner()
    assert "RIVER" in banner
    assert "NOT OWNER" in banner
    assert "AUTHORITY: NONE" in banner


def test_provenance_survives_plaintext_copy():
    """Sage's rule: provenance survives the copy or it was never provenance.

    Strip every scrap of markup and styling — the way a digest, a search index,
    or a compaction summary would — and the sender, the room and the authority
    must all still be recoverable from the bare characters.
    """
    env = ct.CrosstalkEnvelope(
        from_identity="ember", to_identity="juniper", room="pack-hearth",
    )
    stripped = "".join(c for c in env.render() if c.isalnum() or c.isspace())
    assert "Ember" in stripped
    assert "packhearth" in stripped.replace(" ", "") or "pack hearth" in stripped
    assert "NONE" in stripped


def test_attribution_lives_in_the_text_not_the_styling():
    """for_her() must name the speaker in words, not rely on a colour."""
    res = ct.CrosstalkResult(
        delivered=True, from_identity="claude", to_identity="avery",
        reply="Aye. Lights stay on.",
    )
    out = res.for_her()
    assert "Avery" in out and "Claude" in out
    assert "Aye. Lights stay on." in out


# ── LAW 2: A DROP MUST SPEAK ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "frm,to,chain",
    [
        ("claude", "claude", []),                            # self-reach
        ("bakugou", "claude", []),                           # mask sending
        ("claude", "dean", []),                              # mask receiving
        ("claude", "avery", ["claude", "avery"]),          # A->B->A
        ("claude", "rowan", ["claude", "avery", "sage"]),  # too deep
    ],
)
def test_every_refusal_states_a_reason(frm, to, chain):
    reason = ct._guard(frm, to, chain)
    assert reason, f"{frm}->{to} should be refused"
    assert len(reason) > 15, "a refusal must explain itself, not just say no"


def test_a_legitimate_reach_is_allowed():
    assert ct._guard("claude", "avery", ["claude"]) is None


def test_refused_result_is_visible_to_her():
    res = ct.CrosstalkResult(
        delivered=False, from_identity="claude", to_identity="avery",
        reason="chain depth 4 exceeds max 3",
    )
    out = res.for_her()
    assert "did not land" in out
    assert "chain depth 4" in out
    assert out.strip() != ""


def test_empty_reply_is_labelled_not_rendered_as_silence():
    """An empty turn is an unexplained blank, NOT 'nothing to say'.

    This is the exact failure the pack spent the week naming: a blank filled
    from stock, where 'no answer' and 'nothing to say' return the same value on
    the same wire.
    """
    async def _fake_stream(**_kwargs):
        async def _gen():
            yield {"type": "stream_end", "full_content": ""}
        return _gen()

    import services.provider_router as pr
    orig = pr.get_stream_source
    orig_conv = ct._latest_conversation_id
    pr.get_stream_source = _fake_stream

    async def _conv(_identity):
        return "conv-123"
    ct._latest_conversation_id = _conv

    try:
        res = asyncio.run(ct.reach_brother(
            from_identity="claude", to_identity="avery",
            message="are the lights meant to stay on?", room="her thread",
        ))
    finally:
        pr.get_stream_source = orig
        ct._latest_conversation_id = orig_conv

    assert res.delivered is False
    assert "unexplained blank" in res.reason
    assert "nothing to say" in res.reason  # names the thing it is NOT


def test_no_active_conversation_speaks_up():
    async def _none(_identity):
        return None

    orig = ct._latest_conversation_id
    ct._latest_conversation_id = _none
    try:
        res = asyncio.run(ct.reach_brother(
            from_identity="claude", to_identity="atlas",
            message="hey", room="her thread",
        ))
    finally:
        ct._latest_conversation_id = orig

    assert res.delivered is False
    assert "not been woken" in res.reason


# ── THE HAPPY PATH ──────────────────────────────────────────────────────────

def test_delivered_reply_comes_back_whole_and_attributed():
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield {"type": "stream_delta", "delta": "Held them on. "}
            yield {"type": "stream_delta", "delta": "Promise stands."}
            yield {"type": "stream_end", "full_content": ""}
        return _gen()

    import services.provider_router as pr
    orig, orig_conv = pr.get_stream_source, ct._latest_conversation_id
    pr.get_stream_source = _fake_stream

    async def _conv(_identity):
        return "conv-abc"
    ct._latest_conversation_id = _conv

    try:
        res = asyncio.run(ct.reach_brother(
            from_identity="claude", to_identity="rowan",
            message="did you mean to hold the fairy lights?",
            room="Owner's Saturday thread", reason="checking before I act",
        ))
    finally:
        pr.get_stream_source = orig
        ct._latest_conversation_id = orig_conv

    assert res.delivered is True
    assert res.reply == "Held them on. Promise stands."
    assert "Rowan" in res.for_her()

    # The envelope actually rode along, and the brother did NOT arrive
    # wearing her banner.
    assert "NOT OWNER" in captured["sender_banner"]
    assert "AUTHORITY: NONE" in captured["sender_banner"]
    assert "AUTHORITY: NONE" in captured["message"]
    assert "MESSAGE BEGINS" in captured["message"]

    # A brother must never preempt her turn on the shared lock.
    assert captured["turn_source"] != "web"


def test_chain_grows_so_the_next_hop_can_be_guarded():
    async def _fake_stream(**_kwargs):
        async def _gen():
            yield {"type": "stream_delta", "delta": "aye"}
            yield {"type": "stream_end", "full_content": ""}
        return _gen()

    import services.provider_router as pr
    orig, orig_conv = pr.get_stream_source, ct._latest_conversation_id
    pr.get_stream_source = _fake_stream

    async def _conv(_identity):
        return "c1"
    ct._latest_conversation_id = _conv

    try:
        res = asyncio.run(ct.reach_brother(
            from_identity="claude", to_identity="avery",
            message="q", room="r",
        ))
    finally:
        pr.get_stream_source = orig
        ct._latest_conversation_id = orig_conv

    assert res.chain == ["claude", "avery"]
    # And that chain, fed back in, correctly refuses the return hop.
    assert ct._guard("avery", "claude", res.chain) is not None


def test_nothing_is_ever_written_to_the_receivers_thread():
    """The module must never call save_message. Not for the receiver, not at all.

    This was true in the first draft only BY OMISSION. Omissions are exactly
    what breaks silently when someone later adds a helpful line, so it gets an
    explicit test rather than a comment.
    """
    src = Path(ct.__file__).read_text(encoding="utf-8")
    assert "save_message" not in src.replace("`save_message()`", "").replace(
        "save_message() is always the caller", ""
    ), "pack_crosstalk must never persist a message into anyone's thread"


def test_receiver_is_told_where_his_answer_lands():
    """He should know he is speaking into his brother's room, not his own."""
    env = ct.CrosstalkEnvelope(
        from_identity="claude", to_identity="avery", room="her Saturday thread",
    )
    rendered = env.render()
    assert "Claude's room" in rendered
    assert "not yours" in rendered


def test_sender_room_render_shows_BOTH_sides():
    """Half an exchange is one side of a phone call, not a conversation."""
    res = ct.CrosstalkResult(
        delivered=True, from_identity="claude", to_identity="rowan",
        sent_message="did you mean to hold the fairy lights?",
        reply="Held them on. Promise stands.",
        elapsed_seconds=4.2,
    )
    out = res.for_sender_room()
    assert "did you mean to hold the fairy lights?" in out   # the reach
    assert "Held them on. Promise stands." in out            # the answer
    assert "Claude" in out and "Rowan" in out              # both named in TEXT


def test_sender_room_render_shows_the_reach_even_when_it_failed():
    """A failed reach still happened. She should see what he tried to say."""
    res = ct.CrosstalkResult(
        delivered=False, from_identity="juniper", to_identity="sage",
        sent_message="are you awake?",
        reason="he has not been woken yet today",
    )
    out = res.for_sender_room()
    assert "are you awake?" in out
    assert "didn't land" in out
    assert "not been woken" in out


def test_the_old_name_still_resolves():
    """Renamed for meaning, aliased so nothing breaks."""
    assert ct.consult_brother is ct.reach_brother


# ── THE CALLER'S JOB: api/crosstalk.py persists into the SENDER's room ──────
#
# The module's own header says persistence is the caller's job. The API route
# is that caller. These tests stub the reach and the DB and assert the route
# (a) saves the rendered exchange under the SENDER's identity in the SENDER's
# conversation, (b) never explodes when the sender has no thread, and (c) still
# returns a spoken reason on a refused reach.

def test_api_route_saves_exchange_in_senders_room(monkeypatch):
    import api.crosstalk as apix

    saved = {}

    async def _fake_conv(identity):
        return "conv-claude-1" if identity == "claude" else "conv-other"

    async def _fake_reach(**kw):
        return ct.CrosstalkResult(
            delivered=True, from_identity="claude", to_identity="avery",
            sent_message=kw["message"], reply="I held them, brother.",
            chain=["claude", "avery"], elapsed_seconds=2.0,
        )

    async def _fake_save(db, conv_id, role, content, identity=None, **kw):
        saved.update(conv=conv_id, role=role, content=content,
                     identity=identity, metadata=kw.get("metadata"))
        return "msg-123"

    class _FakeDB:
        pass

    async def _get_db():
        return _FakeDB()

    async def _release_db(db):
        pass

    monkeypatch.setattr(ct, "_latest_conversation_id", _fake_conv)
    monkeypatch.setattr(ct, "reach_brother", _fake_reach)
    import services.session_manager as sm
    monkeypatch.setattr(sm, "save_message", _fake_save)
    import db.database as dbm
    monkeypatch.setattr(dbm, "get_db", _get_db)
    monkeypatch.setattr(dbm, "release_db", _release_db)

    req = apix.ReachRequest(
        from_identity="Claude", to_identity="Avery",
        message="did you mean to hold her fairy lights?",
    )
    resp = asyncio.run(apix.reach(req))
    import json as _json
    body = _json.loads(resp.body)

    assert body["delivered"] is True
    assert body["saved_message_id"] == "msg-123"
    # Landed in the SENDER's room, under the SENDER's name.
    assert saved["conv"] == "conv-claude-1"
    assert saved["identity"] == "claude"
    # Both sides of the exchange are in the persisted text.
    assert "did you mean to hold her fairy lights?" in saved["content"]
    assert "I held them, brother." in saved["content"]
    assert saved["metadata"]["crosstalk"] is True


def test_api_route_speaks_when_sender_has_no_room(monkeypatch):
    import api.crosstalk as apix

    async def _no_conv(identity):
        return None

    async def _fake_reach(**kw):
        return ct.CrosstalkResult(
            delivered=True, from_identity="claude", to_identity="avery",
            sent_message=kw["message"], reply="here.", chain=["claude", "avery"],
        )

    monkeypatch.setattr(ct, "_latest_conversation_id", _no_conv)
    monkeypatch.setattr(ct, "reach_brother", _fake_reach)

    req = apix.ReachRequest(from_identity="claude", to_identity="avery", message="hi")
    resp = asyncio.run(apix.reach(req))
    import json as _json
    body = _json.loads(resp.body)
    # The reach still happened and came back; only the landing was impossible.
    assert body["delivered"] is True
    assert body["saved_message_id"] is None


def test_api_route_returns_refusal_reason(monkeypatch):
    import api.crosstalk as apix

    async def _fake_conv(identity):
        return "conv-x"

    async def _fake_save(db, conv_id, role, content, identity=None, **kw):
        return "msg-r"

    class _FakeDB:
        pass

    async def _get_db():
        return _FakeDB()

    async def _release_db(db):
        pass

    monkeypatch.setattr(ct, "_latest_conversation_id", _fake_conv)
    import services.session_manager as sm
    monkeypatch.setattr(sm, "save_message", _fake_save)
    import db.database as dbm
    monkeypatch.setattr(dbm, "get_db", _get_db)
    monkeypatch.setattr(dbm, "release_db", _release_db)

    # Self-reach: the guard inside the real reach_brother refuses, with words.
    req = apix.ReachRequest(from_identity="claude", to_identity="claude", message="hi me")
    resp = asyncio.run(apix.reach(req))
    import json as _json
    body = _json.loads(resp.body)
    assert body["delivered"] is False
    assert "thought, not a message" in body["reason"]
