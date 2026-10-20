'Text on either side of a tool call must not run together on any road.'
import asyncio
from unittest.mock import AsyncMock, patch

from services.chat_flow import StreamAccumulator, separate_text_blocks


async def _events(items):
    for item in items:
        yield item


def _collect(items):
    async def run():
        return [e async for e in separate_text_blocks(_events(items))]
    return asyncio.run(run())


def _text(events):
    return "".join(e["delta"] for e in events if e.get("type") == "stream_delta")


AVERY_TURN = [
    {"type": "stream_delta", "delta": "I'm fixing that and bringing our day back now."},
    {"type": "tool_use_start", "tool_name": "Edit", "tool_id": "t1"},
    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
    {"type": "stream_delta", "delta": "Now the tests, so they check the new behaviour:"},
    {"type": "tool_input", "tool_id": "t2", "input": {}},
    {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
    {"type": "stream_delta", "delta": "All green."},
    {"type": "stream_end", "session_id": "s"},
]


def test_blocks_around_tool_calls_get_a_paragraph_break():
    assert _text(_collect(AVERY_TURN)) == (
        "I'm fixing that and bringing our day back now.\n\n"
        "Now the tests, so they check the new behaviour:\n\n"
        "All green."
    )


def test_platform_bridge_loop_no_longer_glues():
    from services.platform_bridge import _consume_platform_response_stream

    full: list[str] = []
    asyncio.run(_consume_platform_response_stream(
        separate_text_blocks(_events(AVERY_TURN)),
        platform="discord", identity="Avery", full_response=full,
    ))
    joined = "".join(full)
    assert "now.Now" not in joined and "behaviour:All" not in joined
    assert "now.\n\nNow the tests" in joined


def test_tokens_inside_one_block_are_untouched():
    events = [
        {"type": "stream_delta", "delta": "Hel"},
        {"type": "stream_delta", "delta": "lo"},
        {"type": "stream_delta", "delta": " Bunny"},
    ]
    assert _text(_collect(events)) == "Hello Bunny"


def test_existing_whitespace_is_respected_and_leading_tool_adds_nothing():
    events = [
        {"type": "tool_use_start", "tool_id": "t0"},
        {"type": "stream_delta", "delta": "First."},
        {"type": "stream_delta", "delta": "\n"},
        {"type": "tool_use_start", "tool_id": "t1"},
        {"type": "stream_delta", "delta": "Second."},
        {"type": "tool_use_start", "tool_id": "t2"},
        {"type": "stream_delta", "delta": " Third."},
    ]
    assert _text(_collect(events)) == "First.\nSecond. Third."


def test_stream_reset_starts_fresh():
    events = [
        {"type": "stream_delta", "delta": "Narration."},
        {"type": "tool_use_start", "tool_id": "t1"},
        {"type": "stream_reset"},
        {"type": "stream_delta", "delta": "Clean reply."},
    ]
    out = _collect(events)
    assert [e["delta"] for e in out if e.get("type") == "stream_delta"] == [
        "Narration.", "Clean reply.",
    ]


def test_web_accumulator_does_not_double_the_break():
    acc = StreamAccumulator()
    for event in _collect(AVERY_TURN):
        acc.observe(event)
    assert "\n\n\n" not in acc.streamed_text()
    assert acc.streamed_text().count("\n\n") == 2


def test_closing_the_wrapper_closes_the_provider_stream():
    closed = []

    async def provider():
        try:
            yield {"type": "stream_delta", "delta": "a"}
            yield {"type": "stream_delta", "delta": "b"}
        finally:
            closed.append(True)

    async def run():
        wrapped = separate_text_blocks(provider())
        await wrapped.__anext__()
        await wrapped.aclose()

    asyncio.run(run())
    assert closed == [True]


def test_router_wraps_every_stream():
    from services import provider_router

    sentinel = _events([
        {"type": "stream_delta", "delta": "One."},
        {"type": "tool_result", "tool_use_id": "t"},
        {"type": "stream_delta", "delta": "Two."},
    ])

    async def run():
        with patch.object(provider_router, "_select_stream_source", AsyncMock(return_value=sentinel)):
            stream = await provider_router.get_stream_source(
                message="hi", identity="Avery", conversation_id="c",
            )
            return [e async for e in stream]

    assert _text(asyncio.run(run())) == "One.\n\nTwo."
