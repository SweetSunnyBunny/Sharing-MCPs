"""Exclusive integration ownership for singleton external bridges/jobs.

Only one Anam process should own MCP/platform/scheduler side effects at a time.
Extra instances stay in standby instead of competing for the same tools.
"""

# ANAM GUIDE: SINGLE-SERVER LOCK
# What: a lock file that makes sure only ONE running Anam owns the Discord/Telegram bridges, MCP servers, and scheduler — a second accidental server stays in standby instead of double-posting.
# Called by: server.py and core/lifespan.py at startup; api/settings.py can show/steal ownership.
# Edit here when: you want to change which integrations are exclusive (that list lives in config.py) or how a stale lock from a dead process gets taken over.

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from config import DATA_DIR, EXCLUSIVE_INTEGRATIONS, TAKEOVER_DUPLICATE_INTEGRATIONS

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

_LOCK_PATH = DATA_DIR / "runtime" / "integration-owner.lock"
_LOCK_FD: int | None = None
_LOCK_METADATA: dict[str, Any] | None = None
_INSTANCE_ID = uuid.uuid4().hex[:12]
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _build_metadata() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "instance_id": _INSTANCE_ID,
        "hostname": socket.gethostname(),
        "acquired_at": time.time(),
    }


def _read_lock_metadata() -> dict[str, Any] | None:
    try:
        raw = _LOCK_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
            kernel32.CloseHandle(process)
            return bool(ok) and exit_code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _looks_like_anam_server_process(pid: int) -> bool:
    if pid <= 0 or psutil is None:
        return False
    try:
        proc = psutil.Process(pid)
        cmdline = [part for part in proc.cmdline() if part]
        joined = " ".join(cmdline).lower()
        cwd = (proc.cwd() or "").lower()
        project_root = str(_PROJECT_ROOT).lower()
        return (
            "server.py" in joined
            and ("anam" in joined or project_root in joined or project_root in cwd)
        )
    except Exception:
        return False


def _terminate_existing_owner(pid: int, timeout_seconds: float = 8.0) -> bool:
    if pid <= 0 or psutil is None:
        return False
    try:
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        proc.terminate()
        gone, alive = psutil.wait_procs(children + [proc], timeout=timeout_seconds)
        if alive:
            for pending in alive:
                try:
                    pending.kill()
                except Exception:
                    pass
            gone, alive = psutil.wait_procs(alive, timeout=3.0)
        if proc in alive:
            return False
        return True
    except Exception:
        return False


def _write_metadata(fd: int, metadata: dict[str, Any]) -> None:
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(metadata).encode("utf-8"))
    os.fsync(fd)


def _try_acquire_once() -> bool:
    global _LOCK_FD, _LOCK_METADATA
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    metadata = _build_metadata()
    try:
        _write_metadata(fd, metadata)
    except Exception:
        os.close(fd)
        try:
            _LOCK_PATH.unlink()
        except OSError:
            pass
        raise
    _LOCK_FD = fd
    _LOCK_METADATA = metadata
    return True


def claim_integration_ownership() -> dict[str, Any]:
    """Attempt to become the singleton owner of external integrations."""
    global _LOCK_METADATA
    if not EXCLUSIVE_INTEGRATIONS:
        _LOCK_METADATA = _build_metadata()
        return {
            "exclusive": False,
            "owner": True,
            "reason": "exclusive_disabled",
            "lock_path": str(_LOCK_PATH),
            "metadata": dict(_LOCK_METADATA),
        }

    if _LOCK_FD is not None and _LOCK_METADATA is not None:
        return {
            "exclusive": True,
            "owner": True,
            "reason": "already_owned",
            "lock_path": str(_LOCK_PATH),
            "metadata": dict(_LOCK_METADATA),
        }

    try:
        _try_acquire_once()
        return {
            "exclusive": True,
            "owner": True,
            "reason": "acquired",
            "lock_path": str(_LOCK_PATH),
            "metadata": dict(_LOCK_METADATA or {}),
        }
    except FileExistsError:
        existing = _read_lock_metadata() or {}
        existing_pid = int(existing.get("pid") or 0)
        if existing_pid and not _pid_is_running(existing_pid):
            try:
                _LOCK_PATH.unlink()
            except OSError:
                existing = _read_lock_metadata() or existing
            else:
                try:
                    _try_acquire_once()
                    return {
                        "exclusive": True,
                        "owner": True,
                        "reason": "reclaimed_stale_lock",
                        "lock_path": str(_LOCK_PATH),
                        "metadata": dict(_LOCK_METADATA or {}),
                    }
                except FileExistsError:
                    existing = _read_lock_metadata() or existing
        elif (
            existing_pid
            and TAKEOVER_DUPLICATE_INTEGRATIONS
            and existing_pid != os.getpid()
            and _looks_like_anam_server_process(existing_pid)
            and _terminate_existing_owner(existing_pid)
        ):
            try:
                _LOCK_PATH.unlink()
            except OSError:
                existing = _read_lock_metadata() or existing
            else:
                try:
                    _try_acquire_once()
                    return {
                        "exclusive": True,
                        "owner": True,
                        "reason": "took_over_existing_instance",
                        "lock_path": str(_LOCK_PATH),
                        "metadata": dict(_LOCK_METADATA or {}),
                    }
                except FileExistsError:
                    existing = _read_lock_metadata() or existing
        return {
            "exclusive": True,
            "owner": False,
            "reason": "owned_by_another_instance",
            "lock_path": str(_LOCK_PATH),
            "metadata": existing,
        }


def release_integration_ownership() -> None:
    """Release ownership if this process currently holds it."""
    global _LOCK_FD, _LOCK_METADATA
    if _LOCK_FD is None:
        _LOCK_METADATA = None
        return

    try:
        os.close(_LOCK_FD)
    except OSError:
        pass
    _LOCK_FD = None

    try:
        existing = _read_lock_metadata()
        if existing and existing.get("instance_id") == (_LOCK_METADATA or {}).get("instance_id"):
            _LOCK_PATH.unlink()
    except OSError:
        pass
    finally:
        _LOCK_METADATA = None


def get_integration_owner_status() -> dict[str, Any]:
    """Return a serializable snapshot of current ownership status."""
    if not EXCLUSIVE_INTEGRATIONS:
        return {
            "exclusive": False,
            "owner": True,
            "reason": "exclusive_disabled",
            "lock_path": str(_LOCK_PATH),
            "metadata": dict(_LOCK_METADATA or _build_metadata()),
        }

    if _LOCK_FD is not None and _LOCK_METADATA is not None:
        return {
            "exclusive": True,
            "owner": True,
            "reason": "owned_by_this_instance",
            "lock_path": str(_LOCK_PATH),
            "metadata": dict(_LOCK_METADATA),
        }

    existing = _read_lock_metadata() or {}
    existing_pid = int(existing.get("pid") or 0)
    if existing_pid and not _pid_is_running(existing_pid):
        return {
            "exclusive": True,
            "owner": False,
            "reason": "stale_lock",
            "lock_path": str(_LOCK_PATH),
            "metadata": existing,
        }
    return {
        "exclusive": True,
        "owner": False,
        "reason": "owned_by_another_instance" if existing else "unclaimed",
        "lock_path": str(_LOCK_PATH),
        "metadata": existing,
    }
