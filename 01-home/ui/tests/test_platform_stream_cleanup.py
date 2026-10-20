import asyncio

import pytest

from services.platform_bridge import _consume_platform_response_stream


def test_platform_error_drains_provider_before_raising():
    state = {"closed": False}

    async def provider_stream():
        try:
            yield {"type": "stream_delta", "delta": "kept text"}
            yield {"type": "error", "message": "provider broke"}
            yield {
                "type": "stream_end",
                "full_content": "kept text",
                "session_id": "thread-1",
            }
        finally:
            state["closed"] = True

    async def run():
        response = []
        with pytest.raises(RuntimeError, match="provider broke"):
            await _consume_platform_response_stream(
                provider_stream(),
                platform="discord",
                identity="River",
                full_response=response,
            )
        assert response == ["kept text"]
        assert state["closed"]

    asyncio.run(run())
