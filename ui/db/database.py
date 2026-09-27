"""Async SQLite connection manager with simple connection pool."""

# ANAM GUIDE: DATABASE CONNECTION POOL
# What: Hands out connections to the one SQLite database file (data/anam.db) and recycles them in a small pool.
# Called by: Almost everything — nearly every api/ route and services/ module does get_db()/release_db() through here.
# Edit here when: You need to tune how SQLite behaves (timeouts, caching, pool size). Table shapes live in db/schema.py, not here.

import asyncio

import aiosqlite
from config import DB_PATH

_pool: list[aiosqlite.Connection] = []
_pool_lock = asyncio.Lock()
_MAX_POOL_SIZE = 5


async def _create_connection() -> aiosqlite.Connection:
    """Create and configure a new database connection."""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA wal_autocheckpoint=500")
    # Wait for a competing writer instead of failing instantly with "database
    # is locked" — the pool means concurrent writes DO happen (chat saves,
    # embeddings, hub timeline, timers). 30s rather than 5s because the first
    # ~2 minutes after a restart are a write storm: every Discord bot identity
    # primes its mentions cursor and server map at once, and a chat save
    # arriving in that window used to exhaust a 5s wait and drop her message.
    await db.execute("PRAGMA busy_timeout=30000")
    # NORMAL is the recommended pairing with WAL: fsync only at checkpoint.
    # Durability tradeoff is a power-loss losing the last few commits, never
    # corruption — and it meaningfully speeds up every write.
    await db.execute("PRAGMA synchronous=NORMAL")
    # Per-connection memory hygiene: a 64 MiB page cache (negative = KiB) keeps
    # hot embedding/message pages resident, 256 MiB mmap lets reads bypass the
    # page-cache copy entirely, and in-memory temp stores keep sort/join
    # scratch space off disk.
    await db.execute("PRAGMA cache_size=-65536")
    await db.execute("PRAGMA mmap_size=268435456")
    await db.execute("PRAGMA temp_store=MEMORY")
    return db


async def get_db() -> aiosqlite.Connection:
    """Get a database connection — reuses from pool if available."""
    async with _pool_lock:
        if _pool:
            return _pool.pop()
    return await _create_connection()


async def release_db(db: aiosqlite.Connection):
    """Return a connection to the pool instead of closing it."""
    try:
        await db.rollback()
    except Exception:
        pass

    try:
        # Verify connection is still usable
        await db.execute("SELECT 1")
    except Exception:
        try:
            await db.close()
        except Exception:
            pass
        return

    async with _pool_lock:
        if len(_pool) < _MAX_POOL_SIZE:
            _pool.append(db)
            return
    # Pool full — close the connection
    await db.close()


async def close_all_db_connections():
    """Close pooled connections during shutdown to avoid stale file handles."""
    async with _pool_lock:
        pooled = list(_pool)
        _pool.clear()

    for db in pooled:
        try:
            await db.close()
        except Exception:
            pass
