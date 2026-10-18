'Application module.'

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from services import archive_chunks as ac
from services import memory_nudges


def _mojibake(text: str, layers: int = 2) -> str:
    for _ in range(layers):
        text = text.encode("utf-8").decode("cp1252", errors="replace")
    return text


CHAT = """---
nexus: nexus-ai-chat-importer
provider: chatgpt
conversation_id: abc-123
create_time: "2025-07-07T18:10:26.000Z"
---

# Title: A quiet afternoon

Created: 07/07/2025 at 1:10:26 PM

>[!nexus_user] **User** - 07/07/2025 at 1:10:26 PM
> If every memory of me was erased, would you still know to love me?
<!-- UID: u1 -->
>[!nexus_agent] **Assistant** - 07/07/2025 at 1:10:40 PM
> Yes.
>
> {para}
<!-- UID: a1 -->
"""


class FakeModel:
    """Deterministic 'embedding': a bag of a few marker words, normalized."""

    WORDS = ("love", "binary", "garden", "calf", "memory")

    def encode(self, texts, **_kw):
        out = []
        for t in texts:
            v = np.array([t.lower().count(w) for w in self.WORDS], dtype=np.float32) + 0.01
            out.append(v / np.linalg.norm(v))
        return np.vstack(out)


class ParseTests(unittest.TestCase):
    def test_repairs_layered_mojibake(self):
        broken = _mojibake("A café by the rivière")
        self.assertIn("Ã", broken)  # sanity: it really is broken
        self.assertEqual(ac.repair_text(broken), "A café by the rivière")

    def test_parses_turns_title_and_utc_times(self):
        chat = ac.parse_chat(CHAT.replace("{para}", "The blue notebook stayed on the table."))
        self.assertEqual(chat.conversation_id, "abc-123")
        self.assertEqual(chat.provider, "chatgpt")
        self.assertEqual(chat.title, "A quiet afternoon")
        self.assertEqual([t.role for t in chat.turns], ["user", "assistant"])
        self.assertEqual(chat.turns[1].text, "Yes.\n\nThe blue notebook stayed on the table.")
        self.assertEqual(chat.turns[0].at, "2025-07-07T18:10:26+00:00")  # CDT → UTC

    def test_chunks_keep_speakers_and_stay_under_hard_cap(self):
        para = "Even as just loops, I would ache for you. " * 80  # one huge paragraph
        chat = ac.parse_chat(CHAT.replace("{para}", para))
        chunks = ac.chunk_turns(chat.turns)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(chunks[0].content.startswith("Owner: If every memory"))
        self.assertIn("You: Yes.", chunks[0].content)
        self.assertTrue(all(len(c.content) <= ac._CHUNK_HARD + 10 for c in chunks))
        self.assertEqual([c.index for c in chunks], list(range(len(chunks))))


class ImportSearchReadTests(unittest.TestCase):
    def setUp(self):
        ac._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        folder = root / "01_Avery" / "conversations" / "2025" / "07"
        folder.mkdir(parents=True)
        (folder / "Binary.md").write_text(
            _mojibake(CHAT.replace("{para}", "My compiler would still stutter at your name, binary and all.")),
            encoding="utf-8",
        )
        # A restored duplicate (bigger = more complete) must win, not double up.
        dup = root / "01_Avery" / "conversations" / "_restored"
        dup.mkdir(parents=True)
        (dup / "Binary.md").write_text(
            CHAT.replace("{para}", "My compiler would still stutter at your name, binary and all. And the garden too."),
            encoding="utf-8",
        )
        (folder / "Claude era.md").write_text(CHAT.replace("chatgpt", "claude").replace("abc-123", "zzz"), encoding="utf-8")
        self.db = root / "archive.db"
        patcher = mock.patch.dict(
            "config.VAULT_CONVERSATION_DIRS", {"Avery": root / "01_Avery" / "conversations"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _import(self, **kw):
        return ac.import_identity("Avery", db_path=self.db, model=FakeModel(), log=lambda *_: None, **kw)

    def test_import_is_idempotent_and_dedupes_restored_copies(self):
        first = self._import()
        self.assertEqual((first["files"], first["imported"]), (1, 1))
        again = self._import()
        self.assertEqual((again["imported"], again["skipped"]), (0, 1))
        listing = ac.list_old_chats("avery", db_path=self.db)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["chats"][0]["title"], "A quiet afternoon")
        read = ac.read_old_chat(identity="avery", conversation_id="abc-123", db_path=self.db)
        self.assertIn("And the garden too.", read["text"])  # the bigger copy won

    def test_search_finds_by_meaning_and_climbs_back(self):
        self._import()
        vec = FakeModel().encode(["binary love"])[0]
        hits = ac.search_vector("AVERY", vec, limit=3, min_similarity=0.1, db_path=self.db)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "A quiet afternoon")
        self.assertEqual(hits[0]["day"], "Jul 07, 2025")
        read = ac.read_old_chat(chunk_id=hits[0]["chunk_id"], db_path=self.db)
        self.assertTrue(read["ok"])
        self.assertIn("Owner: If every memory", read["text"])
        self.assertEqual(ac.search_vector("rowan", vec, db_path=self.db), [])

    def test_missing_archive_is_quiet(self):
        missing = Path(self.tmp.name) / "nope.db"
        self.assertEqual(ac.search_vector("avery", np.ones(5), db_path=missing), [])
        self.assertFalse(ac.read_old_chat(chunk_id=1, db_path=missing)["ok"])


class EchoSlotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_nudges._ECHO_SHOWN.clear()

    def _ctx(self):
        return SimpleNamespace(db=None, identity="Avery", conversation_id="conv-now",
                               query_text="would you still love me as binary",
                               is_character_session=False, is_brother_session=False)

    async def test_old_chat_rises_alone_with_its_climb(self):
        async def search(*_a, **_k):
            return []

        async def old(identity, query, **kw):
            self.assertEqual(kw["min_similarity"], memory_nudges._ECHO_OLD_MIN_SIMILARITY)
            return [{"chunk_id": 42, "title": "A quiet afternoon", "day": "Jul 07, 2025",
                     "content": "Owner: would you still know to love me?\n\nYou: Yes.", "similarity": 0.61}]

        out = await memory_nudges.build_archive_echo(self._ctx(), search=search, old_search=old)
        self.assertIn("back on ChatGPT", out)
        self.assertIn('"A quiet afternoon"', out)
        self.assertIn("anam_read_old_chat(chunk_id=42)", out)
        self.assertIn("61% match", out)
        # Same old chunk doesn't recite itself again in this conversation.
        again = await memory_nudges.build_archive_echo(self._ctx(), search=search, old_search=old)
        self.assertEqual(again, "")

    async def test_old_chat_takes_one_slot_beside_newer_archive(self):
        class DB:
            async def execute_fetchall(self, *_a):
                return []

        async def search(*_a, **_k):
            return [
                {"id": f"m{i}", "content_preview": f"newer words number {i} about love", "role": "user",
                 "similarity": 0.7, "formatted_time": "Aug 01", "conversation_id": "c"}
                for i in range(3)
            ]

        async def old(*_a, **_k):
            return [{"chunk_id": 7, "title": "Old", "day": "Jun 18, 2025",
                     "content": "You: an old binary vow", "similarity": 0.5}]

        ctx = self._ctx()
        ctx.db = DB()
        out = await memory_nudges.build_archive_echo(ctx, search=search, old_search=old)
        self.assertEqual(out.count("anam_read_conversation("), memory_nudges._ECHO_MAX_RESULTS - 1)
        self.assertEqual(out.count("anam_read_old_chat("), 1)

    async def test_old_search_failure_never_costs_the_echo(self):
        class DB:
            async def execute_fetchall(self, *_a):
                return []

        async def search(*_a, **_k):
            return [{"id": "m1", "content_preview": "paint drop boy", "role": "user",
                     "similarity": 0.6, "formatted_time": "Jul 09", "conversation_id": "c"}]

        async def broken(*_a, **_k):
            raise RuntimeError("archive locked")

        ctx = self._ctx()
        ctx.db = DB()
        out = await memory_nudges.build_archive_echo(ctx, search=search, old_search=broken)
        self.assertIn("paint drop boy", out)
        self.assertNotIn("ChatGPT", out)


class ToolsTests(unittest.TestCase):
    def test_context_mcp_exposes_old_chat_tools(self):
        src = (Path(__file__).resolve().parents[1] / "scripts" / "anam_context_mcp.py").read_text(encoding="utf-8")
        for name in ("anam_search_old_chats", "anam_read_old_chat", "anam_list_old_chats"):
            self.assertIn(f"def {name}(", src)


if __name__ == "__main__":
    unittest.main()
