"""Reusable test staleness support."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from services import context_hooks
from services.context_hooks import HookContext, _age_seconds, _age_stamp, _format_age


def _iso_ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def _epoch_ago(**kwargs) -> float:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).timestamp()


class _FakeDb:
    """Minimal aiosqlite stand-in: execute_fetchall returns canned rows."""

    def __init__(self, rows):
        self._rows = rows

    async def execute_fetchall(self, *_args, **_kwargs):
        return self._rows


class AgeStampTests(unittest.TestCase):
    def test_fresh_timestamp_returns_none(self):
        self.assertIsNone(_age_stamp(_iso_ago(minutes=2)))

    def test_hours_old_iso_stamps_hours(self):
        self.assertEqual(_age_stamp(_iso_ago(hours=3, minutes=5)), "as of 3h ago")

    def test_minutes_old_stamps_minutes(self):
        self.assertEqual(_age_stamp(_iso_ago(minutes=30)), "as of 30m ago")

    def test_days_old_stamps_days(self):
        self.assertEqual(_age_stamp(_epoch_ago(days=3)), "as of 3d ago")

    def test_epoch_input_supported(self):
        self.assertEqual(_age_stamp(_epoch_ago(hours=5)), "as of 5h ago")

    def test_datetime_input_supported(self):
        then = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertEqual(_age_stamp(then), "as of 2h ago")

    def test_missing_or_garbage_returns_none(self):
        self.assertIsNone(_age_stamp(None))
        self.assertIsNone(_age_stamp(""))
        self.assertIsNone(_age_stamp("not-a-timestamp"))

    def test_age_seconds_never_negative(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertEqual(_age_seconds(future), 0.0)

    def test_format_age_floors_to_one_minute(self):
        self.assertEqual(_format_age(5), "1m")


class EmotionalContextStalenessTests(unittest.IsolatedAsyncioTestCase):
    _SNAPSHOT = {
        "summary": "Warm and easy back-and-forth.",
        "tone": "warm",
        "energy": "steady",
        "arc": "settling",
    }

    async def _run(self, epoch):
        ctx = HookContext(db=_FakeDb([(epoch,)]), identity="Avery")
        with patch(
            "services.emotional_capture.get_latest_snapshot",
            new=AsyncMock(return_value=dict(self._SNAPSHOT)),
        ):
            return await context_hooks._hook_emotional_context(ctx)

    async def test_fresh_snapshot_renders_without_stamp(self):
        text = await self._run(_epoch_ago(minutes=1))
        self.assertIn("[Emotional Context]", text)
        self.assertNotIn("as of", text)

    async def test_hour_old_snapshot_carries_age_stamp(self):
        text = await self._run(_epoch_ago(hours=1))
        self.assertIn("[Emotional Context (as of 1h ago)]", text)

    async def test_snapshot_older_than_two_hours_is_dropped(self):
        text = await self._run(_epoch_ago(hours=3))
        self.assertEqual(text, "")

    async def test_unknown_age_keeps_prior_behavior(self):
        # Age lookup returning nothing → keep the block, unstamped.
        ctx = HookContext(db=_FakeDb([]), identity="Avery")
        with patch(
            "services.emotional_capture.get_latest_snapshot",
            new=AsyncMock(return_value=dict(self._SNAPSHOT)),
        ):
            text = await context_hooks._hook_emotional_context(ctx)
        self.assertIn("[Emotional Context]", text)


class SpoonsForecastStalenessTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, wellness_ts):
        return {
            "wellness": {"timestamp": wellness_ts, "sleep_hours": "7"},
            "energy": "low",
            "pain": "moderate",
            "spoons": 5,
        }

    async def _run(self, wellness_ts):
        ctx = HookContext(db=None, identity="Juniper")
        with patch(
            "asyncio.to_thread",
            new=AsyncMock(return_value=self._state(wellness_ts)),
        ), patch.object(
            context_hooks,
            "_weather_pain_risk",
            new=AsyncMock(return_value=(False, "")),
        ):
            return await context_hooks._hook_spoons_forecast(ctx)

    async def test_morning_log_read_in_evening_is_stamped(self):
        text = await self._run(_iso_ago(hours=9))
        self.assertIn("(as of 9h ago)", text)
        self.assertIn("OWNER'S DAY", text)

    async def test_fresh_log_carries_no_stamp(self):
        text = await self._run(_iso_ago(minutes=3))
        self.assertNotIn("as of", text)
        self.assertIn("OWNER'S DAY", text)

    async def test_missing_timestamp_carries_no_stamp(self):
        text = await self._run("")
        self.assertNotIn("as of", text)


class SessionNotesStalenessTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_note(self, tmp_path, note):
        notes_file = tmp_path / "session_notes.json"
        notes_file.write_text(json.dumps({"Claude": note}), encoding="utf-8")
        ctx = HookContext(db=None, identity="Claude")
        with patch.object(context_hooks, "RITUALS_DIR", tmp_path):
            return await context_hooks._hook_session_notes(ctx)

    async def test_hours_old_note_is_stamped(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            text = await self._run_with_note(
                Path(tmp),
                {"note": "Finish the orb polish.", "timestamp": _iso_ago(hours=5)},
            )
        self.assertIn("[Your note from last session (as of 5h ago):]", text)
        self.assertIn("Finish the orb polish.", text)

    async def test_fresh_note_keeps_plain_label(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            text = await self._run_with_note(
                Path(tmp),
                {"note": "Just wrote this.", "timestamp": _iso_ago(minutes=1)},
            )
        self.assertIn("[Your note from last session:]", text)
