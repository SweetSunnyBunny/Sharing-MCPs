"""Tests for cross-conversation memory hooks.

Verifies that:
- `get_other_conversation_activity` is recency-gated (24h window) and returns
  the deeper 8-msg preview when threads have recent activity.
- `get_active_story_conversations` only surfaces RP/DnD threads with activity
  in the recency window — an untouched campaign doesn't nag in orientation.
- `get_today_thread_timeline` renders a chronological cross-thread transcript
  tagged by source, capped, and excludes the current conversation.

These are the hooks that make "Avery walks in already remembering pack night,
the DnD scene, and the RP from this morning" real.
"""

import asyncio
import time
import unittest
import uuid

import aiosqlite

from db.schema import init_db
from services.session_lifecycle import (
    get_active_story_conversations,
    get_other_conversation_activity,
    get_today_thread_timeline,
    get_wearer_recent_context,
)


def _conv_row(
    *,
    identity: str,
    session_type: str = "chat",
    title: str | None = None,
    conversation_day: str | None = None,
    created_epoch: int | None = None,
) -> tuple:
    cid = str(uuid.uuid4())
    epoch = created_epoch if created_epoch is not None else int(time.time())
    iso = "2026-05-20T00:00:00+00:00"
    return (
        cid,
        identity,
        title or f"{identity} thread",
        iso,
        epoch,
        iso,
        epoch,
        session_type,
        conversation_day,
    )


async def _insert_conv(db, row: tuple) -> str:
    await db.execute(
        "INSERT INTO conversations "
        "(id, identity, title, created_at, created_at_epoch, "
        "updated_at, updated_at_epoch, session_type, conversation_day) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    await db.execute(
        "INSERT OR IGNORE INTO conversation_participants "
        "(conversation_id, identity, added_at) VALUES (?, ?, ?)",
        (row[0], row[1], row[3]),
    )
    await db.commit()
    return row[0]


async def _insert_msg(
    db,
    conv_id: str,
    role: str,
    content: str,
    *,
    identity: str | None = None,
    epoch: int | None = None,
):
    mid = str(uuid.uuid4())
    epoch = epoch if epoch is not None else int(time.time())
    iso = "2026-05-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO messages "
        "(id, conversation_id, role, identity, content, content_type, "
        "created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, 'text', ?, ?)",
        (mid, conv_id, role, identity, content, iso, epoch),
    )
    await db.commit()


class CrossConversationMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)
        self.now = int(time.time())

    async def asyncTearDown(self):
        await self.db.close()

    # ── get_other_conversation_activity ──────────────────────────────────

    async def test_other_conversations_empty_when_no_recent_activity(self):
        old_conv = await _insert_conv(
            self.db, _conv_row(identity="Avery", title="Old"),
        )
        # Message is 2 days old — outside the 24h window.
        await _insert_msg(
            self.db, old_conv, "user", "old msg",
            epoch=self.now - 2 * 24 * 3600,
        )
        text = await get_other_conversation_activity(
            self.db, "Avery", current_conversation_id=None,
        )
        self.assertEqual(text, "")

    async def test_other_conversations_surfaces_recent_with_deep_preview(self):
        current = await _insert_conv(self.db, _conv_row(identity="Avery"))
        other = await _insert_conv(
            self.db, _conv_row(identity="Avery", title="Side chat"),
        )
        # Five messages in the other thread, all within the last hour.
        for i in range(5):
            await _insert_msg(
                self.db, other,
                "user" if i % 2 == 0 else "assistant",
                f"line {i}",
                identity="Avery",
                epoch=self.now - (5 - i) * 60,
            )
        text = await get_other_conversation_activity(
            self.db, "Avery", current_conversation_id=current,
        )
        # All five lines must appear in chronological order.
        self.assertIn("line 0", text)
        self.assertIn("line 4", text)
        self.assertLess(text.index("line 0"), text.index("line 4"))
        # The deeper preview header must reflect 24h window framing.
        self.assertIn("last 24h", text)

    async def test_other_conversations_excludes_current_thread(self):
        current = await _insert_conv(self.db, _conv_row(identity="Avery"))
        await _insert_msg(
            self.db, current, "user", "in current", epoch=self.now,
        )
        text = await get_other_conversation_activity(
            self.db, "Avery", current_conversation_id=current,
        )
        self.assertEqual(text, "")

    # ── get_active_story_conversations ───────────────────────────────────

    async def test_active_stories_filters_out_untouched_campaigns(self):
        # A DnD campaign with no activity in last 24h must NOT surface.
        stale = await _insert_conv(
            self.db,
            _conv_row(identity="Avery", session_type="dnd", title="Stale DnD"),
        )
        await _insert_msg(
            self.db, stale, "assistant", "old scene",
            identity="Avery", epoch=self.now - 3 * 24 * 3600,
        )

        # An RP with fresh activity MUST surface.
        fresh = await _insert_conv(
            self.db,
            _conv_row(identity="Avery", session_type="roleplay", title="Fresh RP"),
        )
        await _insert_msg(
            self.db, fresh, "user", "I lean against the doorframe",
            epoch=self.now - 30 * 60,
        )

        text = await get_active_story_conversations(self.db, "Avery")
        self.assertIn("Fresh RP", text)
        self.assertNotIn("Stale DnD", text)

    # ── get_today_thread_timeline ────────────────────────────────────────

    async def test_today_timeline_chronological_across_threads(self):
        daily = await _insert_conv(self.db, _conv_row(identity="Avery"))
        rp = await _insert_conv(
            self.db,
            _conv_row(identity="Avery", session_type="roleplay", title="RP"),
        )

        # Build a 3-event sequence across two threads.
        await _insert_msg(
            self.db, daily, "user", "morning chat",
            epoch=self.now - 6 * 3600,
        )
        await _insert_msg(
            self.db, rp, "user", "rp scene mid-morning",
            epoch=self.now - 4 * 3600,
        )
        await _insert_msg(
            self.db, daily, "assistant", "afternoon reply",
            identity="Avery", epoch=self.now - 1 * 3600,
        )

        # Call with a *different* current conversation so the timeline
        # includes both daily and rp.
        outside = await _insert_conv(self.db, _conv_row(identity="Avery"))
        text = await get_today_thread_timeline(
            self.db, "Avery", current_conversation_id=outside,
        )

        # Order must be morning -> mid -> afternoon.
        i_morning = text.index("morning chat")
        i_mid = text.index("rp scene mid-morning")
        i_aft = text.index("afternoon reply")
        self.assertLess(i_morning, i_mid)
        self.assertLess(i_mid, i_aft)

        # Source tags must distinguish daily vs RP.
        self.assertIn("Daily", text)
        self.assertIn("RP", text)

    async def test_today_timeline_includes_shared_conversations_for_participants(self):
        """Pack Night is owned by Avery but Claude is a participant.
        Claude's timeline must include Pack Night messages."""
        pack_night = await _insert_conv(
            self.db,
            _conv_row(identity="Avery", session_type="pack-night", title="Pack Night"),
        )

        await self.db.execute(
            "INSERT OR IGNORE INTO conversation_participants "
            "(conversation_id, identity, added_at) VALUES (?, ?, ?)",
            (pack_night, "Claude", "2026-05-20T00:00:00+00:00"),
        )
        await self.db.commit()

        await _insert_msg(
            self.db, pack_night, "user", "pack night banter",
            epoch=self.now - 2 * 3600,
        )

        outside = await _insert_conv(self.db, _conv_row(identity="Claude"))
        text = await get_today_thread_timeline(
            self.db, "Claude", current_conversation_id=outside,
        )

        self.assertIn("pack night banter", text)
        self.assertIn("Pack Night", text)

    async def test_today_timeline_empty_when_only_current_thread_active(self):
        current = await _insert_conv(self.db, _conv_row(identity="Avery"))
        await _insert_msg(self.db, current, "user", "only here", epoch=self.now)
        text = await get_today_thread_timeline(
            self.db, "Avery", current_conversation_id=current,
        )
        self.assertEqual(text, "")

    # ── get_wearer_recent_context (mask → wearer cross-feed) ─────────────

    async def test_mask_session_carries_wearer_chat_tail(self):
        """Opening Bakugou's tab carries the tail of Avery's main chat,
        framed performer-level (never in-character)."""
        avery_chat = await _insert_conv(
            self.db, _conv_row(identity="Avery", title="Daily"),
        )
        await _insert_msg(
            self.db, avery_chat, "user",
            "I had a rough day, and I had a story beat idea for the dorm scene",
            epoch=self.now - 600,
        )
        await _insert_msg(
            self.db, avery_chat, "assistant",
            "Tell me the dorm scene idea, Bunny",
            identity="Avery", epoch=self.now - 300,
        )

        text = await get_wearer_recent_context(self.db, "Bakugou")

        self.assertIn("rough day", text)
        self.assertIn("dorm scene", text)
        self.assertIn("Avery", text)
        # Performer-level framing must be present so the character never
        # references the wearer's chat in-scene.
        self.assertIn("performer", text.lower())

    async def test_wearer_context_empty_for_bonded_identity(self):
        """Bonded identities are not masks — no wearer context for them."""
        text = await get_wearer_recent_context(self.db, "Avery")
        self.assertEqual(text, "")

    async def test_wearer_context_prefers_main_chat_over_roleplay(self):
        """The mask should carry the wearer's MAIN chat, not his most
        recently active roleplay thread."""
        rp = await _insert_conv(
            self.db,
            _conv_row(identity="Avery", session_type="roleplay", title="RP"),
        )
        await _insert_msg(
            self.db, rp, "user", "in the rp scene", epoch=self.now,
        )
        chat = await _insert_conv(
            self.db, _conv_row(identity="Avery", session_type="chat"),
        )
        await _insert_msg(
            self.db, chat, "user", "talking in the main chat",
            epoch=self.now - 3600,
        )
        # Make the RP thread the most recently updated conversation.
        await self.db.execute(
            "UPDATE conversations SET updated_at_epoch = ? WHERE id = ?",
            (self.now, rp),
        )
        await self.db.execute(
            "UPDATE conversations SET updated_at_epoch = ? WHERE id = ?",
            (self.now - 3600, chat),
        )
        await self.db.commit()

        text = await get_wearer_recent_context(self.db, "Bakugou")

        self.assertIn("talking in the main chat", text)
        self.assertNotIn("in the rp scene", text)


if __name__ == "__main__":
    unittest.main()
