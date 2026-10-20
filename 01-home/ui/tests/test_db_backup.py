import sqlite3
from contextlib import closing

import pytest

from services import db_backup


@pytest.fixture
def backup_paths(tmp_path, monkeypatch):
    source = tmp_path / "live.db"
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("CREATE TABLE conversations (content TEXT)")
        db.execute("INSERT INTO conversations VALUES ('first')")
    monkeypatch.setattr(db_backup, "DB_PATH", source)
    monkeypatch.setattr(db_backup, "DB_BACKUP_DIR", tmp_path / "vault")
    monkeypatch.setattr(db_backup, "DB_BACKUP_KEEP", 1)
    return source, tmp_path / "vault"


def test_one_recovery_copy_refreshes_and_removes_older_snapshots(backup_paths):
    source, vault = backup_paths
    vault.mkdir()
    (vault / "anam-20260101_033000.db").write_bytes(b"old copy")
    first = db_backup._backup_sync()
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("INSERT INTO conversations VALUES ('second')")
    assert db_backup._backup_sync() == first
    assert list(vault.iterdir()) == [vault / "anam-latest.db"]
    with closing(sqlite3.connect(first)) as db:
        assert db.execute("SELECT content FROM conversations").fetchall() == [("first",), ("second",)]
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_missing_source_keeps_the_last_good_copy(backup_paths):
    source, vault = backup_paths
    db_backup._backup_sync()
    before = (vault / "anam-latest.db").read_bytes()
    source.unlink()
    with pytest.raises(sqlite3.OperationalError):
        db_backup._backup_sync()
    assert not source.exists()
    assert (vault / "anam-latest.db").read_bytes() == before
    assert list(vault.iterdir()) == [vault / "anam-latest.db"]
