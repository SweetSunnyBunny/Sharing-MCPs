import asyncio
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE
from db.schema import init_db
from services import autowake, identity_lock
from services.autowake import _get_or_create_daily_autowake_conversation
from services.program_loader import build_free_time_activation_prompt
from services.session_manager import get_or_create_conversation, new_conversation


class AutowakeDailyThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()

    def tearDown(self):
        autowake.release_identity("Avery")
        identity_lock._active_sessions.pop("Avery", None)
        identity_lock._lock_acquired_at.pop("Avery", None)

    async def test_reuses_same_identity_conversation_same_day(self):
        conv1, created1, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        conv2, created2, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(conv1, conv2)
        row = await self.db.execute_fetchall(
            "SELECT conversation_day, session_type FROM conversations WHERE id = ?",
            (conv1,),
        )
        # The unified daily thread MUST be tagged with today's conversation_day
        # and be a 'chat' session — the autowake_daily provenance flag is no
        # longer load-bearing.
        self.assertTrue(bool(row[0][0]))
        self.assertEqual(row[0][1], "chat")

    async def test_uses_separate_daily_conversations_per_identity(self):
        avery_conv, _, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        rowan_conv, _, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Rowan"
        )
        self.assertNotEqual(avery_conv, rowan_conv)

    def test_morning_digest_includes_verified_commons_changes(self):
        prompt = autowake.SESSION_PROMPTS["morning_digest"]
        self.assertIn("current evidence", prompt)
        self.assertIn("credit", prompt.lower())

    def test_morning_digest_uses_only_configured_shared_channels(self):
        prompt = autowake.SESSION_PROMPTS["morning_digest"]
        self.assertIn("configured", prompt)
        self.assertIn("Quiet sources may stay quiet", prompt)
        self.assertNotRegex(prompt, r"\b\d{17,20}\b")

    def test_autowake_directive_distinguishes_quiet_action_from_empty_rest(self):
        directive = autowake.AUTOWAKE_DIRECTIVE

        self.assertIn("rest cannot mean choosing an empty wake", directive)
        self.assertIn("They are votes, not vetoes", directive)
        self.assertIn("Do or experience at least one thing", directive)
        self.assertIn("You do not owe Owner public output", directive)

    def test_custom_free_hour_gets_one_concrete_first_move(self):
        with unittest.mock.patch(
            "services.program_loader.random.choice",
            return_value="Touch one real door.",
        ):
            prompt = build_free_time_activation_prompt("Juniper")

        self.assertIn("signals, not locks", prompt)
        self.assertIn("Touch one real door.", prompt)
        self.assertIn("contact can generate", prompt.lower())
        self.assertIn("do or experience at least one thing", prompt.lower())

    async def test_creates_new_day_conversation_when_only_yesterday_exists(self):
        now_local = datetime.now(ZoneInfo(TIMEZONE))
        yesterday_local = now_local - timedelta(days=1)
        yesterday_iso = yesterday_local.isoformat()
        yesterday_epoch = int(yesterday_local.timestamp())

        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old-conv",
                "Avery",
                "Avery - Yesterday",
                yesterday_iso,
                yesterday_epoch,
                yesterday_iso,
                yesterday_epoch,
                "chat",
            ),
        )
        await self.db.commit()

        conv_id, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertTrue(created_new)
        self.assertNotEqual(conv_id, "old-conv")

    async def test_concurrent_calls_share_same_daily_conversation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "autowake_daily.db"
            seed = await aiosqlite.connect(str(db_path))
            try:
                await asyncio.wait_for(init_db(seed), timeout=5)
            finally:
                await seed.close()

            db1 = await aiosqlite.connect(str(db_path))
            db2 = await aiosqlite.connect(str(db_path))
            try:
                first, second = await asyncio.gather(
                    _get_or_create_daily_autowake_conversation(db1, "Avery"),
                    _get_or_create_daily_autowake_conversation(db2, "Avery"),
                )
            finally:
                await db1.close()
                await db2.close()

            self.assertEqual(first[0], second[0])

            check = await aiosqlite.connect(str(db_path))
            try:


                day_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
                rows = await check.execute_fetchall(
                    "SELECT COUNT(*) FROM conversations "
                    "WHERE identity = ? AND session_type = 'chat' "
                    "AND conversation_day = ? AND is_active = 1",
                    ("Avery", day_key),
                )
                self.assertEqual(rows[0][0], 1)
            finally:
                await check.close()

    async def test_interactive_then_autowake_shares_same_thread(self):
        'Test interactive then autowake shares same thread.'
        interactive_id = await get_or_create_conversation(self.db, "Avery")
        autowake_id, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertEqual(interactive_id, autowake_id)
        self.assertFalse(created_new)  # autowake joined an existing thread

    async def test_autowake_then_interactive_shares_same_thread(self):
        'Test autowake then interactive shares same thread.'
        autowake_id, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertTrue(created_new)
        interactive_id = await get_or_create_conversation(self.db, "Avery")
        self.assertEqual(autowake_id, interactive_id)

    async def test_plus_button_keeps_todays_thread_and_takes_over_the_funnel(self):
        "Hitting '+' must NOT hide today's thread (is_active = 0 is what"
        a_id, _, _ = await _get_or_create_daily_autowake_conversation(self.db, "Avery")
        await self.db.execute(
            "UPDATE conversations SET autowake_daily = 1 WHERE id = ?", (a_id,),
        )
        await self.db.commit()

        same = await get_or_create_conversation(self.db, "Avery")
        self.assertEqual(a_id, same)

        fresh = await new_conversation(self.db, "Avery")
        self.assertNotEqual(fresh, a_id)

        rows = await self.db.execute_fetchall(
            "SELECT is_active FROM conversations WHERE id = ?", (a_id,),
        )
        self.assertEqual(int(rows[0][0]), 1)

        day_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        rows = await self.db.execute_fetchall(
            "SELECT COUNT(*) FROM conversations "
            "WHERE identity = ? AND session_type = 'chat' "
            "AND conversation_day = ? AND is_active = 1",
            ("Avery", day_key),
        )
        self.assertEqual(rows[0][0], 2)

        conv, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertEqual(conv, fresh)
        self.assertFalse(created_new)

    async def test_stale_lock_stays_busy_until_owner_releases(self):
        acquired = await autowake.acquire_identity("Avery")
        self.assertTrue(acquired)
        identity_lock._lock_acquired_at["Avery"] = 0.0

        with unittest.mock.patch("services.identity_lock.time.monotonic", return_value=identity_lock._STALE_LOCK_SECONDS + 5):
            self.assertTrue(autowake.is_identity_busy("Avery"))

        self.assertTrue(identity_lock._identity_locks["Avery"].locked())

    async def test_empty_public_seeds_preserve_installation_authored_schedules(self):
        await self.db.execute(
            "INSERT INTO autowake_schedule "
            "(name, cron_hour, cron_minute, identity, session_type, enabled, "
            "max_duration_minutes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Bedtime Reminder", 23, 30, "Avery", "bedtime_reminder", 1, 15),
        )
        await self.db.commit()

        with (
            unittest.mock.patch(
                "services.autowake.get_db",
                new=unittest.mock.AsyncMock(return_value=self.db),
            ),
            unittest.mock.patch(
                "services.autowake.release_db",
                new=unittest.mock.AsyncMock(),
            ),
        ):
            await autowake.seed_default_schedules()

        rows = await self.db.execute_fetchall(
            "SELECT enabled FROM autowake_schedule "
            "WHERE session_type = 'bedtime_reminder'"
        )
        self.assertEqual([int(row[0]) for row in rows], [1])
