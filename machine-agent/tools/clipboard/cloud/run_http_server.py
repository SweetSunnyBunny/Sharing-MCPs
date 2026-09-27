"""
Run Clipboard MCP as an HTTP server for local access.
"""

import sys
import os
from pathlib import Path

parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

from clipboard_server import mcp

if __name__ == "__main__":
    print("=" * 60)
    print("Clipboard HTTP Server")
    print("Listening on: http://0.0.0.0:8788")
    print("Endpoint:     http://localhost:8788/mcp")
    print("=" * 60)

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8788)
