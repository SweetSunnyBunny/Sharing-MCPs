'Application module.'

import inspect
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite

from services import autowake, platform_bridge


SETTINGS = {
    "failsafe_enabled": "true",
    "failsafe_gentle_minutes": "120",
    "failsafe_concerned_minutes": "720",
    "failsafe_emergency_minutes": "1440",
}


class QuietClockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await self.db.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, identity TEXT, "
            "content TEXT, metadata TEXT, created_at_epoch INTEGER)"
        )
        self.now = int(time.time())

    async def asyncTearDown(self):
        await self.db.close()

    async def _add(self, minutes_ago, *, role="user", identity="Avery", metadata=None):
        await self.db.execute(
            "INSERT INTO messages (role, identity, content, metadata, created_at_epoch) "
            "VALUES (?, ?, 'x', ?, ?)",
            (role, identity, json.dumps(metadata) if metadata else None,
             self.now - minutes_ago * 60),
        )
        await self.db.commit()

    async def _failsafe_wants_to_fire(self) -> bool:
        """Run check_failsafe up to the point where it would take the lock."""
        acquire = AsyncMock(return_value=False)
        with patch.object(autowake, "get_db", AsyncMock(return_value=self.db)), \
                patch.object(autowake, "release_db", AsyncMock()), \
                patch.object(autowake, "is_web_active", return_value=False), \
                patch.object(autowake, "get_failsafe_settings", AsyncMock(return_value=SETTINGS)), \
                patch.object(autowake, "acquire_identity", acquire):
            await autowake.check_failsafe()
        return acquire.await_count == 1

    async def test_only_owner_resets_the_clock(self):
        await self._add(300)  # her last real message, on the web
        await self._add(200, identity="system", metadata={"autowake": True})
        await self._add(150, identity="system", metadata={"timer": True, "timer_id": 1})
        await self._add(100, identity=None, metadata={"trigger": True, "trigger_id": 3})
        await self._add(50, identity="Claude", metadata={"discord_mention": True, "sender_kind": "brother"})
        await self._add(20, identity="Claude", metadata={"discord_mention": True, "sender_kind": "friend"})
        await self._add(10, role="assistant")
        self.assertEqual(await autowake.last_owner_message_epoch(self.db), self.now - 300 * 60)

    async def test_her_other_surfaces_count(self):
        await self._add(300)
        await self._add(90, identity="Claude", metadata={"platform": "discord", "sender_id": "1"})
        self.assertEqual(await autowake.last_owner_message_epoch(self.db), self.now - 90 * 60)
        await self._add(60, identity="Claude", metadata={"discord_mention": True, "sender_kind": "owner"})
        self.assertEqual(await autowake.last_owner_message_epoch(self.db), self.now - 60 * 60)
        await self._add(30, identity=None, metadata={"source": "wearable"})
        self.assertEqual(await autowake.last_owner_message_epoch(self.db), self.now - 30 * 60)

    async def test_no_messages_means_no_clock(self):
        self.assertIsNone(await autowake.last_owner_message_epoch(self.db))
        self.assertIsNone(await autowake.get_last_owner_activity(self.db))

    async def test_failsafe_does_not_step_back_down(self):
        # 14h quiet: gentle fired at 2h, concerned at 12h. Before the fix,
        # "gentle" became eligible again here and the two alternated.
        await self._add(14 * 60)
        await self._add(12 * 60, identity="system", metadata={"failsafe": True, "failsafe_level": "gentle"})
        await self._add(2 * 60, identity="system", metadata={"failsafe": True, "failsafe_level": "concerned"})
        self.assertFalse(await self._failsafe_wants_to_fire())

    async def test_failsafe_escalates_upward(self):
        await self._add(13 * 60)
        await self._add(11 * 60, identity="system", metadata={"failsafe": True, "failsafe_level": "gentle"})
        self.assertTrue(await self._failsafe_wants_to_fire())

    async def test_failsafe_from_an_earlier_quiet_spell_does_not_silence_this_one(self):
        await self._add(30 * 60, identity="system", metadata={"failsafe": True, "failsafe_level": "gentle"})
        await self._add(3 * 60)  # she came back, then went quiet again
        self.assertTrue(await self._failsafe_wants_to_fire())

    async def test_failsafe_waits_for_the_first_threshold(self):
        await self._add(60)
        self.assertFalse(await self._failsafe_wants_to_fire())


def test_platform_dm_never_releases_the_identity_lock():
    # The DM turn does not hold the lock, so it must not release it either.
    assert "release_identity" not in inspect.getsource(platform_bridge)
