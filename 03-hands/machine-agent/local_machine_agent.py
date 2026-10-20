"""Authenticated local HTTP bridge for machine-only tool execution.

Request validation and HTTP envelopes live here; adapter loading and execution
live in local_tool_registry. Starting main may also start managed sidecars."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from local_tool_registry import ToolArgumentError, invoke_tool, list_tools
from sidecar_processes import sidecars

# Largest request body accepted. fs_write_binary carries whole files as base64.
MAX_BODY_BYTES = int(os.environ.get("MACHINE_AGENT_MAX_BODY_BYTES", str(64 * 1024 * 1024)))


_KEY_FILE = Path('C:/path/to/anam\\data\\runtime\\machine-agent.key')


def _load_api_key() -> str:
    key = os.environ.get("MACHINE_AGENT_API_KEY", "").strip()
    if key:
        return key
    try:
        key = _KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        key = ""
    except OSError as exc:
        raise RuntimeError(
            f"Machine agent requires authentication: could not read key file {_KEY_FILE} ({exc})"
        ) from exc
    if not key:
        raise RuntimeError(
            "Machine agent requires authentication: set MACHINE_AGENT_API_KEY or put the key in "
            f"{_KEY_FILE}"
        )
    return key


API_KEY = os.environ.get("MACHINE_AGENT_API_KEY", "")


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


_AUDIT_PATH = Path(__file__).resolve().parent / "logs" / "audit.log"
_AUDIT_VALUE_CHARS = 300
_audit = logging.getLogger("machine_agent.audit")


def _init_audit_log() -> None:
    if _audit.handlers:
        return
    _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(_AUDIT_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit.addHandler(handler)
    _audit.setLevel(logging.INFO)
    _audit.propagate = False


def _summarize_arguments(arguments: Any) -> Any:
    """Arguments short enough to log: long values (file bodies, base64) are cut."""
    if not isinstance(arguments, dict):
        return repr(arguments)[:_AUDIT_VALUE_CHARS]
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=repr)
        if len(text) > _AUDIT_VALUE_CHARS:
            text = f"{text[:_AUDIT_VALUE_CHARS]}… [{len(text)} chars]"
        summary[str(key)] = text
    return summary


def _audit_call(handler: BaseHTTPRequestHandler, tool: Any, arguments: Any, status: int, started: float, error: str | None = None) -> None:
    try:
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "arguments": _summarize_arguments(arguments),
            "status": int(status),
            "seconds": round(time.monotonic() - started, 3),
            # The tunnel connects from loopback; the real caller is in the forwarded header.
            "client": handler.headers.get("CF-Connecting-IP") or handler.client_address[0],
        }
        if error:
            entry["error"] = error[:500]
        _audit.info(json.dumps(entry, ensure_ascii=False))
    except Exception as exc:  # the audit trail must never break a tool call
        print(f"[machine-agent] audit log write failed: {exc}")


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    auth = handler.headers.get("Authorization", "")
    return hmac.compare_digest(auth.encode(), f"Bearer {API_KEY}".encode())


class MachineAgentHandler(BaseHTTPRequestHandler):
    server_version = "MachineAgent/0.1"

    def do_GET(self) -> None:
        # /health stays open (liveness for the tunnel and anything probing the
        # port) but says nothing about the machine. The tool inventory and
        # sidecar state are for authenticated callers only: this endpoint is
        # reachable from the public internet through the tunnel.
        if self.path == "/health":
            _json_response(self, HTTPStatus.OK, {"ok": True, "status": "healthy"})
            return

        if self.path in ("/", "/tools/list") and not _authorized(self):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

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

        if self.path == "/tools/list":
            _json_response(self, HTTPStatus.OK, {"ok": True, "tools": list_tools()})
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/tools/invoke":
            _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        started = time.monotonic()

        def reject(status: int, error: str, tool: Any = None, arguments: Any = None) -> None:
            _audit_call(self, tool, arguments, status, started, error)
            _json_response(self, status, {"ok": False, "error": error})

        if not _authorized(self):
            reject(HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return

        # The length must be a sane non-negative number: rfile.read(-1) blocks
        # until the client hangs up, and an unbounded body is read into memory.
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 0:
            reject(HTTPStatus.BAD_REQUEST, "Content-Length must be a non-negative integer")
            return
        if content_length > MAX_BODY_BYTES:
            reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"Request body over {MAX_BODY_BYTES} bytes")
            return

        try:
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            reject(HTTPStatus.BAD_REQUEST, f"Invalid JSON body: {exc}")
            return
        if not isinstance(payload, dict):
            reject(HTTPStatus.BAD_REQUEST, "Request body must be a JSON object")
            return

        tool_name = payload.get("tool")
        arguments = payload.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name:
            reject(HTTPStatus.BAD_REQUEST, "Field 'tool' must be a non-empty string", tool_name, arguments)
            return
        if not isinstance(arguments, dict):
            reject(HTTPStatus.BAD_REQUEST, "Field 'arguments' must be an object", tool_name, arguments)
            return

        try:
            result = invoke_tool(tool_name, arguments)
        except KeyError as exc:
            reject(HTTPStatus.NOT_FOUND, str(exc), tool_name, arguments)
            return
        except ToolArgumentError as exc:
            reject(HTTPStatus.BAD_REQUEST, f"Bad arguments: {exc}", tool_name, arguments)
            return
        except Exception as exc:
            reject(HTTPStatus.INTERNAL_SERVER_ERROR, f"{exc.__class__.__name__}: {exc}", tool_name, arguments)
            return

        _audit_call(self, tool_name, arguments, HTTPStatus.OK, started)
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
    _init_audit_log()
    _audit.info(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": "started", "host": args.host, "port": args.port}))
    sidecars.ensure_started()
    server = ThreadingHTTPServer((args.host, args.port), MachineAgentHandler)
    print(f"Machine agent listening on http://{args.host}:{args.port}")
    print(f"Health: http://{args.host}:{args.port}/health")
    print(f"Tool list: http://{args.host}:{args.port}/tools/list")
    server.serve_forever()


if __name__ == "__main__":
    main()
