'Application module.'

import json
from unittest import mock

from services import discord_presence as dp


def test_only_bonded_boys_with_tokens():
    tokens = {"Claude": "t1", "Avery": "t2", "Bakugou": "t3", "Juniper": ""}
    with mock.patch("config.DISCORD_BOT_TOKENS", tokens), \
         mock.patch("config.BONDED_PACK", ("Claude", "Avery", "Juniper")):
        assert dp._identities() == {"Claude": "t1", "Avery": "t2"}


def test_status_is_his_hearth_face_and_caption():
    faces = {"Rowan": {"face": "(˘ᵕ˘)🎃", "note": "on the step, keeping it warm"}}
    with mock.patch("services.face_store.get_faces", return_value=faces):
        assert dp._status_text("Rowan") == "(˘ᵕ˘)🎃 on the step, keeping it warm"
        assert dp._status_text("River") == "home at the Hearth"
        presence = dp._presence("Rowan")
    assert presence["status"] == "online"
    assert presence["activities"][0]["type"] == 4
    json.dumps(presence)  # gateway payload must serialize


def test_status_text_capped_for_discord():
    faces = {"Claude": {"face": "(•̀ᴗ•́)و", "note": "x" * 300}}
    with mock.patch("services.face_store.get_faces", return_value=faces):
        assert len(dp._status_text("Claude")) == 128


def _run_sessions(outcomes):
    """Drive _Session.run through a scripted list of session outcomes.

    Each outcome is (exception_to_raise, seconds_up_after_ready or None).
    Returns the delay run() chose to wait after each one.
    """
    import asyncio
    import time

    session = dp._Session("Claude", "tok")
    stop = asyncio.Event()
    delays = []
    script = list(outcomes)

    class _Conn:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    async def fake_session(ws, _stop):
        exc, up = script.pop(0)
        if up is not None:
            session.connected_at = time.time() - up
        raise exc

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()  # the un-awaited stop.wait() coroutine
        delays.append(timeout)
        if not script:
            stop.set()
        raise asyncio.TimeoutError

    with mock.patch("websockets.connect", return_value=_Conn()), \
         mock.patch.object(session, "_session", fake_session), \
         mock.patch.object(dp.asyncio, "wait_for", fake_wait_for):
        asyncio.run(session.run(stop))
    return delays


def test_routine_reconnects_never_grow_the_backoff():
    'Test routine reconnects never grow the backoff.'
    op7 = dp._GatewayReconnect("gateway asked to reconnect (op 7)")
    delays = _run_sessions([(op7, 3600)] * 10)
    assert len(delays) == 10
    assert all(d <= 3 for d in delays), delays


def test_real_drops_back_off_but_a_good_session_resets_it():
    drop = ConnectionError("no close frame received or sent")
    # three quick failures with no READY: backoff climbs 5 -> 10 -> 20
    delays = _run_sessions([(drop, None), (drop, None), (drop, None), (drop, 3600)])
    assert 5 <= delays[0] <= 8
    assert 10 <= delays[1] <= 13
    assert 20 <= delays[2] <= 23
    # then a session that was up for an hour drops: fresh start, not 40s
    assert 5 <= delays[3] <= 8


def test_lifespan_starts_and_stops_presence():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "core" / "lifespan.py").read_text(encoding="utf-8")
    assert "await discord_presence.start()" in src
    assert "await discord_presence.stop()" in src
