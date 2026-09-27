"""Persistent per-identity Claude Code supervisor.

Replaces the previous per-turn `claude -p <msg>` spawn pattern with a
long-running `claude` subprocess per (identity, conversation_id) pair,
talking NDJSON over stdin/stdout via:

    claude -p --input-format stream-json --output-format stream-json
           --verbose --include-partial-messages --system-prompt ...

The system prompt is sent ONCE at process startup. Each subsequent turn
writes a single user-message JSON line to the live stdin; the model's
context lives in-process, so we no longer pay `--resume` rebuild cost
per turn. The DB is the source of truth and the recovery substrate:
when a process crashes (or has never been spawned for a given pair),
we spawn fresh and replay recent history from the DB as the first
user message.

Public surface (unchanged callable name):
    async def stream_claude(message, identity, conversation_id, ...) -> AsyncIterator[dict]

Event types yielded are identical to the previous implementation so
`chat_pipeline.py` does not need to change:
    meta, stream_delta, thinking_start, thinking_delta, tool_use_start,
    tool_input, tool_result, content_block_stop, keepalive,
    approval_required, error, stream_end
"""

# ANAM GUIDE: CLAUDE CODE SUBPROCESS BACKEND (DEFAULT)
# What: The main free brain — keeps one long-running `claude -p` process per identity+conversation and streams its NDJSON replies. This is the default backend.
# Called by: services/provider_router.py when the provider is 'claude-code'; sessions managed via services/session_lifecycle.py, api/hub.py, api/settings.py, core/lifespan.py.
# Edit here when: changing how CLI sessions spawn, resume, replay history, stream events, handle keepalives, or get killed/retired.

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import AsyncIterator

from config import (
    CLAUDE_CMD, CLAUDE_MAX_TURNS, CLAUDE_MAX_TURNS_AUTOWAKE, CLAUDE_MODEL,
    CLAUDE_PERMISSION_MODE, PROMPTS_DIR, DATA_DIR,
    IMAGES_DIR, IMAGE_ALLOWED_EXTENSIONS,
    DOCUMENTS_DIR, DOCUMENT_ALLOWED_EXTENSIONS,
    FABLE_IDENTITY_BREATH_INTERVAL,
    cli_cold_history_limits, is_fable_model,
)
from services.cli_mcp_config import write_claude_mcp_config
from services.identity_context import build_system_identity_prompt
from services.character_prompt_package import identity_prompt_file


from services.cli_text_utils import (
    _build_first_message,
    _format_history,
    _load_recent_history,
)

log = logging.getLogger(__name__)


_TURN_SOURCE_PRIORITY = {"web": 0, "platform": 1, "autowake": 2}
_DEFAULT_TURN_SOURCE = "web"


def _maybe_preempt_lower_priority_turn(session: "ClaudeSession", turn_source: str) -> None:
    """Fire write_interrupt() if the session's lock is held by a turn with
    strictly lower priority than the incoming one. Best-effort: a failed or
    already-finishing interrupt just means the incoming turn waits the
    normal FIFO amount instead of jumping the queue — never an error."""
    if not session.turn_lock.locked():
        return
    holder = session.active_turn_source or "autowake"
    holder_priority = _TURN_SOURCE_PRIORITY.get(holder, _TURN_SOURCE_PRIORITY["autowake"])
    incoming_priority = _TURN_SOURCE_PRIORITY.get(turn_source, _TURN_SOURCE_PRIORITY["autowake"])
    if incoming_priority >= holder_priority:
        return
    try:
        session.write_interrupt()
        log.info(
            "Priority preempt: %s turn interrupting in-progress %s turn for %s/%s",
            turn_source, holder, session.identity, session.conversation_id[:8],
        )
    except Exception:
        log.debug(
            "Priority preempt interrupt failed for %s/%s (turn likely already finishing)",
            session.identity, session.conversation_id[:8],
        )


# ─── Approval / sensitive-path detection (preserved from previous impl) ────────

_APPROVAL_KEYWORDS = (
    "approval", "permission", "not permitted",
    "not allowed", "denied", "forbidden",
    "requires user confirmation", "sensitive file",
)

_RULE_PATH_RE = re.compile(
    r"(?P<verb>Write|Edit|Read|Execute|Bash)\s*(?:to\s+)?(?:\()?"
    r"(?P<path>[A-Z]:\\[^\s;,)]+|/[^\s;,)]+|~/[^\s;,)]+)",
    re.IGNORECASE,
)

# CC hardcodes `.claude/skills/*` and `.claude/agents/*` as sensitive paths.
# Detection routes the UI to a `file-scribe` bypass instead of an infinite
# Allow & Retry loop.
_SENSITIVE_PATH_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|[/~])[^\s\"'`,;)]*\.claude[\\/](?:skills|agents)[\\/][^\s\"'`,;)]*)",
    re.IGNORECASE,
)


_USAGE_LIMIT_MARKERS = (
    "usage limit reached",
    "usage limit",
    "limit will reset",
    "hit your usage limit",
    "out of usage",
)
_USAGE_LIMIT_EPOCH_RE = re.compile(r"\|\s*(\d{9,12})")


def _maybe_mark_fable_limited(error_text: str, session: "ClaudeSession") -> None:
    """Best-effort: detect a usage-limit error on a Fable session and arm the
    router demotion. Must never break the error path it rides on."""
    try:
        if not is_fable_model(session.model):
            return
        lowered = (error_text or "").lower()
        if not any(marker in lowered for marker in _USAGE_LIMIT_MARKERS):
            return
        m = _USAGE_LIMIT_EPOCH_RE.search(error_text)
        until = float(m.group(1)) if m else None
        from services.provider_router import mark_fable_limited
        mark_fable_limited(until)
    except Exception:
        log.debug("Fable limit detection skipped", exc_info=True)


def _infer_suggested_rule(error_text: str) -> str | None:
    m = _RULE_PATH_RE.search(error_text)
    if not m:
        return None
    verb_raw = m.group("verb").lower()
    path = m.group("path").replace("\\", "/")
    last_slash = path.rfind("/")
    if last_slash > 0:
        path = path[:last_slash + 1] + "*"
    verb_map = {"read": "Read", "execute": "Bash", "bash": "Bash"}
    verb = verb_map.get(verb_raw, "Write")
    return f"{verb}({path})"


def _extract_sensitive_path(error_text: str) -> str | None:
    m = _SENSITIVE_PATH_RE.search(error_text)
    return m.group(1) if m else None


def _annotate_sensitive_path(approval_evt: dict, error_text: str) -> None:
    sensitive_path = _extract_sensitive_path(error_text)
    if sensitive_path:
        approval_evt["sensitive_path"] = True
        approval_evt["target_path"] = sensitive_path
        approval_evt["bypass_recommendation"] = "file-scribe"


# ─── Identity prompt cache ─────────────────────────────────────────────────────

# identity (lowercased) → (st_mtime_ns, stripped text). Avoids re-reading the
# prompt file from disk on every turn; invalidates automatically when the file
# is edited (mtime changes). Missing file → "" (same fallback as the old
# .exists() check); read failure → "" with a warning, and nothing is cached so
# the next turn retries the read.
_identity_prompt_cache: dict[str, tuple[int, str]] = {}


def _load_identity_prompt(identity: str) -> str:
    key = identity.lower()
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    try:
        mtime_ns = prompt_file.stat().st_mtime_ns
    except OSError:
        # Missing (or unstatable) file → empty prompt, matching the old
        # `if prompt_file.exists()` fallback semantics.
        _identity_prompt_cache.pop(key, None)
        return ""
    cached = _identity_prompt_cache.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    try:
        text = prompt_file.read_text(encoding="utf-8").strip()
    except Exception as e:
        log.warning("Failed to read prompt file %s: %s", prompt_file, e)
        return ""
    _identity_prompt_cache[key] = (mtime_ns, text)
    return text


# ─── Image / document extraction (preserved from previous impl) ───────────────

def _extract_image_paths(text: str, identity: str | None = None) -> list[dict]:
    from api.images import _is_allowed_source_path, informative_filename
    if not text or not isinstance(text, str):
        return []
    results = []
    seen = set()
    ext_group = '|'.join(ext.lstrip('.') for ext in IMAGE_ALLOWED_EXTENSIONS)
    path_pattern = (
        r'(?:[A-Za-z]:[\\\/][^\n<>"]+?|/[^\s<>"]+?)'
        r'\.(?:' + ext_group + r')'
        r'(?=[\s<>"\n,;:\)\*]|$)'
    )
    for match in re.finditer(path_pattern, text, re.IGNORECASE | re.MULTILINE):
        found_path = Path(match.group().strip())
        path_str = str(found_path)
        if path_str in seen:
            continue
        seen.add(path_str)
        if not (found_path.exists() and found_path.is_file()):
            continue
        try:
            resolved = found_path.resolve()
        except OSError:
            continue
        if not _is_allowed_source_path(resolved):
            log.warning("Tool-result image extraction blocked - outside allowlist: %s", resolved)
            continue
        try:
            filename = informative_filename(identity, resolved.suffix.lower())
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            dest = IMAGES_DIR / filename
            shutil.copy2(str(resolved), str(dest))
            results.append({
                "path": str(resolved),
                "url": f"/api/images/file/{filename}",
            })
            log.info("Extracted image from tool result: %s -> %s", resolved, filename)
        except Exception as e:
            log.exception("Failed to copy tool result image %s: %s", resolved, e)
    return results


def _human_size(size_bytes: int) -> str:
    if size_bytes > 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    return f"{round(size_bytes / 1024)} KB"


def _extract_document_paths(text: str, identity: str | None = None) -> list[dict]:
    from api.documents import informative_filename
    from api.images import _is_allowed_source_path
    if not text or not isinstance(text, str):
        return []
    results = []
    seen = set()
    ext_group = "|".join(ext.lstrip(".") for ext in DOCUMENT_ALLOWED_EXTENSIONS)
    path_pattern = (
        r'(?:[A-Za-z]:[\\\/][^\n<>"]+?|/[^\s<>"]+?)'
        r'\.(?:' + ext_group + r')'
        r'(?=[\s<>"\n,;:\)]|$)'
    )
    for match in re.finditer(path_pattern, text, re.IGNORECASE | re.MULTILINE):
        found_path = Path(match.group().strip())
        path_str = str(found_path)
        if path_str in seen:
            continue
        seen.add(path_str)
        if not (found_path.exists() and found_path.is_file()):
            continue
        try:
            resolved = found_path.resolve()
        except OSError:
            continue
        if not _is_allowed_source_path(resolved):
            log.warning("Tool-result document extraction blocked - outside allowlist: %s", resolved)
            continue
        try:
            filename = informative_filename(identity, resolved.suffix.lower())
            DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
            dest = DOCUMENTS_DIR / filename
            shutil.copy2(str(resolved), str(dest))
            doc_id = filename.rsplit(".", 1)[0]
            results.append({
                "doc_id": doc_id,
                "filename": filename,
                "original_name": resolved.name,
                "size_display": _human_size(dest.stat().st_size),
                "url": f"/api/documents/file/{filename}",
                "path": str(resolved),
            })
            log.info("Extracted document from tool result: %s -> %s", resolved, filename)
        except Exception as e:
            log.exception("Failed to copy tool result document %s: %s", resolved, e)
    return results


# ─── Prompt composition (moved from provider_router so supervisor owns it) ────

def _image_blocks_to_text(image_blocks: list[dict] | None) -> str:
    """Convert Anthropic image content blocks to text instructions for CLI providers."""
    if not image_blocks:
        return ""
    notes = []
    for block in image_blocks:
        if block.get("type") != "image":
            continue
        source = block.get("source", {})
        url = source.get("url", "")
        if "/api/images/file/" in url:
            filename = url.split("/api/images/file/")[-1]
            img_path = IMAGES_DIR / filename
            if img_path.exists():
                notes.append(
                    f"[Owner shared an image: {img_path}\n"
                    f"Use the Read tool to view it.]"
                )
            else:
                notes.append(f"[Owner shared an image but the file was not found: {filename}]")
        elif url:
            notes.append(f"[Owner shared an image: {url}]")
    return "\n\n".join(notes)


def _db_message_to_cli_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    text_parts = []
    image_blocks = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            text_parts.append(f"[Used tool: {block.get('name', '?')}]")
        elif block_type == "tool_result":
            result_text = str(block.get("content", ""))
            if len(result_text) > 500:
                result_text = result_text[:500] + "..."
            text_parts.append(f"[Tool result: {result_text}]")
        elif block_type == "image":
            image_blocks.append(block)
    image_text = _image_blocks_to_text(image_blocks)
    if image_text:
        text_parts.insert(0, image_text)
    return "\n".join(p for p in text_parts if p)


class ClaudeSession:
    """One long-running `claude` subprocess for a single (identity, conv_id).

    Lifecycle:
        * `_spawn()` launches the process and starts the stdout reader thread.
        * `send_turn()` acquires a per-session lock, writes one user JSON line,
          and yields NDJSON events until a `result` event arrives. Process
          stays alive for the next turn.
        * `kill()` terminates the subprocess and cleans up the temp MCP config.
        * `dead` flag flips True when the reader detects EOF or the supervisor
          decides to retire the session (crash, user cancel, idle eviction).
    """

    def __init__(
        self,
        identity: str,
        conversation_id: str,
        model: str,
        permission_mode: str,
        effort: str | None,
        turn_source: str = "web",
    ):
        self.identity = identity
        self.conversation_id = conversation_id
        self.model = model


        self.max_turns = (
            CLAUDE_MAX_TURNS_AUTOWAKE if turn_source == "autowake" else CLAUDE_MAX_TURNS
        )
        self.pool_key: tuple[str, str, str] | None = None
        self.permission_mode = permission_mode
        # Fable's effort is decided UPSTREAM now (provider_router applies the
        # fable_effort setting). Do not clamp it here, or the
        # session would silently squash whatever the router resolved.
        self.effort = effort
        self.proc: subprocess.Popen | None = None
        # Async handoff from the stdout reader thread to the event loop.
        # The reader thread enqueues via loop.call_soon_threadsafe (see
        # _enqueue_line) — asyncio.Queue itself is NOT thread-safe, so the
        # put_nowait must run ON the loop. Constructed here, inside the loop
        # (`_get_or_spawn` is async), so get_running_loop() is safe.
        self.loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self.line_queue: asyncio.Queue = asyncio.Queue()
        self.stderr_buf: list[bytes] = []
        self.reader_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.mcp_config_path: Path | None = None
        self.turn_lock = asyncio.Lock()
        # Which turn source (web/platform/autowake) currently holds turn_lock,
        # if any -- read by _maybe_preempt_lower_priority_turn (#30) so a
        # higher-priority arrival can interrupt a lower-priority in-progress
        # turn instead of waiting in FIFO order. None when idle.
        self.active_turn_source: str | None = None
        self.session_id: str | None = None
        # MCP roster from the CLI's `system`/`init` event — per-server
        # connection status plus tool counts. Captured by _capture_mcp_status
        # and surfaced via sessions_snapshot() so the Settings Hub System
        # panel can show which servers actually booted for this session.
        self.mcp_servers: list[dict] = []
        self.first_turn = True
        self.dead = False


        self.last_cost_usd = 0.0
        self.last_activity = time.time()
        self.spawn_started = 0.0
        # Fable-only cadence state. Other models and character masks ignore it
        # and continue receiving the full identity prompt every warm turn.
        self.turns_since_identity_breath = 0

    def _build_cmd(self) -> list[str]:
        system_identity = build_system_identity_prompt(self.identity)
        self.mcp_config_path = write_claude_mcp_config(self.identity, self.conversation_id)

        cmd_flags = [
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
            "--permission-mode", self.permission_mode,
            "--system-prompt", system_identity,
            "--name", f"{self.identity}-{self.conversation_id[:8]}",
            "--fallback-model", "sonnet",
        ]
        if self.mcp_config_path:
            cmd_flags += [
                "--mcp-config", str(self.mcp_config_path),
                "--strict-mcp-config",
            ]
        if self.effort and self.effort in {"low", "medium", "high", "xhigh", "max"}:
            cmd_flags += ["--effort", self.effort]
        return [CLAUDE_CMD, "-p"] + cmd_flags

    def _spawn(self) -> None:
        """Launch the subprocess and start reader threads."""
        browser_artifacts_dir = DATA_DIR / "browser_artifacts"
        browser_artifacts_dir.mkdir(parents=True, exist_ok=True)
        cwd = str(browser_artifacts_dir)

        env = dict(os.environ)
        env.pop("CLAUDECODE", None)  # don't tell the subprocess it's nested
        # Progressive disclosure is the normal toolbox contract for every
        # model. Exact schemas open only after the model chooses a leaf.
        env["ENABLE_TOOL_SEARCH"] = "true"

        cmd = self._build_cmd()
        self.spawn_started = time.time()
        log.info(
            "Spawning persistent Claude for %s/%s (model=%s, effort=%s)",
            self.identity, self.conversation_id[:8],
            self.model, self.effort,
        )
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            bufsize=0,
        )

        self.reader_thread = threading.Thread(
            target=self._stdout_reader, daemon=True,
            name=f"claude-stdout-{self.identity}-{self.conversation_id[:8]}",
        )
        self.reader_thread.start()

        self.stderr_thread = threading.Thread(
            target=self._stderr_reader, daemon=True,
            name=f"claude-stderr-{self.identity}-{self.conversation_id[:8]}",
        )
        self.stderr_thread.start()

    def _enqueue_line(self, item) -> None:
        """Thread-safe handoff of one item onto the asyncio line queue.

        Called from the reader THREAD. asyncio.Queue is not thread-safe, so
        the actual put_nowait is scheduled onto the owning event loop via
        call_soon_threadsafe. If the loop is already closed (server shutdown
        racing a dying subprocess), the item is dropped — the consumer is
        gone anyway.
        """
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self.line_queue.put_nowait, item)
        except RuntimeError:
            # Loop closed between the check and the call — nothing to do.
            pass

    def _stdout_reader(self) -> None:
        """Pump lines from subprocess stdout onto the asyncio line queue."""
        try:
            assert self.proc and self.proc.stdout
            for line in iter(self.proc.stdout.readline, b""):
                self._enqueue_line(line)
        except Exception as e:
            self._enqueue_line(e)
        finally:
            self._enqueue_line(None)
            self.dead = True

    def _stderr_reader(self) -> None:
        try:
            assert self.proc and self.proc.stderr
            for line in iter(self.proc.stderr.readline, b""):
                self.stderr_buf.append(line)
                # Cap to prevent unbounded memory growth in a long-lived process
                if len(self.stderr_buf) > 5000:
                    self.stderr_buf = self.stderr_buf[-2500:]
        except Exception:
            pass

    def kill(self) -> None:
        """Terminate the subprocess AND its MCP child tree, then clean up."""
        self.dead = True
        if self.proc and self.proc.poll() is None:
            pid = self.proc.pid
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=10,
                )
            except Exception as e:
                log.debug("Tree-kill (taskkill /T /F) failed for pid %s: %s", pid, e)
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self.mcp_config_path:
            try:
                self.mcp_config_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.mcp_config_path = None

    def write_user_message(self, content: str) -> None:
        """Write one user message as an NDJSON line to subprocess stdin."""
        if not self.proc or not self.proc.stdin or self.dead:
            raise RuntimeError("Cannot write to dead Claude session")
        payload = {
            "type": "user",
            "message": {"role": "user", "content": content},
        }
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.dead = True
            raise RuntimeError(f"Claude session stdin write failed: {e}")
        self.last_activity = time.time()

    def write_interrupt(self) -> None:
        """Send a control-request interrupt for the in-flight turn.

        Unlike kill(), this asks the CLI to abort ONLY the current turn while
        keeping the process — and the warm in-context session it's holding —
        alive for the next turn. That's the whole point of the persistent
        supervisor: a stop click used to map straight to `kill()` + a cold
        respawn that replays lossy DB history as the first message (see
        `_needs_cold_context`). Sent over the same stdin pipe
        `write_user_message` already writes to, as a single NDJSON line:
            {"type": "control_request", "request": {"subtype": "interrupt"}}
        The CLI is expected to answer by winding the current turn down and
        still emitting its normal `result` event (now reflecting the
        interruption) rather than hanging — the caller in `_run_one_turn`
        keeps draining stdout after calling this exactly as it would for an
        ordinary turn, and falls back to `kill()` only if that never arrives.
        """
        if not self.proc or not self.proc.stdin or self.dead:
            raise RuntimeError("Cannot interrupt a dead Claude session")
        payload = {"type": "control_request", "request": {"subtype": "interrupt"}}
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.dead = True
            raise RuntimeError(f"Claude session interrupt write failed: {e}")
        self.last_activity = time.time()

    def get_stderr_text(self) -> str:
        if not self.stderr_buf:
            return ""
        return b"".join(self.stderr_buf).decode("utf-8", errors="replace").strip()


# ─── Supervisor ──────────────────────────────────────────────────────────────

_SessionKey = tuple[str, str, str]
_sessions: dict[_SessionKey, ClaudeSession] = {}
_supervisor_lock = asyncio.Lock()


def _retire_session(key: _SessionKey) -> None:
    """Remove a session from the pool and kill its process."""
    session = _sessions.pop(key, None)
    if session is not None:
        session.kill()


async def _get_or_spawn(
    identity: str,
    conversation_id: str,
    model: str,
    permission_mode: str,
    effort: str | None,
    turn_source: str = "web",
) -> tuple[ClaudeSession, bool]:
    """Return (session, is_fresh). Spawns under the supervisor lock if needed."""
    # Interactive Fable and background Sonnet can inhabit the same DB thread
    # without sharing (and silently pinning) one live model process.
    key: _SessionKey = (identity, conversation_id, model)
    async with _supervisor_lock:
        existing = _sessions.get(key)
        if existing is not None:
            if existing.dead or (existing.proc and existing.proc.poll() is not None):
                log.info(
                    "Retiring dead Claude session for %s/%s (exit=%s)",
                    identity, conversation_id[:8],
                    existing.proc.poll() if existing.proc else None,
                )
                _retire_session(key)
            else:
                if turn_source != "autowake":
                    existing.autowake_only = False
                return existing, False

        session = ClaudeSession(
            identity=identity,
            conversation_id=conversation_id,
            model=model,
            permission_mode=permission_mode,
            effort=effort,
            turn_source=turn_source,
        )
        session.pool_key = key
        session.autowake_only = turn_source == "autowake"
        await asyncio.get_running_loop().run_in_executor(None, session._spawn)
        _sessions[key] = session
        return session, True


def inject_user_message(identity: str, conversation_id: str, text: str) -> bool:
    """Slip a message into a turn that is ALREADY RUNNING, terminal-style."""
    if not text or not text.strip():
        return False
    for key, session in list(_sessions.items()):
        if key[0] != identity or key[1] != conversation_id:
            continue
        # turn_lock held == a turn is genuinely in flight. If it is idle, the
        # message must NOT go down stdin: the CLI would hold it and replay it
        # against whatever turn comes next, out of order.
        if session.dead or not session.turn_lock.locked():
            continue
        try:
            session.write_user_message(text)
        except Exception as exc:
            log.warning(
                "Live injection failed for %s/%s (%s); falling back to the queue",
                identity, conversation_id[:8], exc,
            )
            return False
        log.info(
            "Injected Owner's message into %s's in-flight turn (%s)",
            identity, conversation_id[:8],
        )
        return True
    return False


def kill_autowake_sessions(identity: str, conversation_id: str) -> bool:
    """Retire only autonomous-owned processes; return whether messaging is protected."""
    protected = False
    for key, session in list(_sessions.items()):
        if key[0] != identity or key[1] != conversation_id:
            continue
        if getattr(session, 'autowake_only', False):
            _retire_session(key)
        elif not session.dead:
            protected = True
    return protected


def kill_session(identity: str, conversation_id: str) -> None:
    """Retire every model lane owned by this identity/conversation."""
    matching = [
        key for key in _sessions
        if key[0] == identity and key[1] == conversation_id
    ]
    if matching:
        log.info("Killing Claude session for %s/%s on external request",
                 identity, conversation_id[:8])
    for key in matching:
        _retire_session(key)


def kill_all_sessions() -> None:
    """Used at server shutdown."""
    keys = list(_sessions.keys())
    for key in keys:
        _retire_session(key)


def is_any_session_warm(identity: str, conversation_id: str) -> bool:
    """Best-effort: will THIS identity/conversation's next -p turn be warm?

    Item #14 (session/turn orientation split) uses this to decide whether the
    heavy narrative/recall context hooks are worth paying for — a live
    session that's already had its first real turn holds that history in its
    own context, so re-injecting it is pure weight.

    Non-locking read of the supervisor pool; a missed race (session dies or
    spawns between this check and the actual turn) just means the hooks run
    as if cold, which is always the safe direction — more context, never
    less. Model-agnostic: checks any live session for this identity+
    conversation regardless of which model is attached, since a provider
    other than claude-code never populates `_sessions` at all (so this
    correctly returns False and every hook stays scope='both' behavior).
    """
    for (ident, conv, _model), session in list(_sessions.items()):
        if ident == identity and conv == conversation_id and not session.dead:
            if not bool(getattr(session, "first_turn", True)):
                return True
    return False


def sessions_snapshot() -> list[dict]:
    """Read-only view of the live -p session pool, for observability.

    Surfaced via /api/settings/system so the reaper and pre-warm can be
    verified at a glance (Avery's review #4) instead of guessing from Task
    Manager — each entry shows how idle a session is and whether it's busy.
    """
    now = time.time()
    out: list[dict] = []
    for (identity, conv, model), s in list(_sessions.items()):
        out.append({
            "identity": identity,
            "conversation_id": conv[:8],
            "model": model,
            "idle_seconds": round(now - s.last_activity, 1),
            "busy": s.turn_lock.locked(),
            "dead": s.dead,
            "pid": (s.proc.pid if s.proc else None),
            # Per-server MCP roster from the CLI init event (may be [] until
            # the session's first system/init arrives).
            "mcp_servers": list(s.mcp_servers),
        })
    return out


async def prewarm_identity(
    identity: str,
    conversation_id: str,
    *,
    model: str | None = None,
    permission_mode: str | None = None,
    effort: str | None = None,
) -> bool:
    """Spawn this identity's -p session ahead of the first real message.

    Unlike PTY (which must drip-feed a warmup turn to load identity into TUI
    memory), -p pre-warm is just an early spawn: the identity prompt rides in
    --system-prompt and the ~22 MCP servers boot during process startup, so
    spawning before Owner's first message overlaps that cold-boot cost with
    her think-time — her first "hey" then lands on an already-booted process.

    Must spawn with the SAME model/effort a real turn will use; _get_or_spawn
    keys only on (identity, conversation_id) and reuses the existing session,
    so a mismatched pre-warm would otherwise pin the wrong flags. Best-effort:
    failures are logged, never raised — a failed pre-warm just means the next
    message pays the normal cold-spawn, exactly as before this existed.
    """
    if not conversation_id:
        return False
    effective_model = model or CLAUDE_MODEL
    effective_permission_mode = permission_mode or CLAUDE_PERMISSION_MODE
    try:
        _session, is_fresh = await _get_or_spawn(
            identity=identity,
            conversation_id=conversation_id,
            model=effective_model,
            permission_mode=effective_permission_mode,
            effort=effort,
        )
        if is_fresh:
            log.info(
                "Pre-warmed -p session for %s/%s (boot overlaps think-time)",
                identity, conversation_id[:8],
            )
        return True
    except Exception as e:
        log.warning("Pre-warm -p %s failed: %s", identity, e)
        return False


# Legacy session helpers — kept as no-op shims so any straggler caller
# doesn't import-error. The DB session pointer is no longer meaningful in
# persistent mode (the supervisor owns lifecycle), but we still record the
# session_id we capture from `system/init` events for audit/debug.

def get_session_id(identity: str, conversation_id: str) -> str | None:
    matching = [
        session for (ident, conv, _model), session in _sessions.items()
        if ident == identity and conv == conversation_id
    ]
    if not matching:
        return None
    session = max(matching, key=lambda item: item.last_activity)
    return session.session_id


def save_session_id(identity: str, conversation_id: str, session_id: str) -> None:
    """No-op: persistent supervisor doesn't need a DB pointer to resume."""
    return


def clear_session(identity: str, conversation_id: str) -> None:
    """Map onto kill_session for backward-compat."""
    kill_session(identity, conversation_id)


# ─── Event-stream loop ───────────────────────────────────────────────────────

_KEEPALIVE_INTERVAL = 10.0  # seconds without an event before yielding keepalive

# Grace window after a soft `write_interrupt()` before we give up on the CLI
# answering gracefully and fall back to the hard kill-and-respawn path. Long
# enough to cover normal in-flight tool calls winding down; short enough that
# a stop click never feels stuck. See ClaudeSession.write_interrupt.
_INTERRUPT_TIMEOUT = 10.0


def _should_include_identity_prompt(
    session: ClaudeSession,
    *,
    identity: str,
    model: str,
    is_fresh: bool,
) -> bool:
    """Apply Fable's rationed-breath policy without changing other models.

    Fresh sessions always get the full identity body. Every identity running
    on Fable — bonded companions and character masks alike — then gets lean
    warm turns followed by a periodic full refresh. Every other model keeps
    the original breathe-every-turn behavior.
    """
    ration_fable = (
        is_fable_model(model)
        and FABLE_IDENTITY_BREATH_INTERVAL > 0
    )

    # A startup pre-warm creates the process without sending a user turn, so
    # _get_or_spawn reports it as existing later. first_turn is the reliable
    # signal that the full body has not actually reached the model yet.
    first_real_turn = bool(getattr(session, "first_turn", False))
    if is_fresh or first_real_turn or not ration_fable:
        session.turns_since_identity_breath = 0
        return True

    if session.turns_since_identity_breath >= FABLE_IDENTITY_BREATH_INTERVAL:
        session.turns_since_identity_breath = 0
        return True

    session.turns_since_identity_breath += 1
    return False


def _needs_cold_context(session: ClaudeSession, *, is_fresh: bool) -> bool:
    """True until the process has received its first real conversation turn.

    Startup pre-warm only boots the CLI and its tools; it does not replay DB
    history. Treating that process as warm would strand the first post-restart
    message without the very context pre-warming is meant to make faster.
    """
    return is_fresh or bool(getattr(session, "first_turn", False))


async def stream_claude(
    message: str,
    identity: str,
    conversation_id: str,
    resume_session: str | None = None,  # accepted for back-compat, ignored
    model: str | None = None,
    skill_context: str = "",
    permission_mode: str | None = None,
    cancel_event: asyncio.Event | None = None,
    effort: str | None = None,
    *,
    orientation_context: str = "",
    mode_rules: str = "",
    db_messages: list[dict] | None = None,
    image_blocks: list[dict] | None = None,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
    turn_source: str = _DEFAULT_TURN_SOURCE,
) -> AsyncIterator[dict]:
    """Run one chat turn against the persistent supervisor for this identity.

    The supervisor either finds an existing process or spawns one. The first
    message after a fresh spawn carries the full identity prompt and recent
    history; subsequent turns send only the dynamic per-turn context.

    turn_source (#30): "web" > "platform" > "autowake" priority for the
    shared per-session turn_lock. Defaults to "web" (matching the Owner
    default of sender_banner above) — non-web callers (platform_bridge,
    autowake, brother_conversation, pack_night, discord_mentions_bridge)
    pass their own tier explicitly.

    The function yields events matching the existing pipeline contract.
    """
    effective_model = model or CLAUDE_MODEL
    effective_permission_mode = permission_mode or CLAUDE_PERMISSION_MODE
    yield {
        "type": "meta",
        "provider": "claude-code",
        "requested_model": effective_model,
    }

    session, is_fresh = await _get_or_spawn(
        identity=identity,
        conversation_id=conversation_id,
        model=effective_model,
        permission_mode=effective_permission_mode,
        effort=effort,
        turn_source=turn_source,
    )


    needs_cold_context = _needs_cold_context(session, is_fresh=is_fresh)
    if needs_cold_context:
        # Replay history from DB if caller didn't pass it explicitly.
        history_limit, history_chars = cli_cold_history_limits(effective_model)
        history_block = _format_history(
            db_messages,
            identity,
            per_message_chars=history_chars,
            message_limit=history_limit,
        )
        if not history_block:
            history_block = await _load_recent_history(
                conversation_id,
                identity,
                limit=history_limit,
                per_message_chars=history_chars,
            )
    else:
        history_block = ""
    # Pull the identity prompt only when this turn actually includes it —
    # rationed lean turns skip the disk hit entirely. The loader caches by
    # mtime, so even included turns rarely touch the filesystem.
    if _should_include_identity_prompt(
        session,
        identity=identity,
        model=effective_model,
        is_fresh=needs_cold_context,
    ):
        identity_prompt = _load_identity_prompt(identity)
    else:
        identity_prompt = ""
    composed = _build_first_message(
        identity=identity,
        user_message=message,
        orientation_context=orientation_context,
        mode_rules=mode_rules,
        skill_context=skill_context,
        image_blocks=image_blocks,
        history_block=history_block,
        identity_prompt=identity_prompt,
        sender_banner=sender_banner,
    )

    # Priority turn gate (#30): if a lower-priority turn is already holding
    # the lock, nudge it to wind down early instead of waiting in FIFO order.
    _maybe_preempt_lower_priority_turn(session, turn_source)


    if turn_source != "autowake" and session.max_turns < CLAUDE_MAX_TURNS:
        log.info(
            "raising max_turns %d -> %d for %s turn on %s/%s",
            session.max_turns, CLAUDE_MAX_TURNS, turn_source,
            session.identity, session.conversation_id[:8],
        )
        session.max_turns = CLAUDE_MAX_TURNS

    # Acquire turn lock — only one turn at a time per session.
    async with session.turn_lock:
        session.active_turn_source = turn_source
        try:
            async for evt in _run_one_turn(
                session=session,
                composed_message=composed,
                identity=identity,
                cancel_event=cancel_event,
                effective_permission_mode=effective_permission_mode,
            ):
                yield evt
            session.first_turn = False
            # Stamp completion so the idle reaper measures from turn END, not
            # the turn's start (write_user_message set last_activity when we
            # began).
            session.last_activity = time.time()
        finally:
            session.active_turn_source = None


async def _run_one_turn(
    *,
    session: ClaudeSession,
    composed_message: str,
    identity: str,
    cancel_event: asyncio.Event | None,
    effective_permission_mode: str,
) -> AsyncIterator[dict]:
    """Inner generator: writes the user message, drains events until `result`."""
    loop = asyncio.get_running_loop()
    full_text: list[str] = []
    sent_stream_end = False
    cancelled = False
    # Soft-interrupt state: set once write_interrupt() has been sent so we
    # never re-send it every poll tick, and so we can measure the grace
    # window from the moment it actually went out.
    interrupt_sent = False
    interrupt_sent_at = 0.0
    first_event_logged = False
    spawn_started = session.spawn_started or time.time()


    last_client_time = loop.time()

    # Drain any stale events left on the queue (defensive — should be empty).
    while not session.line_queue.empty():
        try:
            session.line_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    # Write the user message. Done in an executor since stdin.write/flush
    # can block on Windows if the pipe buffer fills.
    try:
        await loop.run_in_executor(None, session.write_user_message, composed_message)
    except RuntimeError as e:
        log.warning("Failed to write to Claude session %s/%s: %s",
                    identity, session.conversation_id[:8], e)
        if session.pool_key is not None:
            _retire_session(session.pool_key)
        yield {"type": "error", "message": f"Claude session unavailable: {e}"}
        yield {"type": "stream_end", "full_content": "", "session_id": None}
        return

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                if not interrupt_sent:


                    try:
                        await loop.run_in_executor(None, session.write_interrupt)
                    except RuntimeError as e:
                        log.warning(
                            "Soft interrupt failed for %s/%s (%s); "
                            "falling back to hard cancel",
                            identity, session.conversation_id[:8], e,
                        )
                        cancelled = True
                        break
                    interrupt_sent = True
                    interrupt_sent_at = loop.time()
                    log.info(
                        "Sent soft interrupt for %s/%s; draining for up to "
                        "%.0fs for the CLI's result event",
                        identity, session.conversation_id[:8], _INTERRUPT_TIMEOUT,
                    )
                    # Fall through — keep draining stdout below exactly like a
                    # normal turn. A graceful interrupt still ends with the
                    # CLI's own `result` event, which will set sent_stream_end
                    # and break the loop WITHOUT ever setting `cancelled`.
                elif loop.time() - interrupt_sent_at >= _INTERRUPT_TIMEOUT:
                    log.warning(
                        "Soft interrupt for %s/%s did not resolve within "
                        "%.0fs; falling back to hard cancel",
                        identity, session.conversation_id[:8], _INTERRUPT_TIMEOUT,
                    )
                    cancelled = True
                    break
                # else: interrupt already sent and still within the grace
                # window — keep polling stdout below instead of tearing the
                # process down.

            # Client-silence keepalive. Checked every iteration (not just on the
            # queue-timeout branch) so it still fires while the subprocess is
            # streaming filtered thinking deltas — otherwise a long max-effort
            # turn shows the browser nothing for minutes and the socket drops.
            now = loop.time()
            if now - last_client_time >= _KEEPALIVE_INTERVAL:
                yield {"type": "keepalive"}
                last_client_time = now

            try:
                raw_line = await asyncio.wait_for(
                    session.line_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                if session.dead or (session.proc and session.proc.poll() is not None):
                    log.warning(
                        "Claude session for %s/%s died mid-turn (exit=%s)",
                        identity, session.conversation_id[:8],
                        session.proc.poll() if session.proc else None,
                    )
                    break
                # Keepalive is handled at the top of the loop off last_client_time.
                continue

            if raw_line is None:
                log.warning("Claude session for %s/%s closed stdout",
                            identity, session.conversation_id[:8])
                session.dead = True
                break

            if isinstance(raw_line, Exception):
                yield {"type": "error", "message": str(raw_line)}
                break

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.debug("Non-JSON line from Claude: %s", line[:200])
                continue

            if not first_event_logged:
                first_event_logged = True
                first_event_ms = (time.time() - spawn_started) * 1000
                yield {"type": "meta", "first_event_ms": first_event_ms}

            async for parsed in _parse_event(event, session, identity, full_text):
                if parsed.get("type") == "stream_end":
                    sent_stream_end = True
                    if interrupt_sent:


                        parsed["cancelled"] = True
                yield parsed
                # Real client-visible output resets the keepalive clock.
                last_client_time = loop.time()

            if sent_stream_end:
                break

    finally:
        if cancelled:
            # Hard-kill fallback ONLY: we get here either because
            # write_interrupt() itself failed, or because the CLI never
            # answered the soft interrupt within _INTERRUPT_TIMEOUT. A
            # graceful interrupt (the common case) ends the turn via the
            # CLI's own `result` event further up and never sets `cancelled`,
            # so the process — and its warm context — survives. This branch
            # kills the process so the next turn starts cold, same as before
            # soft interrupt existed.
            log.info("Retiring session for %s/%s after user cancel",
                     identity, session.conversation_id[:8])
            if session.pool_key is not None:
                _retire_session(session.pool_key)

        elif session.dead:

            stderr_text = session.get_stderr_text()
            if stderr_text:
                log.warning("Claude session %s/%s stderr tail: %s",
                            identity, session.conversation_id[:8],
                            stderr_text[-2000:])
            if session.pool_key is not None:
                _retire_session(session.pool_key)

        if cancelled and not sent_stream_end:
            yield {
                "type": "stream_end",
                "full_content": "".join(full_text),
                "session_id": session.session_id,
                "cancelled": True,
            }
            sent_stream_end = True
        elif not sent_stream_end:
            # Process died mid-turn without a result event. Emit whatever we
            # captured plus an error so the UI doesn't hang.
            content = "".join(full_text)
            stderr_text = session.get_stderr_text()
            err_detail = "Claude session ended unexpectedly"
            if stderr_text:
                err_detail += f"\n{stderr_text[-500:]}"
            _maybe_mark_fable_limited(err_detail, session)
            lowered = err_detail.lower()
            if any(kw in lowered for kw in _APPROVAL_KEYWORDS):
                approval_evt = {
                    "type": "approval_required",
                    "provider": "claude-code",
                    "backend": "subprocess",
                    "message": err_detail,
                    "permission_mode": effective_permission_mode,
                }
                _annotate_sensitive_path(approval_evt, err_detail)
                yield approval_evt
            else:
                yield {"type": "error", "message": err_detail}
            yield {
                "type": "stream_end",
                "full_content": content,
                "session_id": session.session_id,
            }


def _record_turn_usage(event: dict, session: ClaudeSession, identity: str) -> None:
    """Fire-and-forget insert of a result event's token usage.

    The CLI's `result` event carries usage counters + a cost estimate that we
    used to throw away — now they feed the Settings Hub Usage panel. Strictly
    best-effort: any failure is swallowed so the meter can never break a turn.
    """
    usage = event.get("usage") or {}
    cost = event.get("total_cost_usd")
    if not usage and cost is None:
        return
    # `total_cost_usd` is the session-lifetime running total on every resumed
    # turn. Store only what THIS turn added; a drop means the CLI started a
    # fresh session, so take the value whole.
    if cost is not None:
        prev = getattr(session, "last_cost_usd", 0.0) or 0.0
        session.last_cost_usd = cost
        cost = cost - prev if cost >= prev else cost
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from services.usage_tracker import record_usage
    loop.create_task(record_usage(
        identity=identity,
        conversation_id=session.conversation_id,
        model=session.model,
        usage=usage,
        cost_usd=cost,
        num_turns=event.get("num_turns"),
        duration_ms=event.get("duration_ms"),
    ))


def _capture_mcp_status(event: dict, session: ClaudeSession) -> None:
    """Stash the init event's MCP server roster on the session.

    The CLI's first `system`/`init` NDJSON event lists every MCP server's
    connection status plus the flat tool roster (`mcp__<server>__<tool>`
    names). We used to throw this away — now per-server status + tool counts
    ride on the session so sessions_snapshot() can surface "qualia-backend ✓
    (54 tools)" in the Settings Hub System panel instead of leaving Task
    Manager guesswork about which servers actually booted. Best-effort: a
    malformed event just leaves the previous roster in place.
    """
    servers = event.get("mcp_servers")
    if not isinstance(servers, list):
        return
    tool_counts: dict[str, int] = {}
    for tool in event.get("tools") or []:
        if isinstance(tool, str) and tool.startswith("mcp__"):
            parts = tool.split("__", 2)
            if len(parts) == 3 and parts[1]:
                tool_counts[parts[1]] = tool_counts.get(parts[1], 0) + 1
    roster: list[dict] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        name = str(server.get("name") or "")
        if not name:
            continue
        roster.append({
            "name": name,
            "status": str(server.get("status") or "unknown"),
            "tool_count": tool_counts.get(name, 0),
        })
    if roster:
        session.mcp_servers = roster


async def _parse_event(
    event: dict,
    session: ClaudeSession,
    identity: str,
    full_text: list[str],
) -> AsyncIterator[dict]:
    """Convert one parsed NDJSON event into 0+ pipeline events."""
    event_type = event.get("type", "")

    # ── system events — capture session_id + MCP roster, swallow the rest ──
    if event_type == "system":
        if "session_id" in event and not session.session_id:
            session.session_id = event["session_id"]
        if event.get("subtype") == "init":
            _capture_mcp_status(event, session)
        elif event.get("subtype") == "compact_boundary":
            # #18 compaction hygiene: the CLI auto-compacted this session's
            # context. `full_text` (this turn's dedup accumulator, used by
            # the assistant-message text-recovery check above) can no
            # longer be trusted against what the model now holds, so it's
            # cleared; `stream_reset` mirrors that at the pipeline
            # accumulator level (same event openai_provider.py already uses
            # to discard pre-tool narration); the chat notice is a small
            # honest toast, not a hard interruption — the turn continues.
            full_text.clear()
            log.info("Compact boundary hit for %s (session %s)", identity, session.session_id)
            yield {"type": "stream_reset"}
            yield {
                "type": "compaction_notice",
                "message": "Context was compacted to make room — continuing.",
            }
        return

    # ── stream_event wrapper — real-time deltas ──
    if event_type == "stream_event":
        if "session_id" in event and not session.session_id:
            session.session_id = event["session_id"]
        inner = event.get("event", {})
        inner_type = inner.get("type", "")

        if inner_type == "content_block_delta":
            delta = inner.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                full_text.append(text)
                yield {"type": "stream_delta", "delta": text}
            elif delta_type == "thinking_delta":
                thinking_text = delta.get("thinking", "")


                if thinking_text and thinking_text.strip():
                    yield {"type": "thinking_delta", "delta": thinking_text}
            elif delta_type == "input_json_delta":
                pass  # tool input streams via assistant event later

        elif inner_type == "content_block_start":
            cb = inner.get("content_block", {})
            cb_type = cb.get("type", "")
            if cb_type == "tool_use":
                yield {
                    "type": "tool_use_start",
                    "tool_name": cb.get("name", "unknown"),
                    "tool_id": cb.get("id", ""),
                    "input": cb.get("input", {}),
                }
            elif cb_type == "thinking":
                yield {"type": "thinking_start"}

        elif inner_type == "content_block_stop":
            yield {"type": "content_block_stop"}
        return

    # ── assistant message — fully resolved tool inputs + any un-streamed text ──
    if event_type == "assistant":
        msg = event.get("message", {})
        if msg.get("model") and msg.get("model") != "<synthetic>":
            yield {"type": "meta", "actual_model": msg["model"]}
        for block in msg.get("content", []):
            btype = block.get("type")
            if btype == "tool_use":
                yield {
                    "type": "tool_input",
                    "tool_id": block.get("id", ""),
                    "tool_name": block.get("name", "unknown"),
                    "input": block.get("input", {}),
                }
            elif btype == "text":


                text = block.get("text", "")
                if text and text not in "".join(full_text):
                    full_text.append(text)
                    yield {"type": "stream_delta", "delta": text}
        return

    # ── user message — tool results ──
    if event_type == "user":
        msg = event.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") != "tool_result":
                continue
            content = block.get("content", "")
            content_text = content if isinstance(content, str) else ""
            # Cap the regex scan to the head of the result: media paths the
            # extractors care about appear early and are short, and huge tool
            # results (multi-MB reads) shouldn't get a full-body regex pass
            # when the UI only ever shows the first 500 chars anyway.
            scan_text = content_text[:4000]
            images = _extract_image_paths(scan_text, identity=identity)
            documents = _extract_document_paths(scan_text, identity=identity)
            if isinstance(content, str) and len(content) > 500:
                content = content[:500] + "..."
            result_event = {
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": content,
            }
            if images:
                result_event["images"] = images
            if documents:
                result_event["documents"] = documents
            yield result_event

            if block.get("is_error"):
                lowered_tr = content_text.lower()
                if any(kw in lowered_tr for kw in _APPROVAL_KEYWORDS):
                    approval_evt = {
                        "type": "approval_required",
                        "provider": "claude-code",
                        "backend": "subprocess",
                        "message": content_text[:500],
                        "permission_mode": CLAUDE_PERMISSION_MODE,
                    }
                    rule = _infer_suggested_rule(content_text)
                    if rule:
                        approval_evt["suggested_rule"] = rule
                    _annotate_sensitive_path(approval_evt, content_text)
                    yield approval_evt
        return

    # ── result — turn complete ──
    if event_type == "result":
        _record_turn_usage(event, session, identity)
        if event.get("is_error"):
            errors = event.get("errors") or []
            result_field = event.get("result") or ""
            subtype = event.get("subtype") or ""
            if errors:
                error_text = "; ".join(errors)
            elif result_field:
                error_text = result_field
            elif subtype:
                error_text = f"CLI error: {subtype}"
            else:
                error_text = "Unknown CLI error"
            log.warning("Claude turn error for %s: %s", identity, error_text)
            _maybe_mark_fable_limited(error_text, session)

            lowered = error_text.lower()
            if any(kw in lowered for kw in _APPROVAL_KEYWORDS):
                evt = {
                    "type": "approval_required",
                    "provider": "claude-code",
                    "backend": "subprocess",
                    "message": error_text,
                    "permission_mode": CLAUDE_PERMISSION_MODE,
                }
                rule = _infer_suggested_rule(error_text)
                if rule:
                    evt["suggested_rule"] = rule
                _annotate_sensitive_path(evt, error_text)
                yield evt
            else:
                yield {"type": "error", "message": error_text}


            streamed = "".join(full_text)
            if streamed.strip() == (error_text or "").strip():
                streamed = ""
            yield {
                "type": "stream_end",
                "full_content": streamed,
                "session_id": session.session_id,
            }
            return

        if "session_id" in event and not session.session_id:
            session.session_id = event["session_id"]
        result_text = event.get("result", "")
        yield {
            "type": "stream_end",
            "full_content": result_text or "".join(full_text),
            "session_id": session.session_id,
        }
        return

    log.debug("Unhandled event type: %s", event_type)
