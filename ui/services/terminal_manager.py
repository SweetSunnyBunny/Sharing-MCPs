"""Stateful terminal sessions with streaming output.

Adapted from terminal_server.py — no FastMCP dependency.
Provides persistent shell sessions (CWD, env vars, aliases survive across commands)
with an optional streaming callback for real-time output to the browser UI.
"""

# ANAM GUIDE: PERSISTENT TERMINAL SESSIONS
# What: Lets the boys run shell commands in a terminal that remembers its folder and
#       variables between commands, and streams the output live to the browser.
# Called by: services/claude_api.py (the direct-API provider's terminal tools);
#            core/lifespan.py closes any open sessions at shutdown.
# Edit here when: You want to change the shell used, command timeouts, output size
#                 limits, or the friendly "how to fix this error" hint messages.

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SHELL = os.environ.get("TERMINAL_SHELL", "bash")
DEFAULT_CWD = os.environ.get("TERMINAL_DEFAULT_CWD", str(Path(__file__).resolve().parents[1]))
COMMAND_TIMEOUT = int(os.environ.get("TERMINAL_TIMEOUT", "120"))
MAX_OUTPUT_CHARS = int(os.environ.get("TERMINAL_MAX_OUTPUT", "100000"))
MAX_SESSIONS = int(os.environ.get("TERMINAL_MAX_SESSIONS", "10"))

_SENTINEL_PREFIX = "__TERMINAL_MCP_DONE__"

# Common error patterns → recovery suggestions
_ERROR_PATTERNS = [
    (re.compile(r"command not found", re.IGNORECASE),
     "Check the command name or install the missing package."),
    (re.compile(r"Permission denied", re.IGNORECASE),
     "Check file/directory permissions."),
    (re.compile(r"No such file or directory", re.IGNORECASE),
     "Verify the path exists. Use `ls` to check."),
    (re.compile(r"ModuleNotFoundError", re.IGNORECASE),
     "Install the missing Python module: pip install <module>"),
    (re.compile(r"npm ERR!", re.IGNORECASE),
     "Try running `npm install` first, then retry."),
    (re.compile(r"ENOENT", re.IGNORECASE),
     "A required file or directory is missing. Check the path."),
    (re.compile(r"SyntaxError", re.IGNORECASE),
     "There's a syntax error in the code. Check the indicated line."),
    (re.compile(r"Connection refused", re.IGNORECASE),
     "The target service isn't running or is on a different port."),
]


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class TerminalSession:
    """A persistent shell session."""
    id: str
    name: str
    shell: str
    cwd: str
    created_at: float
    last_used: float
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "shell": self.shell,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "alive": self.process is not None and self.process.returncode is None,
        }


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------

class SessionManager:
    """Manages multiple persistent terminal sessions with streaming output."""

    def __init__(self):
        self._sessions: dict[str, TerminalSession] = {}

    async def create(
        self,
        name: str | None = None,
        shell: str | None = None,
        cwd: str | None = None,
    ) -> TerminalSession:
        if len(self._sessions) >= MAX_SESSIONS:
            raise RuntimeError(
                f"Maximum sessions ({MAX_SESSIONS}) reached. "
                "Destroy an existing session first."
            )

        session_id = uuid.uuid4().hex[:12]
        name = name or f"session-{session_id[:6]}"
        shell = shell or DEFAULT_SHELL
        cwd = cwd or DEFAULT_CWD

        cwd_path = Path(cwd)
        if not cwd_path.exists():
            cwd_path = Path(DEFAULT_CWD)
            if not cwd_path.exists():
                cwd_path = Path.home()
            cwd = str(cwd_path)

        now = time.time()
        session = TerminalSession(
            id=session_id,
            name=name,
            shell=shell,
            cwd=cwd,
            created_at=now,
            last_used=now,
        )

        await self._spawn(session)
        self._sessions[session_id] = session
        log.info("Created terminal session %s (%s) in %s", session_id, name, cwd)
        return session

    async def _spawn(self, session: TerminalSession):
        env = os.environ.copy()
        env["PS1"] = "$ "
        env["PROMPT_COMMAND"] = ""
        env["TERM"] = "dumb"

        process = await asyncio.create_subprocess_exec(
            session.shell, "--norc", "--noprofile", "-i",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=session.cwd,
            env=env,
        )
        session.process = process

    async def execute(
        self,
        session_id: str,
        command: str,
        timeout: int | None = None,
        on_output: Callable | None = None,
    ) -> dict:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found")

        if session.process is None or session.process.returncode is not None:
            log.warning("Session %s shell died — respawning", session_id)
            await self._spawn(session)

        timeout = timeout or COMMAND_TIMEOUT

        async with session._lock:
            return await self._run_command(session, command, timeout, on_output)

    async def _run_command(
        self,
        session: TerminalSession,
        command: str,
        timeout: int,
        on_output: Callable | None = None,
    ) -> dict:
        proc = session.process
        sentinel = f"{_SENTINEL_PREFIX}{uuid.uuid4().hex[:8]}"

        wrapped = (
            f"{command}\n"
            f"__ec=$?\n"
            f'echo "{sentinel} $__ec $(pwd)"\n'
        )

        proc.stdin.write(wrapped.encode())
        await proc.stdin.drain()

        output_lines = []
        exit_code = 0
        new_cwd = session.cwd
        timed_out = False

        # Build set of command lines for echo filtering
        cmd_lines = set(command.strip().splitlines())

        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    timed_out = True
                    break

                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if not line:
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")

                # Check for sentinel
                if sentinel in decoded:
                    parts = decoded.split(sentinel, 1)[1].strip().split(" ", 1)
                    if parts:
                        try:
                            exit_code = int(parts[0])
                        except ValueError:
                            exit_code = -1
                        if len(parts) > 1:
                            new_cwd = parts[1].strip()
                    break

                # Filter shell noise before streaming
                stripped = decoded.strip().replace("\r", "")
                if stripped.startswith("$ "):
                    continue
                if stripped.startswith("__ec="):
                    continue
                if _SENTINEL_PREFIX in stripped:
                    continue
                if stripped in cmd_lines:
                    continue
                # Skip leading empty lines
                if not output_lines and not stripped:
                    continue

                clean = decoded.replace("\r", "")
                output_lines.append(clean)

                # Stream callback
                if on_output:
                    try:
                        await on_output(clean)
                    except Exception as e:
                        log.warning("on_output callback error: %s", e)

        except Exception as e:
            output_lines.append(f"\n[Terminal error: {e}]")

        # Update session state
        session.cwd = new_cwd
        session.last_used = time.time()

        # Strip trailing empty lines
        while output_lines and not output_lines[-1].strip():
            output_lines.pop()

        output = "\n".join(output_lines)

        # Truncate if too long
        if len(output) > MAX_OUTPUT_CHARS:
            half = MAX_OUTPUT_CHARS // 2
            output = (
                output[:half]
                + f"\n\n... [{len(output) - MAX_OUTPUT_CHARS} chars truncated] ...\n\n"
                + output[-half:]
            )

        result = {
            "output": output,
            "exit_code": exit_code,
            "cwd": new_cwd,
            "timed_out": timed_out,
        }

        if timed_out:
            result["warning"] = f"Command timed out after {timeout}s. Output may be incomplete."

        return result

    async def destroy(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if not session:
            return False

        if session.process and session.process.returncode is None:
            try:
                session.process.kill()
                await session.process.wait()
            except ProcessLookupError:
                pass

        log.info("Destroyed terminal session %s (%s)", session_id, session.name)
        return True

    async def destroy_all(self):
        for sid in list(self._sessions.keys()):
            await self.destroy(sid)

    def list_sessions(self) -> list[dict]:
        return [s.info() for s in self._sessions.values()]

    def get(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    def get_default(self) -> TerminalSession | None:
        if not self._sessions:
            return None
        return max(self._sessions.values(), key=lambda s: s.last_used)


def suggest_error_recovery(output: str, exit_code: int) -> str | None:
    """Scan output for common error patterns and return a suggestion."""
    if exit_code == 0:
        return None
    for pattern, suggestion in _ERROR_PATTERNS:
        if pattern.search(output):
            return suggestion
    return None


def get_terminal_cwd() -> str | None:
    """Return the default terminal session's CWD, or None."""
    session = terminal_manager.get_default()
    return session.cwd if session else None


# Module-level singleton
terminal_manager = SessionManager()
