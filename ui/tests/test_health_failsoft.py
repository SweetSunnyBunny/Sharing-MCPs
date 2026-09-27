import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import server


class _FakeDb:
    async def execute_fetchall(self, _query):
        return [(1,)]


class _FakeMcpBridge:
    def __init__(self, status):
        self._status = status

    def get_status(self):
        return self._status

    def get_tools(self):
        from services.qualia_context import REQUIRED_TOOLS

        return [{"name": name} for name in REQUIRED_TOOLS]


def _parse_json_response(resp):
    return json.loads(resp.body.decode("utf-8"))


class HealthFailSoftTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_mcp_even_when_direct_api_is_disabled(self):
        mcp_status = {
            "server_count": 3,
            "tool_count": 10,
            "critical_server_count": 2,
            "optional_server_count": 1,
            "critical_connected": 2,
            "optional_connected": 0,
            "duplicate_tool_count": 0,
            "critical_failed": [],
            "optional_failed": [],
            "optional_pending": [],
        }

        with patch.object(server, "USE_DIRECT_API", False), patch(
            "server.get_integration_owner_status",
            return_value={
                "exclusive": True,
                "owner": True,
                "reason": "owned_by_this_instance",
                "metadata": {"pid": 1234},
            },
        ), patch(
            "server.get_db", new=AsyncMock(return_value=_FakeDb())
        ), patch("server.release_db", new=AsyncMock()), patch(
            "services.autowake.scheduler", new=SimpleNamespace(running=True)
        ), patch(
            "services.mcp_bridge.mcp_bridge", new=_FakeMcpBridge(mcp_status)
        ), patch(
            "services.google_auth_health.get_google_token_status",
            return_value={"status": "ok", "services": []},
        ), patch(
            "services.claude_api.get_token_status",
            return_value={"status": "ok", "expires_in_minutes": 10},
        ):
            response = await server.health_check()

        payload = _parse_json_response(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["mcp"]["status"], "ok")
        self.assertTrue(payload["mcp"]["bridge_required_for_hub"])

    async def test_standby_instance_reports_scheduler_and_mcp_as_standby(self):
        with patch.object(server, "USE_DIRECT_API", True), patch(
            "server.get_integration_owner_status",
            return_value={
                "exclusive": True,
                "owner": False,
                "reason": "owned_by_another_instance",
                "metadata": {"pid": 1234},
            },
        ), patch(
            "server.get_db", new=AsyncMock(return_value=_FakeDb())
        ), patch("server.release_db", new=AsyncMock()), patch(
            "services.autowake.scheduler", new=SimpleNamespace(running=False)
        ), patch(
            "services.google_auth_health.get_google_token_status",
            return_value={"status": "expired", "services": []},
        ), patch(
            "services.claude_api.get_token_status",
            return_value={"status": "ok", "expires_in_minutes": 10},
        ):
            response = await server.health_check()

        payload = _parse_json_response(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["scheduler"]["status"], "standby")
        self.assertEqual(payload["mcp"]["status"], "standby")
        self.assertFalse(payload["integration_owner"]["owner"])

    async def test_mcp_and_google_failures_do_not_degrade_when_not_required(self):
        mcp_status = {
            "server_count": 2,
            "tool_count": 0,
            "critical_server_count": 1,
            "optional_server_count": 1,
            "critical_connected": 0,
            "optional_connected": 0,
            "duplicate_tool_count": 0,
            "critical_failed": ["filesystem"],
            "optional_failed": [],
            "optional_pending": [],
        }
        google_status = {"status": "expired", "services": []}

        with patch.object(server, "USE_DIRECT_API", True), patch.object(
            server, "HEALTH_REQUIRE_MCP_CRITICAL", False
        ), patch.object(server, "HEALTH_REQUIRE_GOOGLE_OAUTH", False), patch(
            "server.get_db", new=AsyncMock(return_value=_FakeDb())
        ), patch(
            "server.get_integration_owner_status",
            return_value={"exclusive": True, "owner": True, "reason": "owned_by_this_instance", "metadata": {"pid": 1}},
        ), patch("server.release_db", new=AsyncMock()), patch(
            "services.autowake.scheduler", new=SimpleNamespace(running=True)
        ), patch(
            "services.mcp_bridge.mcp_bridge", new=_FakeMcpBridge(mcp_status)
        ), patch(
            "services.claude_api.get_token_status",
            return_value={"status": "ok", "expires_in_minutes": 10},
        ), patch(
            "services.google_auth_health.get_google_token_status",
            return_value=google_status,
        ):
            response = await server.health_check()

        payload = _parse_json_response(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["mcp"]["status"], "degraded")
        self.assertFalse(payload["mcp"]["required_for_health"])
        self.assertFalse(payload["google_oauth"]["required_for_health"])

    async def test_mcp_and_google_failures_degrade_when_required(self):
        mcp_status = {
            "server_count": 2,
            "tool_count": 0,
            "critical_server_count": 1,
            "optional_server_count": 1,
            "critical_connected": 0,
            "optional_connected": 0,
            "duplicate_tool_count": 0,
            "critical_failed": ["filesystem"],
            "optional_failed": [],
            "optional_pending": [],
        }
        google_status = {"status": "expired", "services": []}

        with patch.object(server, "USE_DIRECT_API", True), patch.object(
            server, "HEALTH_REQUIRE_MCP_CRITICAL", True
        ), patch.object(server, "HEALTH_REQUIRE_GOOGLE_OAUTH", True), patch(
            "server.get_db", new=AsyncMock(return_value=_FakeDb())
        ), patch(
            "server.get_integration_owner_status",
            return_value={"exclusive": True, "owner": True, "reason": "owned_by_this_instance", "metadata": {"pid": 1}},
        ), patch("server.release_db", new=AsyncMock()), patch(
            "services.autowake.scheduler", new=SimpleNamespace(running=True)
        ), patch(
            "services.mcp_bridge.mcp_bridge", new=_FakeMcpBridge(mcp_status)
        ), patch(
            "services.claude_api.get_token_status",
            return_value={"status": "ok", "expires_in_minutes": 10},
        ), patch(
            "services.google_auth_health.get_google_token_status",
            return_value=google_status,
        ):
            response = await server.health_check()

        payload = _parse_json_response(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(payload["mcp"]["required_for_health"])
        self.assertTrue(payload["google_oauth"]["required_for_health"])
