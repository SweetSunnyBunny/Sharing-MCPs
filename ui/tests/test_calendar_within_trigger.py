"""calendar_within trigger condition (#23) — "your meeting starts in 15".

Reads the house-snapshot poller's cached calendar fetch (#24) instead of
making a live gcal call from inside the 30s trigger loop. No snapshot,
unparseable data, all-day-only events, or nothing in the window all
evaluate False — a broken calendar must never falsely fire a watcher.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from config import TIMEZONE
from services import house_snapshot
from services.trigger_engine import evaluate_condition


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class CalendarWithinTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    def tearDown(self):
        house_snapshot._SNAPSHOTS.clear()

    async def test_no_snapshot_evaluates_false(self):
        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_event_within_window_matches(self):
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)
        start = now + timedelta(minutes=10)
        raw = '{"events": [{"summary": "Dentist", "start": {"dateTime": "%s"}}]}' % _iso(start)
        house_snapshot._store("calendar", raw)

        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertTrue(result)

    async def test_event_outside_window_does_not_match(self):
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)
        start = now + timedelta(hours=3)
        raw = '{"events": [{"summary": "Dentist", "start": {"dateTime": "%s"}}]}' % _iso(start)
        house_snapshot._store("calendar", raw)

        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_past_event_does_not_match(self):
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)
        start = now - timedelta(minutes=5)
        raw = '{"events": [{"summary": "Already started", "start": {"dateTime": "%s"}}]}' % _iso(start)
        house_snapshot._store("calendar", raw)

        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_all_day_event_never_matches(self):
        house_snapshot._store(
            "calendar", '{"events": [{"summary": "Birthday", "all_day": true, "start": {"date": "2026-07-08"}}]}'
        )
        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_no_events_evaluates_false(self):
        house_snapshot._store("calendar", '{"events": []}')
        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_unparseable_snapshot_never_raises(self):
        house_snapshot._store("calendar", "not json at all")
        result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_snapshot_lookup_exception_never_raises(self):
        with patch("services.house_snapshot.get_snapshot", side_effect=RuntimeError("boom")):
            result = await evaluate_condition({"type": "calendar_within", "minutes": 15})
        self.assertFalse(result)

    async def test_default_minutes_is_15(self):
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)
        start = now + timedelta(minutes=14)
        raw = '{"events": [{"summary": "Standup", "start": {"dateTime": "%s"}}]}' % _iso(start)
        house_snapshot._store("calendar", raw)

        result = await evaluate_condition({"type": "calendar_within"})
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
