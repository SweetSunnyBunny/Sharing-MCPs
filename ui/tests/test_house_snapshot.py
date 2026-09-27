"""House snapshot poller (#24): background pre-fetch, last-good carry-forward.

A slow/unreachable upstream (Google Calendar, HUB_API_BASE, weather) must
never stall a live turn, and a failed poll must never erase a prior good
value — it should just leave the snapshot to age.
"""

import unittest
from unittest.mock import AsyncMock, patch

from services import house_snapshot


class GetSnapshotTests(unittest.TestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    def test_never_fetched_returns_none_none(self):
        self.assertEqual(house_snapshot.get_snapshot("weather"), (None, None))

    def test_store_then_get_returns_data_and_nonnegative_age(self):
        house_snapshot._store("weather", "sunny")
        data, age = house_snapshot.get_snapshot("weather")
        self.assertEqual(data, "sunny")
        self.assertGreaterEqual(age, 0.0)

    def test_store_none_never_clobbers_prior_good_value(self):
        house_snapshot._store("weather", "sunny")
        house_snapshot._store("weather", None)
        data, _age = house_snapshot.get_snapshot("weather")
        self.assertEqual(data, "sunny")


class PollWeatherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    async def test_success_stores_text(self):
        with patch("services.mcp_bridge.mcp_bridge") as mock_bridge:
            mock_bridge.call_tool = AsyncMock(return_value="Clear, 75F")
            await house_snapshot._poll_weather()
        data, _age = house_snapshot.get_snapshot("weather")
        self.assertEqual(data, "Clear, 75F")

    async def test_error_string_does_not_store(self):
        with patch("services.mcp_bridge.mcp_bridge") as mock_bridge:
            mock_bridge.call_tool = AsyncMock(return_value="Error: timeout")
            await house_snapshot._poll_weather()
        self.assertEqual(house_snapshot.get_snapshot("weather"), (None, None))

    async def test_exception_never_raises_and_keeps_last_good(self):
        house_snapshot._store("weather", "Clear, 75F")
        with patch("services.mcp_bridge.mcp_bridge") as mock_bridge:
            mock_bridge.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
            await house_snapshot._poll_weather()  # must not raise
        data, _age = house_snapshot.get_snapshot("weather")
        self.assertEqual(data, "Clear, 75F")


class PollCalendarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    async def test_success_stores_raw_json_text(self):
        raw = '{"events": [{"summary": "Dentist"}]}'
        with patch("services.mcp_bridge.mcp_bridge") as mock_bridge:
            mock_bridge.call_tool = AsyncMock(return_value=raw)
            await house_snapshot._poll_calendar()
        data, _age = house_snapshot.get_snapshot("calendar")
        self.assertEqual(data, raw)

    async def test_error_prefixed_string_does_not_store(self):
        with patch("services.mcp_bridge.mcp_bridge") as mock_bridge:
            mock_bridge.call_tool = AsyncMock(return_value="Error: unreachable")
            await house_snapshot._poll_calendar()
        self.assertEqual(house_snapshot.get_snapshot("calendar"), (None, None))


class PollHubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    async def test_populates_configured_endpoints_only(self):
        with patch("config.HUB_API_BASE", "http://hub.example"), \
             patch("config.RITUALS_API_BASE", ""), \
             patch("services.remote_state.safe_request_json", return_value={"ok": True}):
            await house_snapshot._poll_hub()
        data, _age = house_snapshot.get_snapshot("hub_status")
        self.assertEqual(data, {"ok": True})
        data, _age = house_snapshot.get_snapshot("hub_rituals")
        self.assertEqual(data, {"ok": True})  # RITUALS_API_BASE falls back to HUB_API_BASE

    async def test_no_base_configured_polls_nothing(self):
        with patch("config.HUB_API_BASE", ""), patch("config.RITUALS_API_BASE", ""):
            await house_snapshot._poll_hub()
        self.assertEqual(house_snapshot.get_snapshot("hub_status"), (None, None))


class PollHouseSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        house_snapshot._SNAPSHOTS.clear()

    async def test_never_raises_even_if_every_sub_poll_fails(self):
        with patch.object(house_snapshot, "_poll_weather", AsyncMock(side_effect=RuntimeError("x"))), \
             patch.object(house_snapshot, "_poll_calendar", AsyncMock(side_effect=RuntimeError("x"))), \
             patch.object(house_snapshot, "_poll_hub", AsyncMock(side_effect=RuntimeError("x"))):
            await house_snapshot.poll_house_snapshot()  # must not raise


if __name__ == "__main__":
    unittest.main()
