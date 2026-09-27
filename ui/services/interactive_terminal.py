"""Anam-owned interactive processes with bounded output and explicit sessions."""
from __future__ import annotations

import atexit
import base64
import os
from pathlib import Path
import re
import select
import sys
import threading
import time
import uuid

MAX_SESSIONS = 16
MAX_OUTPUT = 1_000_000
RETENTION = 3600
_sessions = {}
_lock = threading.RLock()
_ansi = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def _terminal_controls(session, text):
    """Answer ConPTY's startup query and track its negotiated input mode."""
    pending = session.get("control_tail", "") + text
    for match in re.finditer(r"\x1b\[([0-?]*)([ -/]*)([@-~])", pending):
        parameters, intermediates, final = match.groups()
        if not intermediates and parameters in {"", "0"} and final == "c":
            session["process"].write("\x1b[?1;2c")
        elif parameters == "?9001" and final in {"h", "l"}:
            session["win32_input"] = final == "h"
    # Only preserve an incomplete CSI, so split queries work without replying twice.
    tail = re.search(r"\x1b(?:\[[0-?]*[ -/]*)?$", pending)
    session["control_tail"] = tail.group()[-128:] if tail else ""


def _get(session_id):
    with _lock:
        session = _sessions.get(session_id)
    if session is None:
        raise ValueError("Unknown interactive session; sessions do not survive an Anam restart")
    return session


def _reader(session):
    proc = session["process"]
    quiet_since = None
    try:
        while not session["closed"]:
            readable, _, _ = select.select([proc.fileobj], [], [], .1)
            if readable:
                try:
                    text = proc.read(4096)
                except EOFError:
                    break
                if text:
                    with session["lock"]:
                        _terminal_controls(session, text)
                        session["output"] += text
                        excess = max(0, len(session["output"]) - MAX_OUTPUT)
                        if excess:
                            session["output"] = session["output"][excess:]
                            session["base"] += excess
                    quiet_since = None
            elif not proc.isalive():
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > .5:
                    break
    except (OSError, ValueError):
        if not session["closed"]:
            session["reader_error"] = "Terminal output stream closed unexpectedly"
    finally:
        session["finished"] = True
        session["finished_at"] = time.monotonic()


def start(command: str, cwd: str, shell: str = "powershell"):
    if not isinstance(command, str) or not command.strip() or len(command) > 32000:
        raise ValueError("Provide a command of 1 to 32,000 characters")
    directory = Path(cwd).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("cwd must be an existing directory")
    if os.name != "nt":
        raise ValueError("This interactive terminal adapter requires Windows ConPTY")
    if shell not in {"powershell", "cmd"}:
        raise ValueError("shell must be powershell or cmd")
    from winpty import PtyProcess
    with _lock:
        for key, old in list(_sessions.items()):
            if old["finished"] and time.monotonic() - old.get("finished_at", 0) > RETENTION:
                close(key)
        if len(_sessions) >= MAX_SESSIONS:
            raise ValueError("Interactive session capacity reached; close an old session")
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        host = Path(__file__).resolve().parents[1] / "scripts" / "anam_terminal_host.py"
        proc = PtyProcess.spawn([sys.executable, str(host), shell, encoded], cwd=str(directory), dimensions=(30, 120))
        session_id = uuid.uuid4().hex
        session = {"process": proc, "output": "", "base": 0, "lock": threading.RLock(),
                   "closed": False, "finished": False, "reader_error": None}
        _sessions[session_id] = session
        threading.Thread(target=_reader, args=(session,), daemon=True, name="anam-pty-" + session_id[:8]).start()
    return read(session_id, wait_ms=100)


def read(session_id: str, cursor: int = 0, max_chars: int = 24000, wait_ms: int = 0):
    if type(cursor) is not int or cursor < 0 or not 1 <= max_chars <= 50000 or not 0 <= wait_ms <= 10000:
        raise ValueError("Invalid cursor, output size or wait duration")
    session = _get(session_id)
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline and not session["finished"]:
        if session["base"] + len(session["output"]) > cursor:
            break
        time.sleep(.05)
    with session["lock"]:
        base, output = session["base"], session["output"]
        end = base + len(output)
        if cursor > end:
            raise ValueError("Cursor is beyond this session's output")
        position = max(cursor, base)
        text = output[position - base:position - base + max_chars]
        proc = session["process"]
        alive = not session["closed"] and proc.isalive()
        return {"session_id": session_id, "status": "running" if alive else "closed" if session["closed"] else "exited",
                "output": _ansi.sub("", text), "next_cursor": position + len(text),
                "output_truncated": cursor < base, "has_more": position + len(text) < end,
                "output_complete": session["finished"], "exit_code": None if alive else proc.exitstatus,
                "error": session["reader_error"]}


def write(session_id: str, text: str, enter: bool = False):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError("Input must be at most 16,000 characters")
    session = _get(session_id)
    with session["lock"]:
        if session["closed"] or not session["process"].isalive():
            raise ValueError("The process has exited; input was not sent")
        session["process"].write(text + ("\r" if enter else ""))
    return {"session_id": session_id, "input_sent": True}


def interrupt(session_id: str):
    session = _get(session_id)
    with session["lock"]:
        if session["closed"] or not session["process"].isalive():
            return {"session_id": session_id, "interrupt_sent": False, "reason": "Process already exited"}
        if session.get("win32_input"):
            # Win32-input-mode: Vk=C, Sc=C, Unicode=ETX, left Ctrl held.
            session["process"].write("\x1b[67;46;3;1;8;1_\x1b[67;46;3;0;8;1_")
        else:
            session["process"].sendintr()
    return {"session_id": session_id, "interrupt_sent": True,
            "hint": "Ctrl+C was sent; read the session to verify whether the process stopped"}


def close(session_id: str):
    session = _get(session_id)
    with session["lock"]:
        if session["closed"]:
            return {"session_id": session_id, "closed": True}
        proc = session["process"]
        session["closed"] = True
        # Descendants belong to the process this adapter spawned. Keep the
        # operation scoped to this session, never a process-name based kill.
        if proc.isalive():
            import psutil
            try:
                owner = psutil.Process(proc.pid)
                children = owner.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                owner.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            proc.close(force=True)
        except (OSError, EOFError):
            pass
        session["finished"] = True
        session["finished_at"] = time.monotonic()
    with _lock:
        _sessions.pop(session_id, None)
    return {"session_id": session_id, "closed": True}


def close_all():
    for session_id in list(_sessions):
        try:
            close(session_id)
        except Exception:
            pass


atexit.register(close_all)
