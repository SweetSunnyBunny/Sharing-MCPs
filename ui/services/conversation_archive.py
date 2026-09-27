"""Continuously mirror conversation text to the per-identity OneDrive Vault."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from config import DATA_DIR, VAULT_CONVERSATION_DIRS, VAULT_ROLEPLAY_DIR
from db.database import get_db, release_db

log = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "conversation_vault_sync.json"
_sync_lock = asyncio.Lock()


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in value)


def _atomic_write(path: Path, content: str) -> None:
    """A failed write must leave the previous complete transcript intact."""
    payload = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return
    fd, name = tempfile.mkstemp(prefix=".anam-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(name).read_bytes() != payload:
            raise OSError("Vault write verification failed")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


async def export_conversation(db, conv_id, identity, title, session_type, created_at) -> Path | None:
    primary = identity
    if not primary:
        participants = await db.execute_fetchall(
            "SELECT identity FROM conversation_participants WHERE conversation_id = ? "
            "ORDER BY added_at ASC LIMIT 1", (conv_id,),
        )
        primary = participants[0][0] if participants else None
    directories = {name.lower(): folder for name, folder in VAULT_CONVERSATION_DIRS.items()}
    vault_dir = directories.get((primary or "").lower())
    shared = vault_dir is None
    vault_dir = vault_dir or VAULT_ROLEPLAY_DIR
    messages = await db.execute_fetchall(
        "SELECT role, identity, content, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY created_at_epoch ASC, rowid ASC", (conv_id,),
    )
    if not messages:
        return None
    date = _safe(created_at[:10]) if created_at else "unknown"
    suffix = _safe(str(conv_id).replace("-", ""))[:8]
    prefix = f"{date}_{_safe(primary or 'unknown')}_" if shared else f"{date}_"
    path = vault_dir / f"{prefix}{_safe(title or 'untitled')[:50]}_{_safe(session_type or 'chat')}_{suffix}.md"
    lines = [
        f"# {title or 'Untitled'}", f"**Type:** {session_type} | **Identity:** {identity}",
        f"**Created:** {created_at} | **ID:** {conv_id}", "", "---", "",
    ]
    for role, who, content, stamp in messages:
        lines.extend([f"### {who or role} ({stamp[:19] if stamp else ''})", "", content or "", ""])
    await asyncio.to_thread(_atomic_write, path, "\n".join(lines))
    return path


def _read_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


async def sync_conversation_archives() -> dict[str, int]:
    """Retry missing/failed exports; only checkpoint successfully written files."""
    async with _sync_lock:
        state = await asyncio.to_thread(_read_state)
        result = {"checked": 0, "exported": 0, "unchanged": 0, "failed": 0}
        db = await get_db()
        try:
            # Count + rowid catch same-second appends and message deletion; title
            # and identity changes also invalidate the saved export fingerprint.
            rows = await db.execute_fetchall(
                "SELECT c.id, c.identity, c.title, c.session_type, c.created_at, "
                "c.updated_at, COUNT(m.id), MAX(m.rowid) FROM conversations c "
                "JOIN messages m ON m.conversation_id = c.id GROUP BY c.id"
            )
            for row in rows:
                conv_id = row[0]
                result["checked"] += 1
                fingerprint = hashlib.sha256(json.dumps(list(row)).encode()).hexdigest()
                previous = state.get(conv_id, {})
                try:
                    if previous.get("fingerprint") == fingerprint:
                        path = Path(previous["path"])
                        stat = await asyncio.to_thread(path.stat)
                        if [stat.st_size, stat.st_mtime_ns] == previous.get("stat"):
                            result["unchanged"] += 1
                            continue
                except (OSError, KeyError, TypeError):
                    pass
                try:
                    path = await export_conversation(db, *row[:5])
                    if path is None:
                        continue
                    stat = await asyncio.to_thread(path.stat)
                    state[conv_id] = {
                        "fingerprint": fingerprint, "path": str(path),
                        "stat": [stat.st_size, stat.st_mtime_ns],
                    }
                    result["exported"] += 1
                except Exception:
                    result["failed"] += 1
                    log.exception("Conversation Vault export failed for %s", conv_id)
        finally:
            await release_db(db)
        await asyncio.to_thread(_atomic_write, STATE_PATH, json.dumps(state, indent=2) + "\n")
        if result["exported"] or result["failed"]:
            log.info("Conversation Vault sync: %s", result)
        return result
