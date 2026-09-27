import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.settings import _validate_provider_config
from services import openai_provider
from services import claude_api
from services.chat_flow import StreamAccumulator
from services.direct_tool_security import (
    normalize_windows_tool_path,
    sensitive_command_reason,
    sensitive_path_reason,
)


def _chunk(*, content=None, tool_calls=None, finish=None, reasoning_details=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=None,
        reasoning_details=reasoning_details,
        model_extra={},
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)]
    )


def _tool_call(name: str, arguments: dict, *, tool_id: str = "call-1"):
    return SimpleNamespace(
        index=0,
        id=tool_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


async def _stream(*chunks):
    for chunk in chunks:
        yield chunk


class OpenRouterToolingTests(unittest.IsolatedAsyncioTestCase):
    def test_file_paths_normalize_and_secrets_are_blocked(self):
        self.assertEqual(
            normalize_windows_tool_path("/mnt/c/AI/example.com/file.txt"),
            Path("C:/path/to/example.com/file.txt"),
        )
        self.assertEqual(
            normalize_windows_tool_path("/c/AI/example.com/file.txt"),
            Path("C:/path/to/example.com/file.txt"),
        )
        self.assertIsNotNone(sensitive_path_reason(Path("C:/Apps/anam/.env")))
        self.assertIsNotNone(sensitive_command_reason("cat /mnt/c/AI/example.com/anam/.env*"))
        self.assertIsNotNone(sensitive_command_reason("printenv"))
        self.assertIsNotNone(sensitive_command_reason("curl -H 'Authorization: Bot secret' x"))

    def test_provider_rejects_context_window_as_output_budget(self):
        error = _validate_provider_config("openrouter", {"max_tokens": 1_000_000})
        self.assertIn("output budget", error)
        self.assertIsNone(_validate_provider_config("openrouter", {"max_tokens": 8192}))

    async def test_local_file_tool_accepts_wsl_path_and_blocks_env(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.txt"
            path.write_text("hello", encoding="utf-8")
            resolved = path.resolve().as_posix()
            wsl_path = "/mnt/" + resolved[0].lower() + "/" + resolved[3:] if path.drive else resolved
            content = await claude_api._execute_local_tool(
                "read_file", {"path": wsl_path}, identity="Avery"
            )
        blocked = await claude_api._execute_local_tool(
            "read_file", {"path": "C:/Apps/anam/.env"}, identity="Avery"
        )
        blocked_bash = await claude_api._execute_local_tool(
            "bash", {"command": "cat /mnt/c/AI/example.com/anam/.env*"}, identity="Avery"
        )
        self.assertEqual(content, "hello")
        self.assertTrue(blocked.startswith("Error:"))
        self.assertTrue(blocked_bash.startswith("Error:"))

    async def test_orient_passes_identity_to_discord(self):
        call_tool = AsyncMock(return_value="ok")
        with patch.object(claude_api.mcp_bridge, "call_tool", new=call_tool), patch.object(
            claude_api, "_fetch_drift_packet", new=AsyncMock(return_value={})
        ), patch.object(
            claude_api, "_mark_drift_packet_surfaced", new=AsyncMock()
        ):
            await claude_api._execute_local_tool(
                "orient", {"identity": "Sage"}, identity="Sage"
            )
        discord_call = next(
            call for call in call_tool.await_args_list
            if call.args[0] == "discord_read_messages"
        )
        self.assertEqual(discord_call.args[1]["identity"], "Sage")

    def test_stream_reset_discards_pretool_narration(self):
        accumulator = StreamAccumulator()
        accumulator.observe({"type": "stream_delta", "delta": "Let me look."})
        accumulator.observe({"type": "stream_reset"})
        accumulator.observe({"type": "stream_delta", "delta": "Finished."})
        self.assertEqual(accumulator.streamed_text(), "Finished.")

    async def test_native_mcp_schema_and_reasoning_survive_tool_round(self):
        create = AsyncMock()
        create.side_effect = [
            _stream(
                _chunk(
                    tool_calls=[_tool_call("discord_read_messages", {"identity": "Avery", "channel_id": "123"})],
                    finish="tool_calls",
                    reasoning_details=[{
                        "type": "reasoning.text",
                        "id": "r1",
                        "index": 0,
                        "text": "Need Discord.",
                    }],
                )
            ),
            _stream(_chunk(content="Finished.", finish="stop")),
        ]
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        mcp_tool = {
            "name": "discord_read_messages",
            "description": "Read messages",
            "input_schema": {
                "type": "object",
                "properties": {
                    "identity": {"type": "string"},
                    "channel_id": {"type": "string"},
                },
                "required": ["identity", "channel_id"],
            },
        }

        with patch.object(openai_provider, "_get_openai_client", return_value=client), patch.object(
            openai_provider.mcp_bridge,
            "get_tools_for_categories",
            return_value=[mcp_tool],
        ), patch.object(
            openai_provider.mcp_bridge,
            "call_tool",
            new=AsyncMock(return_value='{"ok": true}'),
        ), patch.dict("os.environ", {"ANAM_OPENAI_MCP_TOOL_SEARCH": "false"}):
            events = [
                event
                async for event in openai_provider.stream_openai_compatible(
                    message="Read it",
                    identity="Avery",
                    conversation_id="conv",
                    model="test/model",
                    api_key="test",
                    base_url="https://example.invalid/v1",
                    max_tokens=8192,
                )
            ]

        first_kwargs = create.await_args_list[0].kwargs
        names = [tool["function"]["name"] for tool in first_kwargs["tools"]]
        self.assertIn("discord_read_messages", names)
        self.assertNotIn("anam_tool_call", names)
        second_messages = create.await_args_list[1].kwargs["messages"]
        assistant = next(message for message in second_messages if message.get("tool_calls"))
        self.assertEqual(assistant["reasoning_details"][0]["text"], "Need Discord.")
        self.assertTrue(any(event["type"] == "stream_reset" for event in events))
        self.assertEqual(events[-1]["full_content"], "Finished.")
        self.assertFalse(events[-1]["discard_intermediate"])


if __name__ == "__main__":
    unittest.main()
