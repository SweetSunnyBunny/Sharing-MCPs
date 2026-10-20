import unittest

from services import connection_registry


class _DeadSocket:
    async def send_json(self, _message):
        raise RuntimeError("socket closed")


class ConnectionRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        connection_registry._connections.clear()
        connection_registry._connection_identities.clear()
        connection_registry._connection_last_active.clear()
        connection_registry._active_identity = None
        connection_registry._last_disconnect_time = 0

    async def asyncTearDown(self):
        connection_registry._connections.clear()
        connection_registry._connection_identities.clear()
        connection_registry._connection_last_active.clear()
        connection_registry._active_identity = None
        connection_registry._last_disconnect_time = 0

    async def test_broadcast_prunes_dead_socket_presence_state(self):
        ws = _DeadSocket()
        ws_id = id(ws)
        connection_registry._connections.add(ws)
        connection_registry._connection_identities[ws_id] = "Avery"
        connection_registry._connection_last_active[ws_id] = 123.0
        connection_registry._active_identity = "Avery"

        await connection_registry.broadcast({"type": "ping"})

        self.assertFalse(connection_registry._connections)
        self.assertNotIn(ws_id, connection_registry._connection_identities)
        self.assertNotIn(ws_id, connection_registry._connection_last_active)
        self.assertIsNone(connection_registry.get_active_identity())
        self.assertGreater(connection_registry.get_last_disconnect_time(), 0)
