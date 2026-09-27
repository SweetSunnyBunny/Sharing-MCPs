"""Start the X tools as a local stdio MCP server."""

from server import mcp


if __name__ == "__main__":
    mcp.run(transport="stdio")
