"""Offline checks for the Discord map maintenance helper."""
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_server_map.py"
spec = importlib.util.spec_from_file_location("refresh_server_map_under_test", SCRIPT)
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)

class RefreshServerMapTests(unittest.TestCase):
    def test_tokens_follow_custom_identity_registry(self):
        config = types.ModuleType("config")
        config.DISCORD_BOT_TOKENS = {"MyCustomIdentity": "test-placeholder"}
        with patch.dict(sys.modules, {"config": config}):
            tokens = refresh.configured_tokens()
        self.assertEqual(tokens, config.DISCORD_BOT_TOKENS)
        tokens.clear()
        self.assertEqual(len(config.DISCORD_BOT_TOKENS), 1)

    def test_missing_tokens_preserve_existing_map(self):
        with (
            patch.object(refresh, "configured_tokens", return_value={}),
            patch.object(refresh.httpx, "AsyncClient") as client,
            patch.object(refresh.aiosqlite, "connect") as database,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(asyncio.run(refresh.main()), 1)
        client.assert_not_called()
        database.assert_not_called()
        self.assertIn("was not changed", output.getvalue())

    def test_summary_counts_empty_and_populated_indexes(self):
        connection = sqlite3.connect(":memory:")
        try:
            tables = ("discord_server_map", "discord_server_members", "discord_search_channels", "discord_search_members")
            for table in tables:
                connection.execute(f"CREATE TABLE {table} (label TEXT)")
            self.assertEqual(set(refresh.index_summary(connection).values()), {0})
            connection.executemany("INSERT INTO discord_server_members VALUES (?)", [("private member one",), ("private member two",)])
            summary = refresh.index_summary(connection)
            self.assertEqual(summary["members"], 2)
            self.assertEqual(summary["searchable members"], 0)
            self.assertNotIn("private member", str(summary))
        finally:
            connection.close()

if __name__ == "__main__":
    unittest.main()
