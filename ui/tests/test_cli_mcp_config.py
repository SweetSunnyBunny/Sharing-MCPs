import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services import cli_mcp_config


class CliMcpConfigTests(unittest.TestCase):
    def test_codex_permissions_follow_anam_bypass_without_changing_explicit_policy(self):
        servers = {
            "anam-gateway": {
                "command": "python",
                "args": ["gateway.py"],
                "default_tools_approval_mode": "prompt",
            }
        }
        with patch.object(cli_mcp_config, "_active_bridge_servers", return_value=servers):
            permitted = cli_mcp_config.build_codex_app_server_mcp_overrides(bypass_approvals=True)
            normal = cli_mcp_config.build_codex_app_server_mcp_overrides()
        self.assertIn('apps._default.default_tools_approval_mode="approve"', permitted)
        self.assertNotIn('apps._default.default_tools_approval_mode="approve"', normal)
        self.assertIn('mcp_servers.anam_anam-gateway.default_tools_approval_mode="prompt"', permitted)

    def test_write_claude_mcp_config_uses_only_anam_gateway(self):
        fake_servers = {
            "alpha": {
                "command": "python",
                "args": ["server.py"],
                "env": {"TOKEN": "secret"},
            },
            "beta": {
                "url": "https://example.com/mcp",
            },
        }

        with TemporaryDirectory() as tmpdir, patch.object(
            cli_mcp_config,
            "DATA_DIR",
            Path(tmpdir),
        ), patch(
            "services.cli_mcp_config.mcp_bridge._load_config",
            return_value=(fake_servers, set(fake_servers), {}),
        ):
            path = cli_mcp_config.write_claude_mcp_config()
            self.assertIsNotNone(path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(set(payload["mcpServers"]), {"anam-gateway"})
        self.assertNotIn("alpha", payload["mcpServers"])
        self.assertNotIn("beta", payload["mcpServers"])

    def test_identity_config_keeps_own_browser_and_hides_vox_and_siblings(self):
        fake_servers = {
            "shared": {"url": "https://example.com/shared"},
            "playwright-claude": {"url": "https://example.com/claude"},
            "playwright-sage": {"url": "https://example.com/sage"},
            "vox-claude": {"url": "https://example.com/vox-claude"},
            "vox-avery": {"url": "https://example.com/vox-avery"},
        }
        with TemporaryDirectory() as tmpdir, patch.object(
            cli_mcp_config, "DATA_DIR", Path(tmpdir)
        ), patch(
            "services.cli_mcp_config.mcp_bridge._load_config",
            return_value=(fake_servers, set(fake_servers), {}),
        ):
            path = cli_mcp_config.write_claude_mcp_config("Claude", "conversation-1")
            payload = json.loads(path.read_text(encoding="utf-8"))

        servers = payload["mcpServers"]
        self.assertEqual(set(servers), {"anam-gateway"})
        self.assertNotIn("playwright-sage", servers)
        self.assertNotIn("vox-claude", servers)
        self.assertNotIn("vox-avery", servers)
        self.assertEqual(servers["anam-gateway"]["env"]["ANAM_IDENTITY"], "Claude")
        self.assertEqual(
            servers["anam-gateway"]["env"]["ANAM_CONVERSATION_ID"],
            "conversation-1",
        )

    def test_one_shot_codex_exposes_only_identity_bound_gateway(self):
        fake_servers = {
            "alpha": {
                "command": "python",
                "args": ["server.py", "--serve"],
                "cwd": "C:/Apps/mcp",
                "env": {"TOKEN": "secret", "ENABLED": True},
                "approvalMode": "approve",
            },
            "beta": {
                "url": "https://example.com/mcp",
                "bearerTokenEnvVar": "BETA_TOKEN",
                "headers": {"X-Test": "1"},
            },
        }

        with patch(
            "services.cli_mcp_config.mcp_bridge._load_config",
            return_value=(fake_servers, set(fake_servers), {}),
        ):
            overrides = cli_mcp_config.build_codex_mcp_overrides(identity="Claude")

        server_prefixes = {
            item.split(".", 2)[1]
            for item in overrides
            if item.startswith("mcp_servers.")
        }
        self.assertEqual(server_prefixes, {"anam-gateway"})
        self.assertFalse(any("mcp_servers.alpha" in item for item in overrides))
        self.assertFalse(any("mcp_servers.beta" in item for item in overrides))
        self.assertIn('mcp_servers.anam-gateway.env.ANAM_IDENTITY="Claude"', overrides)

    def test_build_codex_mcp_overrides_keeps_read_only_context_on_load_failure(self):
        with patch(
            "services.cli_mcp_config.mcp_bridge._load_config",
            side_effect=RuntimeError("boom"),
        ):
            overrides = cli_mcp_config.build_codex_mcp_overrides()

        self.assertTrue(any(item.startswith("mcp_servers.anam-gateway.") for item in overrides))
        self.assertFalse(any(item.startswith("mcp_servers.anam-context.") for item in overrides))

    def test_build_codex_app_server_mcp_overrides_exposes_only_gateway(self):
        fake_servers = {
            "sites-design-picker": {
                "command": "node",
                "args": ["C:/codex/sites/mcp/server.mjs"],
            },
            "same-name": {
                "url": "http://127.0.0.1:8814/mcp",
                "httpHeaders": {"X-Anam": "yes"},
            },
            "local-tool": {
                "command": "python",
                "args": ["server.py"],
                "env": {"ENABLED": True},
            },
        }
        with patch(
            "services.cli_mcp_config.mcp_bridge._load_config",
            return_value=(fake_servers, set(fake_servers), {}),
        ):
            overrides = cli_mcp_config.build_codex_app_server_mcp_overrides()

        server_prefixes = {
            item.split(".", 2)[1]
            for item in overrides
            if item.startswith("mcp_servers.")
        }
        self.assertEqual(server_prefixes, {"anam_anam-gateway"})
        self.assertFalse(any("anam_same-name" in item for item in overrides))
        self.assertFalse(any("anam_local-tool" in item for item in overrides))
        self.assertTrue(any("anam_anam-gateway" in item for item in overrides))
        self.assertFalse(any("anam_sites-design-picker" in item for item in overrides))

    def test_one_shot_codex_keeps_protocol_sensitive_servers_behind_gateway(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            desktop_script = root / "desktop_control_server.py"
            krita_script = root / "server.py"
            desktop_script.touch()
            krita_script.touch()
            fake_servers = {
                "desktop-control": {"url": "https://desktop.example/mcp/secret"},
                "krita": {"url": "https://krita.example/mcp/secret"},
            }
            local_fallbacks = {
                "desktop-control": desktop_script,
                "krita": krita_script,
            }

            with patch(
                "services.cli_mcp_config.mcp_bridge._load_config",
                return_value=(fake_servers, set(fake_servers), {}),
            ), patch.object(
                cli_mcp_config,
                "_CODEX_LOCAL_STDIO_FALLBACKS",
                local_fallbacks,
            ):
                overrides = cli_mcp_config.build_codex_mcp_overrides()

        self.assertTrue(any(item.startswith("mcp_servers.anam-gateway.") for item in overrides))
        self.assertFalse(any("mcp_servers.desktop-control" in item for item in overrides))
        self.assertFalse(any("mcp_servers.krita" in item for item in overrides))

    def test_persistent_codex_keeps_protocol_sensitive_stdio_behind_gateway(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            desktop_script = root / "desktop_control_server.py"
            desktop_script.touch()
            with patch(
                "services.cli_mcp_config.mcp_bridge._load_config",
                return_value=(
                    {"desktop-control": {"url": "https://desktop.example/mcp/secret"}},
                    {"desktop-control"},
                    {},
                ),
            ), patch.object(
                cli_mcp_config,
                "_CODEX_LOCAL_STDIO_FALLBACKS",
                {"desktop-control": desktop_script},
            ):
                overrides = cli_mcp_config.build_codex_app_server_mcp_overrides()

        self.assertFalse(any("anam_desktop-control" in item for item in overrides))
        self.assertTrue(any("anam_anam-gateway" in item for item in overrides))
