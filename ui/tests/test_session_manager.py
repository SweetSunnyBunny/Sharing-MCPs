import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE
from db.schema import init_db
from services.session_manager import (
    get_or_create_conversation,
    get_provider_session_id_from_db,
    get_session_id_from_db,
    new_conversation,
    update_conversation_session,
    update_provider_session,
)


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await init_db(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    def _day_parts(self, offset_days: int = 0) -> tuple[str, str, int]:
        dt = datetime.now(ZoneInfo(TIMEZONE)) + timedelta(days=offset_days)
        return dt.isoformat(), dt.strftime("%Y-%m-%d"), int(dt.timestamp())

    async def test_provider_sessions_do_not_overwrite_claude_resume_id(self):
        now_iso, today, now_epoch = self._day_parts()
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, "
            "updated_at_epoch, session_type, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "provider-conv", "Avery", "Switch test", now_iso, now_epoch,
                now_iso, now_epoch, "chat", today,
            ),
        )
        await self.db.commit()

        await update_conversation_session(self.db, "provider-conv", "claude-session")
        await update_provider_session(
            self.db, "provider-conv", "codex", "codex-thread"
        )

        self.assertEqual(
            await get_session_id_from_db(self.db, "provider-conv"),
            "claude-session",
        )
        self.assertEqual(
            await get_provider_session_id_from_db(
                self.db, "provider-conv", "codex"
            ),
            "codex-thread",
        )

        await update_provider_session(self.db, "provider-conv", "codex", "")
        self.assertIsNone(
            await get_provider_session_id_from_db(
                self.db, "provider-conv", "codex"
            )
        )
        self.assertEqual(
            await get_session_id_from_db(self.db, "provider-conv"),
            "claude-session",
        )

    async def test_get_or_create_conversation_unifies_autowake_and_interactive(self):
        """After unification, an autowake-created daily thread MUST be
        returned by the default `get_or_create_conversation` path so the
        next interactive turn joins it instead of forking a new thread."""
        today_iso, today_day, today_epoch = self._day_parts()
        # Legacy interactive thread without conversation_day (older).
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chat-conv",
                "Avery",
                "Chat with Avery",
                today_iso,
                today_epoch,
                today_iso,
                today_epoch + 5,
                "chat",
                0,
                None,
            ),
        )
        # Newer autowake thread WITH conversation_day — under the unified
        # model this is the canonical daily thread for today and must win.
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "autowake-conv",
                "Avery",
                f"Avery - {today_day}",
                today_iso,
                today_epoch + 100,
                today_iso,
                today_epoch + 105,
                "chat",
                1,
                today_day,
            ),
        )
        await self.db.commit()

        conversation_id = await get_or_create_conversation(self.db, "Avery")

        # Was previously asserted as "chat-conv" (split behavior). After
        # unification the autowake thread is the canonical daily and wins.
        self.assertEqual(conversation_id, "autowake-conv")

    async def test_get_or_create_conversation_can_include_autowake_daily_threads(self):
        today_iso, today_day, today_epoch = self._day_parts()
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "autowake-conv",
                "Avery",
                f"Avery - {today_day}",
                today_iso,
                today_epoch,
                today_iso,
                today_epoch + 5,
                "chat",
                1,
                today_day,
            ),
        )
        await self.db.commit()

        conversation_id = await get_or_create_conversation(
            self.db,
            "Avery",
            include_autowake_daily=True,
        )

        self.assertEqual(conversation_id, "autowake-conv")

    async def test_get_or_create_conversation_prefers_newest_active_daily_chat(self):
        today_iso, today_day, today_epoch = self._day_parts()
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "older-chat",
                "Avery",
                "Avery - older",
                today_iso,
                today_epoch,
                today_iso,
                today_epoch + 5,
                "chat",
                0,
                today_day,
            ),
        )
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "newer-chat",
                "Avery",
                "Avery - newer",
                today_iso,
                today_epoch + 100,
                today_iso,
                today_epoch + 105,
                "chat",
                0,
                today_day,
            ),
        )
        await self.db.commit()

        conversation_id = await get_or_create_conversation(self.db, "Avery")

        self.assertEqual(conversation_id, "newer-chat")

    async def test_get_or_create_conversation_does_not_relabel_yesterday_as_today(self):
        yesterday_iso, yesterday_day, yesterday_epoch = self._day_parts(-1)
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "yesterday-chat",
                "Avery",
                f"Avery - {yesterday_day}",
                yesterday_iso,
                yesterday_epoch,
                yesterday_iso,
                yesterday_epoch + 5,
                "chat",
                0,
                yesterday_day,
            ),
        )
        await self.db.commit()

        conversation_id = await get_or_create_conversation(self.db, "Avery")

        self.assertNotEqual(conversation_id, "yesterday-chat")
        rows = await self.db.execute_fetchall(
            "SELECT title, conversation_day FROM conversations WHERE id = ?",
            ("yesterday-chat",),
        )
        self.assertEqual(rows[0], (f"Avery - {yesterday_day}", yesterday_day))

    async def test_get_or_create_conversation_does_not_adopt_previous_day_legacy_chat(self):
        yesterday_iso, _, yesterday_epoch = self._day_parts(-1)
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-yesterday",
                "Avery",
                "Chat with Avery",
                yesterday_iso,
                yesterday_epoch,
                yesterday_iso,
                yesterday_epoch + 5,
                "chat",
                0,
                None,
            ),
        )
        await self.db.commit()

        conversation_id = await get_or_create_conversation(self.db, "Avery")

        self.assertNotEqual(conversation_id, "legacy-yesterday")
        rows = await self.db.execute_fetchall(
            "SELECT title, conversation_day FROM conversations WHERE id = ?",
            ("legacy-yesterday",),
        )
        self.assertEqual(rows[0], ("Chat with Avery", None))

    async def test_new_conversation_archives_only_same_day_active_chat_threads(self):
        today_iso, today_day, today_epoch = self._day_parts()
        yesterday_iso, yesterday_day, yesterday_epoch = self._day_parts(-1)
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "existing-chat",
                "Avery",
                "Avery - existing",
                today_iso,
                today_epoch,
                today_iso,
                today_epoch + 5,
                "chat",
                0,
                today_day,
            ),
        )
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "yesterday-chat",
                "Avery",
                "Avery - yesterday",
                yesterday_iso,
                yesterday_epoch,
                yesterday_iso,
                yesterday_epoch + 5,
                "chat",
                0,
                yesterday_day,
            ),
        )
        await self.db.commit()

        new_id = await new_conversation(self.db, "Avery")

        self.assertNotEqual(new_id, "existing-chat")
        rows = await self.db.execute_fetchall(
            "SELECT id, is_active FROM conversations WHERE identity = ? AND session_type = 'chat' ORDER BY created_at_epoch ASC",
            ("Avery",),
        )
        self.assertEqual(rows[0], ("yesterday-chat", 1))
        self.assertEqual(rows[1], ("existing-chat", 0))
        self.assertEqual(rows[2][0], new_id)
        self.assertEqual(rows[2][1], 1)

    async def test_new_conversation_archives_same_day_legacy_chat_without_hiding_yesterday(self):
        today_iso, _, today_epoch = self._day_parts()
        yesterday_iso, yesterday_day, yesterday_epoch = self._day_parts(-1)
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-today",
                "Avery",
                "Chat with Avery",
                today_iso,
                today_epoch,
                today_iso,
                today_epoch + 5,
                "chat",
                0,
                None,
            ),
        )
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, autowake_daily, conversation_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "dated-yesterday",
                "Avery",
                f"Avery - {yesterday_day}",
                yesterday_iso,
                yesterday_epoch,
                yesterday_iso,
                yesterday_epoch + 5,
                "chat",
                0,
                yesterday_day,
            ),
        )
        await self.db.commit()

        new_id = await new_conversation(self.db, "Avery")

        rows = await self.db.execute_fetchall(
            "SELECT id, is_active FROM conversations WHERE identity = ? ORDER BY created_at_epoch ASC",
            ("Avery",),
        )
        self.assertEqual(rows[0], ("dated-yesterday", 1))
        self.assertEqual(rows[1], ("legacy-today", 0))
        self.assertEqual(rows[2][0], new_id)
        self.assertEqual(rows[2][1], 1)
