import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api.hub import _latest_visible_meds, get_today_schedule
from services.identity_context import build_calendar_context
from services.hub_meds import build_meds_context_line, normalize_meds_data


class _FakeMcpBridge:
    def __init__(self, raw_result, status=None):
        if isinstance(raw_result, list):
            self.raw_results = list(raw_result)
        else:
            self.raw_results = [raw_result]
        self.reconnect_calls = 0
        self.status = status or {
            "required_tools": {
                "calendar": {
                    "server": "google",
                    "server_connected": False,
                    "missing": ["gcal_today_events"],
                    "misplaced": [],
                }
            },
            "restart_recommended": False,
        }

    async def call_tool(self, name, arguments, timeout=None):
        if len(self.raw_results) > 1:
            return self.raw_results.pop(0)
        return self.raw_results[0]

    async def reconnect_failed(self):
        self.reconnect_calls += 1

    def get_status(self):
        return self.status


class HubHelperTests(unittest.TestCase):
    def test_normalize_meds_data_migrates_legacy_entries_and_prunes_old_days(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-01": {"am": "8:10a"},
            "2026-03-19": {"pm": "9:05p"},
            "2026-03-20": {"am": {"taken": False}},
        }

        normalized, changed = normalize_meds_data(data, datetime(2026, 3, 20, 9, 0, tzinfo=central))

        self.assertTrue(changed)
        self.assertNotIn("2026-03-01", normalized)
        self.assertEqual(normalized["2026-03-19"]["pm"]["time"], "9:05p")
        self.assertTrue(normalized["2026-03-19"]["pm"]["taken"])
        self.assertIn("taken_at", normalized["2026-03-19"]["pm"])
        self.assertFalse(normalized["2026-03-20"]["am"]["taken"])

    def test_latest_visible_meds_keeps_legacy_yesterday_completion_visible(self):
        data = {
            "2026-03-10": {"am": "8:10a", "pm": "9:05p"},
            "2026-03-11": {},
        }

        visible = _latest_visible_meds(data, "2026-03-11")
        self.assertEqual(visible["am"], "8:10a")
        self.assertTrue(visible["am_stale"])
        self.assertEqual(visible["pm"], "9:05p")
        self.assertTrue(visible["pm_stale"])

    def test_latest_visible_meds_returns_age_metadata_for_current_dose(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-11": {
                "am": {
                    "taken": True,
                    "time": "7:55a",
                    "taken_at": datetime(2026, 3, 11, 7, 55, tzinfo=central).isoformat(),
                }
            }
        }

        visible = _latest_visible_meds(data, datetime(2026, 3, 11, 10, 5, tzinfo=central))
        self.assertTrue(visible["am_taken"])
        self.assertEqual(visible["am_age_label"], "2h ago")
        self.assertEqual(visible["doses"]["am"]["status"], "taken")

    def test_latest_visible_meds_keeps_previous_day_dose_visible_within_24_hours(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-10": {
                "pm": {
                    "taken": True,
                    "time": "11:30p",
                    "taken_at": datetime(2026, 3, 10, 23, 30, tzinfo=central).isoformat(),
                }
            },
            "2026-03-11": {},
        }

        visible = _latest_visible_meds(data, datetime(2026, 3, 11, 22, 45, tzinfo=central))
        self.assertEqual(visible["pm"], "11:30p")
        self.assertEqual(visible["pm_date"], "2026-03-10")
        self.assertTrue(visible["pm_stale"])

    def test_latest_visible_meds_hides_previous_day_dose_after_24_hours(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-10": {
                "pm": {
                    "taken": True,
                    "time": "11:30p",
                    "taken_at": datetime(2026, 3, 10, 23, 30, tzinfo=central).isoformat(),
                }
            },
            "2026-03-11": {},
        }

        visible = _latest_visible_meds(data, datetime(2026, 3, 11, 23, 31, tzinfo=central))
        self.assertIsNone(visible["pm"])
        self.assertFalse(visible["pm_stale"])

    def test_latest_visible_meds_current_clear_blocks_previous_fallback(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-10": {
                "am": {
                    "taken": True,
                    "time": "8:10a",
                    "taken_at": datetime(2026, 3, 10, 8, 10, tzinfo=central).isoformat(),
                }
            },
            "2026-03-11": {
                "am": {
                    "taken": False,
                    "updated_at": datetime(2026, 3, 11, 7, 0, tzinfo=central).isoformat(),
                }
            },
        }

        visible = _latest_visible_meds(data, datetime(2026, 3, 11, 7, 30, tzinfo=central))
        self.assertIsNone(visible["am"])
        self.assertFalse(visible["am_taken"])
        self.assertEqual(visible["doses"]["am"]["status"], "cleared")

    def test_latest_visible_meds_prefers_today_when_present(self):
        data = {
            "2026-03-10": {"am": "8:10a"},
            "2026-03-11": {"am": "7:55a"},
        }

        visible = _latest_visible_meds(data, "2026-03-11")
        self.assertEqual(visible["am"], "7:55a")
        self.assertFalse(visible["am_stale"])

    def test_latest_visible_meds_handles_spring_forward_window(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-08": {
                "am": {
                    "taken": True,
                    "time": "1:30a",
                    "taken_at": datetime(2026, 3, 8, 1, 30, tzinfo=central).isoformat(),
                }
            },
            "2026-03-09": {},
        }

        visible = _latest_visible_meds(data, datetime(2026, 3, 9, 1, 15, tzinfo=central))
        self.assertEqual(visible["am"], "1:30a")
        self.assertEqual(visible["am_age_label"], "23h ago")

    def test_latest_visible_meds_handles_fall_back_window(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-11-01": {
                "pm": {
                    "taken": True,
                    "time": "11:30p",
                    "taken_at": datetime(2026, 11, 1, 23, 30, tzinfo=central).isoformat(),
                }
            },
            "2026-11-02": {},
        }

        visible = _latest_visible_meds(data, datetime(2026, 11, 2, 23, 45, tzinfo=central))
        self.assertIsNone(visible["pm"])
        self.assertIsNone(visible["pm_age_label"])

    def test_build_meds_context_line_uses_new_last_24h_wording(self):
        central = ZoneInfo("America/Chicago")
        data = {
            "2026-03-11": {
                "am": {
                    "taken": True,
                    "time": "7:55a",
                    "taken_at": datetime(2026, 3, 11, 7, 55, tzinfo=central).isoformat(),
                }
            },
            "2026-03-10": {
                "pm": {
                    "taken": True,
                    "time": "11:30p",
                    "taken_at": datetime(2026, 3, 10, 23, 30, tzinfo=central).isoformat(),
                }
            },
        }

        line = build_meds_context_line(data, datetime(2026, 3, 11, 10, 5, tzinfo=central))
        self.assertIn("AM taken at 7:55a (2h ago)", line)
        self.assertIn("PM last dose at 11:30p (10h ago)", line)


class HubTodayScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_today_schedule_reports_unavailable_when_tool_missing(self):
        fake_bridge = _FakeMcpBridge("Error: Unknown tool 'gcal_today_events'")

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await get_today_schedule()

        self.assertEqual(result["integration_status"], "unavailable")
        self.assertEqual(result["integration_reason"], "server_disconnected")
        self.assertEqual(result["events"], [])
        self.assertIn("Unknown tool", result["error"])
        self.assertEqual(fake_bridge.reconnect_calls, 1)

    async def test_today_schedule_includes_restart_note_when_config_is_stale(self):
        fake_bridge = _FakeMcpBridge(
            "Error: Unknown tool 'gcal_today_events'",
            status={
                "required_tools": {
                    "calendar": {
                        "server": "google",
                        "server_connected": True,
                        "missing": ["gcal_today_events"],
                        "misplaced": [],
                    }
                },
                "restart_recommended": True,
            },
        )

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await get_today_schedule()

        self.assertEqual(result["integration_reason"], "tool_missing")
        self.assertTrue(result["restart_recommended"])
        self.assertIn("Restart Anam", result["note"])

    async def test_today_schedule_retries_after_mcp_reconnect(self):
        fake_bridge = _FakeMcpBridge([
            "Error: Unknown tool 'gcal_today_events'",
            json.dumps({
                "success": True,
                "events": [
                    {
                        "summary": "Vet visit",
                        "start": "2026-03-13T09:00:00",
                        "all_day": False,
                        "location": "Clinic",
                    }
                ],
            }),
        ])

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await get_today_schedule()

        self.assertEqual(fake_bridge.reconnect_calls, 1)
        self.assertEqual(result["integration_status"], "ready")
        self.assertEqual(result["events"][0]["summary"], "Vet visit")

    async def test_today_schedule_formats_successful_events(self):
        fake_bridge = _FakeMcpBridge(json.dumps({
            "success": True,
            "events": [
                {
                    "summary": "Dentist",
                    "start": "2026-03-13T15:30:00",
                    "all_day": False,
                    "location": "Office",
                }
            ],
        }))

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await get_today_schedule()

        self.assertEqual(result["integration_status"], "ready")
        self.assertEqual(result["events"][0]["summary"], "Dentist")
        self.assertEqual(result["events"][0]["time"], "15:30")


class CalendarContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_calendar_context_formats_upcoming_events(self):
        fake_bridge = _FakeMcpBridge(json.dumps({
            "success": True,
            "events": [
                {
                    "summary": "Dentist",
                    "start": "2026-03-13T15:30:00",
                    "all_day": False,
                },
                {
                    "summary": "Dinner",
                    "start": "2026-03-13T19:00:00",
                    "all_day": False,
                },
            ],
        }))

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await build_calendar_context()

        self.assertIn("Today's calendar:", result)
        self.assertIn("3:30 PM Dentist", result)
        self.assertIn("7:00 PM Dinner", result)

    async def test_build_calendar_context_handles_empty_day(self):
        fake_bridge = _FakeMcpBridge(json.dumps({
            "success": True,
            "events": [],
        }))

        with patch("services.mcp_bridge.mcp_bridge", new=fake_bridge):
            result = await build_calendar_context()

        self.assertEqual(result, "[Today's calendar: No events scheduled today]")
