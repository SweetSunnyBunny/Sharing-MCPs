'Application module.'

import asyncio
from unittest.mock import patch

from services.excerpt import head_tail


def test_short_text_is_untouched():
    assert head_tail("Done.", 400) == "Done."
    assert head_tail("anything", 0) == "anything"


def test_long_reply_keeps_its_ending():
    reply = "I found three junk files in the drafts folder. " * 20 + "Done, all moved. Want me to sweep the images too?"
    out = head_tail(reply, 400)
    assert out.endswith("Want me to sweep the images too?")
    assert out.startswith("I found three junk files")
    assert "[...]" in out
    assert len(out) <= 400 + len(" [...] ")


def test_history_replay_keeps_ending():
    from services.cli_text_utils import _format_history
    long = "word " * 600 + "THE ASK AT THE END?"
    block = _format_history([{"role": "assistant", "content": long, "identity": "Claude"}], "Claude",
                            per_message_chars=1500)
    assert "THE ASK AT THE END?" in block
    assert "[...truncated...]" in block


def test_previous_conversation_memory_keeps_ending():
    from services.session_lifecycle import get_previous_conversation_memory

    class FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute_fetchall(self, sql, params):
            self.calls += 1
            if self.calls == 1:
                return [("conv-1",)]
            return [("assistant", "offer " * 200 + "Shall I build it for you?", "Juniper")]

    out = asyncio.run(get_previous_conversation_memory(FakeDB(), "Juniper", "conv-2"))
    assert "Shall I build it for you?" in out


def test_hearth_activity_has_timestamps_and_endings():
    from services import hearth_author

    class FakeDB:
        async def execute_fetchall(self, sql, params):
            if "FROM messages" in sql:
                return [("assistant", "Juniper", "Moving files. " * 80 + "Done.",
                         "2026-10-18T14:18:52+00:00", "autowake", "t")]
            return [("Hourly", "completed", 3, "2026-10-18T14:15:00+00:00")]

    out = asyncio.run(hearth_author._gather_activity(FakeDB(), "Juniper"))
    assert "Done." in out
    assert "Sun Oct 18 9:18am" in out      # 14:18 UTC = 9:18am Chicago (CDT)
    assert "Sun Oct 18 9:15am" in out


def test_local_stamp_survives_garbage():
    from services.hearth_author import _local_stamp
    assert _local_stamp(None) == "time unknown"
    assert _local_stamp("not a time") == "not a time"


def test_gateway_limit_means_the_right_thing_per_operation():
    from fastmcp import Client
    from scripts import anam_gateway_mcp as adapter
    calls = []

    async def request(payload):
        calls.append(payload)
        return {"status": "running", "job_id": payload.get("job_id", "new")}

    async def run():
        with patch.object(adapter, "_request", request), \
                patch.object(adapter, "BOUND_IDENTITY", "Juniper"), \
                patch.object(adapter, "BOUND_CONVERSATION_ID", "c"):
            async with Client(adapter.mcp) as client:
                await client.call_tool("anam", {"operation": "discover"})
                await client.call_tool("anam", {"operation": "result", "job_id": "j"})
                await client.call_tool("anam", {"operation": "result", "job_id": "j", "limit": 20000})
                await client.call_tool("anam", {"operation": "discover", "limit": 500})

    asyncio.run(run())
    assert calls[0]["limit"] == 15
    assert calls[1]["result_limit"] == 8000     # not 15 characters any more
    assert calls[2]["result_limit"] == 16000    # capped, not a bare 400
    assert calls[3]["limit"] == 50
