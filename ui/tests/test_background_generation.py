import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from services import background_generation as bg


def test_auto_uses_current_provider_and_ignores_stale_claude_model():
    with patch("services.provider_router._load_provider", new=AsyncMock(return_value=("codex", {"model": "gpt-6-astra"}))):
        provider, model, _ = asyncio.run(bg.resolve_background_provider("auto", "claude-haiku-4-5"))
    assert (provider, model) == ("codex", "gpt-6-astra")


def test_identity_override_and_explicit_provider_remain_distinct():
    with patch("services.provider_router.resolve_provider_for_identity", new=AsyncMock(return_value=("claude-code", {}))) as resolve:
        provider, _, _ = asyncio.run(bg.resolve_background_provider(identity="Claude"))
        assert provider == "claude-code"
        resolve.assert_awaited_once_with("Claude")
        provider, model, options = asyncio.run(bg.resolve_background_provider("codex", "gpt-6-astra", "Claude"))
        assert (provider, model, options) == ("codex", "gpt-6-astra", {})


@pytest.mark.parametrize("research", [False, True])
def test_codex_is_ephemeral_has_no_shell_or_mcp_and_reads_final_only(research, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-bill")
    process = AsyncMock()
    process.returncode = 0
    captured = {}

    async def spawn(*command, **kwargs):
        captured.update(command=command, **kwargs)
        Path(command[command.index("--output-last-message") + 1]).write_text("Final only", encoding="utf-8")
        process.communicate.return_value = (b'{"type":"item.completed","item":{"type":"web_search"}}', b"")
        return process

    with patch("services.codex_cli.find_codex_executable", return_value="codex.exe"), patch("asyncio.create_subprocess_exec", new=spawn):
        result = asyncio.run(bg._generate_with_codex("gpt-6-astra", "input", "summary", research=research))
    command = captured["command"]
    assert result == "Final only"
    assert "--ephemeral" in command and "--ignore-user-config" in command
    assert "mcp_servers={}" in command
    assert "shell_tool" in command and "plugins" in command
    assert f'web_search="{"live" if research else "disabled"}"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "OPENAI_API_KEY" not in captured["env"]
    assert not Path(captured["cwd"]).exists()


@pytest.mark.parametrize("failure", [asyncio.CancelledError, TimeoutError])
def test_codex_cancellation_reaps_process(failure):
    process = AsyncMock()
    process.returncode = None
    process.kill = Mock()
    process.communicate.side_effect = failure
    with patch("services.codex_cli.find_codex_executable", return_value="codex.exe"), patch("asyncio.create_subprocess_exec", return_value=process):
        with pytest.raises(RuntimeError if failure is TimeoutError else failure):
            asyncio.run(bg._generate_with_codex("gpt-6-astra", "input", "summary"))
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


def test_unsupported_provider_never_falls_back_to_claude():
    with patch.object(bg, "resolve_background_provider", new=AsyncMock(return_value=("chatgpt", "", {}))), patch("services.scribe._generate_with_claude_code", new=AsyncMock()) as claude:
        with pytest.raises(ValueError, match="no provider fallback"):
            asyncio.run(bg.generate_background_text("input", system_prompt="summary"))
    claude.assert_not_awaited()


def test_hearth_and_carry_route_with_identity():
    from services.hearth_author import _run_hearth_one_shot

    with patch.object(bg, "generate_background_text", new=AsyncMock(return_value="{}")) as generate:
        assert asyncio.run(_run_hearth_one_shot("old-claude-model", "activity", "Claude")) == "{}"
    assert generate.await_args.kwargs["identity"] == "Claude"
    assert "model" not in generate.await_args.kwargs


def test_scout_uses_selected_codex_research():
    from services.interest_scout import run_scout_agent

    with patch.object(bg, "resolve_background_provider", new=AsyncMock(return_value=("codex", "gpt-6-astra", {}))), patch.object(bg, "generate_background_text", new=AsyncMock(return_value="tray")) as generate:
        assert asyncio.run(run_scout_agent("Claude", "working thread")) == "tray"
    assert generate.await_args.kwargs["research"] is True
    assert generate.await_args.kwargs["identity"] == "Claude"


def test_scout_rejects_output_without_actual_web_search():
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b'{"type":"turn.completed"}', b"")
    with patch("services.codex_cli.find_codex_executable", return_value="codex.exe"), patch("asyncio.create_subprocess_exec", return_value=process):
        with pytest.raises(RuntimeError, match="without a verified web search"):
            asyncio.run(bg._generate_with_codex("gpt-6-astra", "input", "research", research=True))
