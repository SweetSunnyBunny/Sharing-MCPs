"""Wearable bridge Phase 2 -- outbox (server->phone) and reply routing into chat.

Uses tmp JSONL paths (never the live data/ files) and the session tmp DB
from conftest.py (never the live anam.db).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.database import get_db, release_db
from db.schema import init_db

from api import wearable


class _WearableTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="anam_wearable_test_")
        tmp = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(wearable, "_REPLIES_FILE", tmp / "replies.jsonl"),
            mock.patch.object(wearable, "_OUTBOX_FILE", tmp / "outbox.jsonl"),
            mock.patch.object(wearable, "_OUTBOX_STATE_FILE", tmp / "outbox_state.json"),
        ]
        for p in self._patches:
            p.start()

        app = FastAPI()
        app.include_router(wearable.router)
        self.client = TestClient(app)

        db = await get_db()
        try:
            await init_db(db)
            # Other suites may have created messages with embedding rows or
            # other foreign-key dependants. The wearable tests only require
            # that no earlier conversation is active, so avoid brittle global
            # deletion and retire the previous fixtures in place.
            await db.execute(
                "UPDATE conversations SET is_active = 0, autowake_daily = 0"
            )
            await db.commit()
        finally:
            await release_db(db)

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class OutboxTests(_WearableTestBase):
    async def test_send_requires_text(self):
        r = self.client.post("/api/wearable/send", json={"from_identity": "Avery"})
        self.assertEqual(r.status_code, 400)

    async def test_send_assigns_incrementing_ids(self):
        r1 = self.client.post("/api/wearable/send", json={"text": "hi", "from_identity": "Avery"})
        r2 = self.client.post("/api/wearable/send", json={"text": "again", "from_identity": "Claude"})
        self.assertEqual(r1.json()["queued"]["id"], 1)
        self.assertEqual(r2.json()["queued"]["id"], 2)
        self.assertEqual(r2.json()["queued"]["from_identity"], "Claude")
        self.assertIn("created_at", r1.json()["queued"])

    async def test_outbox_returns_pending_then_marks_delivered(self):
        self.client.post("/api/wearable/send", json={"text": "one", "from_identity": "Avery"})
        self.client.post("/api/wearable/send", json={"text": "two", "from_identity": "Avery"})

        r = self.client.get("/api/wearable/outbox")
        body = r.json()
        self.assertEqual([m["text"] for m in body["messages"]], ["one", "two"])
        self.assertEqual(body["last_id"], 2)

        # Fetch again without since_id: server cursor advanced, nothing pending.
        r2 = self.client.get("/api/wearable/outbox")
        self.assertEqual(r2.json()["messages"], [])

    async def test_outbox_since_id_overrides_cursor(self):
        self.client.post("/api/wearable/send", json={"text": "one", "from_identity": "Avery"})
        self.client.post("/api/wearable/send", json={"text": "two", "from_identity": "Avery"})
        self.client.get("/api/wearable/outbox")  # cursor -> 2

        # Client explicitly asks from id 1 -> re-delivers message 2.
        r = self.client.get("/api/wearable/outbox", params={"since_id": 1})
        self.assertEqual([m["text"] for m in r.json()["messages"]], ["two"])

    async def test_outbox_empty(self):
        r = self.client.get("/api/wearable/outbox")
        self.assertEqual(r.json(), {"ok": True, "messages": [], "last_id": 0})

    async def test_pending_does_not_consume_the_queue(self):
        """The whole point of /pending: looking must never eat her messages."""
        self.client.post("/api/wearable/send", json={"text": "one", "from_identity": "Avery"})
        self.client.post("/api/wearable/send", json={"text": "two", "from_identity": "Sage"})

        peek = self.client.get("/api/wearable/pending").json()
        self.assertEqual(peek["pending_count"], 2)
        self.assertEqual([m["text"] for m in peek["pending"]], ["one", "two"])
        self.assertEqual(peek["last_delivered_id"], 0)

        # Peeking twice still shows both — the cursor never moved.
        self.assertEqual(self.client.get("/api/wearable/pending").json()["pending_count"], 2)

        # And the phone still receives everything it was owed.
        delivered = self.client.get("/api/wearable/outbox").json()
        self.assertEqual([m["text"] for m in delivered["messages"]], ["one", "two"])

    async def test_pending_after_delivery_is_empty(self):
        self.client.post("/api/wearable/send", json={"text": "one", "from_identity": "Avery"})
        self.client.get("/api/wearable/outbox")  # phone collects it

        peek = self.client.get("/api/wearable/pending").json()
        self.assertEqual(peek["pending_count"], 0)
        self.assertEqual(peek["last_delivered_id"], 1)
        self.assertEqual(peek["last_delivered"]["text"], "one")


class ReplyRoutingTests(_WearableTestBase):
    async def test_reply_logs_even_with_no_conversation(self):
        r = self.client.post("/api/wearable/reply", json={"device": "versa2", "text": "hi love"})
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIsNone(body["chat"])  # no active conversation anywhere
        lines = wearable._REPLIES_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(json.loads(lines[-1])["text"], "hi love")

    async def test_reply_inserts_into_most_recent_active_conversation(self):
        from services.session_manager import get_or_create_conversation

        db = await get_db()
        try:
            conv_id = await get_or_create_conversation(db, "Avery")
        finally:
            await release_db(db)

        r = self.client.post("/api/wearable/reply", json={"device": "versa2", "text": "from my wrist"})
        chat = r.json()["chat"]
        self.assertIsNotNone(chat)
        self.assertEqual(chat["identity"], "Avery")
        self.assertEqual(chat["conversation_id"], conv_id)

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT role, content, metadata FROM messages WHERE id = ?",
                (chat["message_id"],),
            )
        finally:
            await release_db(db)
        self.assertEqual(rows[0][0], "user")
        self.assertEqual(rows[0][1], "from my wrist")
        meta = json.loads(rows[0][2])
        self.assertEqual(meta["source"], "wearable")
        self.assertEqual(meta["device"], "versa2")

    async def test_reply_prefers_connection_registry_active_identity(self):
        from services.session_manager import get_or_create_conversation

        db = await get_db()
        try:
            await get_or_create_conversation(db, "Avery")
            claude_conv = await get_or_create_conversation(db, "Claude")
        finally:
            await release_db(db)

        with mock.patch("services.connection_registry.get_active_identity", return_value="Claude"), \
             mock.patch("services.connection_registry.get_active_conversation", return_value=claude_conv):
            r = self.client.post("/api/wearable/reply", json={"text": "hey Claude"})
        chat = r.json()["chat"]
        self.assertEqual(chat["identity"], "Claude")
        self.assertEqual(chat["conversation_id"], claude_conv)


if __name__ == "__main__":
    unittest.main()
