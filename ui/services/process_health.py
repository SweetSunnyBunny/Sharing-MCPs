"""Read-only process diagnostics for logs and health endpoints."""

# ANAM GUIDE: SERVER HEALTH SNAPSHOT
# What: Takes a quick reading of the running server — memory used, uptime, thread count — for logs and the health/diagnostics endpoints.
# Called by: core/lifespan.py (periodic logging) and server.py (health endpoint).
# Edit here when: adding another number to the health readout.

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from typing import Any


log = logging.getLogger("anam.process_health")
_started = time.monotonic()
_last: dict[str, Any] = {}


def collect_process_health(*, emit_log: bool = False) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "pid": os.getpid(),
        "uptime_seconds": round(time.monotonic() - _started, 1),
        "threads": threading.active_count(),
        "gc_counts": list(gc.get_count()),
    }
    try:
        import psutil

        process = psutil.Process()
        memory = process.memory_info()
        snapshot.update(
            {
                "rss_mb": round(memory.rss / 1024 / 1024, 1),
                "vms_mb": round(memory.vms / 1024 / 1024, 1),
                "handles": process.num_handles() if hasattr(process, "num_handles") else None,
            }
        )
    except Exception as exc:
        snapshot["process_metrics"] = f"unavailable: {type(exc).__name__}"

    _last.clear()
    _last.update(snapshot)
    if emit_log:
        log.info("Process health: %s", snapshot)
    return snapshot


def get_process_health() -> dict[str, Any]:
    return dict(_last) if _last else collect_process_health()
