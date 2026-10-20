#!/usr/bin/env python3
"""
Run Google Drive MCP Server

Usage:
    python run_server.py

Make sure to run setup.py first to authenticate.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import mcp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"

    print(f"Starting Google Drive MCP server on http://{host}:{port}")
    print(f"MCP endpoint: http://{host}:{port}/mcp")

    mcp.run(transport="streamable-http", host=host, port=port)
