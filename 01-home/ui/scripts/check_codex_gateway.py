"""Read-only Codex app-server smoke test using Anam's real MCP configuration."""
import json
import re
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.codex_app_server import CodexAppServerClient
from services.codex_cli import find_codex_executable
from services.cli_mcp_config import build_codex_app_server_mcp_overrides


def main():
    conversation_id = "codex-gateway-check-" + uuid.uuid4().hex
    args = []
    for override in build_codex_app_server_mcp_overrides(
        identity="Claude",
        conversation_id=conversation_id,
        bypass_approvals=True,
    ):
        args.extend(["-c", override])
    client = CodexAppServerClient(find_codex_executable(), args)
    report = {"tools": [], "startup_failures": [], "final": ""}
    try:
        client.initialize()
        thread = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": True,
            "approvalPolicy": "never", "sandbox": "read-only",
            "developerInstructions": "This is a bounded read-only Anam infrastructure diagnostic. Do not edit files, send messages, operate the desktop, or use shell. Use only the single anam MCP toolbox on anam_anam-gateway. No roleplay or additional task execution."})
        thread_id = thread["thread"]["id"]
        client.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text":
            "Use the anam tool on the anam_anam-gateway server. First use operation=discover, then operation=invoke. Discover anam-context anam_list_canvases and invoke it with identity Claude and limit 1. Discover desktop-control get_screen_size and invoke it with active identity Claude. If a job is running collect it with operation=job. Return the actual list total and screen dimensions in one brief line, or actual errors. Do not use any other tools."}]})
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            request = client.take_server_request()
            if request:
                client.respond_error(request["id"], -32601, "Interactive actions disabled in diagnostic")
            event = client.take_notification(.5)
            if not event:
                continue
            method = event.get("method")
            params = event.get("params", {})
            if method == "error":
                print("Provider error:", re.sub(r'https?://[^\s"\']+', '<redacted-url>', str(params.get("error", {}))), flush=True)
            if method == "mcpServer/startupStatus/updated" and params.get("status") == "failed":
                report["startup_failures"].append(params.get("name"))
                print("MCP startup failed:", params.get("name"), flush=True)
            if method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "mcpToolCall":
                    entry = {k: item.get(k) for k in ("server", "tool", "status")}
                    report["tools"].append(entry)
                    print("Tool:", entry, flush=True)
                if item.get("type") == "agentMessage":
                    report["final"] += item.get("text", "")
            if method == "turn/completed":
                report["status"] = params.get("turn", {}).get("status")
                if params.get("turn", {}).get("error"):
                    report["error"] = params["turn"]["error"]
                break
        else:
            report["status"] = "timeout"
        (ROOT / "data/runtime/codex-gateway-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Result:", json.dumps(report, ensure_ascii=True), flush=True)
    except Exception:
        print(re.sub(r'https?://[^\s"\']+', '<redacted-url>', client.stderr_tail()), flush=True)
        raise
    finally:
        client.close()


if __name__ == "__main__":
    main()
