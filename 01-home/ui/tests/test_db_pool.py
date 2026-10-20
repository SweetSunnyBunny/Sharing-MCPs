import unittest

import aiosqlite

from db import database


class DbPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await database.close_all_db_connections()

    async def test_release_db_rolls_back_before_pooling(self):
        calls = []

        class FakeConnection:
            async def rollback(self):
                calls.append("rollback")

            async def execute(self, sql):
                calls.append(sql)

            async def close(self):
                calls.append("close")

        db = FakeConnection()

        await database.release_db(db)

        self.assertEqual(calls[:2], ["rollback", "SELECT 1"])
        self.assertIn(db, database._pool)

    async def test_close_all_db_connections_drains_pool(self):
        db = await aiosqlite.connect(":memory:")
        database._pool.append(db)

        await database.close_all_db_connections()

        self.assertEqual(database._pool, [])
        with self.assertRaises((aiosqlite.ProgrammingError, ValueError)):
            await db.execute("SELECT 1")
