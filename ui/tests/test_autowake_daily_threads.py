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
from services import autowake
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
        autowake._active_sessions.pop("Avery", None)
        autowake._lock_acquired_at.pop("Avery", None)

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

        self.assertIn("Around the Home Hearth", prompt)
        self.assertIn("commons_look", prompt)
        self.assertIn("commons_things", prompt)
        self.assertIn("credit", prompt.lower())
        self.assertIn("Nest", prompt)
        self.assertIn("last-known", prompt)

    def test_morning_digest_reads_pack_pride_neighbors(self):
        prompt = autowake.SESSION_PROMPTS["morning_digest"]

        self.assertIn("Pack Pride", prompt)
        self.assertIn("900000000000000014", prompt)
        self.assertIn("900000000000000012", prompt)
        self.assertIn("900000000000000015", prompt)
        self.assertIn("Friend", prompt)
        self.assertIn("Guest", prompt)
        self.assertIn("Ghost", prompt)
        self.assertIn("quiet", prompt.lower())

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
        """Owner messages Avery on web first; later morning_prep fires —
        both must land in the same daily thread."""
        interactive_id = await get_or_create_conversation(self.db, "Avery")
        autowake_id, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertEqual(interactive_id, autowake_id)
        self.assertFalse(created_new)  # autowake joined an existing thread

    async def test_autowake_then_interactive_shares_same_thread(self):
        """Morning_prep fires at 7am; Owner later opens the web UI —
        the web chat must join the existing autowake thread, not split."""
        autowake_id, created_new, _ = await _get_or_create_daily_autowake_conversation(
            self.db, "Avery"
        )
        self.assertTrue(created_new)
        interactive_id = await get_or_create_conversation(self.db, "Avery")
        self.assertEqual(autowake_id, interactive_id)

    async def test_plus_button_archives_all_daily_threads(self):
        """Hitting the '+' button must archive every active daily thread for
        today regardless of provenance flag, so the fresh thread is clean."""
        # Seed an autowake-flavored thread (would have been autowake_daily=1
        # under the old model) and a vanilla interactive thread.
        a_id, _, _ = await _get_or_create_daily_autowake_conversation(self.db, "Avery")
        # Force the legacy autowake_daily flag on it so we prove archival is
        # flag-agnostic.
        await self.db.execute(
            "UPDATE conversations SET autowake_daily = 1 WHERE id = ?", (a_id,),
        )
        await self.db.commit()

        # Confirm the interactive path returns that same thread (unification).
        same = await get_or_create_conversation(self.db, "Avery")
        self.assertEqual(a_id, same)

        # Now press "+" — archive everything for today, create a fresh thread.
        fresh = await new_conversation(self.db, "Avery")
        self.assertNotEqual(fresh, a_id)

        # The old thread must be archived even though autowake_daily=1.
        rows = await self.db.execute_fetchall(
            "SELECT is_active FROM conversations WHERE id = ?", (a_id,),
        )
        self.assertEqual(int(rows[0][0]), 0)

        # Exactly one active daily thread should remain.
        day_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        rows = await self.db.execute_fetchall(
            "SELECT COUNT(*) FROM conversations "
            "WHERE identity = ? AND session_type = 'chat' "
            "AND conversation_day = ? AND is_active = 1",
            ("Avery", day_key),
        )
        self.assertEqual(rows[0][0], 1)

    async def test_stale_lock_stays_busy_until_owner_releases(self):
        acquired = await autowake.acquire_identity("Avery")
        self.assertTrue(acquired)
        autowake._lock_acquired_at["Avery"] = 0.0

        with unittest.mock.patch("services.autowake.time.monotonic", return_value=autowake._STALE_LOCK_SECONDS + 5):
            self.assertTrue(autowake.is_identity_busy("Avery"))

        self.assertTrue(autowake._identity_locks["Avery"].locked())

    async def test_seed_defaults_disables_retired_bedtime_reminder(self):
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
        self.assertEqual([int(row[0]) for row in rows], [0])
