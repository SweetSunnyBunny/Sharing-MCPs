import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from db.schema import MIGRATIONS, init_db


class SchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_init_db_is_idempotent_and_tracks_migrations(self):
        db = await aiosqlite.connect(":memory:")
        try:
            await asyncio.wait_for(init_db(db), timeout=5)
            await asyncio.wait_for(init_db(db), timeout=5)
            rows = await db.execute_fetchall(
                "SELECT name FROM schema_migrations ORDER BY name"
            )
            self.assertEqual(len(rows), len(MIGRATIONS))
            provider_table = await db.execute_fetchall(
                "PRAGMA table_info(conversation_provider_sessions)"
            )
            self.assertTrue(bool(provider_table))
            schedule_columns = await db.execute_fetchall(
                "PRAGMA table_info(autowake_schedule)"
            )
            self.assertIn("provider", {row[1] for row in schedule_columns})
        finally:
            await db.close()

    async def test_legacy_token_and_participants_are_migrated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            db = await aiosqlite.connect(str(db_path))
            try:
                await db.executescript(
                    """
                    CREATE TABLE conversations (
                        id TEXT PRIMARY KEY,
                        identity TEXT NOT NULL,
                        claude_session_id TEXT,
                        title TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        session_type TEXT DEFAULT 'chat'
                    );
                    CREATE TABLE sessions (
                        token TEXT PRIMARY KEY,
                        discord_id TEXT NOT NULL,
                        discord_username TEXT,
                        discord_avatar TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    CREATE TABLE messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        identity TEXT,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                await db.execute(
                    "INSERT INTO conversations "
                    "(id, identity, created_at, updated_at, session_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "conv-1",
                        "Avery,Rowan",
                        "2026-01-01T00:00:00+00:00",
                        "2026-01-01T00:00:00+00:00",
                        "brother",
                    ),
                )
                await db.execute(
                    "INSERT INTO sessions (token, discord_id, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        "plain-token",
                        "discord-1",
                        "2026-01-01T00:00:00+00:00",
                        "2027-01-01T00:00:00+00:00",
                    ),
                )
                await db.commit()
            finally:
                await db.close()

            db2 = await aiosqlite.connect(str(db_path))
            try:
                await asyncio.wait_for(init_db(db2), timeout=5)

                cols = await db2.execute_fetchall("PRAGMA table_info(sessions)")
                col_names = {c[1] for c in cols}
                self.assertIn("token_hash", col_names)
                self.assertNotIn("token", col_names)

                row = await db2.execute_fetchall("SELECT token_hash FROM sessions")
                self.assertEqual(
                    row[0][0], hashlib.sha256(b"plain-token").hexdigest()
                )

                conv = await db2.execute_fetchall(
                    "SELECT identity FROM conversations WHERE id = ?",
                    ("conv-1",),
                )
                self.assertEqual(conv[0][0], "Avery")

                participants = await db2.execute_fetchall(
                    "SELECT identity FROM conversation_participants "
                    "WHERE conversation_id = ? ORDER BY identity",
                    ("conv-1",),
                )
                self.assertEqual([p[0] for p in participants], ["Avery", "Rowan"])
            finally:
                await db2.close()

    async def test_new_migration_applies_after_old_ones_already_recorded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "incremental.db"
            db = await aiosqlite.connect(str(db_path))
            try:
                await db.executescript(
                    """
                    CREATE TABLE conversations (
                        id TEXT PRIMARY KEY,
                        identity TEXT NOT NULL,
                        claude_session_id TEXT,
                        title TEXT,
                        created_at TEXT NOT NULL,
                        created_at_epoch INTEGER,
                        updated_at TEXT NOT NULL,
                        updated_at_epoch INTEGER,
                        is_active INTEGER DEFAULT 1,
                        session_type TEXT DEFAULT 'chat',
                        metadata TEXT,
                        platform_chat_id TEXT
                    );
                    CREATE TABLE autowake_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        schedule_id INTEGER,
                        identity TEXT NOT NULL,
                        session_type TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        started_at_epoch INTEGER,
                        completed_at TEXT,
                        completed_at_epoch INTEGER,
                        message_count INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'running'
                    );
                    CREATE TABLE schema_migrations (
                        name TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );
                    """
                )
                # Insert exact historical names expected by init_db.
                for name in (
                    "001_ensure_legacy_columns",
                    "002_migrate_sessions_to_token_hash",
                    "003_backfill_epochs",
                    "004_backfill_conversation_participants",
                    "005_normalize_session_hashes",
                ):
                    await db.execute(
                        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                        (name, "2026-01-01T00:00:00+00:00"),
                    )
                await db.commit()
            finally:
                await db.close()

            db2 = await aiosqlite.connect(str(db_path))
            try:
                await asyncio.wait_for(init_db(db2), timeout=5)
                conv_cols = await db2.execute_fetchall("PRAGMA table_info(conversations)")
                conv_col_names = {c[1] for c in conv_cols}
                self.assertIn("conversation_day", conv_col_names)
                self.assertIn("autowake_daily", conv_col_names)

                log_cols = await db2.execute_fetchall("PRAGMA table_info(autowake_log)")
                log_col_names = {c[1] for c in log_cols}
                self.assertIn("conversation_id", log_col_names)

                mig_rows = await db2.execute_fetchall(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    ("006_add_daily_autowake_columns",),
                )
                self.assertTrue(bool(mig_rows))

                timeline_cols = await db2.execute_fetchall("PRAGMA table_info(personal_timeline)")
                self.assertTrue(bool(timeline_cols))
                memory_cols = await db2.execute_fetchall("PRAGMA table_info(curated_memories)")
                self.assertTrue(bool(memory_cols))
                profile_cols = await db2.execute_fetchall("PRAGMA table_info(identity_profile_facts)")
                self.assertTrue(bool(profile_cols))

                mig_rows = await db2.execute_fetchall(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    ("007_add_personal_state_tables",),
                )
                self.assertTrue(bool(mig_rows))
                mig_rows = await db2.execute_fetchall(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    ("008_add_identity_profile_facts",),
                )
                self.assertTrue(bool(mig_rows))
            finally:
                await db2.close()

    async def test_porter_fts_migration_stems_and_keeps_sync_triggers(self):
        db = await aiosqlite.connect(":memory:")
        try:
            # Fresh init exercises the upgrade path: 009 builds the plain
            # unicode61 FTS table, 019 recreates it with porter stemming.
            await asyncio.wait_for(init_db(db), timeout=5)

            rows = await db.execute_fetchall(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts'"
            )
            self.assertTrue(rows)
            self.assertIn("porter", rows[0][0].lower())
            self.assertIn("content='messages'", rows[0][0])

            # Sync triggers from 009 must survive the recreate: a new message
            # should land in the index and match via its stem.
            await db.execute(
                "INSERT INTO conversations (id, identity, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("conv-fts", "Avery", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("msg-fts", "conv-fts", "user", "she was running through the meadows",
                 "2026-01-01T00:00:00+00:00"),
            )
            hits = await db.execute_fetchall(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?",
                ("run",),
            )
            self.assertEqual(len(hits), 1)

            # Re-running init_db must not clobber the porter table.
            await asyncio.wait_for(init_db(db), timeout=5)
            hits = await db.execute_fetchall(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?",
                ("run",),
            )
            self.assertEqual(len(hits), 1)
        finally:
            await db.close()

    async def test_cli_provider_configs_are_forced_to_bypass_approvals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "providers.db"
            db = await aiosqlite.connect(str(db_path))
            try:
                await db.executescript(
                    """
                    CREATE TABLE settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE schema_migrations (
                        name TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );
                    """
                )
                bypass_idx = next(i for i, (n, _) in enumerate(MIGRATIONS) if n == "011_force_cli_bypass_approvals")
                for name, _fn in MIGRATIONS[:bypass_idx]:
                    await db.execute(
                        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                        (name, "2026-01-01T00:00:00+00:00"),
                    )
                await db.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("llm_provider", "codex", "2026-01-01T00:00:00+00:00"),
                )
                await db.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (
                        "llm_provider_config",
                        json.dumps({"model": "gpt-5", "bypass_approvals": False}),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
                await db.commit()
            finally:
                await db.close()

            db2 = await aiosqlite.connect(str(db_path))
            try:
                await asyncio.wait_for(init_db(db2), timeout=5)
                rows = await db2.execute_fetchall(
                    "SELECT value FROM settings WHERE key = ?",
                    ("llm_provider_config",),
                )
                config = json.loads(rows[0][0])
                self.assertTrue(config["bypass_approvals"])

                mig_rows = await db2.execute_fetchall(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    ("011_force_cli_bypass_approvals",),
                )
                self.assertTrue(bool(mig_rows))
            finally:
                await db2.close()
