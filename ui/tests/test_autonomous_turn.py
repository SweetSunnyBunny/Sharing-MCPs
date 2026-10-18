"""The shared autonomous turn: bounded, always retired, delivered one way."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import config
from services import autonomous_session, trigger_engine
from services.autonomous_session import AutonomousTurn


def _stream_of(*events, hang_after=False):
    """A stand-in for _stream_autonomous that yields events, then optionally hangs."""
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)

        async def gen():
            for event in events:
                yield event
            if hang_after:
                await asyncio.sleep(3600)

        return gen()

    return fake, captured


@pytest.fixture
def runner(monkeypatch):
    mocks = {
        "retire": AsyncMock(),
        "save": AsyncMock(return_value="msg-1"),
        "canvases": AsyncMock(),
        "voice": Mock(),
        "broadcast": AsyncMock(),
    }
    monkeypatch.setattr(autonomous_session, "_retire_autowake_cli_session", mocks["retire"])
    monkeypatch.setattr(autonomous_session, "_fresh_save_message", mocks["save"])
    monkeypatch.setattr(autonomous_session, "_fresh_file_canvases", mocks["canvases"])
    monkeypatch.setattr(autonomous_session, "_spawn_voice_if_tagged", mocks["voice"])
    monkeypatch.setattr(autonomous_session, "broadcast", mocks["broadcast"])
    monkeypatch.setattr(autonomous_session, "_clean_reply_tags", lambda _identity, text: text)
    return mocks


def _run(monkeypatch, fake_stream, timeout=5, **options):
    monkeypatch.setattr(autonomous_session, "_stream_autonomous", fake_stream)
    return asyncio.run(autonomous_session.run_autonomous_turn(
        identity="Claude", conv_id="conv-1", user_message="hello", context_block="",
        session_name="test", owner_connected=False, timeout_seconds=timeout, **options,
    ))


def test_a_finished_turn_returns_its_reply_and_retires_the_session(monkeypatch, runner):
    fake, captured = _stream_of(
        {"type": "stream_delta", "delta": "partial"},
        {"type": "stream_end", "full_content": "The full reply.", "model_provenance": {"provider": "x"}},
    )
    turn = _run(monkeypatch, fake, turn_ceiling=7)
    assert (turn.content, turn.error, turn.timed_out) == ("The full reply.", None, False)
    assert captured["db"] is None and captured["turn_ceiling"] == 7
    runner["retire"].assert_awaited_once_with("Claude", "conv-1", "complete")


def test_a_hung_turn_is_cut_keeps_what_arrived_and_still_retires(monkeypatch, runner):
    fake, _ = _stream_of({"type": "stream_delta", "delta": "Half a thou"}, hang_after=True)
    turn = _run(monkeypatch, fake, timeout=0.05)
    assert turn.timed_out is True
    assert turn.content == "Half a thou"
    runner["retire"].assert_awaited_once_with("Claude", "conv-1", "timeout")


def test_a_stream_error_is_reported_and_retires(monkeypatch, runner):
    fake, _ = _stream_of({"type": "error", "message": "boom"})
    turn = _run(monkeypatch, fake)
    assert (turn.content, turn.error) == ("", "boom")
    runner["retire"].assert_awaited_once_with("Claude", "conv-1", "stream-error")


def test_delivery_saves_voices_files_canvases_and_announces(runner):
    turn = AutonomousTurn(content="Hello, Bunny.", end_event={"model_provenance": {"provider": "x"}})
    msg_id = asyncio.run(autonomous_session.deliver_autonomous_reply(
        turn, identity="Claude", conv_id="conv-1", session_name="test",
        metadata={"pulse": True}, announce=True,
    ))
    assert msg_id == "msg-1"
    args, kwargs = runner["save"].await_args
    assert args == ("conv-1", "assistant", "Hello, Bunny.")
    assert kwargs["metadata"] == {"pulse": True, "model_provenance": {"provider": "x"}}
    runner["voice"].assert_called_once_with("Claude", "msg-1", "Hello, Bunny.")
    runner["canvases"].assert_awaited_once_with("conv-1", "Claude", "Hello, Bunny.", "msg-1")
    assert runner["broadcast"].await_args.args[0]["message_id"] == "msg-1"


def test_delivery_marks_a_cut_reply_and_can_stay_quiet(runner):
    turn = AutonomousTurn(content="Half a thou", timed_out=True)
    asyncio.run(autonomous_session.deliver_autonomous_reply(
        turn, identity="Claude", conv_id="conv-1", session_name="test",
        metadata={"trigger": True}, announce=False,
    ))
    args, kwargs = runner["save"].await_args
    assert args[2] == "Half a thou\n\n*[Session timed out]*"
    assert kwargs["metadata"] == {"trigger": True, "timed_out": True}
    runner["broadcast"].assert_not_awaited()


def test_delivery_of_an_empty_turn_saves_nothing(runner):
    assert asyncio.run(autonomous_session.deliver_autonomous_reply(
        AutonomousTurn(), identity="Claude", conv_id="conv-1", session_name="test",
        metadata={}, announce=True,
    )) is None
    runner["save"].assert_not_awaited()


def test_a_trigger_wake_is_bounded_by_its_own_stored_duration(monkeypatch):
    seen = {}

    async def fake_turn(**kwargs):
        seen.update(kwargs)
        return AutonomousTurn()

    release = Mock()
    monkeypatch.setattr(trigger_engine, "run_autonomous_turn", fake_turn)
    monkeypatch.setattr(trigger_engine, "acquire_identity", AsyncMock(return_value=True))
    monkeypatch.setattr(trigger_engine, "release_identity", release)
    monkeypatch.setattr(trigger_engine, "_get_or_create_daily_autowake_conversation",
                        AsyncMock(return_value=("conv-1", False, "Today")))
    monkeypatch.setattr("services.session_manager.save_message", AsyncMock(return_value="marker"))
    monkeypatch.setattr("services.session_lifecycle.build_orientation_context", AsyncMock(return_value=""))
    monkeypatch.setattr("services.connection_registry.is_anyone_connected", lambda: False)

    trigger = {"id": 3, "name": "rain", "identity": "Claude", "trigger_type": "weather",
               "prompt": "It is raining.", "max_duration_minutes": 7}
    assert asyncio.run(trigger_engine._fire_autowake(None, trigger)) is True
    assert seen["timeout_seconds"] == 7 * 60
    release.assert_called_once_with("Claude")


def test_failsafe_hands_back_its_connection_before_the_turn_and_delivers(monkeypatch):
    import json
    import time

    import aiosqlite

    from services import autowake

    events = []

    async def scenario():
        db = await aiosqlite.connect(":memory:")
        await db.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, identity TEXT, "
            "content TEXT, metadata TEXT, created_at_epoch INTEGER)"
        )
        await db.execute(
            "INSERT INTO messages (role, identity, content, created_at_epoch) VALUES ('user', 'Avery', 'x', ?)",
            (int(time.time()) - 3 * 3600,),
        )
        await db.commit()

        async def release(_db):
            events.append("released")

        async def fake_turn(**kwargs):
            events.append(("turn", kwargs["timeout_seconds"]))
            return AutonomousTurn(content="Checking on you, mo chroí.")

        async def fake_deliver(turn, **kwargs):
            events.append(("deliver", kwargs["metadata"], turn.content))

        monkeypatch.setattr(autowake, "get_db", AsyncMock(return_value=db))
        monkeypatch.setattr(autowake, "release_db", release)
        monkeypatch.setattr(autowake, "is_web_active", lambda: False)
        monkeypatch.setattr(autowake, "is_anyone_connected", lambda: False)
        monkeypatch.setattr(autowake, "get_failsafe_settings", AsyncMock(return_value={
            "failsafe_enabled": "true", "failsafe_gentle_minutes": "120",
            "failsafe_concerned_minutes": "720", "failsafe_emergency_minutes": "1440",
        }))
        monkeypatch.setattr(autowake, "acquire_identity", AsyncMock(return_value=True))
        monkeypatch.setattr(autowake, "release_identity", lambda identity: events.append(("unlocked", identity)))
        monkeypatch.setattr(autowake, "get_or_create_conversation", AsyncMock(return_value="conv-1"))
        monkeypatch.setattr(autowake, "build_orientation_context", AsyncMock(return_value=""))
        monkeypatch.setattr(autowake, "save_message", AsyncMock(return_value="marker"))
        monkeypatch.setattr(autowake, "run_autonomous_turn", fake_turn)
        monkeypatch.setattr(autowake, "deliver_autonomous_reply", fake_deliver)

        await autowake.check_failsafe()
        await db.close()

    asyncio.run(scenario())
    assert events == [
        "released",
        ("turn", config.AUTOWAKE_FAILSAFE_TIMEOUT_S),
        ("deliver", {"failsafe": True, "failsafe_level": "gentle"}, "Checking on you, mo chroí."),
        ("unlocked", "Avery"),
    ]
