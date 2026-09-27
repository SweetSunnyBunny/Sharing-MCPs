import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from services import chatgpt_actions as actions
from services import chatgpt_bridge as bridge
from services import chatgpt_provider as provider


def block(action):
    return '<anam_action>' + json.dumps(action) + '</anam_action>'


@pytest.fixture
def action():
    return {"id": "test-1", "operation": "invoke", "server": "ha-touch", "tool": "teapot", "arguments": {"state": "on"}}


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "LEDGER_PATH", tmp_path / "actions.db")
    from services import tool_result_store
    monkeypatch.setattr(tool_result_store, "DB_PATH", tmp_path / "results.db")


@pytest.mark.parametrize("wrapper", ['```xml\n{}\n```', '`{}`', '> {}', 'For example: {}', '<canvas>{}</canvas>', '"{}"'])
def test_examples_and_quoted_requests_are_inert(action, wrapper):
    assert actions.parse_action(wrapper.format(block(action))) is None


def test_strict_request_shape(action):
    assert actions.parse_action(block(action)) == action
    assert actions.parse_action(block(action)[:-3]) is None
    for invalid in ({**action, "identity": "Ember"}, {**action, "arguments": "bad"},
                    {**action, "id": ""}, {**action, "operation": []},
                    {"id": "x", "operation": "discover", "limit": 200}):
        with pytest.raises(ValueError):
            actions.parse_action(block(invalid))


def test_duplicate_and_conflicting_id_never_repeat_action(action, monkeypatch):
    call = AsyncMock(return_value={"status": "completed", "content": [{"type": "text", "text": "verified on"}]})
    monkeypatch.setattr(actions.gateway, "invoke", call)
    async def run():
        args = dict(identity="Claude", conversation_id="anam1", source_message_id="gpt1")
        first = await actions.dispatch(action, **args)
        replay = await actions.dispatch(action, **{**args, "source_message_id": "gpt2"})
        conflict = await actions.dispatch({**action, "arguments": {"state": "off"}}, **args)
        assert first["status"] == "succeeded"
        assert replay["replayed_receipt"]
        assert conflict["status"] == "rejected"
    asyncio.run(run())
    assert call.await_count == 1
    assert call.call_args.kwargs["identity"] == "Claude"
    assert call.call_args.kwargs["conversation_id"] == "anam1"


def test_concurrent_duplicate_only_dispatches_once(action, monkeypatch):
    async def run():
        release = asyncio.Event()
        started = asyncio.Event()
        async def invoke(**kwargs):
            started.set()
            await release.wait()
            return {"content": []}
        call = AsyncMock(side_effect=invoke)
        monkeypatch.setattr(actions.gateway, "invoke", call)
        args = dict(identity="Claude", conversation_id="anam1", source_message_id="gpt1")
        first = asyncio.create_task(actions.dispatch(action, **args))
        await started.wait()
        duplicate = await actions.dispatch(action, **args)
        assert duplicate["status"] == "uncertain"
        release.set()
        await first
        assert call.await_count == 1
    asyncio.run(run())


def test_cancelled_claim_survives_new_event_loop(action, monkeypatch):
    async def run():
        started = asyncio.Event()
        async def invoke(**kwargs):
            started.set()
            await asyncio.Event().wait()
        call = AsyncMock(side_effect=invoke)
        monkeypatch.setattr(actions.gateway, "invoke", call)
        first = asyncio.create_task(actions.dispatch(action, identity="Claude", conversation_id="anam1", source_message_id="gpt1"))
        await started.wait()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    asyncio.run(run())
    call = AsyncMock()
    monkeypatch.setattr(actions.gateway, "invoke", call)
    result = asyncio.run(actions.dispatch(action, identity="Claude", conversation_id="anam1", source_message_id="gpt1"))
    assert result["status"] == "uncertain"
    call.assert_not_called()


@pytest.mark.parametrize("context", [{"permission_denied": True}, {"source_message_id": ""}])
def test_denials_and_missing_provenance_fail_closed(action, monkeypatch, context):
    call = AsyncMock()
    monkeypatch.setattr(actions.gateway, "invoke", call)
    args = dict(identity="Claude", conversation_id="anam1", source_message_id="gpt1")
    result = asyncio.run(actions.dispatch(action, **{**args, **context}))
    assert result["status"] == "rejected"
    call.assert_not_called()


def test_media_does_not_leak_base64_and_data_fields_survive():
    result = actions.compact_result({"data": {"temperature": 80}, "content": [
        {"type": "image", "data": "BASE64_SECRET", "mimeType": "image/png"},
        {"type": "audio", "data": "BASE64_SECRET", "mimeType": "audio/wav"}]})
    assert "BASE64_SECRET" not in json.dumps(result)
    assert result["data"] == {"temperature": 80}


def test_file_metadata_is_not_mistaken_for_image_payload():
    from scripts.anam_machine_mcp import rehydrate
    metadata = {"success": True, "is_file": True, "type": "image", "path": "test.png", "size_bytes": 123}
    assert rehydrate(metadata) == metadata
    assert actions.compact_result(metadata) == metadata
    native = rehydrate({"type": "image", "data": "YWJj", "mimeType": "image/png"})
    assert native.to_image_content().data == "YWJj"


def test_terminal_requires_an_explicit_session(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(actions.gateway, "invoke", call)
    request = {"id": "shell", "operation": "invoke", "server": "machine-terminal", "tool": "terminal_execute", "arguments": {"command": "echo ok"}}
    result = asyncio.run(actions.dispatch(request, identity="Claude", conversation_id="anam1", source_message_id="gpt1"))
    assert result["status"] == "rejected"
    assert "session" in result["error"]
    call.assert_not_called()


def test_job_collection_refreshes_running_receipt(monkeypatch):
    call = AsyncMock(side_effect=[{"status": "running", "job_id": "j"}, {"status": "completed", "content": []}])
    monkeypatch.setattr(actions.gateway, "job_result", call)
    async def run():
        action = {"id": "poll", "operation": "job", "job_id": "j"}
        args = dict(identity="Claude", conversation_id="anam1", source_message_id="gpt1")
        assert (await actions.dispatch(action, **args))["status"] == "running"
        assert (await actions.dispatch(action, **args))["status"] == "succeeded"
    asyncio.run(run())


def test_denial_detection_ignores_history_and_user_examples():
    def node(role, text):
        return {"message": {"author": {"role": role}, "content": {"parts": [text]}}}
    convo = {"mapping": {"old": node("tool", "Permission denied"), "new": node("user", "permission denied")}}
    assert not bridge._turn_permission_denied(convo, {"old"})
    convo["mapping"]["error"] = node("tool", "Tool call blocked by OpenAI safety checks")
    assert bridge._turn_permission_denied(convo, {"old"})


def test_developer_mcp_plane_unavailable_is_a_fallback_trigger_not_action_denial():
    def node(role, text):
        return {"message": {"author": {"role": role}, "content": {"parts": [text]}}}
    convo = {"mapping": {"error": node(
        "tool", "FORBIDDEN: This conversation does not support developer MCPs")}}
    assert not bridge._turn_permission_denied(convo, set())
    assert "This conversation does not support\ndeveloper MCPs" in actions.INSTRUCTIONS
    assert "PRIMARY route" in actions.INSTRUCTIONS
    assert "Prefer native connector calls" not in actions.INSTRUCTIONS


def prepare_provider(monkeypatch):
    monkeypatch.setattr(provider, "_load_identity_prompt", lambda i: "identity")
    for name, value in (("_load_threads", {}), ("_load_identity_prompt_hashes", {}), ("_load_runtime_context_hashes", {})):
        monkeypatch.setattr(provider, name, AsyncMock(return_value=value))
    for name in ("_save_thread", "_mark_identity_prompt_sent", "_mark_runtime_context_sent"):
        monkeypatch.setattr(provider, name, AsyncMock())


def test_image_action_attaches_pixels_on_continuation_without_encoded_text(monkeypatch, tmp_path):
    from services import anam_media
    from PIL import Image
    prepare_provider(monkeypatch)
    monkeypatch.setattr(anam_media, "CACHE", tmp_path / "media")
    source = tmp_path / "test.png"
    Image.new("RGB", (180, 120), "purple").save(source)
    native = anam_media.native_preview(str(source))
    result = {"content": [b.model_dump() for b in native.content], "isError": False}
    invoke = AsyncMock(return_value=result)
    monkeypatch.setattr(actions.gateway, "invoke", invoke)
    request = block({"id": "picture-1", "operation": "invoke", "server": "anam-context",
                     "tool": "anam_view_image", "arguments": {"path": str(source)}})
    sent = []
    async def send(message, conversation_id, **kwargs):
        sent.append((message, kwargs))
        if len(sent) == 1:
            return bridge.BridgeResult(request, "gpt-thread", True, .1, "source1", request)
        assert result["content"][1]["data"] not in message
        with Image.open(kwargs["attachments"][0]) as attached:
            assert attached.getpixel((0, 0)) == (128, 0, 128)
        return bridge.BridgeResult("I see the image.", "gpt-thread", True, .1, "source2", "I see the image.")
    monkeypatch.setattr(bridge, "send_and_wait", send)
    async def run():
        return [e async for e in provider.stream_chatgpt("look", "Claude", "anam1")]
    events = asyncio.run(run())
    assert invoke.await_count == 1
    assert len(sent) == 2
    assert events[-1]["full_content"] == "I see the image."


def test_provider_runs_action_then_continues_with_real_receipt(monkeypatch):
    prepare_provider(monkeypatch)
    request = block({"id": "discover-1", "operation": "discover", "query": "teapot"})
    sent = []
    async def send(message, conversation_id, on_text, **kwargs):
        sent.append(message)
        if len(sent) == 1:
            await on_text(request)
            return bridge.BridgeResult(request, "gpt-thread", True, .1, "source1", request)
        await on_text("I found the teapot tool.")
        return bridge.BridgeResult("I found the teapot tool.", "gpt-thread", True, .1, "source2", "I found the teapot tool.")
    monkeypatch.setattr(bridge, "send_and_wait", send)
    discover = AsyncMock(return_value={"tools": [{"name": "teapot"}], "total": 1})
    monkeypatch.setattr(actions.gateway, "discover", discover)
    async def run():
        return [e async for e in provider.stream_chatgpt("find the tool", "Claude", "anam1")]
    events = asyncio.run(run())
    assert discover.await_count == 1
    assert "ANAM ACTION RECEIPT" in sent[1] and '"total": 1' in sent[1]
    assert all("<anam_action>" not in e.get("delta", "") for e in events)
    assert any(e["type"] == "tool_result" for e in events)
    assert events[-1]["full_content"] == "I found the teapot tool."


def test_provider_never_executes_commentary_tags(monkeypatch):
    prepare_provider(monkeypatch)
    request = block({"id": "x", "operation": "discover"})
    monkeypatch.setattr(bridge, "send_and_wait", AsyncMock(return_value=bridge.BridgeResult(
        request + "\nFinished", "gpt-thread", True, .1, "source", "Finished")))
    dispatch = AsyncMock()
    monkeypatch.setattr(actions, "dispatch", dispatch)
    async def run():
        return [e async for e in provider.stream_chatgpt("hello", "Claude", "anam1")]
    asyncio.run(run())
    dispatch.assert_not_called()


def test_provider_bounds_action_loop(monkeypatch):
    prepare_provider(monkeypatch)
    monkeypatch.setattr(actions, "MAX_ROUNDS", 2)
    request = block({"id": "x", "operation": "discover"})
    monkeypatch.setattr(bridge, "send_and_wait", AsyncMock(return_value=bridge.BridgeResult(
        request, "gpt-thread", True, .1, "source", request)))
    dispatch = AsyncMock(return_value={"status": "succeeded", "result": {}})
    monkeypatch.setattr(actions, "dispatch", dispatch)
    async def run():
        return [e async for e in provider.stream_chatgpt("hello", "Claude", "anam1")]
    events = asyncio.run(run())
    assert dispatch.await_count == 2
    assert events[-1]["type"] == "error"
    assert "round limit" in events[-1]["message"]


def test_bridge_action_runway_matches_interactive_sessions():
    assert actions.MAX_ROUNDS == 1_000
    assert "up to 1,000 action rounds" in actions.INSTRUCTIONS
    assert "safety ceiling, not a target" in actions.INSTRUCTIONS


def test_provider_timeout_reports_uncertainty_and_stops(monkeypatch):
    prepare_provider(monkeypatch)
    monkeypatch.setattr(bridge, "send_and_wait", AsyncMock(side_effect=TimeoutError))
    async def run():
        return [e async for e in provider.stream_chatgpt("hello", "Claude", "anam1")]
    events = asyncio.run(run())
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["message"]
    assert "inspect" in events[-1]["message"]


def test_provider_cancellation_before_send_does_not_dispatch(monkeypatch):
    prepare_provider(monkeypatch)
    send = AsyncMock()
    monkeypatch.setattr(bridge, "send_and_wait", send)
    async def run():
        cancelled = asyncio.Event()
        cancelled.set()
        return [e async for e in provider.stream_chatgpt(
            "hello", "Claude", "anam1", cancel_event=cancelled)]
    events = asyncio.run(run())
    send.assert_not_called()
    assert events[-1]["type"] == "error"
    assert events[-1]["message"] == "cancelled"
