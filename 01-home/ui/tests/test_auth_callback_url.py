import importlib
import unittest
from unittest.mock import patch

from starlette.requests import Request

import api.auth as auth_module


def _reload_auth():
    return importlib.reload(auth_module)


def _request_with_headers(headers: dict[str, str]) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in headers.items()
    ]
    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("localhost", 8790),
        "method": "GET",
        "path": "/auth/discord",
        "raw_path": b"/auth/discord",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


class AuthCallbackUrlTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reload_auth)

    def test_prefers_public_base_url_when_site_url_is_unset(self):
        with patch.object(auth_module, "SITE_URL", ""), patch.object(
            auth_module, "PUBLIC_BASE_URL", "https://anam.example"
        ):
            request = _request_with_headers(
                {
                    "host": "attacker.example",
                    "x-forwarded-proto": "https",
                }
            )
            self.assertEqual(
                auth_module._get_callback_url(request),
                "https://anam.example/auth/callback",
            )

    def test_post_login_redirect_stays_local(self):
        response = auth_module.RedirectResponse("/")
        self.assertEqual(response.headers["location"], "/")
