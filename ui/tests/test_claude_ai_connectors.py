'Application module.'
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import services.cc_mcp_oauth as oauth
import services.claude_ai_connectors as cac

LIST = {
    "data": [
        {"id": "mcpsrv_gmail", "display_name": "Gmail", "eligibility_reason": "connected"},
        {"id": "mcpsrv_cal", "display_name": "Google Calendar", "eligibility_reason": "connected"},
        {"id": "mcpsrv_m365", "display_name": "Microsoft 365", "eligibility_reason": "needs_auth"},
    ]
}


class ClaudeAiConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.creds = root / ".credentials.json"
        self._write_token("tok-1")
        self.env = patch.dict(os.environ, {"ANAM_CC_CREDENTIALS_FILE": str(self.creds)})
        self.env.start()
        self.cache = patch.object(cac, "_cache_path", lambda: root / "claude_ai_connectors.json")
        self.cache.start()
        oauth._cache.update(path=None, mtime=None, data={})
        cac._ids.clear()
        cac._ids_fetched_at = 0.0
        cac._ids_loaded_from_disk = False
        cac._refresh_lock = None
        self.list_calls = []

    def tearDown(self):
        self.cache.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _write_token(self, token):
        self.creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": token}, "mcpOAuth": {}}), encoding="utf-8")
        oauth._cache.update(path=None, mtime=None, data={})

    def _patch_list(self, status=200, body=LIST):
        def handler(request: httpx.Request):
            self.list_calls.append(request)
            return httpx.Response(status, json=body)

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        return patch.object(cac.httpx, "AsyncClient", factory)

    # -- config ---------------------------------------------------------------
    def test_config_entries_mark_connectors_and_skip_notes(self):
        entries = cac.config_entries({"_note": "x", "gmail": "Gmail", "": "Bad", "cal": ""})
        self.assertEqual(list(entries), ["gmail"])
        self.assertEqual(entries["gmail"][cac.CONFIG_MARKER], "Gmail")
        self.assertNotIn("url", entries["gmail"])  # id not known yet
        self.assertTrue(cac.is_connector(entries["gmail"]))
        self.assertFalse(cac.is_connector({"url": "https://x"}))
        self.assertEqual(cac.config_entries(None), {})

    def test_known_ids_from_disk_fill_the_url(self):
        Path(cac._cache_path()).write_text(json.dumps({"ids": {"Gmail": "mcpsrv_disk"}}), encoding="utf-8")
        entries = cac.config_entries({"gmail": "Gmail"})
        self.assertEqual(entries["gmail"]["url"], cac.PROXY_BASE + "mcpsrv_disk")

    # -- id lookup ------------------------------------------------------------
    def test_resolve_looks_up_by_display_name_and_persists(self):
        with self._patch_list():
            cfg = asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Google Calendar"}))
        self.assertEqual(cfg["url"], cac.PROXY_BASE + "mcpsrv_cal")
        sent = self.list_calls[0]
        self.assertEqual(sent.headers["authorization"], "Bearer tok-1")
        self.assertEqual(sent.headers["anthropic-beta"], cac.LIST_BETA)
        saved = json.loads(Path(cac._cache_path()).read_text(encoding="utf-8"))["ids"]
        self.assertEqual(saved["Gmail"], "mcpsrv_gmail")
        self.assertNotIn("Microsoft 365", saved)  # not connected on the account

    def test_list_is_cached_between_resolves(self):
        with self._patch_list():
            asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Gmail"}))
            asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Google Calendar"}))
        self.assertEqual(len(self.list_calls), 1)

    def test_unconnected_connector_fails_plainly(self):
        with self._patch_list():
            with self.assertRaisesRegex(RuntimeError, "not connected"):
                asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Microsoft 365"}))

    def test_list_outage_falls_back_to_cached_id(self):
        cac._ids["Gmail"] = "mcpsrv_cached"
        cac._ids_loaded_from_disk = True
        with self._patch_list(status=503, body={}):
            cfg = asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Gmail"}))
        self.assertEqual(cfg["url"], cac.PROXY_BASE + "mcpsrv_cached")

    def test_list_outage_without_cache_raises(self):
        with self._patch_list(status=503, body={}):
            with self.assertRaisesRegex(RuntimeError, "could not be looked up"):
                asyncio.run(cac.resolve({cac.CONFIG_MARKER: "Gmail"}))

    # -- auth -----------------------------------------------------------------
    def test_session_header_is_fresh_each_time(self):
        a = cac.session_headers({"X": "1"})
        b = cac.session_headers()
        self.assertEqual(a["X"], "1")
        self.assertNotEqual(a[cac.SESSION_HEADER], b[cac.SESSION_HEADER])

    def test_auth_attaches_login_and_rereads_once_on_401(self):
        seen = []

        def handler(request):
            seen.append(request.headers.get("authorization"))
            if len(seen) == 1:
                self._write_token("tok-2")  # the CLI rotated the login meanwhile
                return httpx.Response(401)
            return httpx.Response(200, json={})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=cac.ClaudeAccountAuth()) as c:
                return await c.post("https://mcp-proxy.anthropic.com/v1/mcp/x", json={})

        resp = asyncio.run(go())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen, ["Bearer tok-1", "Bearer tok-2"])

    def test_auth_does_not_loop_when_login_unchanged(self):
        seen = []

        def handler(request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(401)

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=cac.ClaudeAccountAuth()) as c:
                return await c.get("https://mcp-proxy.anthropic.com/v1/mcp/x")

        self.assertEqual(asyncio.run(go()).status_code, 401)
        self.assertEqual(len(seen), 1)


class BridgeWiringTests(unittest.TestCase):
    def test_bridge_loads_connectors_from_mcp_servers_json(self):
        from services.mcp_bridge import MCPBridge

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "mcp-servers.json"
        cfg.write_text(json.dumps({
            "claudeAiConnectors": {"_note": "n", "gmail": "Gmail"},
            "optionalServers": ["gmail"],
        }), encoding="utf-8")
        with patch("services.mcp_bridge.MCP_SERVERS_FILE", cfg), \
                patch("services.mcp_bridge._global_mcp_config_candidates", lambda: []), \
                patch("services.mcp_bridge._discover_codex_sites_design_picker", lambda: None), \
                patch.object(cac, "known_id", lambda name: ""):
            bridge = MCPBridge()
            active, configured, _ = bridge._load_config(emit_logs=False)
        self.assertIn("gmail", active)
        self.assertTrue(cac.is_connector(active["gmail"]))
        self.assertIn("gmail", bridge._optional_servers)

    def test_unresolvable_connector_is_recorded_as_failed_not_raised(self):
        from services.mcp_bridge import MCPBridge

        async def boom(config):
            raise RuntimeError("claude.ai connector 'Gmail' is not connected")

        bridge = MCPBridge()
        with patch.object(cac, "resolve", boom):
            asyncio.run(bridge._connect_server("gmail", {cac.CONFIG_MARKER: "Gmail"}))
        self.assertIn("not connected", bridge._failed_servers["gmail"])
        self.assertNotIn("gmail", bridge._clients)


if __name__ == "__main__":
    unittest.main()
