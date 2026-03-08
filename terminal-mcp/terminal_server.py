"""
Stateful Terminal MCP Server
Persistent shell sessions for Claude API — state (cwd, env vars, aliases)
survives across tool calls within the same session.
"""

import asyncio
import os
import uuid
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastmcp import FastMCP

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SHELL = os.environ.get("TERMINAL_SHELL", "bash")
DEFAULT_CWD = os.environ.get("TERMINAL_DEFAULT_CWD", str(Path.home()))
COMMAND_TIMEOUT = int(os.environ.get("TERMINAL_TIMEOUT", "120"))  # seconds
MAX_OUTPUT_CHARS = int(os.environ.get("TERMINAL_MAX_OUTPUT", "100000"))
MAX_SESSIONS = int(os.environ.get("TERMINAL_MAX_SESSIONS", "10"))

# Sentinel used to detect when a command finishes
_SENTINEL_PREFIX = "__TERMINAL_MCP_DONE__"


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
    """Manages multiple persistent terminal sessions."""

    def __init__(self):
        self._sessions: dict[str, TerminalSession] = {}

    async def create(
        self,
        name: str | None = None,
        shell: str | None = None,
        cwd: str | None = None,
    ) -> TerminalSession:
        """Spawn a new persistent shell session."""
        if len(self._sessions) >= MAX_SESSIONS:
            raise RuntimeError(
                f"Maximum sessions ({MAX_SESSIONS}) reached. "
                "Destroy an existing session first."
            )

        session_id = uuid.uuid4().hex[:12]
        name = name or f"session-{session_id[:6]}"
        shell = shell or DEFAULT_SHELL
        cwd = cwd or DEFAULT_CWD

        # Resolve cwd
        cwd_path = Path(cwd)
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

        # Spawn the shell process
        await self._spawn(session)
        self._sessions[session_id] = session
        log.info("Created session %s (%s) in %s", session_id, name, cwd)
        return session

    async def _spawn(self, session: TerminalSession):
        """Start the underlying shell process."""
        # Build env — inherit current env
        env = os.environ.copy()
        # Disable prompts that could interfere with output parsing
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
    ) -> dict:
        """Execute a command in a session, returning output + exit code."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found")

        # Check if process is still alive; respawn if needed
        if session.process is None or session.process.returncode is not None:
            log.warning("Session %s shell died — respawning", session_id)
            await self._spawn(session)

        timeout = timeout or COMMAND_TIMEOUT

        async with session._lock:
            return await self._run_command(session, command, timeout)

    async def _run_command(
        self,
        session: TerminalSession,
        command: str,
        timeout: int,
    ) -> dict:
        """Send command to shell and collect output until sentinel."""
        proc = session.process
        sentinel = f"{_SENTINEL_PREFIX}{uuid.uuid4().hex[:8]}"

        # Build the wrapped command:
        # 1. Run the actual command
        # 2. Capture exit code
        # 3. Print the cwd for tracking
        # 4. Echo the sentinel with exit code
        wrapped = (
            f"{command}\n"
            f"__ec=$?\n"
            f'echo "{sentinel} $__ec $(pwd)"\n'
        )

        proc.stdin.write(wrapped.encode())
        await proc.stdin.drain()

        # Read output until sentinel appears
        output_lines = []
        exit_code = 0
        new_cwd = session.cwd
        timed_out = False

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
                    # Process exited
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")

                # Check for sentinel
                if sentinel in decoded:
                    # Parse: SENTINEL EXIT_CODE CWD
                    parts = decoded.split(sentinel, 1)[1].strip().split(" ", 1)
                    if parts:
                        try:
                            exit_code = int(parts[0])
                        except ValueError:
                            exit_code = -1
                        if len(parts) > 1:
                            new_cwd = parts[1].strip()
                    break

                output_lines.append(decoded)

        except Exception as e:
            output_lines.append(f"\n[Terminal error: {e}]")

        # Update session state
        session.cwd = new_cwd
        session.last_used = time.time()

        # Filter out shell noise: prompts, echoed commands, internal vars
        # bash -i echoes commands and prints PS1 prompts
        cmd_lines = set(command.strip().splitlines())
        clean_lines = []
        for line in output_lines:
            stripped = line.strip()
            # Remove carriage returns / ANSI escapes
            stripped_clean = stripped.replace("\r", "")
            # Skip prompt lines
            if stripped_clean.startswith("$ "):
                continue
            # Skip our internal bookkeeping
            if stripped_clean.startswith("__ec="):
                continue
            if sentinel in stripped_clean:
                continue
            if _SENTINEL_PREFIX in stripped_clean:
                continue
            # Skip exact echoes of command lines
            if stripped_clean in cmd_lines:
                continue
            # Skip empty lines at the very start
            if not clean_lines and not stripped_clean:
                continue
            clean_lines.append(line.replace("\r", ""))

        # Strip trailing empty lines
        while clean_lines and not clean_lines[-1].strip():
            clean_lines.pop()

        output = "\n".join(clean_lines)

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
        """Kill a session and its shell process."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return False

        if session.process and session.process.returncode is None:
            try:
                session.process.kill()
                await session.process.wait()
            except ProcessLookupError:
                pass

        log.info("Destroyed session %s (%s)", session_id, session.name)
        return True

    async def destroy_all(self):
        """Kill all sessions."""
        for sid in list(self._sessions.keys()):
            await self.destroy(sid)

    def list_sessions(self) -> list[dict]:
        """Return info for all sessions."""
        return [s.info() for s in self._sessions.values()]

    def get(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    def get_default(self) -> TerminalSession | None:
        """Return the most recently used session, if any."""
        if not self._sessions:
            return None
        return max(self._sessions.values(), key=lambda s: s.last_used)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Stateful Terminal",
    instructions=(
        "Persistent terminal sessions for Claude. "
        "Shell state (cwd, env vars, aliases) survives across commands."
    ),
)

manager = SessionManager()


@mcp.tool()
async def terminal_execute(
    command: str,
    session_id: str = "",
    timeout: int = COMMAND_TIMEOUT,
) -> str:
    """Execute a command in a persistent terminal session.

    State (working directory, environment variables, aliases) persists
    between calls within the same session.

    Args:
        command: The shell command to run.
        session_id: Session to run in. If empty, uses the most recent session
                    or creates a new default session automatically.
        timeout: Max seconds to wait for the command (default 120).
    """
    # Auto-create or find session
    if not session_id:
        session = manager.get_default()
        if not session:
            session = await manager.create(name="default")
        session_id = session.id

    try:
        result = await manager.execute(session_id, command, timeout)
    except KeyError as e:
        return f"Error: {e}"

    # Format response
    parts = []
    if result.get("warning"):
        parts.append(f"⚠ {result['warning']}")
    parts.append(result["output"])
    parts.append(f"\n[exit: {result['exit_code']}] [cwd: {result['cwd']}]")

    return "\n".join(parts)


@mcp.tool()
async def terminal_create(
    name: str = "",
    cwd: str = "",
    shell: str = "",
) -> str:
    """Create a new persistent terminal session.

    Args:
        name: Human-friendly name for the session (auto-generated if empty).
        cwd: Starting working directory (defaults to home directory).
        shell: Shell to use (defaults to bash).
    """
    try:
        session = await manager.create(
            name=name or None,
            cwd=cwd or None,
            shell=shell or None,
        )
        info = session.info()
        return (
            f"Created session '{info['name']}'\n"
            f"  ID:    {info['id']}\n"
            f"  Shell: {info['shell']}\n"
            f"  CWD:   {info['cwd']}\n"
            f"  Alive: {info['alive']}"
        )
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool()
async def terminal_list() -> str:
    """List all active terminal sessions."""
    sessions = manager.list_sessions()
    if not sessions:
        return "No active sessions."

    lines = [f"Active sessions ({len(sessions)}):"]
    for s in sessions:
        status = "alive" if s["alive"] else "dead"
        lines.append(
            f"  [{s['id']}] {s['name']} — {s['cwd']} ({status})"
        )
    return "\n".join(lines)


@mcp.tool()
async def terminal_destroy(session_id: str) -> str:
    """Destroy a terminal session and kill its shell process.

    Args:
        session_id: The session ID to destroy.
    """
    if await manager.destroy(session_id):
        return f"Session {session_id} destroyed."
    return f"Session {session_id} not found."


@mcp.tool()
async def terminal_get_info(session_id: str = "") -> str:
    """Get detailed info about a terminal session.

    Args:
        session_id: Session ID to inspect. If empty, shows the default session.
    """
    if not session_id:
        session = manager.get_default()
        if not session:
            return "No active sessions."
    else:
        session = manager.get(session_id)
        if not session:
            return f"Session '{session_id}' not found."

    info = session.info()
    return (
        f"Session: {info['name']}\n"
        f"  ID:       {info['id']}\n"
        f"  Shell:    {info['shell']}\n"
        f"  CWD:      {info['cwd']}\n"
        f"  Created:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info['created_at']))}\n"
        f"  Last used: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info['last_used']))}\n"
        f"  Alive:    {info['alive']}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
