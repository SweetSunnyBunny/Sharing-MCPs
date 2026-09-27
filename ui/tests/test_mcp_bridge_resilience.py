import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.mcp_bridge as mcp_bridge_module
from services.mcp_bridge import MCPBridge


class _FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class _FakeClient:
    def __init__(self, tools: list[_FakeTool]):
        self._tools = tools

    async def list_tools(self):
        return self._tools


class _FakeConnectedClient:
    async def __aexit__(self, *_args):
        return None


class MCPBridgeResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ,
            {"APPDATA": "", "ANAM_MCP_GLOBAL_CONFIG_PATH": ""},
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    async def test_discovers_newest_codex_sites_design_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            older = fake_home / ".codex" / "plugins" / "cache" / "openai-bundled" / "sites" / "0.1.9" / "mcp" / "server.mjs"
            newer = fake_home / ".codex" / "plugins" / "cache" / "openai-bundled" / "sites" / "0.1.27" / "mcp" / "server.mjs"
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.write_text("", encoding="utf-8")
            newer.write_text("", encoding="utf-8")

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home):
                config = mcp_bridge_module._discover_codex_sites_design_picker()

        self.assertEqual(config, {"command": "node", "args": [str(newer)]})

    async def test_unreal_mcp_is_only_discovered_when_editor_port_is_open(self):
        connection = unittest.mock.Mock()
        with patch.dict(os.environ, {"ANAM_UNREAL_MCP_URL": ""}, clear=False), patch.object(
            mcp_bridge_module.socket,
            "create_connection",
            return_value=connection,
        ) as connect:
            config = mcp_bridge_module._discover_unreal_engine_mcp()

        self.assertEqual(config, {"type": "http", "url": "http://127.0.0.1:8000/mcp"})
        connect.assert_called_once_with(("127.0.0.1", 8000), timeout=0.15)
        connection.close.assert_called_once_with()

        with patch.object(
            mcp_bridge_module.socket,
            "create_connection",
            side_effect=OSError("editor closed"),
        ):
            self.assertIsNone(mcp_bridge_module._discover_unreal_engine_mcp())

    async def test_connect_timeout_marks_server_failed(self):
        bridge = MCPBridge()
        bridge._connect_timeout_seconds = 0.01

        async def fake_connect_stdio(_name: str, _config: dict):
            await asyncio.sleep(0.2)

        with patch.object(bridge, "_connect_stdio", side_effect=fake_connect_stdio):
            await bridge._connect_server("slow-server", {"command": "python"})

        self.assertIn("slow-server", bridge._failed_servers)
        self.assertIn("timeout", bridge._failed_servers["slow-server"].lower())

    async def test_status_reports_connected_skipped_and_failed(self):
        bridge = MCPBridge()
        bridge._started = True
        bridge._configured_servers = {"a", "b", "c"}
        bridge._skipped_servers = {"b": "disabled_by_env"}
        bridge._failed_servers = {"c": "connect timeout"}
        bridge._clients = {"a": object()}

        status = bridge.get_status()
        by_name = {item["name"]: item for item in status["servers"]}

        self.assertEqual(by_name["a"]["status"], "connected")
        self.assertEqual(by_name["b"]["status"], "skipped")
        self.assertEqual(by_name["b"]["reason"], "disabled_by_env")
        self.assertEqual(by_name["c"]["status"], "failed")
        self.assertEqual(by_name["c"]["reason"], "connect timeout")

    async def test_status_reports_required_calendar_tools_and_restart_recommendation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "mcp-servers.json"
            cfg_path.write_text("{}", encoding="utf-8")
            os.utime(cfg_path, (200, 200))

            bridge = MCPBridge()
            bridge._process_started_at = 100.0
            bridge._started = True
            bridge._configured_servers = {"google"}
            bridge._clients = {"google": object()}
            bridge._tool_server_map = {
                "gcal_today_events": "google",
                "gcal_create_event": "google",
            }

            with patch.object(mcp_bridge_module, "MCP_SERVERS_FILE", cfg_path):
                status = bridge.get_status()

        calendar = status["required_tools"]["calendar"]
        self.assertTrue(status["restart_recommended"])
        self.assertTrue(status["config_changed_since_start"])
        self.assertTrue(calendar["server_connected"])
        self.assertEqual(set(calendar["missing"]), {"gcal_update_event", "gcal_delete_event"})
        self.assertEqual(set(calendar["present"]), {"gcal_today_events", "gcal_create_event"})
        self.assertFalse(calendar["ok"])

    async def test_load_config_applies_skip_filters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {"command": "alpha"},
                            "beta": {"command": "beta"},
                            "gamma": {"command": "gamma"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text(
                json.dumps(
                    {
                        "enabledServers": ["alpha", "beta", "gamma"],
                        "skipServers": ["beta"],
                        "directBridgeSkipServers": ["alpha"],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ), patch.dict(os.environ, {"ANAM_MCP_SKIP_SERVERS": "gamma"}, clear=False):
                bridge = MCPBridge()
                active, configured, skipped = bridge._load_config(emit_logs=False)
                cli_active, _, _ = bridge._load_config(
                    emit_logs=False,
                    apply_direct_bridge_skips=False,
                )

            self.assertEqual(set(active.keys()), set())
            self.assertEqual(set(cli_active.keys()), {"alpha"})
            self.assertEqual(configured, {"alpha", "beta", "gamma"})
            self.assertEqual(skipped.get("beta"), "disabled_by_skipServers")
            self.assertEqual(skipped.get("gamma"), "disabled_by_env")
            self.assertEqual(skipped.get("alpha"), "disabled_for_direct_bridge")

    async def test_http_transport_receives_configured_authorization_headers(self):
        captured = {}

        class FakeTransport:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeClient:
            def __init__(self, transport, timeout):
                self.transport = transport
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        bridge = MCPBridge()
        with patch.object(mcp_bridge_module, "StreamableHttpTransport", FakeTransport), patch.object(
            mcp_bridge_module, "Client", FakeClient
        ):
            await bridge._connect_http(
                "vox",
                "https://vox.example/mcp",
                {"headers": {"Authorization": "Bearer private-token"}},
            )

        self.assertEqual(
            captured["headers"],
            {"Authorization": "Bearer private-token"},
        )

    async def test_load_config_parses_disabled_tools_marker_and_preferred_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "memory-core": {"command": "memory-core"},
                            "obsidian-vault": {"command": "obsidian-vault"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text(
                json.dumps(
                    {
                        "preferredToolServers": {"add_journal_entry": "memory-core"},
                        "disabledTools": ["##nolongerused @mcptool obsidian-vault:add_journal_entry"],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ):
                bridge = MCPBridge()
                bridge._load_config(emit_logs=False)

            self.assertEqual(
                bridge._preferred_tool_servers.get("add_journal_entry"),
                "memory-core",
            )
            self.assertIn(
                ("obsidian-vault", "add_journal_entry"),
                bridge._disabled_tools_by_server,
            )

    async def test_discover_tools_honors_preferred_server_and_disabled_rules(self):
        bridge = MCPBridge()
        bridge._clients = {
            "alpha": _FakeClient(
                [
                    _FakeTool("dup", description="alpha version"),
                    _FakeTool("global_disabled"),
                ]
            ),
            "beta": _FakeClient(
                [
                    _FakeTool("dup", description="beta version"),
                    _FakeTool("server_disabled"),
                ]
            ),
        }
        bridge._preferred_tool_servers = {"dup": "beta"}
        bridge._disabled_tools_global = {"global_disabled"}
        bridge._disabled_tools_by_server = {("beta", "server_disabled")}

        await bridge._discover_tools()

        self.assertEqual(bridge._tool_server_map.get("dup"), "beta")
        dup_schema = next(tool for tool in bridge._tool_schemas if tool["name"] == "dup")
        self.assertEqual(dup_schema["description"], "beta version")
        self.assertNotIn("global_disabled", bridge._tool_server_map)
        self.assertNotIn("server_disabled", bridge._tool_server_map)

    async def test_load_config_merges_local_server_override_deeply_and_unsets_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "discord": {
                                "command": "uv",
                                "args": ["run", "discord"],
                                "env": {
                                    "DISCORD_BOT_TOKEN_CLAUDE": "x",
                                    "DISCORD_BOT_TOKEN_CECIL": "y",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "discord": {
                                "unsetEnv": ["DISCORD_BOT_TOKEN_CECIL"]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ):
                bridge = MCPBridge()
                active, _configured, _skipped = bridge._load_config(emit_logs=False)

            server = active["discord"]
            self.assertEqual(server.get("command"), "uv")
            self.assertEqual(server.get("args"), ["run", "discord"])
            self.assertEqual(server.get("env", {}).get("DISCORD_BOT_TOKEN_CLAUDE"), "x")
            self.assertNotIn("DISCORD_BOT_TOKEN_CECIL", server.get("env", {}))

    async def test_load_config_applies_critical_optional_tiers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {"command": "alpha"},
                            "beta": {"command": "beta"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text(
                json.dumps(
                    {
                        "criticalServers": ["alpha"],
                        "optionalServers": ["beta"],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ):
                bridge = MCPBridge()
                bridge._load_config(emit_logs=False)

            self.assertEqual(bridge._critical_servers, {"alpha"})
            self.assertEqual(bridge._optional_servers, {"beta"})
            self.assertEqual(bridge._declared_critical_servers, {"alpha"})
            self.assertEqual(bridge._declared_optional_servers, {"beta"})

    async def test_status_marks_optional_pending_as_connecting(self):
        bridge = MCPBridge()
        bridge._started = True
        bridge._configured_servers = {"alpha", "beta"}
        bridge._critical_servers = {"alpha"}
        bridge._optional_servers = {"beta"}
        bridge._pending_optional_servers = {"beta"}
        bridge._clients = {"alpha": object()}

        status = bridge.get_status()
        by_name = {item["name"]: item for item in status["servers"]}

        self.assertEqual(status["startup_phase"], "warming_optional")
        self.assertEqual(by_name["alpha"]["status"], "connected")
        self.assertEqual(by_name["alpha"]["tier"], "critical")
        self.assertEqual(by_name["beta"]["status"], "connecting")
        self.assertEqual(by_name["beta"]["tier"], "optional")

    async def test_start_connects_optional_in_background(self):
        bridge = MCPBridge()
        bridge._connect_optional_in_background = True
        calls: list[str] = []

        def fake_load_config(emit_logs: bool = True):
            _ = emit_logs
            bridge._critical_servers = {"alpha"}
            bridge._optional_servers = {"beta"}
            bridge._declared_critical_servers = {"alpha"}
            bridge._declared_optional_servers = {"beta"}
            return (
                {
                    "alpha": {"command": "alpha"},
                    "beta": {"command": "beta"},
                },
                {"alpha", "beta"},
                {},
            )

        async def fake_connect(name: str, _cfg: dict):
            calls.append(name)
            if name == "beta":
                await asyncio.sleep(0.05)
            bridge._clients[name] = _FakeConnectedClient()

        async def fake_discover():
            return None

        with patch.object(bridge, "_load_config", side_effect=fake_load_config), patch.object(
            bridge, "_connect_server", side_effect=fake_connect
        ), patch.object(bridge, "_discover_tools", side_effect=fake_discover):
            await bridge.start()
            self.assertTrue(bridge._started)
            self.assertIn("alpha", calls)
            self.assertIn("beta", bridge._pending_optional_servers)
            await asyncio.sleep(0.08)
            self.assertIn("beta", calls)
            self.assertNotIn("beta", bridge._pending_optional_servers)
            await bridge.stop()

    async def test_load_config_falls_back_to_claude_desktop_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            fake_appdata = root / "appdata"
            desktop_cfg = fake_appdata / "Claude" / "claude_desktop_config.json"
            desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
            local_cfg = root / "mcp-servers.json"

            desktop_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {"command": "alpha"},
                            "beta": {"command": "beta"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text("{}", encoding="utf-8")

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ), patch.dict(
                os.environ,
                {"APPDATA": str(fake_appdata), "ANAM_MCP_GLOBAL_CONFIG_PATH": ""},
                clear=False,
            ):
                bridge = MCPBridge()
                active, configured, _skipped = bridge._load_config(emit_logs=False)

            self.assertEqual(set(active.keys()), {"alpha", "beta"})
            self.assertEqual(configured, {"alpha", "beta"})

    async def test_load_config_prefers_home_over_desktop_for_same_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            fake_appdata = root / "appdata"
            desktop_cfg = fake_appdata / "Claude" / "claude_desktop_config.json"
            desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps({"mcpServers": {"alpha": {"command": "home-alpha"}}}),
                encoding="utf-8",
            )
            desktop_cfg.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {"command": "desktop-alpha"},
                            "beta": {"command": "desktop-beta"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            local_cfg.write_text("{}", encoding="utf-8")

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ), patch.dict(
                os.environ,
                {"APPDATA": str(fake_appdata), "ANAM_MCP_GLOBAL_CONFIG_PATH": ""},
                clear=False,
            ):
                bridge = MCPBridge()
                active, _configured, _skipped = bridge._load_config(emit_logs=False)

            self.assertEqual(active["alpha"]["command"], "home-alpha")
            self.assertEqual(active["beta"]["command"], "desktop-beta")

    async def test_load_config_prefers_explicit_global_config_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_home = root / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            claude_cfg = fake_home / ".claude.json"
            fake_appdata = root / "appdata"
            desktop_cfg = fake_appdata / "Claude" / "claude_desktop_config.json"
            desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
            explicit_cfg = root / "explicit.json"
            local_cfg = root / "mcp-servers.json"

            claude_cfg.write_text(
                json.dumps({"mcpServers": {"alpha": {"command": "home-alpha"}}}),
                encoding="utf-8",
            )
            desktop_cfg.write_text(
                json.dumps({"mcpServers": {"alpha": {"command": "desktop-alpha"}}}),
                encoding="utf-8",
            )
            explicit_cfg.write_text(
                json.dumps({"mcpServers": {"alpha": {"command": "explicit-alpha"}}}),
                encoding="utf-8",
            )
            local_cfg.write_text("{}", encoding="utf-8")

            with patch.object(mcp_bridge_module.Path, "home", return_value=fake_home), patch.object(
                mcp_bridge_module, "MCP_SERVERS_FILE", local_cfg
            ), patch.dict(
                os.environ,
                {
                    "APPDATA": str(fake_appdata),
                    "ANAM_MCP_GLOBAL_CONFIG_PATH": str(explicit_cfg),
                },
                clear=False,
            ):
                bridge = MCPBridge()
                active, _configured, _skipped = bridge._load_config(emit_logs=False)

            self.assertEqual(active["alpha"]["command"], "explicit-alpha")
