from __future__ import annotations

import argparse
import os
import sys

from server_factory import create_server
from tool_specs import SERVERS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a cloud MCP proxy server for the local machine agent."
    )
    parser.add_argument(
        "server",
        nargs="?",
        default=os.environ.get("MACHINE_AGENT_SERVER", ""),
        choices=sorted(SERVERS.keys()),
        help="Proxy server to run.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="MCP transport to use.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host for streamable-http mode.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "0")),
        help="Port for streamable-http mode. Defaults to the server's configured port.",
    )
    args = parser.parse_args()
    if not args.server:
        parser.error(
            "A server name is required. Choose one of: "
            + ", ".join(sorted(SERVERS.keys()))
        )
    return args


def main() -> None:
    args = parse_args()
    spec = SERVERS[args.server]
    mcp = create_server(args.server)

    if args.transport == "stdio":
        print(f"Starting {spec['display_name']} over stdio", file=sys.stderr)
        mcp.run(transport="stdio")
        return

    port = args.port or spec["default_port"]
    print(f"Starting {spec['display_name']} on http://{args.host}:{port}", file=sys.stderr)
    print(f"MCP endpoint: http://{args.host}:{port}/mcp", file=sys.stderr)
    mcp.run(transport="streamable-http", host=args.host, port=port)


if __name__ == "__main__":
    main()
