import unittest

import aiosqlite

from db.schema import init_db
from services.autowake_service import create_schedule, list_schedules, update_schedule


class AutowakeScheduleProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await init_db(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_provider_override_round_trips_and_can_be_cleared(self):
        created = await create_schedule(
            self.db,
            name="Claude on ChatGPT",
            cron_hour=10,
            cron_minute=15,
            identity="Claude",
            provider="chatgpt",
        )
        self.assertEqual(created["provider"], "chatgpt")
        self.assertEqual((await list_schedules(self.db))[0]["provider"], "chatgpt")

        changed = await update_schedule(
            self.db, created["id"], updates={"provider": ""}
        )
        self.assertTrue(changed)
        self.assertIsNone((await list_schedules(self.db))[0]["provider"])

