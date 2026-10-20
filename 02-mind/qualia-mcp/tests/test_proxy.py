import unittest
from unittest.mock import patch

from fastmcp import Client, FastMCP

from qualia_server import create_proxy


class QualiaProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_catalog_and_calls_are_forwarded_without_local_memory(self):
        remote = FastMCP("Test Qualia")

        @remote.tool()
        def mind_health() -> str:
            return "healthy"

        with patch("qualia_server.StreamableHttpTransport", return_value=remote) as transport:
            proxy = create_proxy("https://qualia.example.com/mcp", "test-key")
            transport.assert_called_once_with(
                "https://qualia.example.com/mcp",
                headers={"Authorization": "Bearer test-key"},
            )
            async with Client(proxy) as client:
                names = {tool.name for tool in await client.list_tools()}
                self.assertEqual(names, {"mind_health"})
                result = await client.call_tool("mind_health", {})
                self.assertEqual(result.content[0].text, "healthy")

    def test_missing_or_placeholder_configuration_fails_before_connecting(self):
        for url, token in (("", "test-key"), ("file:///private", "test-key"), ("https://example.com/mcp", "your-key"), ("https://example.com/mcp", "")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_proxy(url, token)


if __name__ == "__main__":
    unittest.main()
