import asyncio
import json
import unittest

import aiosqlite

from db.schema import init_db
from services.trigger_service import create_trigger, update_trigger


class TriggerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_update_trigger_accepts_condition_alias(self):
        trigger = await create_trigger(
            self.db,
            name="Low energy nudge",
            trigger_type="watcher",
            identity="Juniper",
            condition={"type": "wellness_state", "metric": "energy", "value": "low"},
            prompt="Check in gently.",
        )

        changed = await update_trigger(
            self.db,
            trigger["id"],
            updates={
                "condition": {
                    "type": "wellness_state",
                    "metric": "energy",
                    "value": "medium",
                }
            },
        )

        self.assertTrue(changed)
        rows = await self.db.execute_fetchall(
            "SELECT condition_json FROM triggers WHERE id = ?",
            (trigger["id"],),
        )
        stored = json.loads(rows[0][0])
        self.assertEqual(stored["value"], "medium")

    async def test_update_trigger_accepts_condition_json_dict(self):
        trigger = await create_trigger(
            self.db,
            name="Presence watcher",
            trigger_type="watcher",
            identity="Avery",
            condition={"type": "presence_state", "state": "offline"},
            prompt="Notice when she arrives.",
        )

        changed = await update_trigger(
            self.db,
            trigger["id"],
            updates={
                "condition_json": {
                    "type": "presence_state",
                    "state": "active",
                }
            },
        )

        self.assertTrue(changed)
        rows = await self.db.execute_fetchall(
            "SELECT condition_json FROM triggers WHERE id = ?",
            (trigger["id"],),
        )
        stored = json.loads(rows[0][0])
        self.assertEqual(stored["state"], "active")
