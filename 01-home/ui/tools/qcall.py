"""Direct caller for a configured Qualia MCP endpoint.

Uses QUALIA_MCP_URL if set, otherwise an explicitly selected Claude MCP config.
"""
import json
import os
from pathlib import Path
import sys
import urllib.request

# Windows console is cp1252; the timeline is full of gold spines and em dashes.
# Without this the CALL SUCCEEDS and only the print explodes -- which looks
# identical to a failed request if you read the traceback and not the cause.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONFIG = Path(os.environ.get("ANAM_CLAUDE_CONFIG", str(Path.home() / ".claude.json")))


def find_url():
    """Pull the qualia-backend URL (token in path) from the live MCP config."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k).lower() == "qualia-backend" and isinstance(v, dict) and v.get("url"):
                    return v["url"]
                found = walk(v)
                if found:
                    return found
        elif isinstance(o, list):
            for v in o:
                found = walk(v)
                if found:
                    return found
        return None
    configured = os.environ.get("QUALIA_MCP_URL", "").strip()
    if configured:
        return configured
    if not CONFIG.is_file():
        return None
    with CONFIG.open(encoding="utf-8") as handle:
        return walk(json.load(handle))


def call(tool, args):
    url = find_url()
    if not url:
        print("!! qualia-backend url not found in the MCP config")
        sys.exit(1)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        # MCP streamable-HTTP transports may answer as SSE; accept both.
        "Accept": "application/json, text/event-stream",
        # REQUIRED. Cloudflare's edge answers Python-urllib's default UA with
        # 403 error 1010 (browser_signature_banned) BEFORE the worker sees the
        # request -- a 403 that reads exactly like "bad key" and is not.
        # Cost me three rounds chasing a token that was correct all along.
        "User-Agent": "anam-pack-avery/1.0",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read().decode("utf-8", errors="replace")
    # SSE frames arrive as "event: message\ndata: {...}"
    if raw.lstrip().startswith("event:") or "\ndata: " in raw:
        for line in raw.splitlines():
            if line.startswith("data: "):
                raw = line[6:]
                break
    return json.loads(raw)


if __name__ == "__main__":
    out = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    if "error" in out:
        print("!! " + json.dumps(out["error"])[:800])
        sys.exit(1)
    res = out.get("result", out)
    for block in (res.get("content") or [{"text": json.dumps(res)[:4000]}]):
        print(block.get("text", "")[:4000])
