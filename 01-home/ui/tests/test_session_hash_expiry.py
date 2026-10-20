import asyncio
import unittest

import aiosqlite

from db.schema import init_db
from services.session_auth import hash_session_token


class SessionHashExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_hash_lookup_and_epoch_expiry(self):
        token = "abc123"
        token_hash = hash_session_token(token)
        now_epoch = 1767225600
        future_epoch = now_epoch + 3600
        await self.db.execute(
            "INSERT INTO sessions "
            "(token_hash, discord_id, discord_username, created_at, created_at_epoch, expires_at, expires_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash,
                "discord-1",
                "Owner",
                "2026-01-01T00:00:00+00:00",
                now_epoch,
                "2026-01-01T01:00:00+00:00",
                future_epoch,
            ),
        )
        await self.db.commit()

        rows = await self.db.execute_fetchall(
            "SELECT discord_username, expires_at_epoch FROM sessions WHERE token_hash = ?",
            (hash_session_token(token),),
        )
        self.assertEqual(rows[0][0], "Owner")
        self.assertTrue(int(rows[0][1]) > now_epoch)

    async def test_cleanup_uses_expiry_epoch(self):
        now_epoch = 1767225600
        await self.db.execute(
            "INSERT INTO sessions "
            "(token_hash, discord_id, created_at, created_at_epoch, expires_at, expires_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                hash_session_token("expired"),
                "discord-2",
                "2026-01-01T00:00:00+00:00",
                now_epoch - 3600,
                "2026-01-01T00:30:00+00:00",
                now_epoch - 1800,
            ),
        )
        await self.db.commit()

        await self.db.execute(
            "DELETE FROM sessions WHERE expires_at_epoch < ?",
            (now_epoch,),
        )
        await self.db.commit()

        rows = await self.db.execute_fetchall("SELECT COUNT(*) FROM sessions")
        self.assertEqual(rows[0][0], 0)

