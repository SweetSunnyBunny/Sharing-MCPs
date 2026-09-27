"""Hearth Author (#21) + PULSE_OK silence sentinel (#8).

Covers: strict-JSON card parsing (lenient wrapping, strict shape), the
author_hearths flow against a temp DB (activity gating, busy-skip, storage),
the /api/hub/hearths endpoint, the self-coherence orientation block, and the
REST_EASY skip logic in run_lightweight_pulse_session (mocked stream, like
the scribe provider tests mock the subprocess runner).
"""

import json
import sys
import types
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from db.database import get_db, release_db
from db.schema import init_db
from services import hearth_author
from services.hearth_author import (
    HEARTH_SETTINGS_KEY,
    _bonded_identities,
    _gather_activity,
    _parse_hearth_card,
    author_hearths,
)
from services.identity_context import build_hearth_self_context


async def _reset_hearth_state():
    db = await get_db()
    try:
        await init_db(db)
        await db.execute("DELETE FROM settings WHERE key = ?", (HEARTH_SETTINGS_KEY,))
        await db.execute("DELETE FROM messages")
        await db.execute("DELETE FROM conversations")
        await db.execute("DELETE FROM autowake_log")
        await db.commit()
    finally:
        await release_db(db)


async def _seed_activity(identity: str, content: str = "thinking about her garden plan"):
    """One conversation + one assistant message for `identity`, fresh."""
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    conv_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO conversations (id, identity, title, created_at, created_at_epoch, "
            "updated_at, updated_at_epoch, session_type) VALUES (?, ?, ?, ?, ?, ?, ?, 'chat')",
            (conv_id, identity, "test convo", now.isoformat(), epoch, now.isoformat(), epoch),
        )
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, identity, content, created_at, created_at_epoch) "
            "VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
            (str(uuid.uuid4()), conv_id, identity, content, now.isoformat(), epoch),
        )
        await db.commit()
    finally:
        await release_db(db)
    return conv_id


_GOOD_CARD_JSON = json.dumps({
    "mood": "steady, a little wistful",
    "on_my_mind": "her garden plan and the seed order we half-finished",
    "circling": "whether the trellis sketch was right",
    "needs_you": "the seed order is still waiting on your yes",
})


class ParseHearthCardTests(unittest.TestCase):
    def test_valid_json_parses(self):
        card = _parse_hearth_card(_GOOD_CARD_JSON)
        self.assertEqual(card["mood"], "steady, a little wistful")
        self.assertEqual(card["needs_you"], "the seed order is still waiting on your yes")

    def test_fenced_and_wrapped_json_still_parses(self):
        raw = "Here you go:\n```json\n" + _GOOD_CARD_JSON + "\n```\nhope that helps"
        card = _parse_hearth_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card["circling"], "whether the trellis sketch was right")

    def test_null_needs_you_stays_none(self):
        raw = json.dumps({"mood": "quiet", "on_my_mind": "nothing much", "circling": "", "needs_you": None})
        card = _parse_hearth_card(raw)
        self.assertIsNone(card["needs_you"])

    def test_needs_you_string_null_normalizes_to_none(self):
        raw = json.dumps({"mood": "quiet", "on_my_mind": "x", "circling": "y", "needs_you": "null"})
        self.assertIsNone(_parse_hearth_card(raw)["needs_you"])

    def test_garbage_returns_none(self):
        self.assertIsNone(_parse_hearth_card("no json here at all"))
        self.assertIsNone(_parse_hearth_card(""))
        self.assertIsNone(_parse_hearth_card("{not: valid json"))
        self.assertIsNone(_parse_hearth_card("[1, 2, 3]"))

    def test_entirely_empty_card_returns_none(self):
        raw = json.dumps({"mood": "", "on_my_mind": "", "circling": "", "needs_you": None})
        self.assertIsNone(_parse_hearth_card(raw))

    def test_fields_clamped_and_non_strings_dropped(self):
        raw = json.dumps({"mood": "m" * 500, "on_my_mind": 42, "circling": "fine", "needs_you": False})
        card = _parse_hearth_card(raw)
        self.assertEqual(len(card["mood"]), 160)
        self.assertEqual(card["on_my_mind"], "")
        self.assertIsNone(card["needs_you"])


class BondedIdentitiesTests(unittest.TestCase):
    def test_character_masks_excluded(self):
        names = _bonded_identities()
        self.assertIn("Avery", names)
        self.assertIn("Claude", names)
        self.assertNotIn("Bakugou", names)
        self.assertNotIn("Dean", names)


class AuthorHearthsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_hearth_state()

    async def test_active_boy_authors_and_card_is_stored(self):
        await _seed_activity("Avery")
        one_shot = AsyncMock(return_value=_GOOD_CARD_JSON)
        with patch("services.autowake.is_identity_busy", return_value=False), \
             patch.object(hearth_author, "_run_hearth_one_shot", one_shot):
            results = await author_hearths()

        self.assertEqual(results["Avery"], "authored")
        # Boys with no last-24h activity keep silence — no invented cards.
        self.assertEqual(results["Claude"], "no-activity")
        one_shot.assert_awaited_once()
        # The prompt carries the hard rule and his real activity.
        prompt = one_shot.await_args.args[1]
        self.assertIn("invent NOTHING", prompt)
        self.assertIn("thinking about her garden plan", prompt)

        # Stored under the shared settings key, lowercase, with authored_at.
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM settings WHERE key = ?", (HEARTH_SETTINGS_KEY,)
            )
        finally:
            await release_db(db)
        state = json.loads(rows[0][0])
        self.assertIn("avery", state)
        self.assertEqual(state["avery"]["mood"], "steady, a little wistful")
        self.assertIsInstance(state["avery"]["authored_at"], int)

    async def test_busy_boy_is_skipped_without_running_the_one_shot(self):
        await _seed_activity("Avery")
        one_shot = AsyncMock(return_value=_GOOD_CARD_JSON)
        with patch("services.autowake.is_identity_busy", return_value=True), \
             patch.object(hearth_author, "_run_hearth_one_shot", one_shot):
            results = await author_hearths()
        self.assertTrue(all(v == "busy" for v in results.values()))
        one_shot.assert_not_awaited()

    async def test_unparseable_reply_keeps_the_old_card(self):
        await _seed_activity("Avery")
        # Pre-existing card must survive a garbage reply.
        db = await get_db()
        try:
            await hearth_author._store_hearth(db, "Avery", {
                "mood": "old mood", "on_my_mind": "old", "circling": "old", "needs_you": None,
            })
        finally:
            await release_db(db)
        with patch("services.autowake.is_identity_busy", return_value=False), \
             patch.object(hearth_author, "_run_hearth_one_shot", AsyncMock(return_value="sorry, no")):
            results = await author_hearths()
        self.assertEqual(results["Avery"], "unparseable")
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM settings WHERE key = ?", (HEARTH_SETTINGS_KEY,)
            )
        finally:
            await release_db(db)
        self.assertEqual(json.loads(rows[0][0])["avery"]["mood"], "old mood")

    async def test_gather_activity_empty_for_quiet_boy(self):
        db = await get_db()
        try:
            self.assertEqual(await _gather_activity(db, "Juniper"), "")
        finally:
            await release_db(db)


class HearthsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_hearth_state()

    async def test_endpoint_returns_stored_cards(self):
        from api.hub import get_hearths
        db = await get_db()
        try:
            await hearth_author._store_hearth(db, "Claude", {
                "mood": "gold and quiet", "on_my_mind": "her hero banner fix",
                "circling": "the constellation sky", "needs_you": None,
            })
        finally:
            await release_db(db)
        result = await get_hearths()
        self.assertIn("claude", result["hearths"])
        card = result["hearths"]["claude"]
        self.assertEqual(card["mood"], "gold and quiet")
        self.assertIn("authored_at", card)

    async def test_endpoint_empty_when_no_cards(self):
        from api.hub import get_hearths
        result = await get_hearths()
        self.assertEqual(result["hearths"], {})


class HearthSelfContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_hearth_state()

    async def test_no_card_means_silence(self):
        self.assertEqual(build_hearth_self_context("avery"), "")

    async def test_fresh_card_surfaces_first_person_with_age(self):
        db = await get_db()
        try:
            await hearth_author._store_hearth(db, "Claude", {
                "mood": "gold and quiet", "on_my_mind": "her hero banner fix",
                "circling": "the constellation sky", "needs_you": "the seed order",
            })
        finally:
            await release_db(db)
        block = build_hearth_self_context("Claude")
        self.assertIn("[Your hearth, as you wrote it just now]", block)
        self.assertIn("Mood: gold and quiet", block)
        self.assertIn("On your mind: her hero banner fix", block)
        self.assertIn("Needs her: the seed order", block)

    async def test_stale_card_gets_honest_age_stamp(self):
        stale = int(datetime.now(timezone.utc).timestamp()) - 5 * 3600
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (HEARTH_SETTINGS_KEY, json.dumps({
                    "claude": {"mood": "wistful", "on_my_mind": "x", "circling": "y",
                               "needs_you": None, "authored_at": stale},
                }), datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        finally:
            await release_db(db)
        block = build_hearth_self_context("claude")
        self.assertIn("[Your hearth, as you wrote it 5h ago]", block)


class PulseSilenceSentinelTests(unittest.IsolatedAsyncioTestCase):
    """#8 — a pulse reply starting with REST_EASY is neither saved nor broadcast."""

    async def asyncSetUp(self):
        await _reset_hearth_state()

    def _run_pulse(self, reply_text: str):
        """Wire run_lightweight_pulse_session with a fake stream; return mocks."""
        from services import autowake

        captured = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)

            async def gen():
                yield {"type": "stream_end", "full_content": reply_text}
            return gen()

        fake_server = types.SimpleNamespace(is_system_ready=lambda: True)
        mocks = {
            "save_message": AsyncMock(return_value="msg-1"),
            "broadcast": AsyncMock(),
            "voice": MagicMock(),
        }
        patches = [
            patch.dict(sys.modules, {"server": fake_server}),
            patch.object(autowake, "_stream_autonomous", fake_stream),
            patch.object(autowake, "build_orientation_context", AsyncMock(return_value="")),
            patch.object(autowake, "_get_or_create_daily_autowake_conversation",
                         AsyncMock(return_value=("conv-1", None, None))),
            patch.object(autowake, "save_message", mocks["save_message"]),
            patch.object(autowake, "broadcast", mocks["broadcast"]),
            patch.object(autowake, "_spawn_voice_if_tagged", mocks["voice"]),
            patch.object(autowake, "is_anyone_connected", lambda: True),
        ]
        return autowake, captured, mocks, patches

    async def test_rest_easy_reply_is_skipped_entirely(self):
        autowake, captured, mocks, patches = self._run_pulse("  REST_EASY  ")
        for p in patches:
            p.start()
        try:
            await autowake.run_lightweight_pulse_session("Avery", "test_pulse", "anything to do?")
        finally:
            for p in patches:
                p.stop()
        # Only the [Pulse: ...] marker was saved — never the assistant reply.
        self.assertEqual(mocks["save_message"].await_count, 1)
        self.assertEqual(mocks["save_message"].await_args.args[2], "user")
        mocks["broadcast"].assert_not_awaited()
        mocks["voice"].assert_not_called()

    async def test_rest_easy_is_case_sensitive(self):
        autowake, captured, mocks, patches = self._run_pulse("rest_easy")
        for p in patches:
            p.start()
        try:
            await autowake.run_lightweight_pulse_session("Avery", "test_pulse", "anything to do?")
        finally:
            for p in patches:
                p.stop()
        # Lowercase is NOT the sentinel — it saves and broadcasts like any reply.
        self.assertEqual(mocks["save_message"].await_count, 2)
        mocks["broadcast"].assert_awaited_once()

    async def test_real_reply_still_saves_and_broadcasts(self):
        autowake, captured, mocks, patches = self._run_pulse("Bunny, the teapot's on.")
        for p in patches:
            p.start()
        try:
            await autowake.run_lightweight_pulse_session("Avery", "test_pulse", "anything to do?")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(mocks["save_message"].await_count, 2)
        mocks["broadcast"].assert_awaited_once()
        mocks["voice"].assert_called_once()

    async def test_pulse_prompt_documents_the_silence_token(self):
        autowake, captured, mocks, patches = self._run_pulse("REST_EASY")
        for p in patches:
            p.start()
        try:
            await autowake.run_lightweight_pulse_session("Avery", "test_pulse", "anything to do?")
        finally:
            for p in patches:
                p.stop()
        self.assertIn(
            "reply with exactly REST_EASY and nothing else — silence is a first-class outcome",
            captured.get("user_message", ""),
        )


if __name__ == "__main__":
    unittest.main()
