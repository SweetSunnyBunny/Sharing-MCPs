from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from local_tool_registry import invoke_tool, list_tools
from sidecar_processes import sidecars


API_KEY = os.environ.get("MACHINE_AGENT_API_KEY", "")
if not API_KEY:
    raise RuntimeError("Machine agent requires authentication")


_DISCONNECT_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)
    except _DISCONNECT_ERRORS as exc:
        # Client (Cloudflare tunnel / upstream MCP client) gave up before we
        # could write the response. The work succeeded; the listener just left.
        # Log one quiet line instead of a stack trace.
        print(f"[machine-agent] client disconnected before response sent: {type(exc).__name__}")


class MachineAgentHandler(BaseHTTPRequestHandler):
    server_version = "MachineAgent/0.1"

    def do_GET(self) -> None:
        if self.path == "/":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "machine-agent",
                    "status": "healthy",
                    "endpoints": {
                        "health": "/health",
                        "tools_list": "/tools/list",
                        "tools_invoke": "/tools/invoke",
                    },
                    "sidecars": sidecars.health(),
                },
            )
            return

        if self.path == "/health":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "status": "healthy",
                    "sidecars": sidecars.health(),
                },
            )
            return

        if self.path == "/tools/list":
            _json_response(self, HTTPStatus.OK, {"ok": True, "tools": list_tools()})
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/tools/invoke":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        if API_KEY:
            auth = self.headers.get("Authorization", "")
            expected = f"Bearer {API_KEY}"
            if not hmac.compare_digest(auth.encode(), expected.encode()):
                _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
                return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": f"Invalid JSON body: {exc}"},
            )
            return

        tool_name = payload.get("tool")
        arguments = payload.get("arguments", {})
        if tool_name in {"anam_discover", "anam_invoke", "anam_job", "anam_result"} and not API_KEY:
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Machine authentication required"})
            return
        if not isinstance(tool_name, str) or not tool_name:
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Field 'tool' must be a non-empty string"},
            )
            return
        if not isinstance(arguments, dict):
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Field 'arguments' must be an object"},
            )
            return

        try:
            result = invoke_tool(tool_name, arguments)
        except KeyError as exc:
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        except TypeError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Bad arguments: {exc}"})
            return
        except Exception as exc:
            _json_response(
                self,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return

        _json_response(self, HTTPStatus.OK, {"ok": True, "result": result})

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the single local machine-agent bridge for cloud MCP proxies."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MACHINE_AGENT_HOST", "127.0.0.1"),
        help="Host to bind.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MACHINE_AGENT_PORT", "8811")),
        help="Port to bind.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sidecars.ensure_started()
    server = ThreadingHTTPServer((args.host, args.port), MachineAgentHandler)
    print(f"Machine agent listening on http://{args.host}:{args.port}")
    print(f"Health: http://{args.host}:{args.port}/health")
    print(f"Tool list: http://{args.host}:{args.port}/tools/list")
    server.serve_forever()


if __name__ == "__main__":
    main()
