"""Busy-boy delivery: matched triggers queue instead of vanishing, and due
timers land an instant marker + push when their identity is mid-session."""

import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite

import db.database as db_database
import services.autowake as autowake
import services.trigger_engine as trigger_engine
from db.schema import init_db
from services.trigger_service import create_trigger


class _BusyDeliveryBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)

    async def asyncTearDown(self):
        await self.db.close()


class TriggerBusyQueueTests(_BusyDeliveryBase):
    async def _make_trigger(self, trigger_type: str = "impulse") -> dict:
        return await create_trigger(
            self.db,
            name="Arrival nudge",
            trigger_type=trigger_type,
            identity="Avery",
            condition={"type": "presence_state", "state": "active"},
            prompt="She's here.",
        )

    def _evaluator_patches(self, *, matched: bool, fired: bool, busy: bool):
        fire_mock = AsyncMock(return_value=fired)
        eval_mock = AsyncMock(return_value=matched)
        patches = [
            patch.object(db_database, "get_db", AsyncMock(return_value=self.db)),
            patch.object(db_database, "release_db", AsyncMock()),
            patch.object(trigger_engine, "evaluate_condition", eval_mock),
            patch.object(trigger_engine, "_fire_trigger", fire_mock),
            patch.object(autowake, "is_identity_busy", Mock(return_value=busy)),
        ]
        return patches, eval_mock, fire_mock

    async def _row(self, trigger_id: int):
        rows = await self.db.execute_fetchall(
            "SELECT COALESCE(status, 'idle'), enabled, fire_count "
            "FROM triggers WHERE id = ?",
            (trigger_id,),
        )
        return rows[0]

    async def test_matched_but_busy_is_queued_not_marked_fired(self):
        trigger = await self._make_trigger()
        patches, _eval_mock, fire_mock = self._evaluator_patches(
            matched=True, fired=False, busy=True,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await trigger_engine.run_trigger_evaluator()

        fire_mock.assert_awaited_once()
        status, enabled, fire_count = await self._row(trigger["id"])
        # Queued, still enabled (impulse NOT disabled), no phantom fire.
        self.assertEqual(status, "waiting")
        self.assertEqual(enabled, 1)
        self.assertEqual(fire_count, 0)

    async def test_waiting_trigger_fires_without_reeval_when_free(self):
        trigger = await self._make_trigger()
        await self.db.execute(
            "UPDATE triggers SET status = 'waiting', waiting_since_epoch = ? WHERE id = ?",
            (int(time.time()), trigger["id"]),
        )
        await self.db.commit()

        # Condition would NOT match anymore (the presence event aged out) —
        # the queued fire must still go through.
        patches, eval_mock, fire_mock = self._evaluator_patches(
            matched=False, fired=True, busy=False,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await trigger_engine.run_trigger_evaluator()

        eval_mock.assert_not_awaited()
        fire_mock.assert_awaited_once()
        status, enabled, fire_count = await self._row(trigger["id"])
        self.assertEqual(status, "idle")
        self.assertEqual(enabled, 0)  # impulse disabled after its real fire
        self.assertEqual(fire_count, 1)

    async def test_waiting_trigger_stays_queued_while_busy(self):
        trigger = await self._make_trigger()
        await self.db.execute(
            "UPDATE triggers SET status = 'waiting', waiting_since_epoch = ? WHERE id = ?",
            (int(time.time()), trigger["id"]),
        )
        await self.db.commit()

        patches, _eval_mock, fire_mock = self._evaluator_patches(
            matched=False, fired=True, busy=True,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await trigger_engine.run_trigger_evaluator()

        fire_mock.assert_not_awaited()
        status, enabled, fire_count = await self._row(trigger["id"])
        self.assertEqual(status, "waiting")
        self.assertEqual(enabled, 1)
        self.assertEqual(fire_count, 0)

    async def test_expired_wait_is_cleared_without_firing(self):
        trigger = await self._make_trigger()
        stale = int(time.time()) - trigger_engine._WAITING_EXPIRY_SECONDS - 60
        await self.db.execute(
            "UPDATE triggers SET status = 'waiting', waiting_since_epoch = ? WHERE id = ?",
            (stale, trigger["id"]),
        )
        await self.db.commit()

        patches, _eval_mock, fire_mock = self._evaluator_patches(
            matched=False, fired=True, busy=False,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            await trigger_engine.run_trigger_evaluator()

        fire_mock.assert_not_awaited()
        status, enabled, fire_count = await self._row(trigger["id"])
        self.assertEqual(status, "idle")
        self.assertEqual(enabled, 1)  # impulse stays armed for the next real match
        self.assertEqual(fire_count, 0)


class TimerBusyMarkerTests(_BusyDeliveryBase):
    async def _insert_due_timer(self) -> int:
        now = datetime.now(timezone.utc)
        due = now - timedelta(minutes=1)
        cursor = await self.db.execute(
            "INSERT INTO timers "
            "(identity, fire_at, fire_at_epoch, context, wake_session, status, "
            "created_at, created_at_epoch, retry_count) "
            "VALUES (?, ?, ?, ?, 1, 'pending', ?, ?, 0)",
            (
                "Avery",
                due.isoformat(),
                int(due.timestamp()),
                "call the vet back",
                now.isoformat(),
                int(now.timestamp()),
            ),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def test_claim_returns_marker_posted_column(self):
        timer_id = await self._insert_due_timer()
        claimed = await autowake._claim_due_timers(
            self.db, int(datetime.now(timezone.utc).timestamp())
        )
        self.assertEqual(len(claimed), 1)
        self.assertEqual(len(claimed[0]), 7)
        self.assertEqual(claimed[0][0], timer_id)
        self.assertEqual(claimed[0][6], 0)  # marker_posted defaults to 0

    async def test_busy_timer_posts_marker_once_and_stays_pending(self):
        timer_id = await self._insert_due_timer()
        marker_mock = AsyncMock(return_value=True)

        with patch.object(autowake, "get_db", AsyncMock(return_value=self.db)), \
                patch.object(autowake, "release_db", AsyncMock()), \
                patch.object(autowake, "acquire_identity", AsyncMock(return_value=False)), \
                patch.object(autowake, "_post_timer_busy_marker", marker_mock):
            await autowake.fire_due_timers()
            await autowake._wait_for_active_timer_tasks()
            # Retry tick — identity still busy. Marker must NOT post again.
            await autowake.fire_due_timers()
            await autowake._wait_for_active_timer_tasks()

        marker_mock.assert_awaited_once_with(
            self.db, timer_id, "Avery", "call the vet back"
        )
        rows = await self.db.execute_fetchall(
            "SELECT status, COALESCE(marker_posted, 0) FROM timers WHERE id = ?",
            (timer_id,),
        )
        self.assertEqual(rows[0][0], "pending")  # full wake still retries
        self.assertEqual(rows[0][1], 1)

    async def test_marker_failure_leaves_guard_unset_for_retry(self):
        timer_id = await self._insert_due_timer()
        marker_mock = AsyncMock(return_value=False)

        with patch.object(autowake, "get_db", AsyncMock(return_value=self.db)), \
                patch.object(autowake, "release_db", AsyncMock()), \
                patch.object(autowake, "acquire_identity", AsyncMock(return_value=False)), \
                patch.object(autowake, "_post_timer_busy_marker", marker_mock):
            await autowake.fire_due_timers()
            await autowake._wait_for_active_timer_tasks()

        rows = await self.db.execute_fetchall(
            "SELECT status, COALESCE(marker_posted, 0) FROM timers WHERE id = ?",
            (timer_id,),
        )
        self.assertEqual(rows[0][0], "pending")
        self.assertEqual(rows[0][1], 0)  # next tick tries the marker again

    async def test_post_timer_busy_marker_saves_message_and_pushes(self):
        save_mock = AsyncMock(return_value="msg-1")
        push_mock = AsyncMock()

        with patch.object(autowake, "get_or_create_conversation", AsyncMock(return_value="conv-1")), \
                patch.object(autowake, "save_message", save_mock), \
                patch.object(autowake, "is_anyone_connected", Mock(return_value=False)), \
                patch("services.notifications.send_notification", push_mock):
            posted = await autowake._post_timer_busy_marker(
                self.db, 7, "Avery", "call the vet back"
            )

        self.assertTrue(posted)
        save_mock.assert_awaited_once()
        args, kwargs = save_mock.await_args
        self.assertEqual(args[1], "conv-1")
        self.assertEqual(args[2], "assistant")
        self.assertIn("call the vet back", args[3])
        self.assertEqual(kwargs["metadata"]["timer_marker"], True)
        self.assertEqual(kwargs["metadata"]["timer_id"], 7)
        push_mock.assert_awaited_once()
        _push_args, push_kwargs = push_mock.await_args
        self.assertIn("Avery", push_kwargs["title"])
        self.assertIn("call the vet back", push_kwargs["body"])

    async def test_post_timer_busy_marker_returns_false_on_save_failure(self):
        with patch.object(autowake, "get_or_create_conversation", AsyncMock(return_value="conv-1")), \
                patch.object(autowake, "save_message", AsyncMock(side_effect=RuntimeError("db down"))), \
                patch("services.notifications.send_notification", AsyncMock()):
            posted = await autowake._post_timer_busy_marker(
                self.db, 7, "Avery", "call the vet back"
            )
        self.assertFalse(posted)
