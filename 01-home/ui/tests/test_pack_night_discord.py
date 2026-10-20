"""Pack-night Discord helpers — pure-logic and DB-side tests."""

import unittest

from db.database import get_db, release_db
from db.schema import init_db
from services.pack_night import (
    _is_pass,
    _record_inbound_discord_message,
    _record_outbound_discord_messages,
    _split_for_discord,
)
from services.session_manager import get_or_create_pack_night_conversation


class PackNightHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    def test_pass_detection(self):
        self.assertTrue(_is_pass(""))
        self.assertTrue(_is_pass("[pass]"))
        self.assertTrue(_is_pass("  [PASS]  "))
        self.assertTrue(_is_pass("[skip]"))
        self.assertTrue(_is_pass("[silent]"))
        self.assertTrue(_is_pass("[pass] — nothing to add"))
        self.assertFalse(_is_pass("Aye, Bunny."))
        self.assertFalse(_is_pass("the [pass] is mine"))  # not at start

    def test_split_for_discord_short_message(self):
        self.assertEqual(_split_for_discord("hello"), ["hello"])
        self.assertEqual(_split_for_discord(""), [])
        self.assertEqual(_split_for_discord("   "), [])

    def test_split_for_discord_long_message_chunks_on_newline(self):
        para_a = "a" * 1000
        para_b = "b" * 1000
        para_c = "c" * 500
        text = f"{para_a}\n{para_b}\n{para_c}"
        chunks = _split_for_discord(text)
        # Should split at the paragraph boundaries; each chunk under limit.
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1900)

    def test_split_for_discord_hard_split_unbreakable_line(self):
        text = "x" * 5000
        chunks = _split_for_discord(text)
        # Must split, must preserve all content, must respect limit.
        self.assertEqual("".join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1900)

    async def test_record_outbound_discord_messages_idempotent(self):
        # Use synthetic IDs that won't collide with real Discord traffic in
        # the live DB. The test_id_prefix narrows the SELECT so we're not
        # asserting against rows produced by actual pack-night runs.
        prefix = "test-outbound-"
        ids_to_insert = [f"{prefix}aa", f"{prefix}bb"]

        db = await get_db()
        try:
            cid = await get_or_create_pack_night_conversation(db)
        finally:
            await release_db(db)

        await _record_outbound_discord_messages("Avery", cid, ids_to_insert)
        # Second call with the same IDs should not error or duplicate.
        await _record_outbound_discord_messages("Avery", cid, ids_to_insert)

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT external_message_id, direction FROM platform_message_map "
                "WHERE platform = ? AND bot_identity = ? AND conversation_id = ? "
                "AND external_message_id LIKE ?",
                ("discord", "Avery", cid, f"{prefix}%"),
            )
        finally:
            await release_db(db)
        ids = sorted((r[0], r[1]) for r in rows)
        self.assertEqual(
            ids,
            [(f"{prefix}aa", "outbound"), (f"{prefix}bb", "outbound")],
            "Each test Discord message should appear exactly once.",
        )

    async def test_record_inbound_discord_message_dedups(self):
        ext_id = "test-inbound-zz"

        db = await get_db()
        try:
            cid = await get_or_create_pack_night_conversation(db)
        finally:
            await release_db(db)

        await _record_inbound_discord_message("Avery", cid, ext_id)
        await _record_inbound_discord_message("Avery", cid, ext_id)  # idempotent

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM platform_message_map "
                "WHERE platform = ? AND external_message_id = ? AND direction = ?",
                ("discord", ext_id, "inbound"),
            )
        finally:
            await release_db(db)
        self.assertEqual(rows[0][0], 1)


if __name__ == "__main__":
    unittest.main()
