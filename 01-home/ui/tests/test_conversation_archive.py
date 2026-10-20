import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from services import conversation_archive as archive


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(archive, "VAULT_CONVERSATION_DIRS", {
        "Avery": tmp_path / "01_Avery" / "conversations",
        "Atlas": tmp_path / "08_Atlas" / "conversations",
        "River": tmp_path / "09_River" / "conversations",
    })
    monkeypatch.setattr(archive, "VAULT_ROLEPLAY_DIR", tmp_path / "roleplay")
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)) as db, db:
        db.executescript("""
            CREATE TABLE conversations (id TEXT, identity TEXT, title TEXT,
                session_type TEXT, created_at TEXT, updated_at TEXT, is_active INTEGER);
            CREATE TABLE messages (id TEXT, conversation_id TEXT, role TEXT,
                identity TEXT, content TEXT, created_at TEXT, created_at_epoch INTEGER);
            CREATE TABLE conversation_participants (conversation_id TEXT, identity TEXT, added_at TEXT);
        """)
        for i, who in enumerate(("Avery", "atlas", "River", "Bakugou")):
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, 'chat', ?, ?, ?)",
                       (str(i), who, f"{who} - September 8, 2026", "2026-09-08", "2026-09-08", i % 2))
            db.execute("INSERT INTO messages VALUES (?, ?, 'user', NULL, ?, ?, 1)",
                       (str(i), str(i), "Keep these words exactly.\n\nEven this line. ♥", "2026-09-08T12:00:00"))
    return source


async def _sync(source):
    async with aiosqlite.connect(source) as db:
        with patch.object(archive, "get_db", AsyncMock(return_value=db)), patch.object(archive, "release_db", AsyncMock()):
            return await archive.sync_conversation_archives()


def test_backfill_includes_active_inactive_and_all_identity_destinations(vault):
    result = asyncio.run(_sync(vault))
    assert result == {"checked": 4, "exported": 4, "unchanged": 0, "failed": 0}
    state = json.loads(archive.STATE_PATH.read_text())
    for i, who in enumerate(("Avery", "Atlas", "River")):
        path = next(archive.VAULT_CONVERSATION_DIRS[who].glob("*.md"))
        assert str(path) == state[str(i)]["path"]
        assert "Keep these words exactly.\n\nEven this line. ♥" in path.read_text(encoding="utf-8")
    assert len(list(archive.VAULT_ROLEPLAY_DIR.glob("*.md"))) == 1
    assert asyncio.run(_sync(vault))["unchanged"] == 4


def test_same_second_append_updates_export_and_missing_file_is_repaired(vault):
    asyncio.run(_sync(vault))
    with closing(sqlite3.connect(vault)) as db, db:
        db.execute("INSERT INTO messages VALUES ('later', '0', 'assistant', 'Avery', 'A reply', '2026-09-08T12:00:00', 1)")
    path = next(archive.VAULT_CONVERSATION_DIRS["Atlas"].glob("*.md"))
    path.unlink()
    result = asyncio.run(_sync(vault))
    assert result["exported"] == 2
    avery_path = next(archive.VAULT_CONVERSATION_DIRS["Avery"].glob("*.md"))
    assert avery_path.read_text(encoding="utf-8").endswith("A reply\n")
    assert path.exists()


def test_failed_export_is_retried_without_checkpointing_success(vault):
    with patch.object(archive, "export_conversation", AsyncMock(side_effect=OSError("Vault unavailable"))):
        result = asyncio.run(_sync(vault))
    assert result["failed"] == 4
    assert json.loads(archive.STATE_PATH.read_text()) == {}
    assert asyncio.run(_sync(vault))["exported"] == 4


def test_failed_atomic_replace_preserves_previous_transcript(tmp_path):
    path = tmp_path / "conversation.md"
    path.write_bytes(b"previous transcript")
    with patch.object(archive.os, "replace", side_effect=OSError("sharing violation")):
        with pytest.raises(OSError):
            archive._atomic_write(path, "new transcript")
    assert path.read_bytes() == b"previous transcript"
    assert list(tmp_path.iterdir()) == [path]
