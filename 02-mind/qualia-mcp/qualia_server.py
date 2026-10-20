"""Stdio adapter for the current cloud Qualia MCP service.

The memory implementation lives in the independent mind-backend package.
This adapter forwards its live tools; it does not create a second memory store.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport


def create_proxy(url: str, api_key: str) -> FastMCP:
    """Construct a proxy without connecting until the MCP client requests it."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("QUALIA_URL must be an HTTP(S) MCP endpoint")
    if parsed.username or parsed.password:
        raise ValueError("Use the API key setting instead of URL user information")
    if not api_key.strip() or api_key.lower().startswith(("your-", "replace-")):
        raise ValueError("Set your own MIND_API_KEY as QUALIA_API_KEY")
    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {api_key.strip()}"},
    )
    return FastMCP.as_proxy(Client(transport), name="Qualia")


def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("QUALIA_URL", ""))
    parser.add_argument("--token-file", type=Path, help="Optional local text file containing the API key")
    args = parser.parse_args()
    api_key = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else os.environ.get("QUALIA_API_KEY", "")
    try:
        proxy = create_proxy(args.url, api_key)
    except ValueError as exc:
        parser.error(str(exc))
    proxy.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
