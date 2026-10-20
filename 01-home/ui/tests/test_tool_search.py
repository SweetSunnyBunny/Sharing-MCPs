import json
import unittest
from unittest.mock import patch

from services.tool_search import ToolSearchCatalog


def _tool(name: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }


def test_auto_mode_defers_large_catalog():
    tools = [_tool(f"github_action_{i}", "create and inspect github issues") for i in range(25)]
    with patch.dict(
        "os.environ",
        {
            "ANAM_MCP_TOOL_SEARCH": "auto",
            "ANAM_MCP_TOOL_SEARCH_MIN_TOOLS": "20",
            "ANAM_MCP_TOOL_SEARCH_MIN_CHARS": "1",
        },
    ):
        catalog = ToolSearchCatalog.maybe_build(tools)
    assert catalog is not None
    assert {item["name"] for item in catalog.bridge_schemas()} == {
        "anam_tool_search",
        "anam_tool_describe",
        "anam_tool_call",
    }


def test_search_and_describe_return_scoped_tool():
    catalog = ToolSearchCatalog([
        _tool("github_create_issue", "Create a GitHub issue"),
        _tool("calendar_list_events", "List calendar events"),
    ])
    assert catalog.search("create github issue")[0]["name"] == "github_create_issue"
    described = catalog.describe("github_create_issue")
    assert described["ok"] is True
    assert described["tool"]["input_schema"]["properties"]["query"]


class ToolSearchAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_call_cannot_escape_catalog(self):
        class Bridge:
            async def call_tool(self, name, arguments):
                return json.dumps({"name": name, "arguments": arguments})

        catalog = ToolSearchCatalog([_tool("allowed", "Allowed tool")])
        denied = await catalog.execute(
            "anam_tool_call",
            {"name": "not_allowed", "arguments": {}},
            Bridge(),
        )
        self.assertIn("not available", denied)
        allowed = await catalog.execute(
            "anam_tool_call",
            {"name": "allowed", "arguments": {"query": "x"}},
            Bridge(),
        )
        self.assertEqual(json.loads(allowed)["name"], "allowed")
