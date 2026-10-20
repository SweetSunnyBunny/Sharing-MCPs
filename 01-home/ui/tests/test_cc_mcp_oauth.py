"""Borrowing Claude Code's stored hosted-MCP sign-ins (services/cc_mcp_oauth.py).

Never touches the real ~/.claude/.credentials.json: every test points
ANAM_CC_CREDENTIALS_FILE at a temp copy and mocks all network calls.
"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import services.cc_mcp_oauth as oauth

URL = "https://api.us.elevenlabs.io/v1/mcp"
META = "https://api.us.elevenlabs.io/.well-known/oauth-authorization-server"
TOKEN = "https://api.us.elevenlabs.io/v1/oauth/token"


def _store(expires_in_ms: int = 3_600_000, **extra) -> dict:
    entry = {
        "serverName": "elevenlabs-creative",
        "serverUrl": URL,
        "clientId": "client-123",
        "accessToken": "old-access",
        "refreshToken": "old-refresh",
        "expiresAt": int(time.time() * 1000) + expires_in_ms,
        "issuer": "https://api.us.elevenlabs.io",
        "discoveryState": {"authorizationServerUrl": "https://api.us.elevenlabs.io"},
        **extra,
    }
    return {
        "mcpOAuth": {
            "krita|aaa": {"serverName": "krita", "serverUrl": "https://krita.example/mcp?token=x", "accessToken": "k"},
            "elevenlabs-creative|bbb": entry,
        },
        "claudeAiOauth": {"accessToken": "keep-me"},
    }


class CCMcpOAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".credentials.json"
        self.env = patch.dict(os.environ, {"ANAM_CC_CREDENTIALS_FILE": str(self.path)})
        self.env.start()
        oauth._cache.update(path=None, mtime=None, data={})
        oauth._token_endpoints.clear()
        self.token_calls = []
        self.mcp_auth_headers = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _write(self, data: dict):
        self.path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        oauth._cache.update(path=None, mtime=None, data={})

    def _handler(self, *, token_status=200, first_mcp_status=200):
        state = {"mcp_calls": 0}

        def handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == META:
                return httpx.Response(200, json={"token_endpoint": TOKEN})
            if url == TOKEN:
                self.token_calls.append(dict(httpx.QueryParams(request.content.decode())))
                if token_status != 200:
                    return httpx.Response(token_status, json={"error": "invalid_grant"})
                return httpx.Response(200, json={
                    "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 86400,
                })
            state["mcp_calls"] += 1
            self.mcp_auth_headers.append(request.headers.get("authorization"))
            if state["mcp_calls"] == 1 and first_mcp_status != 200:
                return httpx.Response(first_mcp_status)
            return httpx.Response(200, json={"ok": True})

        return handle

    def _post(self, handler) -> httpx.Response:
        transport = httpx.MockTransport(handler)
        auth = oauth.auth_for("elevenlabs-creative", URL, None)
        self.assertIsNotNone(auth)

        async def run():
            with patch.object(oauth, "_make_refresh_client", lambda: httpx.AsyncClient(transport=transport)):
                async with httpx.AsyncClient(transport=transport, auth=auth) as client:
                    return await client.post(URL, content=b'{"jsonrpc":"2.0"}')

        return asyncio.run(run())

    # -- matching ---------------------------------------------------------
    def test_finds_entry_by_name_and_url_ignoring_trailing_slash(self):
        self._write(_store())
        found = oauth.find_entry("elevenlabs-creative", URL + "/")
        self.assertEqual(found[0], "elevenlabs-creative|bbb")
        self.assertIsNone(oauth.find_entry("elevenlabs-creative", "https://other.example/mcp"))
        self.assertIsNone(oauth.find_entry("someone-else", URL))

    def test_auth_for_skips_static_auth_token_in_url_connectors_and_missing_file(self):
        self._write(_store())
        self.assertIsNone(oauth.auth_for("elevenlabs-creative", URL, {"Authorization": "Bearer x"}))
        self.assertIsNone(oauth.auth_for("krita", "https://krita.example/mcp?token=x", None))
        self.path.unlink()
        oauth._cache.update(path=None, mtime=None, data={})
        self.assertIsNone(oauth.auth_for("elevenlabs-creative", URL, None))

    # -- flows ------------------------------------------------------------
    def test_attaches_bearer_without_refreshing_a_live_token(self):
        self._write(_store())
        resp = self._post(self._handler())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mcp_auth_headers, ["Bearer old-access"])
        self.assertEqual(self.token_calls, [])

    def test_expiring_token_is_refreshed_and_written_back_without_clobbering(self):
        self._write(_store(expires_in_ms=10_000))
        resp = self._post(self._handler())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mcp_auth_headers, ["Bearer new-access"])
        self.assertEqual(self.token_calls, [{
            "grant_type": "refresh_token", "refresh_token": "old-refresh", "client_id": "client-123",
        }])
        raw = self.path.read_text(encoding="utf-8")
        self.assertFalse(raw.endswith("\n"))
        self.assertNotIn(", ", raw)
        saved = json.loads(raw)
        entry = saved["mcpOAuth"]["elevenlabs-creative|bbb"]
        self.assertEqual(entry["accessToken"], "new-access")
        self.assertEqual(entry["refreshToken"], "new-refresh")
        self.assertGreater(entry["expiresAt"], time.time() * 1000 + 80_000_000)
        self.assertEqual(entry["clientId"], "client-123")
        self.assertEqual(saved["mcpOAuth"]["krita|aaa"]["accessToken"], "k")
        self.assertEqual(saved["claudeAiOauth"], {"accessToken": "keep-me"})

    def test_401_triggers_one_refresh_and_one_retry(self):
        self._write(_store())
        resp = self._post(self._handler(first_mcp_status=401))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.mcp_auth_headers, ["Bearer old-access", "Bearer new-access"])
        self.assertEqual(len(self.token_calls), 1)

    def test_failed_refresh_leaves_the_file_alone_and_does_not_loop(self):
        self._write(_store())
        before = self.path.read_text(encoding="utf-8")
        resp = self._post(self._handler(token_status=400, first_mcp_status=401))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.mcp_auth_headers, ["Bearer old-access"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_picks_up_a_token_the_cli_already_refreshed(self):
        self._write(_store())
        auth = oauth.auth_for("elevenlabs-creative", URL, None)
        fresh = _store()
        fresh["mcpOAuth"]["elevenlabs-creative|bbb"]["accessToken"] = "cli-refreshed"
        time.sleep(0.01)
        self._write(fresh)
        transport = httpx.MockTransport(self._handler())

        async def run():
            async with httpx.AsyncClient(transport=transport, auth=auth) as client:
                return await client.post(URL, content=b"{}")

        asyncio.run(run())
        self.assertEqual(self.mcp_auth_headers, ["Bearer cli-refreshed"])
        self.assertEqual(self.token_calls, [])


if __name__ == "__main__":
    unittest.main()
