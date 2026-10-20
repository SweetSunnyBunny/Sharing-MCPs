import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from services import codex_app_server, codex_sessions

@pytest.fixture(autouse=True)
def isolate_pool(monkeypatch):
    monkeypatch.setattr(codex_sessions, "_clients", {})
    monkeypatch.setattr(codex_sessions, "_locks", {})
    monkeypatch.setattr(codex_sessions, "_background", {})


class _FakeClient:
    instances = []

    def __init__(self, _codex_cmd, _extra_args):
        self.requests = []
        self.alive = True
        self.notifications = [
            {
                "method": "mcpServer/startupStatus/updated",
                "params": {
                    "name": "anam_example",
                    "status": "failed",
                    "error": "handshake failed",
                },
            },
            {"method": "turn/started", "params": {"turn": {"id": "turn-1"}}},
            {
                "method": "item/reasoning/summaryTextDelta",
                "params": {"itemId": "reason-1", "delta": "I know who I am."},
            },
            {
                "method": "item/completed",
                "params": {"item": {"id": "reason-1", "type": "reasoning"}},
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "message-1", "delta": "Warm and present."},
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-1", "status": "completed"}},
            },
        ]
        self.__class__.instances.append(self)

    def initialize(self):
        return {}

    def request(self, method, params=None, timeout=30):
        self.requests.append((method, params or {}, timeout))
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/start":
            return {"thread": {"id": "thread-new"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    def take_server_request(self):
        return None

    def take_notification(self, _timeout=0):
        return self.notifications.pop(0) if self.notifications else None

    def is_alive(self):
        return self.alive

    def stderr_tail(self, _count=30):
        return ""

    def close(self):
        self.alive = False


def _bundle(identity="Sol", base="BASE", developer="DEVELOPER", fingerprint="fingerprint"):
    return codex_app_server.CodexInstructionBundle(
        identity=identity,
        base_text=base,
        developer_text=developer,
        base_path=Path(f"{identity}.md"),
        developer_path=Path("anam-contract.md"),
        base_sha256=codex_app_server._sha256(base),
        developer_sha256=codex_app_server._sha256(developer),
        fingerprint=fingerprint,
    )


def test_instruction_layers_keep_identity_stable_and_turn_context_fresh(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "sol.md").write_text(
        "SOL IDENTITY\n\n## Image Sharing\nDUPLICATE IMAGE MANUAL\n\n"
        "## My Voice\nVOICE THAT MUST STAY",
        encoding="utf-8",
    )
    contract = prompts / "codex" / "anam-contract.md"
    contract.parent.mkdir()
    contract.write_text("STABLE ANAM CONTRACT", encoding="utf-8")

    bundle = codex_app_server._build_instruction_bundle("Sol", prompts_dir=prompts)
    turn_context = codex_app_server._build_turn_context(
        "Sol",
        orientation_context="ORIENTATION",
        mode_rules="MODE",
        skill_context="SKILL",
        prompts_dir=prompts,
    )
    turn_input = codex_app_server._format_turn_input(
        "hello", turn_context=turn_context, bootstrap_history="Owner: earlier"
    )

    assert "SOL IDENTITY" in bundle.base_text
    assert "DUPLICATE IMAGE MANUAL" not in bundle.base_text
    assert "VOICE THAT MUST STAY" in bundle.base_text
    assert bundle.developer_text == "STABLE ANAM CONTRACT"
    assert "ORIENTATION" not in bundle.base_text + bundle.developer_text
    assert "ORIENTATION" in turn_input
    assert "MODE" in turn_input
    assert "SKILL" in turn_input
    assert "Owner: earlier" in turn_input
    assert turn_input.endswith(
        "[CURRENT MESSAGE FROM OWNER]\nhello\n[/CURRENT MESSAGE FROM OWNER]"
    )

    refreshed_context = codex_app_server._build_turn_context(
        "Sol",
        orientation_context="NEW ORIENTATION",
        mode_rules="MODE",
        skill_context="SKILL",
        prompts_dir=prompts,
    )
    same_bundle = codex_app_server._build_instruction_bundle("Sol", prompts_dir=prompts)
    assert same_bundle.fingerprint == bundle.fingerprint
    assert "NEW ORIENTATION" in refreshed_context

    (prompts / "sol.md").write_text("SOL IDENTITY CHANGED", encoding="utf-8")
    changed_bundle = codex_app_server._build_instruction_bundle("Sol", prompts_dir=prompts)
    assert changed_bundle.fingerprint != bundle.fingerprint


def test_missing_identity_is_a_visible_configuration_error(tmp_path):
    prompts = tmp_path / "prompts"
    (prompts / "codex").mkdir(parents=True)
    (prompts / "codex" / "anam-contract.md").write_text("contract", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Missing Codex base instructions"):
        codex_app_server._build_instruction_bundle("Sol", prompts_dir=prompts)


def test_resident_process_is_reused_for_same_hash_and_recycled_for_new_hash():
    class ProcessStub:
        def __init__(self, number):
            self.number = number
            self.alive = True

        def is_alive(self):
            return self.alive

        def close(self):
            self.alive = False

    created = []

    def factory():
        process = ProcessStub(len(created) + 1)
        created.append(process)
        return process

    async def run():
        async with codex_sessions.acquire("Avery", factory, ("stable-hash",)) as first:
            first_process = first.client
            assert not first.reused
        async with codex_sessions.acquire("Avery", factory, ("stable-hash",)) as second:
            assert second.reused
            assert second.client is first_process
        async with codex_sessions.acquire("Avery", factory, ("changed-hash",)) as third:
            assert not third.reused
            assert third.client is not first_process
            assert not first_process.alive

    asyncio.run(run())
    assert len(created) == 2


def test_saved_thread_id_reads_codex_owned_session(monkeypatch):
    fake_db = object()
    getter = AsyncMock(return_value="codex-thread")
    releaser = AsyncMock()
    monkeypatch.setattr(
        codex_app_server, "get_db", AsyncMock(return_value=fake_db)
    )
    monkeypatch.setattr(codex_app_server, "release_db", releaser)
    monkeypatch.setattr(codex_app_server, "get_provider_session_id_from_db", getter)

    result = asyncio.run(codex_app_server._saved_thread_id("conversation-1"))

    assert result == "codex-thread"
    getter.assert_awaited_once_with(fake_db, "conversation-1", "codex")
    releaser.assert_awaited_once_with(fake_db)


def test_app_server_resumes_thread_streams_native_deltas_and_reports_mcp_failures(
    monkeypatch, caplog
):
    _FakeClient.instances.clear()
    monkeypatch.setattr(codex_app_server, "CodexAppServerClient", _FakeClient)
    monkeypatch.setattr(codex_app_server, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_app_server, "get_codex_default_model_id", lambda: "gpt-test")
    monkeypatch.setattr(codex_app_server, "build_codex_app_server_mcp_overrides", lambda **kwargs: [])

    async def saved_thread(_conversation_id):
        return "thread-existing"

    monkeypatch.setattr(codex_app_server, "_saved_thread_id", saved_thread)
    monkeypatch.setattr(codex_app_server, "_build_instruction_bundle", lambda identity: _bundle(identity))
    monkeypatch.setattr(codex_app_server, "_build_turn_context", lambda *args, **kwargs: "TURN CONTEXT")

    async def collect():
        return [
            event
            async for event in codex_app_server.stream_codex_app_server(
                message="hello",
                identity="Sol",
                conversation_id="conversation-1",
                bypass_approvals=True,
            )
        ]

    events = asyncio.run(collect())
    client = _FakeClient.instances[0]
    resume = next(params for method, params, _timeout in client.requests if method == "thread/resume")
    resume_timeout = next(timeout for method, _params, timeout in client.requests if method == "thread/resume")
    turn = next(params for method, params, _timeout in client.requests if method == "turn/start")
    turn_timeout = next(timeout for method, _params, timeout in client.requests if method == "turn/start")

    assert resume["threadId"] == "thread-existing"
    assert resume["baseInstructions"] == "BASE"
    assert resume["developerInstructions"] == "DEVELOPER"
    assert resume["personality"] == "none"
    assert resume["sandbox"] == "danger-full-access"
    assert resume_timeout == codex_app_server._THREAD_OPEN_TIMEOUT
    turn_text = turn["input"][0]["text"]
    assert "TURN CONTEXT" in turn_text
    assert turn_text.endswith(
        "[CURRENT MESSAGE FROM OWNER]\nhello\n[/CURRENT MESSAGE FROM OWNER]"
    )
    assert turn_timeout == codex_app_server._TURN_START_TIMEOUT
    assert {event["type"] for event in events} >= {
        "thinking_start",
        "thinking_delta",
        "stream_delta",
        "stream_end",
    }
    assert next(event for event in events if event["type"] == "stream_end")["session_id"] == "thread-existing"
    assert "Codex MCP server anam_example failed to start: handshake failed" in caplog.text
    first_event_meta = next(event for event in events if "first_event_ms" in event)
    assert first_event_meta["type"] == "meta"


def test_multiple_agent_messages_replace_commentary_with_final(monkeypatch):
    class MultipleAgentMessageClient(_FakeClient):
        def __init__(self, codex_cmd, extra_args):
            super().__init__(codex_cmd, extra_args)
            self.notifications = [
                {"method": "turn/started", "params": {"turn": {"id": "turn-1"}}},
                {
                    "method": "item/started",
                    "params": {"item": {"id": "commentary-1", "type": "agentMessage"}},
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": "commentary-1", "delta": "I am checking this now."},
                },
                {
                    "method": "item/completed",
                    "params": {"item": {"id": "commentary-1", "type": "agentMessage"}},
                },
                {
                    "method": "item/started",
                    "params": {"item": {"id": "final-1", "type": "agentMessage"}},
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": "final-1", "delta": "The fix is complete."},
                },
                {
                    "method": "item/completed",
                    "params": {"item": {"id": "final-1", "type": "agentMessage"}},
                },
                {
                    "method": "turn/completed",
                    "params": {"turn": {"id": "turn-1", "status": "completed"}},
                },
            ]

    MultipleAgentMessageClient.instances.clear()
    monkeypatch.setattr(codex_app_server, "CodexAppServerClient", MultipleAgentMessageClient)
    monkeypatch.setattr(codex_app_server, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_app_server, "get_codex_default_model_id", lambda: "gpt-test")
    monkeypatch.setattr(codex_app_server, "build_codex_app_server_mcp_overrides", lambda **kwargs: [])
    monkeypatch.setattr(codex_app_server, "_saved_thread_id", AsyncMock(return_value="thread-existing"))
    monkeypatch.setattr(codex_app_server, "_build_instruction_bundle", lambda identity: _bundle(identity))
    monkeypatch.setattr(codex_app_server, "_build_turn_context", lambda *args, **kwargs: "TURN CONTEXT")

    async def collect():
        return [
            event
            async for event in codex_app_server.stream_codex_app_server(
                message="hello",
                identity="Avery",
                conversation_id="conversation-1",
                bypass_approvals=True,
            )
        ]

    events = asyncio.run(collect())
    event_types = [event["type"] for event in events]

    assert event_types.count("stream_reset") == 1
    assert event_types.index("stream_reset") > event_types.index("stream_delta")
    assert next(event for event in events if event["type"] == "stream_end")["full_content"] == "The fix is complete."


def test_closing_stream_after_text_releases_process_without_yielding(monkeypatch):
    monkeypatch.setattr(codex_app_server, 'CodexAppServerClient', _FakeClient)
    monkeypatch.setattr(codex_app_server, 'find_codex_executable', lambda: 'codex')
    monkeypatch.setattr(codex_app_server, 'build_codex_app_server_mcp_overrides', lambda **kw: [])
    monkeypatch.setattr(codex_app_server, '_saved_thread_id', AsyncMock(return_value=None))
    monkeypatch.setattr(codex_app_server, '_build_instruction_bundle', lambda identity: _bundle(identity))
    monkeypatch.setattr(codex_app_server, '_build_turn_context', lambda *a, **kw: '')
    async def run():
        stream = codex_app_server.stream_codex_app_server('hello','Test','chat')
        async for event in stream:
            if event['type'] == 'stream_delta':
                break
        await stream.aclose()
        assert not codex_sessions.status()
        assert not _FakeClient.instances[-1].alive
    asyncio.run(run())


def test_auth_detection_does_not_treat_timestamp_digits_as_http_401():
    assert not codex_app_server._looks_like_auth_failure(
        "2026-09-14T12:18:18.992401Z WARN shell snapshot"
    )
    assert codex_app_server._looks_like_auth_failure("HTTP 401 unauthorized")


def test_thread_open_timeout_does_not_launch_overlapping_fresh_thread(monkeypatch):
    class ResumeTimeoutClient(_FakeClient):
        def request(self, method, params=None, timeout=30):
            self.requests.append((method, params or {}, timeout))
            if method == "thread/resume":
                raise TimeoutError(f"thread/resume timed out after {timeout}s")
            raise AssertionError(f"unexpected request after resume timeout: {method}")

    ResumeTimeoutClient.instances.clear()
    monkeypatch.setattr(codex_app_server, "CodexAppServerClient", ResumeTimeoutClient)
    monkeypatch.setattr(codex_app_server, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_app_server, "build_codex_app_server_mcp_overrides", lambda **kw: [])
    monkeypatch.setattr(codex_app_server, "_saved_thread_id", AsyncMock(return_value="saved-thread"))
    monkeypatch.setattr(codex_app_server, "_build_instruction_bundle", lambda identity: _bundle(identity))
    monkeypatch.setattr(codex_app_server, "_build_turn_context", lambda *a, **kw: "")

    async def run():
        stream = codex_app_server.stream_codex_app_server("hello", "River", "chat")
        seen = []
        async for event in stream:
            seen.append(event)
            if event["type"] == "error":
                # Cleanup must already be complete even if this consumer stops
                # immediately on the error event.
                assert not codex_sessions.status()
                assert not ResumeTimeoutClient.instances[-1].alive
                break
        await stream.aclose()
        return seen

    events = asyncio.run(run())
    requests = ResumeTimeoutClient.instances[-1].requests
    assert [method for method, _params, _timeout in requests] == ["thread/resume"]
    assert requests[0][2] == codex_app_server._THREAD_OPEN_TIMEOUT
    assert "authentication needs attention" not in events[-1]["message"].lower()


@pytest.mark.parametrize(
    "message",
    [
        "thread 1234 not found",
        "unknown thread",
        "thread 1234 already has an active writer",
    ],
)
def test_definitive_resume_rejections_allow_fresh_thread(message):
    error = codex_app_server.CodexRpcError(-32600, message)
    assert codex_app_server._resume_error_allows_fresh_thread(error)


def test_non_thread_resume_errors_do_not_discard_saved_thread():
    error = codex_app_server.CodexRpcError(-32603, "internal transport failure")
    assert not codex_app_server._resume_error_allows_fresh_thread(error)
