"""Regression coverage retained from the retired one-shot timer probe."""

import unittest
from unittest.mock import patch

import aiosqlite

from services import autowake


class TimerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await self.db.execute(
            "CREATE TABLE timers (id INTEGER PRIMARY KEY, identity TEXT, "
            "status TEXT, fire_at_epoch INTEGER, retry_count INTEGER)"
        )
        self.now = 2_000_000
        self.old = self.now - autowake._STRANDED_TIMER_GRACE_SECONDS - 60
        await self.db.executemany(
            "INSERT INTO timers VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Avery", "running", self.old, 1),
                (2, "Claude", "running", self.old, 0),
                (3, "Rowan", "running", self.now - 60, 0),
                (4, "Sage", "pending", self.old, 0),
                (5, "Ember", "fired", self.old, 0),
                (6, "Juniper", "running", self.old, 2),
            ],
        )
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_only_stranded_idle_timers_recover_and_retry_increments_once(self):
        with patch.object(autowake, "is_identity_busy", side_effect=lambda who: who == "Claude"):
            self.assertEqual(await autowake._recover_stranded_running_timers(self.db, self.now), 2)
            self.assertEqual(await autowake._recover_stranded_running_timers(self.db, self.now), 0)
        rows = await self.db.execute_fetchall("SELECT id, status, retry_count FROM timers ORDER BY id")
        self.assertEqual(rows, [
            (1, "pending", 2), (2, "running", 0), (3, "running", 0),
            (4, "pending", 0), (5, "fired", 0), (6, "pending", 3),
        ])

    async def test_all_busy_is_a_noop(self):
        with patch.object(autowake, "is_identity_busy", return_value=True):
            self.assertEqual(await autowake._recover_stranded_running_timers(self.db, self.now), 0)
        self.assertEqual(
            await self.db.execute_fetchall("SELECT id FROM timers WHERE status = 'running' ORDER BY id"),
            [(1,), (2,), (3,), (6,)],
        )

    async def test_no_busy_identities_recovers_all_old_running_rows(self):
        with patch.object(autowake, "is_identity_busy", return_value=False):
            self.assertEqual(await autowake._recover_stranded_running_timers(self.db, self.now), 3)
