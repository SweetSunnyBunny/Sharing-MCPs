"""Nightly online backup of anam.db.

The database is the single file holding every conversation Owner has ever
had with the pack. This service uses SQLite's online backup API,
which safely copies a live database while
the server is still reading and writing it.

Schedule: nightly at 03:30 via APScheduler (registered in core/lifespan.py),
plus a catch-up backup at startup when the newest copy is older than 24h —
covering the machine being asleep or the server being down at 3am.
"""

# ANAM GUIDE: NIGHTLY DATABASE BACKUP
# What: Safely refreshes one recovery copy of anam.db in the Vault each night at 3:30am.
# Called by: core/lifespan.py — registers the 3:30am schedule and runs a catch-up backup at server startup if the newest copy is over a day old.
# Edit here when: changing backup timing, where backups go, or how many copies to keep (those live in config.py as DB_BACKUP_DIR / DB_BACKUP_KEEP).

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from config import DB_PATH, DB_BACKUP_DIR, DB_BACKUP_KEEP

log = logging.getLogger(__name__)


def _backup_sync() -> str:
    """Blocking copy + prune; runs in a thread via run_in_executor."""
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = DB_BACKUP_DIR / ("anam-latest.db" if DB_BACKUP_KEEP == 1 else f"anam-{ts}.db")
    fd, name = tempfile.mkstemp(prefix=".anam-backup-", suffix=".tmp", dir=DB_BACKUP_DIR)
    os.close(fd)
    temporary = Path(name)
    try:
        # Read-only mode refuses a missing source instead of silently creating
        # an empty database and replacing the last good recovery copy with it.
        with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            src.execute("PRAGMA busy_timeout=10000")
            with closing(sqlite3.connect(str(temporary))) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Database backup failed its integrity check")
        # Publish only a complete, checked snapshot. A failed copy leaves the
        # previous recovery file in place; OneDrive never sees a partial .db.
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)

    # Keep this verified snapshot first, then any explicitly requested history.
    older = sorted(
        (p for p in DB_BACKUP_DIR.glob("anam-*.db") if p != dest),
        key=lambda p: p.stat().st_mtime_ns, reverse=True,
    )
    for old in older[DB_BACKUP_KEEP - 1:]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("Could not prune old DB backup %s: %s", old.name, exc)

    return str(dest)


async def backup_database() -> str | None:
    """Copy the live DB into the backup dir; logs and returns None on failure."""
    try:
        loop = asyncio.get_running_loop()
        dest = await loop.run_in_executor(None, _backup_sync)
        log.info("anam.db backed up to %s", dest)
        return dest
    except Exception:
        log.exception("anam.db backup FAILED")
        return None


async def backup_if_stale(max_age_hours: float = 24.0) -> None:
    """Startup catch-up: back up now if the newest copy is older than max_age."""
    try:
        newest = max(
            (p.stat().st_mtime for p in DB_BACKUP_DIR.glob("anam-*.db")),
            default=0.0,
        )
    except OSError:
        newest = 0.0
    if time.time() - newest >= max_age_hours * 3600:
        log.info("No fresh anam.db backup found — taking a catch-up copy now")
        await backup_database()
