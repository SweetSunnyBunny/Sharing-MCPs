import importlib
import os
import unittest
from unittest.mock import patch

import config as config_module


def _reload_config():
    return importlib.reload(config_module)


class ConfigRuntimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reload_config)

    def test_production_rejects_loopback_public_url(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "production",
                "ANAM_PUBLIC_URL": "http://localhost:8790",
                "DISCORD_CLIENT_ID": "",
                "DISCORD_CLIENT_SECRET": "",
                "ALLOWED_DISCORD_IDS": "",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertIn(
                "ANAM_PUBLIC_URL must not point to a loopback host in production.",
                report["errors"],
            )

    def test_auth_requires_secret_and_allowed_ids(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "development",
                "DISCORD_CLIENT_ID": "client-id",
                "DISCORD_CLIENT_SECRET": "",
                "ALLOWED_DISCORD_IDS": "",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertIn(
                "DISCORD_CLIENT_SECRET is required when Discord auth is enabled.",
                report["errors"],
            )
            self.assertIn(
                "ALLOWED_DISCORD_IDS must include at least one Discord user when auth is enabled.",
                report["errors"],
            )

    def test_production_auth_without_site_url_warns(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "production",
                "ANAM_PUBLIC_URL": "https://anam.example",
                "SITE_URL": "",
                "DISCORD_CLIENT_ID": "client-id",
                "DISCORD_CLIENT_SECRET": "secret",
                "ALLOWED_DISCORD_IDS": "123",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertEqual(report["errors"], [])
            self.assertIn(
                "SITE_URL is unset; OAuth callbacks will use ANAM_PUBLIC_URL.",
                report["warnings"],
            )

    def test_no_auth_without_opt_out_is_a_hard_error(self):
        """A dropped/typo'd DISCORD_CLIENT_ID must refuse to start unless
        ANAM_ALLOW_NO_AUTH=1 is explicitly set. Defends against silent
        auto-disable opening the public tunnel to the world."""
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "development",
                "DISCORD_CLIENT_ID": "",
                "DISCORD_CLIENT_SECRET": "",
                "ALLOWED_DISCORD_IDS": "",
                "ANAM_ALLOW_NO_AUTH": "",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertTrue(
                any("Refusing to start" in e for e in report["errors"]),
                f"expected fail-closed error, got: {report['errors']}",
            )

    def test_no_auth_opt_out_works_in_dev(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "development",
                "DISCORD_CLIENT_ID": "",
                "DISCORD_CLIENT_SECRET": "",
                "ALLOWED_DISCORD_IDS": "",
                "ANAM_ALLOW_NO_AUTH": "1",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertEqual(report["errors"], [])

    def test_no_auth_opt_out_does_not_apply_in_production(self):
        """Production must reject AUTH_ENABLED=False even with the opt-out."""
        with patch.dict(
            os.environ,
            {
                "ANAM_ENV": "production",
                "ANAM_PUBLIC_URL": "https://anam.example",
                "DISCORD_CLIENT_ID": "",
                "DISCORD_CLIENT_SECRET": "",
                "ALLOWED_DISCORD_IDS": "",
                "ANAM_ALLOW_NO_AUTH": "1",
            },
            clear=False,
        ):
            cfg = _reload_config()
            report = cfg.validate_runtime_config()
            self.assertTrue(
                any("must be enabled in production" in e for e in report["errors"]),
                f"expected production-only no-auth error, got: {report['errors']}",
            )

    def test_claude_code_cli_is_default_runtime(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_USE_DIRECT_API": "",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertFalse(cfg.USE_DIRECT_API)
