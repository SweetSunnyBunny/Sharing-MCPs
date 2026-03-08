#!/usr/bin/env python3
"""
Run Books MCP Server.

Usage:
    python run_server.py
    python run_server.py --transport stdio
    python run_server.py --transport streamable-http --host 127.0.0.1 --port 8770
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from books_server import mcp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Books MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="MCP transport to use (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host for streamable-http transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8770)),
        help="Port for streamable-http transport (default: 8770).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.transport == "stdio":
        print("Starting Books MCP server over stdio", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        print(f"Starting Books MCP server on http://{args.host}:{args.port}")
        print(f"MCP endpoint: http://{args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
