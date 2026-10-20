'Application module.'
import asyncio
import time

import pytest

from services import platform_bridge as pb


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_caller_no_longer_references_removed_constant():
    import inspect
    src = inspect.getsource(pb)
    assert "_PLATFORM_RESPONSE_TIMEOUT" not in src


def test_busy_turn_longer_than_idle_window_completes():
    activity = {}

    async def busy():
        for _ in range(6):  # 0.3 s total, idle window is 0.15 s
            activity["last"] = time.monotonic()
            await asyncio.sleep(0.05)
        return "ok"

    result = _run(pb._run_with_idle_watchdog(
        busy(), activity, idle_timeout=0.15, max_seconds=5, tick=0.01))
    assert result == "done"


def test_silent_turn_is_cut_as_idle():
    async def hung():
        await asyncio.sleep(10)

    with pytest.raises(asyncio.TimeoutError) as info:
        _run(pb._run_with_idle_watchdog(
            hung(), {}, idle_timeout=0.1, max_seconds=5, tick=0.01))
    assert str(info.value) == "idle"


def test_endless_stream_hits_ceiling():
    activity = {}

    async def chatty_forever():
        while True:
            activity["last"] = time.monotonic()
            await asyncio.sleep(0.01)

    with pytest.raises(asyncio.TimeoutError) as info:
        _run(pb._run_with_idle_watchdog(
            chatty_forever(), activity, idle_timeout=1, max_seconds=0.2, tick=0.01))
    assert str(info.value) == "ceiling"


def test_coroutine_error_surfaces():
    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        _run(pb._run_with_idle_watchdog(
            boom(), {}, idle_timeout=1, max_seconds=1, tick=0.01))


def test_keepalive_does_not_reset_idle_clock():
    activity = {"last": 0.0}

    async def stream():
        yield {"type": "keepalive"}

    _run(pb._consume_platform_response_stream(
        stream(), platform="discord", identity="claude",
        full_response=[], activity=activity))
    assert activity["last"] == 0.0

    async def real():
        yield {"type": "stream_delta", "delta": "hi"}

    _run(pb._consume_platform_response_stream(
        real(), platform="discord", identity="claude",
        full_response=[], activity=activity))
    assert activity["last"] > 0.0
