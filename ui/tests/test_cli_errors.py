import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.cli_errors import claude_exit_detail


def test_reports_stdout_failure_even_when_stderr_has_startup_warnings():
    detail = claude_exit_detail(1, b"Usage limit reached", b"Permission rule warning")
    assert "Usage limit reached" in detail
    assert "Permission rule warning" in detail
    assert "exited with 1" in detail


def test_empty_output_retains_exit_code():
    assert claude_exit_detail(7, b"", b"") == "Claude Code exited with 7"


def test_redacts_secrets_before_clipping_and_handles_invalid_utf8():
    detail = claude_exit_detail(1, b"api_key=secret-value \xff", b"Bearer secret-token")
    assert "secret-value" not in detail
    assert "secret-token" not in detail
    assert "[REDACTED]" in detail
    assert len(claude_exit_detail(1, b"x " * 5000, b"y " * 5000)) < 2500


@pytest.mark.parametrize("runner", ["scribe", "hearth", "scout"])
def test_background_jobs_preserve_stdout_failure(runner):
    from services import hearth_author, interest_scout, scribe

    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b"Usage limit reached", b"")
    with patch("asyncio.create_subprocess_exec", return_value=process), patch(
        "services.background_generation.resolve_background_provider",
        new=AsyncMock(return_value=("claude-code", "test-model", {})),
    ):
        if runner == "scribe":
            call = scribe._generate_with_claude_code("test-model", "test prompt")
        elif runner == "hearth":
            call = hearth_author._run_hearth_one_shot("test-model", "test prompt", "Claude")
        else:
            call = interest_scout.run_scout_agent("Claude", "test prompt")
        with pytest.raises(RuntimeError, match="Usage limit reached"):
            asyncio.run(call)
