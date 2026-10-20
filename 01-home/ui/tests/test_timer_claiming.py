import asyncio
import unittest
from datetime import datetime, timedelta, timezone

import aiosqlite

from db.schema import init_db
from services.autowake import _claim_due_timers


class TimerClaimingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_claim_is_atomic_for_pending_due(self):
        now = datetime.now(timezone.utc)
        due = now - timedelta(minutes=1)
        later = now + timedelta(hours=1)

        await self.db.executemany(
            "INSERT INTO timers "
            "(identity, fire_at, fire_at_epoch, context, wake_session, status, created_at, created_at_epoch, retry_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "Avery",
                    due.isoformat(),
                    int(due.timestamp()),
                    "due-1",
                    1,
                    "pending",
                    now.isoformat(),
                    int(now.timestamp()),
                    0,
                ),
                (
                    "Rowan",
                    due.isoformat(),
                    int(due.timestamp()),
                    "due-2",
                    1,
                    "pending",
                    now.isoformat(),
                    int(now.timestamp()),
                    0,
                ),
                (
                    "Sage",
                    later.isoformat(),
                    int(later.timestamp()),
                    "future",
                    1,
                    "pending",
                    now.isoformat(),
                    int(now.timestamp()),
                    0,
                ),
            ],
        )
        await self.db.commit()

        claimed = await _claim_due_timers(self.db, int(now.timestamp()))
        self.assertEqual(len(claimed), 2)

        claimed_again = await _claim_due_timers(self.db, int(now.timestamp()))
        self.assertEqual(len(claimed_again), 0)

        running = await self.db.execute_fetchall(
            "SELECT COUNT(*) FROM timers WHERE status = 'running'"
        )
        pending = await self.db.execute_fetchall(
            "SELECT COUNT(*) FROM timers WHERE status = 'pending'"
        )
        self.assertEqual(running[0][0], 2)
        self.assertEqual(pending[0][0], 1)

