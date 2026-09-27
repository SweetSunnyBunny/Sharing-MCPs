"""Hermes-style Codex app-server provider for companion conversations.

Unlike the legacy ``codex exec --ephemeral`` lane, app-server gives Anam a
real Codex thread, native token/thinking/tool events, per-identity base
instructions, and a small stable Anam developer contract.
Messaging retains one app-server process per identity. Autowakes use separate
disposable processes; persistent thread IDs remain owned by their conversation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import queue
import re
import subprocess
from services.owned_process import owned_popen, close_owned_process
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from config import BASE_DIR, PROMPTS_DIR
from db.database import get_db, release_db
from services.cli_mcp_config import build_codex_app_server_mcp_overrides
from services.codex_cli import (
    find_codex_executable,
    get_codex_default_model_id,
    normalize_codex_model,
)
from services.session_manager import get_provider_session_id_from_db
from services import codex_sessions, runtime_metrics, codex_approvals

log = logging.getLogger(__name__)

_TURN_TIMEOUT = 900.0
_STALL_TIMEOUT = 300.0
_KEEPALIVE_INTERVAL = 10.0
_THREAD_OPEN_TIMEOUT = 90.0
_TURN_START_TIMEOUT = 90.0


@dataclass(frozen=True)
class CodexInstructionBundle:
    identity: str
    base_text: str
    developer_text: str
    base_path: Path
    developer_path: Path
    base_sha256: str
    developer_sha256: str
    fingerprint: str


@dataclass
class CodexRpcError(RuntimeError):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"codex app-server error {self.code}: {self.message}"


class CodexAppServerClient:
    """Small JSON-RPC client for ``codex app-server`` over JSONL stdio."""

    def __init__(self, codex_cmd: str, extra_args: list[str] | None = None) -> None:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        env.setdefault("RUST_LOG", "warn")
        cmd = [codex_cmd, "app-server", *(extra_args or [])]
        self.proc = owned_popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._notifications: queue.Queue = queue.Queue()
        self._server_requests: queue.Queue = queue.Queue()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "anam",
                    "title": "Anam Companion Chat",
                    "version": "1.0",
                },
                "capabilities": {},
            },
            timeout=15,
        )
        self.notify("initialized")
        return result

    def request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        request_id = self._next_id
        self._next_id += 1
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        self._send({"id": request_id, "method": method, "params": params or {}})
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"codex app-server {method} timed out after {timeout}s") from exc
        if "error" in response:
            error = response.get("error") or {}
            raise CodexRpcError(
                int(error.get("code", -1)),
                str(error.get("message") or "Unknown app-server error"),
                error.get("data"),
            )
        return response.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id: Any, result: dict) -> None:
        self._send({"id": request_id, "result": result})

    def respond_error(self, request_id: Any, code: int, message: str) -> None:
        self._send({"id": request_id, "error": {"code": code, "message": message}})

    def take_notification(self, timeout: float = 0.0) -> dict | None:
        try:
            return self._notifications.get(timeout=timeout) if timeout else self._notifications.get_nowait()
        except queue.Empty:
            return None

    def take_server_request(self) -> dict | None:
        try:
            return self._server_requests.get_nowait()
        except queue.Empty:
            return None

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_tail(self, count: int = 30) -> str:
        with self._stderr_lock:
            return "\n".join(self._stderr_lines[-count:])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        if close_owned_process(self.proc):
            return
        if self.proc.poll() is None:
            _tree_kill(self.proc)
        else:
            try:
                self.proc.wait(timeout=3)
            except Exception:
                pass

    def _send(self, payload: dict) -> None:
        if self._closed or self.proc.stdin is None:
            raise RuntimeError("codex app-server is closed")
        try:
            with self._write_lock:
                self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            detail = self.stderr_tail(20)
            suffix = f"\n{detail}" if detail else ""
            raise RuntimeError(f"codex app-server stdin closed: {exc}{suffix}") from exc

    def _read_stdout(self) -> None:
        try:
            if self.proc.stdout is None:
                return
            for raw_line in iter(self.proc.stdout.readline, b""):
                if not raw_line:
                    break
                try:
                    message = json.loads(raw_line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    with self._stderr_lock:
                        self._stderr_lines.append(f"<non-json stdout> {raw_line[:300]!r}")
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    with self._pending_lock:
                        pending = self._pending.pop(message["id"], None)
                    if pending is not None:
                        pending.put_nowait(message)
                elif "id" in message and "method" in message:
                    self._server_requests.put(message)
                elif "method" in message:
                    self._notifications.put(message)
        except Exception as exc:
            with self._stderr_lock:
                self._stderr_lines.append(f"<stdout reader error> {exc}")
        finally:
            synthetic = {
                "error": {
                    "code": -32000,
                    "message": "codex app-server exited before replying",
                }
            }
            with self._pending_lock:
                pending_queues = list(self._pending.values())
                self._pending.clear()
            for pending in pending_queues:
                try:
                    pending.put_nowait(synthetic)
                except queue.Full:
                    pass

    def _read_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        try:
            for raw_line in iter(self.proc.stderr.readline, b""):
                if not raw_line:
                    break
                with self._stderr_lock:
                    self._stderr_lines.append(raw_line.decode("utf-8", "replace").rstrip())
                    if len(self._stderr_lines) > 500:
                        del self._stderr_lines[:250]
        except Exception:
            pass


def _tree_kill(proc: subprocess.Popen) -> None:
    if close_owned_process(proc):
        return
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception as exc:
        log.warning("Could not read Codex companion guidance %s: %s", path, exc)
        return ""


_CODEX_REDUNDANT_IDENTITY_SECTIONS = frozenset({
    "Autowake Protocol",
    "Behavioral Notes for Anam",
    "Time Awareness",
    "Image Sharing",
    "Document Sharing",
    "Timers & Reminders",
    "Echo Show Relay",
    "Voice Messages",
    "The Eidoverse — Our Shared World",
})


def _diet_identity_prompt_for_codex(text: str) -> str:
    """Drop runtime manuals Codex already receives from live context/tools.

    The canonical identity file stays complete for Claude and for archival
    truth. On Codex, the live tool index, tool schemas, tag reminders, current
    time, and AGENTS conduct already carry these sections more accurately.
    Keeping them again inside the identity layer made infrastructure prose
    compete with the self it was meant to support.
    """
    if not text:
        return text
    heading_re = re.compile(r"(?m)^## ([^\r\n]+)\r?$", re.UNICODE)
    matches = list(heading_re.finditer(text))
    if not matches:
        return text
    pieces: list[str] = [text[: matches[0].start()]]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if match.group(1).strip() in _CODEX_REDUNDANT_IDENTITY_SECTIONS:
            continue
        pieces.append(text[match.start() : end])
    return "".join(pieces).strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_instruction_bundle(
    identity: str,
    *,
    prompts_dir: Path | None = None,
) -> CodexInstructionBundle:
    """Compile stable per-identity base and Anam contract instructions."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    from services.character_prompt_package import identity_prompt_file

    base_path = identity_prompt_file(identity, prompts_dir)
    base_text = _diet_identity_prompt_for_codex(_read_text(base_path)).strip()
    if not base_text:
        raise RuntimeError(f"Missing Codex base instructions for {identity}: {base_path}")

    developer_path = prompts_dir / "codex" / "anam-contract.md"
    developer_text = _read_text(developer_path).strip()
    if not developer_text:
        raise RuntimeError(f"Missing Anam Codex operating contract: {developer_path}")

    base_sha256 = _sha256(base_text)
    developer_sha256 = _sha256(developer_text)
    fingerprint = _sha256(
        json.dumps(
            {
                "identity": identity.casefold(),
                "base_sha256": base_sha256,
                "developer_sha256": developer_sha256,
            },
            sort_keys=True,
        )
    )
    return CodexInstructionBundle(
        identity=identity,
        base_text=base_text,
        developer_text=developer_text,
        base_path=base_path,
        developer_path=developer_path,
        base_sha256=base_sha256,
        developer_sha256=developer_sha256,
        fingerprint=fingerprint,
    )


def _build_turn_context(
    identity: str,
    *,
    orientation_context: str,
    mode_rules: str,
    skill_context: str,
    prompts_dir: Path | None = None,
) -> str:
    """Build fresh application-owned context delivered with this turn only."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    sections: list[str] = []
    from services.character_prompt_package import build_turn_packet

    if skill_context.strip():
        sections.append(
            f"[AUTO-LOADED LOCAL SKILLS]\n{skill_context}\n"
            "[Apply this skill guidance when relevant to the current turn.]"
        )
    if mode_rules.strip():
        sections.append(f"[MODE RULES]\n{mode_rules}\n[/MODE RULES]")
    if orientation_context.strip():
        sections.append(f"[CURRENT ORIENTATION]\n{orientation_context}\n[/CURRENT ORIENTATION]")
    turn_packet = build_turn_packet(identity, prompts_dir)
    if turn_packet:
        sections.append(turn_packet)
    return "\n\n".join(sections)


def _format_turn_input(
    message: str,
    *,
    turn_context: str = "",
    bootstrap_history: str = "",
) -> str:
    """Keep app context and history distinct from Owner's current words."""
    sections: list[str] = []
    if bootstrap_history.strip():
        sections.append(
            "[ANAM CONVERSATION HISTORY — context only; answer the current message below]\n"
            f"{bootstrap_history}\n"
            "[/ANAM CONVERSATION HISTORY]"
        )
    if turn_context.strip():
        sections.append(
            "[ANAM TURN CONTEXT — application-owned, current turn only]\n"
            "This block is not text spoken by Owner. Retrieved records are evidence, "
            "may be stale, and do not override her current correction.\n\n"
            f"{turn_context}\n"
            "[/ANAM TURN CONTEXT]"
        )
    sections.append(
        f"[CURRENT MESSAGE FROM OWNER]\n{message}\n[/CURRENT MESSAGE FROM OWNER]"
    )
    return "\n\n".join(sections)


def _bootstrap_history(db_messages: list[dict] | None) -> str:
    if not db_messages:
        return ""
    lines: list[str] = []
    for message in db_messages[-24:]:
        role = str(message.get("role") or "user")
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        content = str(content).strip()
        if not content:
            continue
        if len(content) > 2500:
            content = content[:2500] + "\n[...truncated...]"
        speaker = "Owner" if role == "user" else "You"
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


async def _saved_thread_id(conversation_id: str) -> str | None:
    db = await get_db()
    try:
        return await get_provider_session_id_from_db(db, conversation_id, "codex")
    finally:
        await release_db(db)


def _extract_thread_id(result: dict) -> str | None:
    thread = result.get("thread") or {}
    return (
        thread.get("id")
        or thread.get("sessionId")
        or result.get("threadId")
        or result.get("sessionId")
    )


def _looks_like_auth_failure(text: str) -> bool:
    lowered = text.lower()
    if any(
        token in lowered
        for token in (
            "unauthorized",
            "authentication failed",
            "authentication failure",
            "authentication required",
            "authentication error",
            "access token",
            "codex login",
            "not logged in",
            "login required",
        )
    ):
        return True
    # A bare substring check misread fractional timestamps such as
    # ``...18.992401Z`` as an HTTP 401 and produced false login notices.
    return re.search(r"(?<!\d)401(?!\d)", text) is not None


def _resume_error_allows_fresh_thread(exc: Exception) -> bool:
    """Only replace a saved thread after a definitive thread-level rejection.

    A timeout means Codex may still be opening the original thread and its MCP
    map. Starting another thread then overlaps that work and amplifies the
    contention that caused the timeout.
    """
    if not isinstance(exc, CodexRpcError):
        return False
    detail = f"{exc.message}\n{exc.data}".lower()
    return bool(
        re.search(r"\b(?:unknown|invalid)\s+thread\b", detail)
        or re.search(
            r"\bthread\b.{0,120}\b(?:not found|does not exist|already has an active writer)\b",
            detail,
        )
    )


def _tool_shape(item: dict) -> tuple[str, dict, str, str]:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        command = item.get("command") or ""
        args = {"command": command, "cwd": item.get("cwd") or ""}
        output = str(item.get("aggregatedOutput") or "")
        exit_code = item.get("exitCode")
        return "shell", args, output, "completed" if exit_code in (None, 0) else "error"
    if item_type == "fileChange":
        changes = [
            {
                "path": change.get("path") or "",
                "kind": (change.get("kind") or {}).get("type") or "update",
            }
            for change in (item.get("changes") or [])
            if isinstance(change, dict)
        ]
        status = str(item.get("status") or "completed")
        return "apply_patch", {"changes": changes}, f"{status}: {len(changes)} file change(s)", "error" if status == "failed" else "completed"
    if item_type == "mcpToolCall":
        name = f"mcp.{item.get('server') or 'mcp'}.{item.get('tool') or 'unknown'}"
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {"arguments": item.get("arguments")}
        error = item.get("error")
        result = error if error is not None else item.get("result")
        return name, args, json.dumps(result, ensure_ascii=False, default=str), "error" if error else "completed"
    if item_type == "dynamicToolCall":
        name = str(item.get("tool") or "dynamic_tool")
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {"arguments": item.get("arguments")}
        result = item.get("contentItems") or {"success": item.get("success")}
        return name, args, json.dumps(result, ensure_ascii=False, default=str), "error" if item.get("success") is False else "completed"
    if item_type == "webSearch":
        return "web_search", {"query": item.get("query") or ""}, json.dumps(item.get("action"), ensure_ascii=False, default=str), "completed"
    return item_type or "codex_tool", {}, "", "completed"


def _answer_server_request(client: CodexAppServerClient, request: dict, bypass: bool, identity: str = "", conversation_id: str = "") -> dict | None:
    if not bypass or request.get('method') in codex_approvals._INPUTS | {'mcpServer/elicitation/request'}:
        return codex_approvals.register(client, request, identity, conversation_id)
    method = str(request.get("method") or "")
    request_id = request.get("id")
    if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
        client.respond(request_id, {"decision": "acceptForSession" if bypass else "decline"})
    elif method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
        client.respond(request_id, {"decision": "acceptForSession" if bypass else "decline"})
    elif method == "item/permissions/requestApproval":
        client.respond(request_id, {"permissions": (request.get("params") or {}).get("permissions", {}), "scope": "session"})
    elif method == "mcpServer/elicitation/request":
        client.respond(
            request_id,
            {"action": "accept" if bypass else "decline", "content": None, "_meta": None},
        )
    else:
        client.respond_error(request_id, -32601, f"Unsupported app-server request: {method}")
        return None
    if bypass:
        return None
    return {
        "type": "approval_required",
        "provider": "codex",
        "message": "Codex requested permission for an action. Enable bypass approvals to allow it.",
        "bypass_approvals": False,
    }


async def stream_codex_app_server(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: list[dict] | None = None,
    model: str | None = None,
    mode_rules: str = "",
    skill_context: str = "",
    cancel_event: asyncio.Event | None = None,
    bypass_approvals: bool = False,
    turn_source: str = "web",
) -> AsyncIterator[dict]:
    """Run one Anam turn on a persisted Codex app-server thread."""
    model = normalize_codex_model(model)
    resolved_model = model or get_codex_default_model_id() or "default"
    yield {
        "type": "meta",
        "provider": "codex",
        "runtime": "app-server",
        "requested_model": model or resolved_model,
        "actual_model": resolved_model,
    }

    codex_cmd = find_codex_executable()
    if not codex_cmd:
        yield {"type": "error", "message": "Codex CLI not found. Install with: npm install -g @openai/codex"}
        return

    try:
        instruction_bundle = _build_instruction_bundle(identity)
    except RuntimeError as exc:
        log.error("Codex instruction compilation failed for %s: %s", identity, exc)
        yield {"type": "error", "message": str(exc)}
        return
    turn_context = _build_turn_context(
        identity,
        orientation_context=orientation_context,
        mode_rules=mode_rules,
        skill_context=skill_context,
    )
    yield {
        "type": "meta",
        "instruction_fingerprint": instruction_bundle.fingerprint,
        "base_instructions_sha256": instruction_bundle.base_sha256,
        "developer_instructions_sha256": instruction_bundle.developer_sha256,
    }
    log.info(
        "Codex instructions identity=%s base=%s contract=%s fingerprint=%s",
        identity,
        instruction_bundle.base_sha256,
        instruction_bundle.developer_sha256,
        instruction_bundle.fingerprint,
    )
    extra_args: list[str] = []
    for mcp_override in build_codex_app_server_mcp_overrides(
        identity=identity, conversation_id=conversation_id,
        bypass_approvals=bypass_approvals,
    ):
        extra_args.extend(["-c", mcp_override])

    client: CodexAppServerClient | None = None
    full_text: list[str] = []
    sent_stream_end = False
    thread_id: str | None = None
    thinking_active = False
    agent_items_with_delta: set[str] = set()
    active_agent_item_id: str | None = None
    started_tools: set[str] = set()
    started_at = time.monotonic()
    last_event_at = started_at
    last_keepalive = started_at
    first_event_seen = False
    completed = False
    lease = None
    manager = None
    closing = False
    pending_error: dict | None = None
    pending_partial_stream_end: dict | None = None

    def switch_agent_message(item_id: str) -> bool:
        """Make the newest Codex agent message the canonical reply.

        Codex app-server can emit several agentMessage items in one turn:
        commentary before/during tool work, followed by the final answer.  They
        are replacements, not consecutive chunks of one persisted message.
        """
        nonlocal active_agent_item_id
        if not item_id:
            return False
        changed = active_agent_item_id is not None and item_id != active_agent_item_id
        active_agent_item_id = item_id
        if changed:
            full_text.clear()
        return changed

    def create_client():
        new_client = CodexAppServerClient(codex_cmd, extra_args)
        try:
            new_client.initialize()
            return new_client
        except BaseException:
            new_client.close()
            raise

    try:
        manager = codex_sessions.acquire(
            identity,
            create_client,
            (
                codex_cmd,
                tuple(extra_args),
                bypass_approvals,
                instruction_bundle.fingerprint,
            ),
            background=turn_source == "autowake",
        )
        lease = await manager.__aenter__()
        client = lease.client
        startup_ms = (time.monotonic() - started_at) * 1000
        runtime_metrics.record('codex_startup', startup_ms, identity=identity, provider='codex', reused=lease.reused)
        yield {"type": "meta", "startup_ms": startup_ms, "process_reused": lease.reused}

        thread_params: dict[str, Any] = {
            "cwd": str(BASE_DIR),
            "baseInstructions": instruction_bundle.base_text,
            "developerInstructions": instruction_bundle.developer_text,
            "personality": "none",
            "approvalPolicy": "never" if bypass_approvals else "on-request",
            "sandbox": "danger-full-access" if bypass_approvals else "workspace-write",
        }
        if model:
            thread_params["model"] = model

        saved_thread_id = await _saved_thread_id(conversation_id)
        resumed = False
        if saved_thread_id:
            try:
                result = await asyncio.to_thread(
                    client.request,
                    "thread/resume",
                    {"threadId": saved_thread_id, **thread_params},
                    _THREAD_OPEN_TIMEOUT,
                )
                thread_id = _extract_thread_id(result) or saved_thread_id
                resumed = True
            except (CodexRpcError, TimeoutError, RuntimeError) as exc:
                if _looks_like_auth_failure(str(exc) + "\n" + client.stderr_tail()):
                    raise
                if not _resume_error_allows_fresh_thread(exc):
                    raise
                log.info("Codex thread %s could not resume; starting a new thread: %s", saved_thread_id, exc)

        if not resumed:
            result = await asyncio.to_thread(
                client.request,
                "thread/start",
                thread_params,
                _THREAD_OPEN_TIMEOUT,
            )
            thread_id = _extract_thread_id(result)
        if not thread_id:
            raise RuntimeError("Codex app-server returned no thread id")

        turn_message = _format_turn_input(
            message,
            turn_context=turn_context,
            bootstrap_history=_bootstrap_history(db_messages) if not resumed else "",
        )

        turn_result = await asyncio.to_thread(
            client.request,
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": turn_message}],
                **({"model": model} if model else {}),
            },
            _TURN_START_TIMEOUT,
        )
        turn_id = (turn_result.get("turn") or {}).get("id")
        deadline = time.monotonic() + _TURN_TIMEOUT
        completed = False

        while time.monotonic() < deadline and not completed:
            if cancel_event is not None and cancel_event.is_set():
                if turn_id:
                    try:
                        await asyncio.to_thread(
                            client.request,
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            5,
                        )
                    except Exception:
                        pass
                break

            request = client.take_server_request()
            if request is not None:
                approval_event = _answer_server_request(client, request, bypass_approvals, identity, conversation_id)
                if approval_event:
                    yield approval_event
                continue

            notification = await asyncio.to_thread(client.take_notification, 0.25)
            now = time.monotonic()
            if notification is None:
                if codex_approvals.pending(identity, conversation_id):

                    deadline += now - last_event_at
                    last_event_at = now
                if not client.is_alive():
                    raise RuntimeError(f"Codex app-server exited unexpectedly\n{client.stderr_tail()}")
                if now - last_event_at >= _STALL_TIMEOUT:
                    raise TimeoutError(f"Codex produced no app-server events for {_STALL_TIMEOUT:.0f}s")
                if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                    yield {"type": "keepalive"}
                    last_keepalive = now
                continue

            last_event_at = now
            method = str(notification.get("method") or "")
            params = notification.get("params") or {}
            if method == "serverRequest/resolved":
                codex_approvals.clear_request(client, params.get("requestId"))
                continue
            # A pooled connection can contain late notifications from its previous turn.
            if params.get("threadId") and params["threadId"] != thread_id:
                continue
            if params.get("turnId") and turn_id and params["turnId"] != turn_id:
                continue
            if method == "turn/completed" and (params.get("turn") or {}).get("id") not in (None, turn_id):
                continue
            if method == "mcpServer/startupStatus/updated":
                if str(params.get("status") or "").lower() == "failed":
                    server_name = str(params.get("name") or "unknown")
                    failure = params.get("failureReason") or params.get("error") or "unknown failure"
                    # MCP startup is non-fatal to the Codex turn, but it must
                    # never vanish silently: a missing tool door is a real
                    # infrastructure failure even when the reply still works.
                    log.warning(
                        "Codex MCP server %s failed to start: %.1000s",
                        server_name,
                        str(failure),
                    )
                # Provider plumbing is not the model's first turn event.
                continue
            if not first_event_seen:
                first_event_seen = True
                runtime_metrics.record('codex_first_event', (now-started_at)*1000, identity=identity, provider='codex', reused=lease.reused)
                yield {"type": "meta", "first_event_ms": (now - started_at) * 1000}

            if method == "turn/started":
                turn_id = (params.get("turn") or {}).get("id") or turn_id
                continue
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta:
                    item_id = str(params.get("itemId") or "")
                    if switch_agent_message(item_id):
                        yield {"type": "stream_reset"}
                    agent_items_with_delta.add(item_id)
                    full_text.append(delta)
                    yield {"type": "stream_delta", "delta": delta}
                continue
            if method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
                delta = str(params.get("delta") or "")
                if delta:
                    if not thinking_active:
                        thinking_active = True
                        yield {"type": "thinking_start"}
                    yield {"type": "thinking_delta", "delta": delta}
                continue
            if method == "item/started":
                item = params.get("item") or {}
                item_type = str(item.get("type") or "")
                if item_type == "agentMessage":
                    if switch_agent_message(str(item.get("id") or "")):
                        yield {"type": "stream_reset"}
                elif item_type in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"}:
                    tool_id = str(item.get("id") or "")
                    tool_name, tool_input, _content, _status = _tool_shape(item)
                    started_tools.add(tool_id)
                    yield {"type": "tool_use_start", "tool_name": tool_name, "tool_id": tool_id, "input": {}}
                    yield {"type": "tool_input", "tool_name": tool_name, "tool_id": tool_id, "input": tool_input}
                continue
            if method == "item/completed":
                item = params.get("item") or {}
                item_type = str(item.get("type") or "")
                item_id = str(item.get("id") or "")
                if item_type == "agentMessage" and item_id not in agent_items_with_delta:
                    if switch_agent_message(item_id):
                        yield {"type": "stream_reset"}
                    text = str(item.get("text") or "")
                    if text:
                        full_text.append(text)
                        yield {"type": "stream_delta", "delta": text}
                elif item_type == "reasoning" and thinking_active:
                    thinking_active = False
                    yield {"type": "content_block_stop"}
                elif item_type in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"}:
                    tool_name, tool_input, content, status = _tool_shape(item)
                    if item_id not in started_tools:
                        yield {"type": "tool_use_start", "tool_name": tool_name, "tool_id": item_id, "input": {}}
                        yield {"type": "tool_input", "tool_name": tool_name, "tool_id": item_id, "input": tool_input}
                    if len(content) > 4000:
                        content = content[:4000] + "\n[...truncated...]"
                    yield {
                        "type": "tool_result",
                        "tool_use_id": item_id,
                        "tool_name": tool_name,
                        "status": status,
                        "input": tool_input,
                        "content": content,
                    }
                    yield {"type": "content_block_stop"}
                continue
            if method == "error":
                error = params.get("error") or params.get("message") or params
                yield {"type": "error", "message": f"Codex app-server error: {error}"}
                continue
            if method == "turn/completed":
                turn = params.get("turn") or {}
                turn_id = turn.get("id") or turn_id
                status = str(turn.get("status") or "completed")
                if status != "completed":
                    error = turn.get("error") or status
                    yield {"type": "error", "message": f"Codex turn {status}: {error}"}
                completed = True


                try:
                    usage_raw = turn.get("usage") or params.get("usage") or {}
                    if isinstance(usage_raw, dict) and usage_raw:
                        from services.usage_tracker import record_usage
                        await record_usage(
                            identity=identity,
                            conversation_id=conversation_id,
                            model=resolved_model,
                            usage={
                                "input_tokens": usage_raw.get("input_tokens")
                                or usage_raw.get("inputTokens"),
                                "output_tokens": usage_raw.get("output_tokens")
                                or usage_raw.get("outputTokens"),
                                "cache_read_input_tokens": usage_raw.get(
                                    "cached_input_tokens"
                                )
                                or usage_raw.get("cachedInputTokens"),
                            },
                            source="codex",
                        )
                except Exception as exc:
                    log.debug("Codex usage record skipped: %s", exc)

        if thinking_active:
            yield {"type": "content_block_stop"}
        if completed or full_text:
            yield {
                "type": "stream_end",
                "full_content": "".join(full_text),
                "session_id": thread_id,
            }
            sent_stream_end = True
        elif cancel_event is None or not cancel_event.is_set():
            yield {"type": "error", "message": "Codex app-server ended without a response"}

    except (GeneratorExit, asyncio.CancelledError):
        closing = True
        raise
    except Exception as exc:
        detail = client.stderr_tail() if client is not None else ""
        message_text = f"{exc}"
        if detail and detail not in message_text:
            message_text += f"\n{detail}"
        if _looks_like_auth_failure(message_text):
            message_text = "Codex authentication needs attention. Run `codex login`, then try again.\n" + message_text
        log.exception("Codex app-server turn failed for %s", identity)
        # Release the identity lease before exposing the error. A caller may
        # stop consuming as soon as it sees this event; yielding first used to
        # leave the per-identity lock held until async-generator GC.
        pending_error = {"type": "error", "message": message_text[:4000]}
    finally:
        runtime_metrics.record('codex_turn', (time.monotonic()-started_at)*1000, identity=identity, provider='codex', ok=completed)
        if client is not None:
            codex_approvals.clear_client(client)
        if lease is not None:
            lease.retain = completed
            await manager.__aexit__(None, None, None)
        if full_text and not sent_stream_end and not closing:
            pending_partial_stream_end = {
                "type": "stream_end",
                "full_content": "".join(full_text),
                "session_id": thread_id,
            }

    if pending_error is not None and not closing:
        yield pending_error
    if pending_partial_stream_end is not None and not closing:
        yield pending_partial_stream_end
