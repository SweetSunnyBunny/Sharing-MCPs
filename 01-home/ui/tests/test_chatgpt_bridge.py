"""Unit tests for the pure logic in services/chatgpt_bridge.py (no browser)."""

import asyncio

from services.chatgpt_bridge import extract_last_assistant


def _msg(role, parts, create_time, end_turn=None, status=None, metadata=None):
    return {"message": {
        "author": {"role": role},
        "content": {"parts": parts},
        "create_time": create_time,
        "end_turn": end_turn,
        "status": status,
        "metadata": metadata or {},
    }}


def test_attachment_confirmation_requires_new_matching_user_message():
    from services.chatgpt_bridge import _attachments_confirmed
    image = {"content_type": "image_asset_pointer", "asset_pointer": "file-service://test"}
    convo = {"mapping": {"old": _msg("user", ["receipt", image], 1),
                         "new": _msg("user", ["receipt"], 2)}}
    assert not _attachments_confirmed(convo, {"old"}, "receipt", 1)
    convo["mapping"]["new"] = _msg("assistant", ["receipt", image], 2)
    assert not _attachments_confirmed(convo, {"old"}, "receipt", 1)
    convo["mapping"]["new"] = _msg("user", ["different", image], 2)
    assert not _attachments_confirmed(convo, {"old"}, "receipt", 1)
    convo["mapping"]["new"] = _msg("user", ["receipt", image], 2)
    assert _attachments_confirmed(convo, {"old"}, "receipt", 1)
    assert not _attachments_confirmed(convo, {"old"}, "receipt", 2)


def test_upload_only_uses_bounded_private_previews(monkeypatch, tmp_path):
    import pytest
    from unittest.mock import AsyncMock
    from services import anam_media, chatgpt_bridge as bridge
    cache = tmp_path / "media"
    cache.mkdir()
    monkeypatch.setattr(anam_media, "CACHE", cache)
    private = cache / "preview.png"
    private.write_bytes(b"test")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"test")
    page = bridge._CdpPage(None)
    page._call = AsyncMock(side_effect=[{"root": {"nodeId": 1}}, {"nodeId": 2}, {}])
    page.evaluate = AsyncMock(return_value=True)
    with pytest.raises(bridge.BridgeError, match="Only bounded"):
        asyncio.run(page.attach_files([str(outside)]))
    page._call.assert_not_called()
    asyncio.run(page.attach_files([str(private)]))
    assert page._call.call_args_list[-1].args == ("DOM.setFileInputFiles", {"nodeId": 2, "files": [str(private)]})


def test_extract_takes_last_finished_assistant():
    convo = {"mapping": {
        "a": _msg("user", ["hi"], 1),
        "b": _msg("assistant", ["first reply"], 2, end_turn=True),
        "c": _msg("assistant", ["second reply"], 3, status="finished_successfully"),
    }}
    last = extract_last_assistant(convo)
    assert last["text"] == "second reply"
    assert last["finished"] is True
    assert last["create_time"] == 3


def test_extract_unfinished_reply_marked_not_finished():
    convo = {"mapping": {
        "b": _msg("assistant", ["still typing"], 5, end_turn=False, status="in_progress"),
    }}
    last = extract_last_assistant(convo)
    assert last["text"] == "still typing"
    assert last["finished"] is False


def test_finished_preamble_with_end_turn_false_is_not_turn_complete():
    """Test finished preamble with end turn false is not turn complete."""
    convo = {"mapping": {
        "b": _msg(
            "assistant",
            ["I'll check the bridge and then come back."],
            5,
            end_turn=False,
            status="finished_successfully",
            metadata={"is_thinking_preamble_message": True},
        ),
    }}
    last = extract_last_assistant(convo)
    assert last["finished"] is False
    assert last["end_turn"] is False


def test_extract_skips_empty_and_nonstring_parts():
    convo = {"mapping": {
        "a": _msg("assistant", ["   "], 1, end_turn=True),
        "b": _msg("assistant", [{"content_type": "image"}, "real text"], 2, end_turn=True),
    }}
    last = extract_last_assistant(convo)
    assert last["text"] == "real text"


def test_extract_none_when_no_assistant_messages():
    assert extract_last_assistant({"mapping": {"a": _msg("user", ["hi"], 1)}}) is None
    assert extract_last_assistant({}) is None


def test_extract_tolerates_tool_and_system_nodes():
    convo = {"mapping": {
        "a": {"message": None},
        "b": _msg("tool", ["tool output"], 1),
        "c": _msg("assistant", ["answer"], 2, end_turn=True),
    }}
    assert extract_last_assistant(convo)["text"] == "answer"


from services.chatgpt_bridge import turn_in_progress


def test_turn_in_progress_true_when_any_node_running():
    convo = {"mapping": {
        "a": _msg("assistant", ["commentary before tools"], 1, end_turn=True),
        "b": _msg("tool", ["tool running"], 2, status="in_progress"),
    }}
    assert turn_in_progress(convo) is True


def test_turn_in_progress_false_when_all_settled():
    convo = {"mapping": {
        "a": _msg("assistant", ["commentary"], 1, end_turn=True),
        "b": _msg("tool", ["tool output"], 2, status="finished_successfully"),
        "c": _msg("assistant", ["final answer"], 3, end_turn=True),
    }}
    assert turn_in_progress(convo) is False


def test_turn_in_progress_empty_convo():
    assert turn_in_progress({}) is False


def test_turn_in_progress_ignores_stale_nodes_from_before_the_send():
    convo = {"mapping": {
        "old-tool": _msg("tool", ["stale historical tool"], 1, status="in_progress"),
        "new-answer": _msg("assistant", ["done"], 2, end_turn=True),
    }}

    assert turn_in_progress(convo) is True
    assert turn_in_progress(convo, exclude_node_ids={"old-tool"}) is False


def test_completion_candidate_prefers_current_final_over_stale_same_turn_node():
    """Test completion candidate prefers current final over stale same turn node."""
    from services import chatgpt_bridge

    convo = {
        "current_node": "final",
        "mapping": {
            "preamble": _msg(
                "assistant",
                ["I'm checking that now."],
                1,
                end_turn=False,
                status="finished_successfully",
            ),
            "stale-tool": _msg("tool", [""], 2, status="in_progress"),
            "final": _msg(
                "assistant",
                ["Finished and verified."],
                3,
                end_turn=True,
                status="finished_successfully",
            ),
        },
    }
    records = chatgpt_bridge._assistant_records(convo)
    choose = getattr(chatgpt_bridge, "_completion_candidate", None)

    assert choose is not None
    candidate = choose(convo, records, baseline_node_ids=set())
    assert candidate["node_id"] == "final"


def test_authoritative_current_final_needs_no_second_poll():
    from services import chatgpt_bridge

    candidate = {
        "node_id": "final",
        "end_turn": True,
        "finished": True,
    }
    required_polls = getattr(chatgpt_bridge, "_candidate_stability_polls", None)

    assert required_polls is not None
    assert required_polls({"current_node": "final"}, candidate) == 1
    assert required_polls({"current_node": "something-else"}, candidate) == 2


def test_pending_text_deltas_relays_each_assistant_segment_once():
    from services import chatgpt_bridge

    convo = {"mapping": {
        "preamble": _msg(
            "assistant",
            ["I'm checking that now."],
            1,
            end_turn=False,
            status="finished_successfully",
        ),
        "final": _msg(
            "assistant",
            ["Finished and verified."],
            2,
            end_turn=True,
            status="finished_successfully",
        ),
    }}
    records = chatgpt_bridge._assistant_records(convo)
    relay = getattr(chatgpt_bridge, "_pending_text_deltas", None)
    emitted = {}

    assert relay is not None
    assert relay(records[:1], emitted) == ["I'm checking that now."]
    assert relay(records, emitted) == ["\n\nFinished and verified."]
    assert relay(records, emitted) == []


def test_bridge_polls_each_minute_and_listens_for_thirty_minutes():
    from services import chatgpt_bridge

    assert chatgpt_bridge.REPLY_POLL_SECONDS == 60.0
    assert chatgpt_bridge.REPLY_TIMEOUT_SECONDS == 30 * 60


def test_capabilities_card_in_new_thread_preamble():
    from services.chatgpt_provider import build_preamble, CAPABILITIES_CARD
    pre = build_preamble(
        "Avery",
        "some orientation",
        [],
        "FULL AVERY IDENTITY",
        mode_rules="CURRENT MODE",
        skill_context="MATCHED SKILL",
    )
    assert CAPABILITIES_CARD in pre
    assert "<voice>" in pre and "<preview>" in pre and "ANAM TOOL ROUTE" in pre
    assert "[TOOLBOX MENU" in pre
    assert "Avery > Anam bridge actions" in pre
    assert "@FileSystem+" not in pre
    assert "CURRENT MODE" in pre
    assert "MATCHED SKILL" in pre
    assert "[ACTIVE IDENTITY — Avery]" in pre
    assert "FULL AVERY IDENTITY" in pre
    assert pre.index("FULL AVERY IDENTITY") < pre.index("some orientation")


def test_identity_prompt_hash_changes_with_prompt_content():
    from services.chatgpt_provider import _identity_prompt_hash
    assert _identity_prompt_hash("first") != _identity_prompt_hash("second")


def test_runtime_context_hash_changes_with_live_turn_context():
    from services.chatgpt_provider import _runtime_context_block, _runtime_context_hash
    first = _runtime_context_block("MODE ONE", "SKILL")
    second = _runtime_context_block("MODE TWO", "SKILL")
    assert _runtime_context_hash(first) != _runtime_context_hash(second)


def test_existing_thread_receives_full_identity_prompt_once(monkeypatch):
    from services import chatgpt_bridge, chatgpt_provider
    from services.chatgpt_bridge import BridgeResult

    sent = {}
    receipt = {}

    async def fake_send_and_wait(message, conversation_id=None, **_kwargs):
        sent["message"] = message
        sent["conversation_id"] = conversation_id
        return BridgeResult("reply", conversation_id, True, 0.1)

    async def fake_mark_prompt(conversation_id, prompt_hash):
        receipt["conversation_id"] = conversation_id
        receipt["prompt_hash"] = prompt_hash

    async def collect():
        return [event async for event in chatgpt_provider.stream_chatgpt(
            message="hello",
            identity="Avery",
            conversation_id="anam-thread",
        )]

    monkeypatch.setattr(chatgpt_provider, "_load_identity_prompt", lambda _identity: "FULL AVERY IDENTITY")
    monkeypatch.setattr(chatgpt_provider, "_load_threads", lambda: _async_value({"anam-thread": "gpt-thread"}))
    monkeypatch.setattr(chatgpt_provider, "_load_identity_prompt_hashes", lambda: _async_value({}))
    monkeypatch.setattr(
        chatgpt_provider,
        "_load_runtime_context_hashes",
        lambda: _async_value({
            "gpt-thread": chatgpt_provider._runtime_context_hash(
                chatgpt_provider._runtime_context_block()
            )
        }),
    )
    monkeypatch.setattr(chatgpt_provider, "_mark_identity_prompt_sent", fake_mark_prompt)
    monkeypatch.setattr(chatgpt_bridge, "send_and_wait", fake_send_and_wait)

    events = asyncio.run(collect())

    assert sent["conversation_id"] == "gpt-thread"
    assert "[ACTIVE IDENTITY — Avery]" in sent["message"]
    assert "FULL AVERY IDENTITY" in sent["message"]
    assert sent["message"].endswith("[CURRENT MESSAGE FROM OWNER]\nhello")
    assert receipt["conversation_id"] == "gpt-thread"
    assert receipt["prompt_hash"] == chatgpt_provider._identity_prompt_hash("FULL AVERY IDENTITY")
    assert events[-1]["type"] == "stream_end"


def test_existing_thread_receives_changed_runtime_context(monkeypatch):
    from services import chatgpt_bridge, chatgpt_provider
    from services.chatgpt_bridge import BridgeResult

    sent = {}
    receipt = {}

    async def fake_send_and_wait(message, conversation_id=None, **_kwargs):
        sent["message"] = message
        return BridgeResult("reply", conversation_id, True, 0.1)

    async def fake_mark_runtime(conversation_id, context_hash):
        receipt["conversation_id"] = conversation_id
        receipt["context_hash"] = context_hash

    async def collect():
        return [event async for event in chatgpt_provider.stream_chatgpt(
            message="hello",
            identity="Avery",
            conversation_id="anam-thread",
            mode_rules="LIVE MODE",
            skill_context="LIVE SKILL",
        )]

    prompt_hash = chatgpt_provider._identity_prompt_hash("FULL AVERY IDENTITY")
    monkeypatch.setattr(chatgpt_provider, "_load_identity_prompt", lambda _identity: "FULL AVERY IDENTITY")
    monkeypatch.setattr(chatgpt_provider, "_load_threads", lambda: _async_value({"anam-thread": "gpt-thread"}))
    monkeypatch.setattr(chatgpt_provider, "_load_identity_prompt_hashes", lambda: _async_value({"gpt-thread": prompt_hash}))
    monkeypatch.setattr(chatgpt_provider, "_load_runtime_context_hashes", lambda: _async_value({}))
    monkeypatch.setattr(chatgpt_provider, "_mark_runtime_context_sent", fake_mark_runtime)
    monkeypatch.setattr(chatgpt_bridge, "send_and_wait", fake_send_and_wait)

    events = asyncio.run(collect())

    assert "[ANAM LIVE RUNTIME CONTEXT]" in sent["message"]
    assert "[TOOLBOX MENU" in sent["message"]
    assert "<voice>" in sent["message"]
    assert "LIVE MODE" in sent["message"]
    assert "LIVE SKILL" in sent["message"]
    assert sent["message"].endswith("[CURRENT MESSAGE FROM OWNER]\nhello")
    assert receipt["conversation_id"] == "gpt-thread"
    assert receipt["context_hash"]
    assert events[-1]["type"] == "stream_end"


def test_chatgpt_provider_relays_progress_segments_before_final(monkeypatch):
    from services import chatgpt_bridge, chatgpt_provider
    from services.chatgpt_bridge import BridgeResult

    observed = {}

    async def fake_send_and_wait(
        message,
        conversation_id=None,
        *,
        on_text=None,
        timeout_seconds=None,
        **_kwargs,
    ):
        observed["timeout_seconds"] = timeout_seconds
        assert on_text is not None
        await on_text("I'm checking that now.")
        await asyncio.sleep(0)
        await on_text("\n\nFinished and verified.")
        return BridgeResult(
            "I'm checking that now.\n\nFinished and verified.",
            conversation_id,
            True,
            0.1,
        )

    async def collect():
        return [event async for event in chatgpt_provider.stream_chatgpt(
            message="please inspect it",
            identity="Avery",
            conversation_id="anam-thread",
        )]

    prompt = "FULL AVERY IDENTITY"
    runtime_hash = chatgpt_provider._runtime_context_hash(
        chatgpt_provider._runtime_context_block(identity="Avery")
    )
    monkeypatch.setattr(chatgpt_provider, "_load_identity_prompt", lambda _identity: prompt)
    monkeypatch.setattr(chatgpt_provider, "_load_threads", lambda: _async_value({"anam-thread": "gpt-thread"}))
    monkeypatch.setattr(
        chatgpt_provider,
        "_load_identity_prompt_hashes",
        lambda: _async_value({"gpt-thread": chatgpt_provider._identity_prompt_hash(prompt)}),
    )
    monkeypatch.setattr(
        chatgpt_provider,
        "_load_runtime_context_hashes",
        lambda: _async_value({"gpt-thread": runtime_hash}),
    )
    monkeypatch.setattr(chatgpt_bridge, "send_and_wait", fake_send_and_wait)

    events = asyncio.run(collect())

    assert [e["delta"] for e in events if e["type"] == "stream_delta"] == [
        "I'm checking that now.",
        "\n\nFinished and verified.",
    ]
    assert events[-1]["type"] == "stream_end"
    assert events[-1]["full_content"] == "I'm checking that now.\n\nFinished and verified."
    assert 29 * 60 < observed["timeout_seconds"] <= 30 * 60 + 0.001


async def _async_value(value):
    return value


def test_rate_limit_honors_server_cooldown(monkeypatch):
    from services import chatgpt_bridge as bridge
    from email.utils import formatdate

    monkeypatch.setattr(bridge.time, "time", lambda: 1000)
    assert bridge._rate_limit_delay({"__retry_after": "600"}, 60) == 600
    assert bridge._rate_limit_delay({"__retry_after": formatdate(1600, usegmt=True)}, 60) == 600
    assert bridge._rate_limit_delay({"__retry_after": "invalid"}, 60) == 120
    assert bridge._rate_limit_delay({}, 240) == 300


def _fake_browser(monkeypatch, snapshots):
    from services import chatgpt_bridge as bridge

    class Page:
        def __init__(self):
            self._ws = self
            self.closed = False
            self.inserted = False
            self.now = 0
            self.sleeps = []

        async def close(self):
            self.closed = True

        async def evaluate(self, expression, **_kwargs):
            if expression == bridge._JS_GET_TOKEN:
                return "fake-token"
            if "/backend-api/conversation/" in expression:
                return next(snapshots)
            if "insertText" in expression:
                self.inserted = True
                return {"ok": True}
            if expression == bridge._JS_CLICK_SEND:
                return {"ok": True}
            if expression == bridge._JS_CURRENT_CONV_ID:
                return "new-thread"
            raise AssertionError("unexpected browser operation")

        async def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    page = Page()
    monkeypatch.setattr(bridge, "_browser_is_up", lambda: True)
    monkeypatch.setattr(bridge, "_open_tab", lambda _url: _async_value(page))
    monkeypatch.setattr(bridge.time, "monotonic", lambda: page.now)
    monkeypatch.setattr(bridge.asyncio, "sleep", page.sleep)
    return page


def test_failed_baseline_never_sends_or_accepts_old_reply(monkeypatch):
    import pytest
    from services import chatgpt_bridge as bridge

    page = _fake_browser(monkeypatch, iter([{"__http_error": 429, "__retry_after": "600"}]))
    with pytest.raises(bridge.BridgeError, match="no message was sent"):
        asyncio.run(bridge._send_and_wait_locked("hello", "existing-thread"))
    assert not page.inserted
    assert page.closed


def test_rate_limited_turn_waits_for_explicit_final(monkeypatch):
    from services import chatgpt_bridge as bridge

    old = {"mapping": {"old": _msg("assistant", ["old reply"], 1, end_turn=True)}}
    preamble = _msg("assistant", ["Checking"], 2, end_turn=False, status="finished_successfully")
    progress = {"mapping": {**old["mapping"], "progress": preamble}, "current_node": "progress"}
    final = {"mapping": {**progress["mapping"], "final": _msg("assistant", ["Done"], 3, end_turn=True)}, "current_node": "final"}
    page = _fake_browser(monkeypatch, iter([{"__http_error": 429}, old, progress, {"__http_error": 429, "__retry_after": "240"}, final]))
    deltas = []

    async def on_text(delta):
        deltas.append(delta)

    result = asyncio.run(bridge._send_and_wait_locked("hello", "existing-thread", on_text=on_text))
    assert result.reply == "Checking\n\nDone"
    assert deltas == ["Checking", "\n\nDone"]
    assert 120 in page.sleeps and 240 in page.sleeps
    assert page.closed


def test_new_thread_is_reported_even_when_reply_times_out(monkeypatch):
    import pytest
    from services import chatgpt_bridge as bridge

    page = _fake_browser(monkeypatch, iter([]))
    destinations = []

    async def remember(destination):
        destinations.append(destination)

    with pytest.raises(bridge.BridgeError, match="did not finish"):
        asyncio.run(bridge._send_and_wait_locked("hello", timeout_seconds=10, on_conversation=remember))
    assert destinations == ["new-thread"]
    assert page.closed
    assert page.sleeps[-1] == 10


def test_bridge_serializes_browser_config_and_releases_after_cancel(monkeypatch):
    from services import chatgpt_bridge as bridge

    async def run():
        entered = []
        gate = asyncio.Event()
        started = asyncio.Event()

        async def fake_send(message, *_args, **_kwargs):
            entered.append((message, bridge.CDP_PORT, bridge.BRIDGE_IDENTITY))
            started.set()
            if message == "one":
                await gate.wait()
            return bridge.BridgeResult("done", "thread", True, 0)

        monkeypatch.setattr(bridge, "_bridge_lock", asyncio.Lock())
        monkeypatch.setattr(bridge, "_send_and_wait_locked", fake_send)
        original = (bridge.CDP_PORT, bridge.BRIDGE_IDENTITY)
        first = asyncio.create_task(bridge.send_and_wait("one", cdp_port=9226, profile_name="One"))
        await started.wait()
        second = asyncio.create_task(bridge.send_and_wait("two", cdp_port=9227, profile_name="Two"))
        await asyncio.sleep(0)
        assert entered == [("one", 9226, "One")]
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await second
        assert entered[-1] == ("two", 9227, "Two")
        assert (bridge.CDP_PORT, bridge.BRIDGE_IDENTITY) == original
        assert not bridge._bridge_lock.locked()

    asyncio.run(run())


def test_provider_close_cancels_browser_and_preserves_thread_mapping(monkeypatch):
    from services import chatgpt_bridge as bridge, chatgpt_provider as provider

    async def run():
        stopped = asyncio.Event()
        saved = []

        async def save(anam_id, gpt_id):
            saved.append((anam_id, gpt_id))

        async def fake_send(*_args, on_text, on_conversation, **_kwargs):
            try:
                await on_conversation("new-thread")
                await on_text("Working")
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(provider, "_load_identity_prompt", lambda _i: "identity")
        monkeypatch.setattr(provider, "_load_threads", lambda: _async_value({}))
        monkeypatch.setattr(provider, "_save_thread", save)
        monkeypatch.setattr(bridge, "send_and_wait", fake_send)
        stream = provider.stream_chatgpt("hello", "River", "anam-thread")
        assert (await anext(stream))["type"] == "stream_delta"
        await stream.aclose()
        assert stopped.is_set()
        assert saved == [("anam-thread", "new-thread")]

    asyncio.run(run())


def test_reused_matching_tab_is_reloaded_before_next_action_round(monkeypatch):
    """A backend-finished turn can leave ChatGPT's visible page stuck running."""
    from unittest.mock import AsyncMock
    import websockets
    from services import chatgpt_bridge as bridge

    url = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000000"
    target = {
        "id": "existing-tab",
        "type": "page",
        "url": url,
        "webSocketDebuggerUrl": "ws://example.invalid/devtools/page/existing-tab",
    }

    class Page:
        def __init__(self, ws):
            self.ws = ws
            self.navigations = []

        async def navigate(self, destination):
            self.navigations.append(destination)

    monkeypatch.setattr(bridge, "_cdp_http", lambda _path: [target])
    monkeypatch.setattr(websockets, "connect", AsyncMock(return_value=object()))
    monkeypatch.setattr(bridge, "_CdpPage", Page)

    page = asyncio.run(bridge._open_tab(url))

    assert page.navigations == [url]
