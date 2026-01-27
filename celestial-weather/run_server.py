#!/usr/bin/env python3
"""
Run Celestial Weather MCP Server

Usage:
    python run_server.py

No API keys required! Uses free Open-Meteo APIs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import mcp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"

    print(f"Starting Celestial Weather MCP server on http://{host}:{port}")
    print(f"MCP endpoint: http://{host}:{port}/mcp")
    print("No API keys required - uses free Open-Meteo APIs")

    mcp.run(transport="streamable-http", host=host, port=port)
