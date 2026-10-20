import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.anam_context_queries import (
    read_conversation,
    recent_conversations,
    search_history,
)


class AnamContextQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "history.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, identity TEXT, title TEXT, created_at TEXT,
                updated_at TEXT, updated_at_epoch INTEGER, is_active INTEGER,
                session_type TEXT
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT,
                identity TEXT, content TEXT, created_at TEXT
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content, content='messages', content_rowid='rowid'
            );
            CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            """
        )
        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("conv-1", "Claude", "Transport work", "2026-01-01", "2026-01-02", 2, 1, "chat"),
        )
        rows = [
            ("m1", "user", None, "We need to investigate the phone transport."),
            ("m2", "assistant", "Claude", "I will inspect WebSocket fallback timing."),
            ("m3", "user", None, "The exact issue is proxy buffering on mobile."),
            ("m4", "assistant", "Claude", "We added no-transform and timing breadcrumbs."),
        ]
        for index, (mid, role, identity, content) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (mid, "conv-1", role, identity, content, f"2026-01-0{index}"),
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_returns_anchor_window_and_bookends(self):
        result = search_history("proxy buffering", mode="keyword", db_path=self.db_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        hit = result["results"][0]
        self.assertEqual(hit["conversation_id"], "conv-1")
        self.assertTrue(any(message["is_anchor"] for message in hit["messages"]))
        self.assertEqual(hit["bookend_start"][0]["id"], "m1")
        self.assertEqual(hit["bookend_end"][-1]["id"], "m4")

    def test_hybrid_falls_back_to_keyword_when_semantic_api_unreachable(self):
        # #12: a down/unreachable server must never make a search worse than
        # keyword-only — this is the "always at least as good" guarantee.
        with patch(
            "services.anam_context_queries._semantic_hits_via_api", return_value=None
        ):
            result = search_history("proxy buffering", db_path=self.db_path)  # default mode=hybrid
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["match_type"], "keyword")

    def test_semantic_hit_produces_bookended_block(self):
        fake_hits = [{"id": "m2", "conversation_id": "conv-1", "created_at": "2026-01-02"}]
        with patch(
            "services.anam_context_queries._semantic_hits_via_api", return_value=fake_hits
        ):
            result = search_history("websocket fallback", mode="semantic", db_path=self.db_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        hit = result["results"][0]
        self.assertEqual(hit["match_type"], "semantic")
        self.assertEqual(hit["match_message_id"], "m2")
        self.assertTrue(any(m["is_anchor"] for m in hit["messages"]))
        self.assertEqual(hit["bookend_start"][0]["id"], "m1")
        self.assertEqual(hit["bookend_end"][-1]["id"], "m4")

    def test_hybrid_dedups_semantic_and_keyword_hits_on_same_conversation(self):
        # Both paths hit conv-1 — hybrid should return ONE block (semantic
        # wins the slot, since it's merged first), not two.
        fake_hits = [{"id": "m2", "conversation_id": "conv-1", "created_at": "2026-01-02"}]
        with patch(
            "services.anam_context_queries._semantic_hits_via_api", return_value=fake_hits
        ):
            result = search_history("proxy buffering", mode="hybrid", db_path=self.db_path)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["match_type"], "semantic")

    def test_speaker_filter_forces_keyword_only_even_in_hybrid_mode(self):
        with patch(
            "services.anam_context_queries._semantic_hits_via_api"
        ) as mock_semantic:
            result = search_history(
                "proxy buffering", speaker="Claude", mode="hybrid", db_path=self.db_path
            )
        mock_semantic.assert_not_called()
        self.assertTrue(result["ok"])

    def test_unparseable_semantic_hits_are_skipped_not_fatal(self):
        fake_hits = [{"id": "", "conversation_id": ""}, {"conversation_id": "conv-1"}]
        with patch(
            "services.anam_context_queries._semantic_hits_via_api", return_value=fake_hits
        ):
            result = search_history("proxy buffering", mode="hybrid", db_path=self.db_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["match_type"], "keyword")

    def test_read_conversation_around_message(self):
        result = read_conversation(
            "conv-1",
            around_message_id="m3",
            window=1,
            db_path=self.db_path,
        )
        self.assertEqual([m["id"] for m in result["messages"]], ["m2", "m3", "m4"])

    def test_recent_conversations(self):
        result = recent_conversations(identity="Claude", db_path=self.db_path)
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["conversations"][0]["latest_content"],
            "We added no-transform and timing breadcrumbs.",
        )
