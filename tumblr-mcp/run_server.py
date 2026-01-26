#!/usr/bin/env python3
"""
Run Tumblr MCP Server

For local use:
    python run_server.py

For cloud deployment (Railway, Render, etc.):
    python run_server.py --cloud

The server will run on port 8080 by default (configurable via PORT env var).
"""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import mcp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"

    print(f"Starting Tumblr MCP server on http://{host}:{port}")
    print(f"MCP endpoint: http://{host}:{port}/mcp")

    mcp.run(transport="streamable-http", host=host, port=port)
