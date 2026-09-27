"""Offline regressions for public setup defaults and optional connections."""
import ast
import asyncio
import logging
import os
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from api import settings
from services import chatgpt_bridge


def test_chatgpt_defaults_match_bundled_launcher():
    assert settings._DEFAULT_PROVIDER_CONFIGS["chatgpt"] == {
        "cdp_port": 9225, "profile_name": "ChatGPT"
    }
    script = (Path(__file__).parents[1] / "scripts/pack-browser.ps1").read_text(encoding="utf-8")
    assert "else { 9225 }" in script
    assert "else { 'ChatGPT' }" in script
    js = (Path(__file__).parents[1] / "static/js/settings.js").read_text(encoding="utf-8")
    assert "cfg.profile_name || 'ChatGPT'" in js


def test_chatgpt_settings_validate_launcher_values():
    for port in [True, "http://example.com", 80, 65536, 9225.5]:
        assert settings._validate_provider_config("chatgpt", {"cdp_port": port})
    assert settings._validate_provider_config("chatgpt", {"profile_name": "../other"})
    assert settings._validate_provider_config("chatgpt", {"cdp_port": "9444", "profile_name": "My_Profile"}) is None


def test_launcher_receives_current_call_port_without_mutating_environment(monkeypatch):
    monkeypatch.setenv("ANAM_CHATGPT_CDP_PORT", "9000")
    with patch.object(chatgpt_bridge, "CDP_PORT", 9444), patch.object(
        chatgpt_bridge, "BRIDGE_IDENTITY", "TestProfile"
    ), patch.object(chatgpt_bridge.subprocess, "run", return_value=Mock(returncode=0)) as run, patch.object(
        chatgpt_bridge, "_browser_is_up", side_effect=[True, False]
    ):
        chatgpt_bridge._open_browser()
        chatgpt_bridge._close_browser()
    assert run.call_count == 2
    for call in run.call_args_list:
        assert call.kwargs["env"]["ANAM_CHATGPT_CDP_PORT"] == "9444"
        assert call.kwargs["env"]["ANAM_CHATGPT_IDENTITY"] == "TestProfile"
        assert "TestProfile" in call.args[0]
    assert os.environ["ANAM_CHATGPT_CDP_PORT"] == "9000"


class Request:
    async def json(self):
        return {"provider": "chatgpt", "config": {"cdp_port": 9444, "profile_name": "TestProfile"}}


def test_connection_check_reads_metadata_without_launching_or_sending():
    urls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            urls.append(url)
            payload = {"Browser": "Chrome/test"} if url.endswith("/version") else [
                {"type": "page", "url": "https://chatgpt.com/"}
            ]
            return Mock(raise_for_status=Mock(), json=Mock(return_value=payload))

    with patch("httpx.AsyncClient", return_value=Client()) as client, patch.object(
        chatgpt_bridge, "_open_browser", side_effect=AssertionError("must not launch")
    ):
        result = asyncio.run(settings.test_provider(Request()))
    assert urls == ["http://127.0.0.1:9444/json/version", "http://127.0.0.1:9444/json"]
    assert client.call_args.kwargs["trust_env"] is False
    assert result["ok"] is True
    assert result["checks"] == {
        "cdp": True, "chatgpt_tab": True, "login": "not_checked", "message_delivery": "not_checked"
    }


def test_connection_check_reports_unreachable_browser():
    with patch("httpx.AsyncClient", side_effect=OSError("unavailable")):
        result = asyncio.run(settings.test_provider(Request()))
    assert result["ok"] is False
    assert "9444" in result["message"]


def readiness_functions():
    """Load only the pure readiness boundary, avoiding application startup side effects."""
    source = (Path(__file__).parents[1] / "core/lifespan.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in {"_mcp_readiness_urls", "_wait_for_mcp_servers"}]
    namespace = {"os": os, "urlsplit": urlsplit, "log": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "readiness", "exec"), namespace)
    return namespace


def test_unconfigured_readiness_never_opens_a_connection(monkeypatch):
    monkeypatch.delenv("ANAM_DISCORD_HEALTH_URL", raising=False)
    monkeypatch.delenv("ANAM_SKIP_MCP_READINESS", raising=False)
    functions = readiness_functions()
    assert functions["_mcp_readiness_urls"]() == []
    with patch("httpx.AsyncClient", side_effect=AssertionError("no configured endpoint")):
        asyncio.run(functions["_wait_for_mcp_servers"]())


def test_readiness_uses_only_real_explicit_endpoints(monkeypatch):
    monkeypatch.delenv("ANAM_SKIP_MCP_READINESS", raising=False)
    get_urls = readiness_functions()["_mcp_readiness_urls"]
    for url in ["https://discord.YOUR-ACCOUNT.workers.dev/health", "https://example.com/health", "file:///health", "http://["]:
        monkeypatch.setenv("ANAM_DISCORD_HEALTH_URL", url)
        assert get_urls() == []
    monkeypatch.setenv("ANAM_DISCORD_HEALTH_URL", "http://127.0.0.1:8787/health")
    assert get_urls() == [("discord-backend", "http://127.0.0.1:8787/health")]
    monkeypatch.setenv("ANAM_SKIP_MCP_READINESS", "true")
    assert get_urls() == []
