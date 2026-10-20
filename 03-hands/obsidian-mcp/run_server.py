#!/usr/bin/env python3
"""
Run Obsidian MCP Server

Usage:
    python run_server.py

Set OBSIDIAN_VAULT_PATH environment variable to your vault location.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import mcp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"

    print(f"Starting Obsidian MCP server on http://{host}:{port}")
    print(f"MCP endpoint: http://{host}:{port}/mcp")
    print(f"Vault path: {os.environ.get('OBSIDIAN_VAULT_PATH', 'Not set - using default')}")

    mcp.run(transport="streamable-http", host=host, port=port)
