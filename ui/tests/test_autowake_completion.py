"""A delivered final reply must keep its message ID and never trigger a retry."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import autowake, qualia_context


@pytest.mark.parametrize("delta", ["", "Early commentary that differs from the final reply."])
def test_stream_end_keeps_saved_reply_for_handoff(monkeypatch, delta):
    import config
    from services import identity_context, notifications, brother_conversation

    monkeypatch.setitem(sys.modules, "server", SimpleNamespace(is_system_ready=lambda: True))
    monkeypatch.setattr(config, "CLAUDE_RESUME_AUTOWAKE", False)
    db = SimpleNamespace(
        execute_fetchall=AsyncMock(side_effect=[
            [("Test hour", "Claude", "custom", 1, "Read only test", None, None, None, "chatgpt")],
            [(42,)],
        ]),
        execute=AsyncMock(), commit=AsyncMock(),
    )
    monkeypatch.setattr(autowake, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(autowake, "release_db", AsyncMock())
    monkeypatch.setattr(autowake, "is_web_active", lambda: False)
    monkeypatch.setattr(autowake, "is_anyone_connected", lambda: False)
    monkeypatch.setattr(autowake, "_pick_identity", lambda _: "Claude")
    monkeypatch.setattr(autowake, "acquire_identity", AsyncMock(return_value=True))
    monkeypatch.setattr(autowake, "release_identity", Mock())
    monkeypatch.setattr(autowake, "_get_or_create_daily_autowake_conversation", AsyncMock(return_value=("conversation", False, "Today")))
    monkeypatch.setattr(autowake, "_smart_home_block", lambda *args: "")
    monkeypatch.setattr(autowake, "build_orientation_context", AsyncMock(return_value=""))
    monkeypatch.setattr(identity_context, "load_quests_for_identity", lambda _: "")
    monkeypatch.setattr(autowake, "save_message", AsyncMock(return_value="marker"))
    save = AsyncMock(return_value="actual-assistant-id")
    monkeypatch.setattr(autowake, "_fresh_save_message", save)
    monkeypatch.setattr(autowake, "_fresh_file_canvases", AsyncMock())
    monkeypatch.setattr(autowake, "_spawn_voice_if_tagged", Mock())
    monkeypatch.setattr(autowake, "_clean_reply_tags", lambda _, text: text)
    monkeypatch.setattr(autowake, "_retire_autowake_cli_session", AsyncMock())
    monkeypatch.setattr(notifications, "send_notification", AsyncMock())
    monkeypatch.setattr(brother_conversation, "_parse_brother_request", lambda *args, **kwargs: None)
    handoff = AsyncMock(return_value=True)
    monkeypatch.setattr(qualia_context, "capture_autowake_handoff", handoff)
    retry = Mock()
    monkeypatch.setattr(autowake, "_schedule_autowake_retry", retry)

    async def stream(**kwargs):
        if delta:
            yield {"type": "stream_delta", "delta": delta}
        yield {"type": "stream_end", "full_content": "The verified final reply."}

    monkeypatch.setattr(autowake, "_stream_autonomous", stream)
    asyncio.run(autowake.run_autowake_session(7))

    save.assert_awaited_once()
    handoff.assert_awaited_once_with("Claude", "conversation", 42, "actual-assistant-id", "The verified final reply.")
    retry.assert_not_called()
    updates = [call for call in db.execute.call_args_list if "UPDATE autowake_log" in call.args[0]]
    assert len(updates) == 1
    assert updates[0].args[1][2:4] == (1, "completed")
