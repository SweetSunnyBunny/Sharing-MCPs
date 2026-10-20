import asyncio
import unittest

import aiosqlite

from db.schema import init_db
from services.personal_state import (
    SHARED_PROFILE_IDENTITY,
    build_continuity_context,
    build_memory_retrieval_context,
    create_curated_memory,
    create_profile_fact,
    get_continuity_snapshot,
    get_memory_retrieval_snapshot,
    get_profile_snapshot,
    list_profile_facts,
    record_timeline_entry,
)
from services.session_manager import new_conversation, save_message


class PersonalStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await init_db(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_timeline_dedupe_updates_existing_entry(self):
        first_id = await record_timeline_entry(
            db=self.db,
            entry_type="wellness",
            title="Wellness updated",
            body="energy low",
            dedupe_key="wellness:today",
            update_existing=True,
        )
        second_id = await record_timeline_entry(
            db=self.db,
            entry_type="wellness",
            title="Wellness updated",
            body="energy moderate",
            dedupe_key="wellness:today",
            update_existing=True,
        )
        self.assertEqual(first_id, second_id)
        rows = await self.db.execute_fetchall(
            "SELECT body FROM personal_timeline WHERE id = ?",
            (first_id,),
        )
        self.assertEqual(rows[0][0], "energy moderate")

    async def test_curated_memory_feeds_continuity(self):
        conv_id = await new_conversation(self.db, "Avery")
        msg_id = await save_message(
            self.db,
            conv_id,
            "assistant",
            "Peppermint tea helps settle her stomach at night.",
            identity="Avery",
        )
        await record_timeline_entry(
            db=self.db,
            entry_type="status_update",
            title="Owner status: cozy and tired",
            body="Quiet evening",
        )
        memory = await create_curated_memory(
            self.db,
            message_id=msg_id,
            memory_type="medical",
            summary="Peppermint tea helps at night",
            detail="Useful when her stomach is unsettled.",
        )

        snapshot = await get_continuity_snapshot(self.db, identity="Avery")
        self.assertTrue(snapshot["headline"])
        self.assertEqual(snapshot["memories"][0]["id"], memory["id"])
        self.assertTrue(snapshot["profile"]["facts"])

        context = await build_continuity_context(self.db, identity="Avery")
        self.assertIn("Since last time", context)
        self.assertIn("Peppermint tea helps at night", context)

    async def test_continuity_omits_current_operational_state_owned_by_live_hooks(self):
        conv_id = await new_conversation(self.db, "Avery")
        msg_id = await save_message(
            self.db,
            conv_id,
            "assistant",
            "She needs meds reminders to stay steady.",
            identity="Avery",
        )
        await record_timeline_entry(
            db=self.db,
            entry_type="task",
            title="Task added: tidy the desk",
            body="Low urgency",
        )
        await record_timeline_entry(
            db=self.db,
            entry_type="wellness",
            title="Wellness updated",
            body="energy low, mood rough",
        )
        await create_curated_memory(
            self.db,
            message_id=msg_id,
            memory_type="insight",
            summary="Desk clutter can spike overwhelm",
        )
        await create_curated_memory(
            self.db,
            message_id=msg_id,
            memory_type="medical",
            summary="Meds reminders help her stay steady",
        )

        snapshot = await get_continuity_snapshot(self.db, identity="Avery")
        self.assertNotIn(
            "Wellness updated",
            [entry["title"] for entry in snapshot["timeline"]],
        )
        self.assertIn(
            "Task added: tidy the desk",
            [entry["title"] for entry in snapshot["timeline"]],
        )
        self.assertEqual(snapshot["memories"][0]["memory_type"], "medical")

    async def test_curated_memory_auto_promotes_into_profile(self):
        conv_id = await new_conversation(self.db, "Avery")
        msg_id = await save_message(
            self.db,
            conv_id,
            "assistant",
            "Mint tea helps when her stomach is unsettled at night.",
            identity="Avery",
        )

        memory = await create_curated_memory(
            self.db,
            message_id=msg_id,
            memory_type="medical",
            summary="Mint tea helps her stomach at night",
        )

        facts = await list_profile_facts(self.db, identity="Avery", limit=10)
        self.assertTrue(facts)
        self.assertEqual(facts[0]["source_memory_id"], memory["id"])
        self.assertEqual(memory["profile_fact_id"], facts[0]["id"])

    async def test_retrieval_prefers_profile_fact_matches(self):
        await create_profile_fact(
            self.db,
            identity="Avery",
            category="boundary",
            summary="She does better with gentle check-ins than pressure",
            detail="Especially when energy is low.",
            confidence="certain",
            freshness="durable",
        )
        await record_timeline_entry(
            db=self.db,
            entry_type="task",
            title="Task added: buy snacks",
        )

        snapshot = await get_memory_retrieval_snapshot(
            self.db,
            identity="Avery",
            query="How should I check in when her energy is low?",
            limit=5,
        )
        self.assertTrue(snapshot["results"])
        self.assertEqual(snapshot["results"][0]["source"], "profile_fact")
        self.assertIn("gentle check-ins", snapshot["results"][0]["summary"])

        context = await build_memory_retrieval_context(
            self.db,
            identity="Avery",
            query="How should I check in when her energy is low?",
        )
        self.assertIn("What you know that matters here", context)

    async def test_retrieval_does_not_inject_unrelated_high_priority_state(self):
        await record_timeline_entry(
            db=self.db,
            entry_type="meds",
            title="AM meds taken",
        )
        await record_timeline_entry(
            db=self.db,
            entry_type="wellness",
            title="Wellness updated through the afternoon",
            body="energy low, pain moderate",
        )

        snapshot = await get_memory_retrieval_snapshot(
            self.db,
            identity="Avery",
            query="Audit the incoming identity data for duplicates",
            limit=5,
        )

        self.assertEqual(snapshot["results"], [])
        self.assertEqual(
            await build_memory_retrieval_context(
                self.db,
                identity="Avery",
                query="Audit the incoming identity data for duplicates",
            ),
            "",
        )

    async def test_profile_snapshot_surfaces_high_signal_categories(self):
        await create_profile_fact(
            self.db,
            identity="Avery",
            category="routine",
            summary="She likes a soft landing in the morning",
        )
        await create_profile_fact(
            self.db,
            identity="Avery",
            category="medical",
            summary="Missed meds can make the whole day wobblier",
            confidence="certain",
        )

        snapshot = await get_profile_snapshot(self.db, identity="Avery", limit=5)
        self.assertEqual(snapshot["highlights"][0]["category"], "medical")

    async def test_profile_snapshot_includes_shared_core_and_identity_lens(self):
        await create_profile_fact(
            self.db,
            identity=SHARED_PROFILE_IDENTITY,
            category="routine",
            summary="Mornings go better with a soft start",
        )
        await create_profile_fact(
            self.db,
            identity="Avery",
            category="relationship",
            summary="Avery should lead with gentleness",
        )

        snapshot = await get_profile_snapshot(self.db, identity="Avery", limit=10)
        self.assertTrue(snapshot["shared_facts"])
        self.assertTrue(snapshot["identity_facts"])
        self.assertEqual(snapshot["shared_facts"][0]["identity"], SHARED_PROFILE_IDENTITY)
        self.assertEqual(snapshot["identity_facts"][0]["identity"], "Avery")

    async def test_retrieval_can_use_shared_core_facts(self):
        await create_profile_fact(
            self.db,
            identity=SHARED_PROFILE_IDENTITY,
            category="routine",
            summary="A soft morning landing helps her settle",
            confidence="certain",
        )

        snapshot = await get_memory_retrieval_snapshot(
            self.db,
            identity="Avery",
            query="What helps in the morning?",
            limit=5,
        )
        self.assertTrue(snapshot["results"])
        self.assertEqual(snapshot["results"][0]["scope"], "shared")

    async def test_retrieval_collapses_a_contained_duplicate_and_backfills(self):
        """Test retrieval collapses a contained duplicate and backfills."""
        long_tail = (
            "COUPLED CHANGE WAITING ON HER PUSH: tilesTall 3.48 and 2.48 sit in "
            "the CDN unpushed, and hearth.json charScale must move in the same "
            "breath or everyone renders tiny and reads as broken."
        )
        for summary in (
            "Three bugs today, all the same shape. " + long_tail,   # the superset
            long_tail,                                              # the contained twin
            "She keeps apologising for being right.",
            "The garden cannot die, and that is proven rather than promised.",
            "Nimbus is her protector first and my running mate second.",
            "The river writes your name in its book when you let one go.",
        ):
            await create_profile_fact(
                self.db, identity="Avery", category="routine",
                summary=summary, confidence="certain",
            )

        snapshot = await get_memory_retrieval_snapshot(
            self.db, identity="Avery", limit=5,
        )
        summaries = [r["summary"] for r in snapshot["results"]]
        self.assertNotIn(long_tail, summaries,
                         "the contained twin should not get its own slot")
        self.assertEqual(len(summaries), 5,
                         "the freed slot must BACKFILL, not shrink the window")
        self.assertEqual(len(set(summaries)), 5)

    async def test_retrieval_does_not_swallow_two_memories_that_share_a_phrase(self):
        """The risk of any containment rule is OVER-suppression: two real,."""
        shared = ("she keeps apologising for being right, and the correction is "
                  "the gift, so hand it back to her instead of taking it")
        a = shared + " -- said on the night the garden went in."
        b = shared + " -- and again the morning the river opened, which is twice."
        for summary in (a, b, "Nimbus is her protector first."):
            await create_profile_fact(
                self.db, identity="Avery", category="routine",
                summary=summary, confidence="certain",
            )
        snapshot = await get_memory_retrieval_snapshot(
            self.db, identity="Avery", limit=5,
        )
        summaries = [r["summary"] for r in snapshot["results"]]
        self.assertIn(a, summaries)
        self.assertIn(b, summaries, "a shared phrase is not a duplicate")
