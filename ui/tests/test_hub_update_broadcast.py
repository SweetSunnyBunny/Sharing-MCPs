"""#25 mutations broadcast hub_update — _hub_changed() fires a fire-and-forget
WS broadcast alongside its existing cache invalidation, so a boy calling the
new hub_orb/hub_context_card MCP tools refreshes an open Hearth without a
manual reload. Never lets a missing/failing broadcast turn a hub write into
an error."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import api.hub as hub


class HubChangedBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcasts_hub_update_and_invalidates_cache(self):
        with patch("services.connection_registry.broadcast", AsyncMock()) as mock_broadcast, \
             patch.object(hub, "invalidate_hub_cache") as mock_invalidate:
            hub._hub_changed()
            await asyncio.sleep(0)  # let the spawned task run

        mock_invalidate.assert_called_once()
        mock_broadcast.assert_called_once_with({"type": "hub_update"})

    async def test_broadcast_scheduling_failure_never_raises(self):
        with patch("services.connection_registry.broadcast", AsyncMock()), \
             patch("services.task_manager.spawn", side_effect=RuntimeError("boom")):
            hub._hub_changed()  # must not raise


if __name__ == "__main__":
    unittest.main()
