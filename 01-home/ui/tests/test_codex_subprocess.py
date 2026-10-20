import asyncio
import io
import json

from services import codex_subprocess


def test_blank_model_meta_uses_cached_default(monkeypatch):
    monkeypatch.setattr(codex_subprocess, "get_codex_default_model_id", lambda: "gpt-5.5")

    async def first_event():
        stream = codex_subprocess.stream_codex(
            message="test",
            identity="UnknownIdentity",
            conversation_id="conversation-1",
            model=None,
        )
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    event = asyncio.run(first_event())

    assert event == {
        "type": "meta",
        "provider": "codex",
        "requested_model": "gpt-5.5",
        "actual_model": "gpt-5.5",
    }


class _FakeProcess:
    def __init__(self, stdout_lines: list[dict], stderr: str, returncode: int):
        payload = "".join(json.dumps(line) + "\n" for line in stdout_lines)
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(payload.encode("utf-8"))
        self.stderr = io.BytesIO(stderr.encode("utf-8"))
        self.returncode = returncode
        self.pid = 12345

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _BrokenPipeInput(io.BytesIO):
    def write(self, _data):
        raise BrokenPipeError(32, "Broken pipe")


def test_structured_failure_is_not_overwritten_by_process_stderr(monkeypatch):
    proc = _FakeProcess(
        stdout_lines=[
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started"},
            {
                "type": "turn.failed",
                "error": {"message": "You've hit your usage limit."},
            },
        ],
        stderr=(
            "Reading prompt from stdin...\n"
            "failed to load skill example\\SKILL.md: "
            "missing YAML frontmatter delimited by ---"
        ),
        returncode=1,
    )
    monkeypatch.setattr(codex_subprocess, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_subprocess, "build_codex_mcp_overrides", lambda **kwargs: [])
    monkeypatch.setattr(codex_subprocess, "owned_popen", lambda *args, **kwargs: proc)

    async def collect_events():
        return [
            event
            async for event in codex_subprocess.stream_codex(
                message="test",
                identity="UnknownIdentity",
                conversation_id="conversation-1",
            )
        ]

    events = asyncio.run(collect_events())

    errors = [event["message"] for event in events if event["type"] == "error"]
    assert errors == ["Codex error: You've hit your usage limit."]
    assert all("Codex process exited" not in message for message in errors)


def test_process_failure_without_structured_error_includes_stderr(monkeypatch):
    proc = _FakeProcess(
        stdout_lines=[{"type": "thread.started", "thread_id": "thread-1"}],
        stderr="fatal startup failure",
        returncode=1,
    )
    monkeypatch.setattr(codex_subprocess, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_subprocess, "build_codex_mcp_overrides", lambda **kwargs: [])
    monkeypatch.setattr(codex_subprocess, "owned_popen", lambda *args, **kwargs: proc)

    async def collect_events():
        return [
            event
            async for event in codex_subprocess.stream_codex(
                message="test",
                identity="UnknownIdentity",
                conversation_id="conversation-1",
            )
        ]

    events = asyncio.run(collect_events())

    errors = [event["message"] for event in events if event["type"] == "error"]
    assert errors == ["Codex process exited with code 1\nfatal startup failure"]


def test_early_exit_reports_stderr_instead_of_broken_pipe(monkeypatch):
    proc = _FakeProcess(
        stdout_lines=[],
        stderr="Error loading config.toml: mixed transport",
        returncode=1,
    )
    proc.stdin = _BrokenPipeInput()
    captured_cmd = []

    def fake_popen(cmd, **_kwargs):
        captured_cmd.extend(cmd)
        return proc

    monkeypatch.setattr(codex_subprocess, "find_codex_executable", lambda: "codex")
    monkeypatch.setattr(codex_subprocess, "build_codex_mcp_overrides", lambda **kwargs: [])
    monkeypatch.setattr(codex_subprocess, "owned_popen", fake_popen)

    async def collect_events():
        return [
            event
            async for event in codex_subprocess.stream_codex(
                message="test",
                identity="UnknownIdentity",
                conversation_id="conversation-1",
            )
        ]

    events = asyncio.run(collect_events())
    errors = [event["message"] for event in events if event["type"] == "error"]

    assert "--ignore-user-config" in captured_cmd
    assert len(errors) == 1
    assert "mixed transport" in errors[0]
    assert "Broken pipe" not in errors[0]
