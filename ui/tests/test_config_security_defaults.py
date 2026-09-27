import importlib
import os
import unittest
from unittest.mock import patch

import config as config_module


def _reload_config():
    return importlib.reload(config_module)


class ConfigSecurityDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reload_config)

    def test_cookie_secure_defaults_true_for_non_loopback_urls(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_AUTH_COOKIE_SECURE": "",
                "SITE_URL": "https://anam.example",
                "ANAM_PUBLIC_URL": "https://anam.example",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertTrue(cfg.AUTH_COOKIE_SECURE)

    def test_cookie_secure_defaults_false_for_loopback(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_AUTH_COOKIE_SECURE": "",
                "SITE_URL": "http://localhost:8790",
                "ANAM_PUBLIC_URL": "http://localhost:8790",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertFalse(cfg.AUTH_COOKIE_SECURE)

    def test_cookie_secure_explicit_override_false(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_AUTH_COOKIE_SECURE": "false",
                "SITE_URL": "https://anam.example",
                "ANAM_PUBLIC_URL": "https://anam.example",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertFalse(cfg.AUTH_COOKIE_SECURE)

    def test_path_override_records_env_source(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_PACK_DIR": r"D:\PackOverride",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertEqual(str(cfg.PACK_DIR), r"D:\PackOverride")
            self.assertEqual(cfg.PATH_CONFIG["ANAM_PACK_DIR"]["source"], "env")

    def test_cookie_samesite_defaults_to_lax(self):
        with patch.dict(
            os.environ,
            {
                "ANAM_PUBLIC_URL": "https://anam.example",
                "SITE_URL": "https://anam.example",
            },
            clear=False,
        ):
            cfg = _reload_config()
            self.assertEqual(cfg.AUTH_COOKIE_SAMESITE, "lax")
