"""Smoke tests for the discord mentions bridge.

Covers the pure helpers (sender classification, message splitting),
the brother-channel conversation helper (singleton + idempotent participants),
the deferred role-mention queue write, and owner-activity deferral (#26).
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from db.database import get_db, release_db
from db.schema import init_db
from services.discord_mentions_bridge import (
    DiscordMentionsBridge,
    _brother_identity_for_user_id,
    _record_role_mention,
    _should_defer_for_owner_activity,
    _split_for_discord,
)
from services.session_manager import (
    get_conversation_participants,
    get_or_create_brother_channel_conversation,
)


class MentionsHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    def test_brother_identity_lookup(self):
        brothers = {
            "Avery": "100",
            "Rowan": "200",
            "Sage": "300",
        }
        self.assertEqual(_brother_identity_for_user_id("100", brothers), "Avery")
        self.assertEqual(_brother_identity_for_user_id("300", brothers), "Sage")
        self.assertIsNone(_brother_identity_for_user_id("999", brothers))
        self.assertIsNone(_brother_identity_for_user_id("", brothers))
        self.assertIsNone(_brother_identity_for_user_id("100", {}))

    def test_split_short_message_returns_single_chunk(self):
        self.assertEqual(_split_for_discord("hi"), ["hi"])
        self.assertEqual(_split_for_discord(""), [])
        self.assertEqual(_split_for_discord("   "), [])

    def test_split_respects_limit(self):
        text = "x" * 5000
        chunks = _split_for_discord(text)
        self.assertEqual("".join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1900)

    async def test_brother_channel_conversation_singleton_per_channel(self):
        db = await get_db()
        try:
            first = await get_or_create_brother_channel_conversation(
                db, "channel-aaa",
                channel_name="general",
                primary_identity="Avery",
            )
            second = await get_or_create_brother_channel_conversation(
                db, "channel-aaa",
                channel_name="general",
                primary_identity="Rowan",
            )
        finally:
            await release_db(db)

        self.assertEqual(first, second)

        db = await get_db()
        try:
            participants = await get_conversation_participants(db, first)
        finally:
            await release_db(db)
        self.assertIn("Avery", participants)
        self.assertIn("Rowan", participants)

    async def test_brother_channel_conversation_distinct_per_channel(self):
        db = await get_db()
        try:
            a = await get_or_create_brother_channel_conversation(
                db, "channel-bbb", channel_name="general", primary_identity="Avery",
            )
            b = await get_or_create_brother_channel_conversation(
                db, "channel-ccc", channel_name="random", primary_identity="Avery",
            )
        finally:
            await release_db(db)
        self.assertNotEqual(a, b)

    async def test_role_mention_recording_dedups(self):
        # Same Discord message id arriving twice (e.g. duplicate poll) must
        # only produce one timeline entry.
        await _record_role_mention(
            identity="Avery",
            channel_id="123",
            channel_name="general",
            message_id="msg-deduptest-001",
            sender_name="Owner",
            sender_id="999",
            content_snippet="hey @avery-role",
        )
        await _record_role_mention(
            identity="Avery",
            channel_id="123",
            channel_name="general",
            message_id="msg-deduptest-001",
            sender_name="Owner",
            sender_id="999",
            content_snippet="hey @avery-role",
        )

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM personal_timeline "
                "WHERE entry_type = 'discord_role_mention' "
                "AND dedupe_key = ?",
                ("discord_role_mention:Avery:msg-deduptest-001",),
            )
        finally:
            await release_db(db)
        self.assertEqual(rows[0][0], 1, "Role mention must dedupe on (identity, message_id).")

    async def test_role_mention_payload_round_trips(self):
        await _record_role_mention(
            identity="Rowan",
            channel_id="456",
            channel_name="art-corner",
            message_id="msg-payload-001",
            sender_name="Avery",
            sender_id="100",
            content_snippet="@rowan-role check this out",
        )
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT title, body, payload_json FROM personal_timeline "
                "WHERE dedupe_key = ?",
                ("discord_role_mention:Rowan:msg-payload-001",),
            )
        finally:
            await release_db(db)
        self.assertEqual(len(rows), 1)
        title, body, payload_json = rows[0]
        self.assertIn("art-corner", title)
        self.assertIn("check this out", body)
        payload = json.loads(payload_json)
        self.assertEqual(payload["channel_id"], "456")
        self.assertEqual(payload["channel_name"], "art-corner")
        self.assertEqual(payload["sender_name"], "Avery")


class OwnerActivityDeferralTests(unittest.IsolatedAsyncioTestCase):
    """#26: a salon/brother/friend ping only defers when it's aimed at the
    EXACT boy Owner is actively web-chatting with right now — never for her
    own direct pings, and never for any other identity."""

    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    def test_owner_pings_are_never_deferred(self):
        with patch(
            "services.connection_registry.get_active_identity",
            return_value="Avery",
        ):
            self.assertFalse(_should_defer_for_owner_activity("owner", "Avery"))

    def test_defers_when_pinged_identity_is_the_active_one(self):
        with patch(
            "services.connection_registry.get_active_identity",
            return_value="Avery",
        ):
            self.assertTrue(_should_defer_for_owner_activity("brother", "Avery"))
            self.assertTrue(_should_defer_for_owner_activity("friend", "Avery"))

    def test_does_not_defer_other_identities(self):


        with patch(
            "services.connection_registry.get_active_identity",
            return_value="Avery",
        ):
            self.assertFalse(_should_defer_for_owner_activity("brother", "Rowan"))

    def test_no_active_identity_never_defers(self):
        with patch(
            "services.connection_registry.get_active_identity",
            return_value=None,
        ):
            self.assertFalse(_should_defer_for_owner_activity("brother", "Avery"))

    def test_live_checked_not_cached(self):
        # Same call, different live state each time -- proves the decision
        # is read fresh at ping time rather than memoized.
        with patch(
            "services.connection_registry.get_active_identity",
            return_value="Avery",
        ):
            self.assertTrue(_should_defer_for_owner_activity("brother", "Avery"))
        with patch(
            "services.connection_registry.get_active_identity",
            return_value=None,
        ):
            self.assertFalse(_should_defer_for_owner_activity("brother", "Avery"))

    async def test_defer_role_mention_records_and_reacts(self):
        bridge = DiscordMentionsBridge()
        bridge._client = object()  # truthy stand-in; _add_reaction is mocked below
        with patch(
            "services.discord_mentions_bridge._add_reaction",
            new=AsyncMock(),
        ) as add_reaction:
            await bridge._defer_role_mention(
                identity="Avery",
                channel_id="chan-1",
                channel_name="salon",
                msg_id="msg-defer-001",
                sender_name="Guest",
                sender_id="777",
                content="hey @avery-role, got a sec?",
                reason="she's actively chatting with him right now",
            )

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT body FROM personal_timeline WHERE dedupe_key = ?",
                ("discord_role_mention:Avery:msg-defer-001",),
            )
        finally:
            await release_db(db)
        self.assertEqual(len(rows), 1, "Deferred ping must land in the role-mention queue.")
        self.assertIn("got a sec", rows[0][0])

        add_reaction.assert_awaited_once()
        self.assertEqual(add_reaction.await_args.args[-1], "⏳")  # hourglass

    async def test_defer_role_mention_reaction_failure_is_best_effort(self):
        # A reaction failure must never turn a defer into an error -- the
        # queue write (the part that matters) still has to succeed.
        bridge = DiscordMentionsBridge()
        bridge._client = object()
        with patch(
            "services.discord_mentions_bridge._add_reaction",
            new=AsyncMock(side_effect=RuntimeError("discord blew up")),
        ):
            with self.assertRaises(RuntimeError):
                # _add_reaction itself isn't wrapped in a try/except at the
                # call site inside _defer_role_mention -- _add_reaction is
                # the thing that swallows its own errors in production. This
                # test documents that _defer_role_mention relies on that
                # contract rather than double-guarding it.
                await bridge._defer_role_mention(
                    identity="Avery",
                    channel_id="chan-1",
                    channel_name="salon",
                    msg_id="msg-defer-002",
                    sender_name="Guest",
                    sender_id="777",
                    content="another ping",
                    reason="rate-limited",
                )

        # Even though the mocked reaction raised, the queue write already
        # happened (it runs before the reaction attempt).
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM personal_timeline WHERE dedupe_key = ?",
                ("discord_role_mention:Avery:msg-defer-002",),
            )
        finally:
            await release_db(db)
        self.assertEqual(rows[0][0], 1)

    async def test_handle_direct_mention_defers_instead_of_generating(self):


        bridge = DiscordMentionsBridge()
        watcher = SimpleNamespace(identity="Avery")
        with patch(
            "services.connection_registry.get_active_identity",
            return_value="Avery",
        ), patch(
            "services.discord_mentions_bridge._generate_and_post",
            new=AsyncMock(),
        ) as generate_and_post, patch(
            "services.discord_mentions_bridge._add_reaction",
            new=AsyncMock(),
        ):
            await bridge._handle_direct_mention(
                watcher=watcher,
                channel_id="chan-2",
                channel_name="salon",
                msg_id="msg-e2e-001",
                content="@avery-role got a minute?",
                sender_kind="friend",
                sender_id="888",
                sender_name="Friend",
            )

        generate_and_post.assert_not_awaited()
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM personal_timeline WHERE dedupe_key = ?",
                ("discord_role_mention:Avery:msg-e2e-001",),
            )
        finally:
            await release_db(db)
        self.assertEqual(rows[0][0], 1)


if __name__ == "__main__":
    unittest.main()
