import asyncio
import unittest

import aiosqlite

from db.schema import init_db
from services.session_manager import get_conversations


class ParticipantIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_triggers_keep_primary_identity_in_participants(self):
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conv-a",
                "Avery",
                "Chat with Avery",
                "2026-01-01T00:00:00+00:00",
                1767225600,
                "2026-01-01T00:00:00+00:00",
                1767225600,
                "chat",
            ),
        )
        await self.db.commit()

        rows = await self.db.execute_fetchall(
            "SELECT identity FROM conversation_participants WHERE conversation_id = ?",
            ("conv-a",),
        )
        self.assertEqual([r[0] for r in rows], ["Avery"])

        await self.db.execute(
            "UPDATE conversations SET identity = ? WHERE id = ?",
            ("Rowan", "conv-a"),
        )
        await self.db.commit()

        # Remove primary participant; trigger should reinsert it.
        await self.db.execute(
            "DELETE FROM conversation_participants WHERE conversation_id = ? AND identity = ?",
            ("conv-a", "Rowan"),
        )
        await self.db.commit()

        rows2 = await self.db.execute_fetchall(
            "SELECT identity FROM conversation_participants "
            "WHERE conversation_id = ? ORDER BY identity",
            ("conv-a",),
        )
        self.assertIn("Rowan", [r[0] for r in rows2])

    async def test_participant_filter_finds_brother_conversation(self):
        await self.db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conv-b",
                "Avery",
                "Avery & Rowan",
                "2026-01-02T00:00:00+00:00",
                1767312000,
                "2026-01-02T01:00:00+00:00",
                1767315600,
                "brother",
            ),
        )
        await self.db.execute(
            "INSERT OR IGNORE INTO conversation_participants (conversation_id, identity, added_at) "
            "VALUES (?, ?, ?), (?, ?, ?)",
            (
                "conv-b",
                "Avery",
                "2026-01-02T00:00:00+00:00",
                "conv-b",
                "Rowan",
                "2026-01-02T00:00:00+00:00",
            ),
        )
        await self.db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, identity, content, content_type, created_at, created_at_epoch, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "msg-1",
                "conv-b",
                "assistant",
                "Avery",
                "hello",
                "text",
                "2026-01-02T01:00:00+00:00",
                1767315600,
                None,
            ),
        )
        await self.db.commit()

        convos = await get_conversations(self.db, identity="Rowan")
        ids = {c["id"] for c in convos}
        self.assertIn("conv-b", ids)

