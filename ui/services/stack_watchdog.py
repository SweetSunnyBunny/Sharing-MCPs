"""Optional watchdog for explicitly configured local services.

ANAM_WATCHDOG_CONFIG points to a JSON list of service definitions. Without
it no process checks or restarts are attempted. Restart failures produce UI notices.
"""

# ANAM GUIDE: SERVICE STACK WATCHDOG
# What: Checks configured local services and reports restart outcomes to the UI.
# Called by: core/lifespan.py (started with the server)
# Edit here when: Changing watchdog checks, restart limits, or notice delivery.

from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger(__name__)

_WATCHDOG_LOG_DIR = DATA_DIR / "logs" / "watchdog"

# How many auto-restarts a service may consume within the rolling window
# before the watchdog declares it crash-looping and backs off.
_MAX_RESTARTS_PER_WINDOW = 3
_RESTART_WINDOW_SECONDS = 3600.0

# Seconds to wait after a relaunch before re-checking whether it took.
_POST_RESTART_GRACE = 8.0

_PORT_CHECK_TIMEOUT = 1.5

# Windows process-creation flags: detached from Anam (survives our restarts),
# its own process group (our shutdown signals don't propagate into it).
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


@dataclass
class WatchedService:
    name: str            # short id, also the log filename
    label: str           # human-facing name for notices
    args: list[str]      # argv for relaunch
    cwd: str | None      # working directory for relaunch
    ports: tuple[int, ...] = ()    # health = every listed TCP port listens…
    process_name: str | None = None  # …or a running image name (cloudflared)
    via_supervisor: bool = False   # relaunch asks anam_stack.py to own it
    # flap-guard state
    restart_times: list[float] = field(default_factory=list)
    backed_off: bool = False


def _build_services() -> list[WatchedService]:
    """Read an installation-owned service list; no private stack assumptions."""
    filename = os.environ.get("ANAM_WATCHDOG_CONFIG", "").strip()
    if not filename:
        return []
    try:
        rows = json.loads(Path(filename).expanduser().read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("expected a JSON list")
        services = []
        for row in rows:
            name = row["name"]
            args = row["args"]
            ports = tuple(row.get("ports", []))
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError("service names must contain only letters, digits, '_' or '-'")
            if not isinstance(args, list) or not args or not all(isinstance(x, str) and x for x in args):
                raise ValueError("args must be a nonempty list of strings")
            if not all(isinstance(p, int) and 1 <= p <= 65535 for p in ports):
                raise ValueError("ports must be valid TCP port numbers")
            services.append(WatchedService(
                name=name, label=row.get("label", name), args=args,
                cwd=row.get("cwd"), ports=ports,
                process_name=row.get("process_name"),
                via_supervisor=bool(row.get("via_supervisor", True)),
            ))
        return services
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("Watchdog configuration was not loaded: %s", exc)
        return []


_services: list[WatchedService] = _build_services()


# ── Health checks ────────────────────────────────────────────────────────────

def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PORT_CHECK_TIMEOUT):
            return True
    except OSError:
        return False


def _process_is_running(image_name: str) -> bool:
    """Check for a running process by image name via tasklist (no deps)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return image_name.lower() in (out.stdout or "").lower()
    except Exception as exc:
        log.warning("Watchdog: tasklist check for %s failed: %s", image_name, exc)
        return True  # can't verify — assume alive rather than restart blindly


def _is_healthy(svc: WatchedService) -> bool:
    if svc.ports:
        return all(_port_is_listening(port) for port in svc.ports)
    if svc.process_name is not None:
        return _process_is_running(svc.process_name)
    return True


# ── Relaunch ─────────────────────────────────────────────────────────────────

def _relaunch(svc: WatchedService) -> bool:
    """Ask the owner to restart; never create an unowned replacement."""
    _WATCHDOG_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _WATCHDOG_LOG_DIR / f"{svc.name}.log"
    try:
        with open(log_path, "ab") as lf:
            lf.write(
                f"\n--- watchdog relaunch {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
                .encode()
            )
            if svc.via_supervisor:
                result = subprocess.run(
                    svc.args,
                    cwd=svc.cwd,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=15,
                    creationflags=0x08000000,
                )
                return result.returncode == 0
            subprocess.Popen(
                svc.args,
                cwd=svc.cwd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_DETACHED,
            )
            return True
    except Exception as exc:
        log.error("Watchdog: relaunch of %s failed: %s", svc.name, exc)
        return False


# ── Notices ──────────────────────────────────────────────────────────────────

async def _notify(message: str) -> None:
    """Broadcast a system_notice to every open chat. Never raises."""
    try:
        from services.connection_registry import broadcast
        await broadcast({"type": "system_notice", "message": message})
    except Exception as exc:
        log.warning("Watchdog: notice broadcast failed: %s", exc)


# ── The scheduled check ──────────────────────────────────────────────────────

async def stack_watchdog_check() -> None:
    """Check every watched service; relaunch + notify on any that fell."""
    now = time.time()
    for svc in _services:
        try:
            healthy = await asyncio.to_thread(_is_healthy, svc)
        except Exception as exc:
            log.warning("Watchdog: health check for %s errored: %s", svc.name, exc)
            continue

        # Trim restart history to the rolling window; a clean window
        # rehabilitates a previously backed-off service.
        svc.restart_times = [
            t for t in svc.restart_times if now - t < _RESTART_WINDOW_SECONDS
        ]
        if healthy:
            if svc.backed_off and not svc.restart_times:
                svc.backed_off = False
                log.info("Watchdog: %s stayed up — back-off cleared", svc.name)
            continue

        if svc.backed_off:
            continue  # crash-looping; already notified, don't churn

        if len(svc.restart_times) >= _MAX_RESTARTS_PER_WINDOW:
            svc.backed_off = True
            log.error(
                "Watchdog: %s is crash-looping (%d restarts in the last hour) "
                "— backing off. See %s",
                svc.name, len(svc.restart_times),
                _WATCHDOG_LOG_DIR / f"{svc.name}.log",
            )
            await _notify(
                f"🛠️ {svc.label} keeps crashing — I've restarted it "
                f"{len(svc.restart_times)} times this hour and it won't stay "
                f"up, so I've stopped trying. Its log is in "
                f"data/logs/watchdog/{svc.name}.log."
            )
            continue

        log.warning("Watchdog: %s is DOWN — relaunching", svc.name)
        launched = await asyncio.to_thread(_relaunch, svc)
        svc.restart_times.append(now)

        if not launched:
            await _notify(
                f"🛠️ {svc.label} is down and the relaunch attempt FAILED — "
                f"it may need a manual start (start-anam.bat)."
            )
            continue

        await asyncio.sleep(_POST_RESTART_GRACE)
        recovered = await asyncio.to_thread(_is_healthy, svc)
        if recovered:
            log.info("Watchdog: %s relaunched and healthy", svc.name)
            await _notify(
                f"🛠️ {svc.label} was down — I restarted it and it's "
                f"healthy again."
            )
        else:
            log.warning(
                "Watchdog: %s relaunched but not yet healthy (may still be "
                "booting; next check will confirm)", svc.name,
            )
            await _notify(
                f"🛠️ {svc.label} was down — I restarted it, but it hasn't "
                f"come healthy yet. I'll keep watching."
            )
