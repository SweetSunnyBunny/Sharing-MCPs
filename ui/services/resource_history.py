"""Small durable resource history: once a minute, at most 48 hours.

Counts and memory only: never command lines, prompts, URLs, or tool results.
Collection and SQLite I/O run off the app's event loop.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

import psutil

from config import DATA_DIR

DB_PATH = Path(DATA_DIR) / "runtime" / "resource-history.db"
INTERVAL = 60
MAX_SAMPLES = 2880
MAX_AGE = 48 * 3600
_MAX_PAYLOAD_SIZE = 8000
_TOP_PROCESS_LIMIT = 8
_BYTES_PER_MB = 2**20
_TRACKED_PROCESS_NAMES = (
    "codex.exe",
    "claude.exe",
    "node.exe",
    "python.exe",
    "pythonw.exe",
    "chrome.exe",
    "chatgpt.exe",
    "vmmemwsl",
)

log = logging.getLogger(__name__)


class _Performance(ctypes.Structure):
    """Windows PERFORMANCE_INFORMATION layout; field order is part of its ABI."""

    _fields_ = (
        [("cb", wintypes.DWORD)]
        + [
            (name, ctypes.c_size_t)
            for name in (
                "CommitTotal",
                "CommitLimit",
                "CommitPeak",
                "PhysicalTotal",
                "PhysicalAvailable",
                "SystemCache",
                "KernelTotal",
                "KernelPaged",
                "KernelNonpaged",
                "PageSize",
            )
        ]
        + [
            (name, wintypes.DWORD)
            for name in ("HandleCount", "ProcessCount", "ThreadCount")
        ]
    )


def _commit() -> dict[str, float]:
    """Read Windows committed memory when that counter is available."""
    if os.name != "nt":
        return {}

    info = _Performance()
    info.cb = ctypes.sizeof(info)
    get_performance_info = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
    get_performance_info.argtypes = [ctypes.POINTER(_Performance), wintypes.DWORD]
    get_performance_info.restype = wintypes.BOOL
    if not get_performance_info(ctypes.byref(info), info.cb):
        return {}

    return {
        "commit_mb": round(info.CommitTotal * info.PageSize / _BYTES_PER_MB, 1),
        "commit_limit_mb": round(info.CommitLimit * info.PageSize / _BYTES_PER_MB, 1),
    }


def _is_owned_process(
    row: dict[str, Any],
    processes: dict[int, dict[str, Any]],
    owner_pid: int,
) -> bool:
    """Follow parent links to Anam, stopping at missing, reused or cyclic PIDs."""
    seen = set()
    while row and row["pid"] not in seen:
        if row["pid"] == owner_pid:
            return True
        seen.add(row["pid"])
        parent = processes.get(row["ppid"])
        # A parent born after its child is a reused PID, not an ancestor.
        if parent and parent["create_time"] > row["create_time"]:
            return False
        row = parent
    return False


def _private_bytes(row: dict[str, Any]) -> int:
    """Use private memory on Windows, with virtual memory as the fallback."""
    memory = row["memory_info"]
    return getattr(memory, "private", memory.vms)


def sample(codex_processes=()) -> dict[str, Any]:
    """Collect bounded process metadata and memory totals for one sample."""
    memory = psutil.virtual_memory()
    owner_pid = os.getpid()
    processes = {}
    for process in psutil.process_iter(
        ["pid", "ppid", "name", "create_time", "memory_info"], ad_value=None
    ):
        row = process.info
        if row["create_time"] is not None and row["memory_info"] is not None:
            processes[row["pid"]] = row

    owned_rows = [
        row
        for row in processes.values()
        if _is_owned_process(row, processes, owner_pid)
    ]
    names = Counter((row["name"] or "unknown").lower() for row in processes.values())
    top_private = sorted(
        processes.values(), key=_private_bytes, reverse=True
    )[:_TOP_PROCESS_LIMIT]
    top_resident = sorted(
        processes.values(), key=lambda row: row["memory_info"].rss, reverse=True
    )[:_TOP_PROCESS_LIMIT]

    return {
        "at": time.time(),
        "boot_at": psutil.boot_time(),
        "anam_pid": owner_pid,
        "memory_percent": memory.percent,
        "available_mb": round(memory.available / _BYTES_PER_MB, 1),
        "physical_total_mb": round(memory.total / _BYTES_PER_MB, 1),
        **_commit(),
        "process_count": len(processes),
        "process_counts": {name: names[name] for name in _TRACKED_PROCESS_NAMES},
        "anam_tree_count": len(owned_rows),
        "anam_tree_private_mb": round(
            sum(_private_bytes(row) for row in owned_rows) / _BYTES_PER_MB, 1
        ),
        "codex_messaging": sum(
            process.get("kind") == "messaging" for process in codex_processes
        ),
        "codex_autowake": sum(
            process.get("kind") == "autowake" for process in codex_processes
        ),
        "top_private_memory": [
            {
                "pid": row["pid"],
                "name": row["name"],
                "private_mb": round(_private_bytes(row) / _BYTES_PER_MB, 1),
            }
            for row in top_private
        ],
        "top_resident_memory": [
            {
                "pid": row["pid"],
                "name": row["name"],
                "resident_mb": round(row["memory_info"].rss / _BYTES_PER_MB, 1),
            }
            for row in top_resident
        ],
    }


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DB_PATH, timeout=5)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA journal_size_limit=1048576")
    database.execute(
        "CREATE TABLE IF NOT EXISTS samples "
        "(id INTEGER PRIMARY KEY, at REAL NOT NULL, payload TEXT NOT NULL)"
    )
    return database


def append(row: dict[str, Any]) -> None:
    """Persist a sample, pruning expired rows and then enforcing the count cap."""
    payload = json.dumps(row, separators=(",", ":"), ensure_ascii=True)
    if len(payload) > _MAX_PAYLOAD_SIZE:
        raise ValueError("Resource sample exceeds size bound")

    database = _connect()
    try:
        with database:
            database.execute(
                "INSERT INTO samples(at,payload) VALUES (?,?)", (row["at"], payload)
            )
            database.execute(
                "DELETE FROM samples WHERE at < ?", (time.time() - MAX_AGE,)
            )
            database.execute(
                "DELETE FROM samples WHERE id NOT IN "
                "(SELECT id FROM samples ORDER BY id DESC LIMIT ?)",
                (MAX_SAMPLES,),
            )
    finally:
        database.close()


def report(limit=120) -> dict[str, Any]:
    """Read the latest bounded slice, returning samples oldest first."""
    limit = max(1, min(int(limit), MAX_SAMPLES))
    database = _connect()
    try:
        total, first, last = database.execute(
            "SELECT count(*),min(at),max(at) FROM samples"
        ).fetchone()
        rows = [
            json.loads(row[0])
            for row in database.execute(
                "SELECT payload FROM samples ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
    finally:
        database.close()

    return {
        "interval_seconds": INTERVAL,
        "retention_hours": 48,
        "sample_count": total,
        "first_at": first,
        "last_at": last,
        "recent": list(reversed(rows)),
    }


async def collect_loop() -> None:
    """Sample off the event loop until shutdown, retrying after transient errors."""
    from services.codex_sessions import status

    while True:
        try:
            state = status()
            row = await asyncio.to_thread(sample, state)
            await asyncio.to_thread(append, row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Resource history sample failed: %s", type(exc).__name__)
        await asyncio.sleep(INTERVAL)
