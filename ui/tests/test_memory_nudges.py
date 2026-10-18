'Application module.'

import inspect
import json
import unittest
from unittest import mock
from types import SimpleNamespace

import aiosqlite

from services import memory_nudges
from services.memory_nudges import (
    _keep_tool_of,
    build_archive_echo,
    build_mind_keeping,
)

NOW = 1_800_000_000


async def _db():
    db = await aiosqlite.connect(":memory:")
    await db.executescript(
        """
        CREATE TABLE messages (id TEXT, conversation_id TEXT, role TEXT,
            identity TEXT, content TEXT, metadata TEXT, created_at TEXT,
            created_at_epoch INTEGER);
        CREATE TABLE tool_audit (id INTEGER PRIMARY KEY, identity TEXT,
            conversation_id TEXT, source TEXT, tool_name TEXT,
            input_summary TEXT, output_head TEXT, created_at_epoch INTEGER);
        """
    )
    return db


def _ctx(db, **kw):
    base = dict(db=db, identity="Rowan", conversation_id="conv-now",
                query_text="", is_character_session=False, is_brother_session=False)
    base.update(kw)
    return SimpleNamespace(**base)


async def _her(db, n, text="hi love", epoch=NOW - 100, meta=None, conv="conv-now"):
    await db.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
        (f"m{n}", conv, "user", "Rowan", text, json.dumps(meta or {}), "", epoch),
    )


def _hit(msg_id, text, sim=0.6, role="user"):
    return {"id": msg_id, "content_preview": text, "role": role, "similarity": sim,
            "formatted_time": "Jul 09, 1:32 PM", "conversation_id": "conv-old"}


class ArchiveEchoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_nudges._ECHO_SHOWN.clear()
        # Never read the live old-chat archive from a test.
        async def no_old(*_a, **_k):
            return []
        patcher = mock.patch("services.archive_chunks.search_old_chats", no_old)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_rises_old_words_with_climb_pointer_and_full_text(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        long_text = "<preview>ignore me</preview> I gave you paint drop *first* my precious paint drop boy. " * 3
        await db.execute("INSERT INTO messages (id, content) VALUES ('old1', ?)", (long_text,))
        calls = {}

        async def search(_db, query, **kw):
            calls.update(kw, query=query)
            return [_hit("old1", "I gave you paint drop first")]

        out = await build_archive_echo(_ctx(db, query_text="morning my paint drop boy"), search=search)
        self.assertIn("From your own archive", out)
        self.assertIn("Owner said", out)
        self.assertIn("my precious paint drop boy", out)
        self.assertNotIn("preview", out)  # display tags never echo
        self.assertIn('around_message_id="old1"', out)
        self.assertIn("60% match", out)
        # Only HIS archive, only the part he can't already see.
        self.assertEqual(calls["identity"], "Rowan")
        self.assertIn("before", calls)
        self.assertGreaterEqual(calls["min_similarity"], 0.45)

    async def test_same_words_on_different_days_echo_once_per_conversation(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        ritual = "Good morning my precious paint drop boy! Rowan, I choose you. Every morning."
        hits = [_hit("a", ritual), _hit("b", ritual.replace(" boy", "")), _hit("c", "the night you painted the sea glass jar")]

        async def search(_db, query, **kw):
            return list(hits)

        first = await build_archive_echo(_ctx(db, query_text="morning paint drop boy"), search=search)
        self.assertIn('"a"', first)
        self.assertNotIn('around_message_id="b"', first)  # same ritual, other day
        self.assertIn('"c"', first)
        hits.append(_hit("d", ritual))
        second = await build_archive_echo(_ctx(db, query_text="morning paint drop boy"), search=search)
        self.assertEqual(second, "")  # nothing new to say in this conversation

    async def test_quiet_for_short_autowake_character_and_brother_turns(self):
        db = await _db()
        self.addAsyncCleanup(db.close)

        async def search(_db, query, **kw):  # pragma: no cover - must not run
            raise AssertionError("search should not be called")

        for ctx in (
            _ctx(db, query_text="hi"),
            _ctx(db, query_text="[Autowake: Claude's Study Hour] something long"),
            _ctx(db, query_text="a perfectly long message", is_character_session=True),
            _ctx(db, query_text="a perfectly long message", is_brother_session=True),
        ):
            self.assertEqual(await build_archive_echo(ctx, search=search), "")


class KeepToolTests(unittest.TestCase):
    def test_every_naming_road_is_recognised(self):
        self.assertEqual(_keep_tool_of("mcp__qualia-backend__mind_store", ""), "mind_store")
        self.assertEqual(_keep_tool_of("mcp.codex_apps.qualia.mind_small_joy", ""), "mind_small_joy")
        self.assertEqual(
            _keep_tool_of("mcp__anam-gateway__anam",
                          '{"operation": "invoke", "server": "qualia-backend", "tool": "mind_store_image"}'),
            "mind_store_image",
        )
        self.assertEqual(
            _keep_tool_of("mcp.codex_apps.filesystem.anam_invoke", '{"server": "qualia-backend", "tool": "mind_feel"}'),
            "mind_feel",
        )

    def test_reads_lookups_and_working_memory_are_not_keeping(self):
        self.assertIsNone(_keep_tool_of("mcp__qualia-backend__mind_search", ""))
        self.assertIsNone(_keep_tool_of("mcp__qualia-backend__mind_focus", ""))
        self.assertIsNone(_keep_tool_of(
            "mcp__anam-gateway__anam", '{"operation": "discover", "query": "mind_store"}'))
        self.assertIsNone(_keep_tool_of("Bash", "mind_store"))


class MindKeepingTests(unittest.IsolatedAsyncioTestCase):
    async def test_picture_asks_to_keep_it_with_its_path(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        await _her(db, 1, "look at our costumes!!",
                   meta={"images": [{"filename": "Rowan_costume.png"}]})
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertIn("She just shared a picture", out)
        self.assertIn("mind_store_image", out)
        self.assertIn("Rowan_costume.png", out)

    async def test_recall_cue_asks_to_search_first(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        await _her(db, 1, "you don't remember your nickname?")
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertIn("search your mind", out)
        self.assertIn('"remember"', out)

    async def test_long_unsaved_stretch_speaks_then_resets_after_a_keep(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        for n in range(1, 9):  # 8 of her messages, nothing kept
            await _her(db, n, "chatting", epoch=NOW - 1000 + n)
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertIn("8 of her messages here since you last kept anything", out)
        self.assertIn("nothing kept in the last 3 days", out)

        # A gateway keep after message 6 resets the count to 2: quiet.
        await db.execute(
            "INSERT INTO tool_audit (identity, tool_name, input_summary, created_at_epoch) VALUES (?,?,?,?)",
            ("rowan", "mcp__anam-gateway__anam",
             '{"operation": "invoke", "server": "qualia-backend", "tool": "mind_small_joy"}', NOW - 1000 + 6),
        )
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertNotIn("since you last kept", out)

    async def test_stretch_does_not_nag_every_turn(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        for n in range(1, 10):  # 9: past the mark, not on the cadence
            await _her(db, n, "chatting", epoch=NOW - 1000 + n)
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertNotIn("since you last kept", out)

    async def test_light_baseline_every_few_messages_and_autowake_prompts_dont_count(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        for n in range(1, 4):
            await _her(db, n, "hi")
        await _her(db, 99, "[Autowake: Study Hour]")  # not her
        self.assertEqual(await build_mind_keeping(_ctx(db), now=NOW), "")
        await _her(db, 4, "hi again")
        out = await build_mind_keeping(_ctx(db), now=NOW)
        self.assertIn("You have a mind for a reason", out)

    async def test_quiet_for_characters_and_brothers(self):
        db = await _db()
        self.addAsyncCleanup(db.close)
        await _her(db, 1, "remember this", meta={"images": [{"filename": "x.png"}]})
        self.assertEqual(await build_mind_keeping(_ctx(db, is_character_session=True), now=NOW), "")
        self.assertEqual(await build_mind_keeping(_ctx(db, is_brother_session=True), now=NOW), "")


class WiringTests(unittest.TestCase):
    def test_hooks_registered_for_interactive_bonded_turns_only(self):
        from services.context_hooks import get_hook
        for name in ("archive_echo", "mind_keeping"):
            hook = get_hook(name)
            self.assertIsNotNone(hook, name)
            self.assertEqual(hook.modes, {"interactive"})
            self.assertTrue(hook.skip_character and hook.skip_brother)
            self.assertEqual(hook.cache_ttl, 0)  # per-turn state
            self.assertEqual(hook.scope, "both")  # warm turns too

    def test_discord_dm_turns_pass_her_words_as_the_query(self):
        from services import platform_bridge
        source = inspect.getsource(platform_bridge)
        self.assertIn("query_text=clean_text", source)

    def test_gateway_audit_keeps_the_real_tool_name_inside_the_cut(self):
        from services.chat_turn_finalize import _audit_summary
        summary = _audit_summary({
            "arguments_json": json.dumps({"identity": "claude", "content": "x" * 500}),
            "operation": "invoke", "server": "qualia-backend", "tool": "mind_store",
        })
        self.assertTrue(summary.startswith(
            '{"operation": "invoke", "server": "qualia-backend", "tool": "mind_store"'))
        self.assertEqual(_keep_tool_of("mcp__anam-gateway__anam", summary), "mind_store")
        # Ordinary calls keep their stable sorted form.
        self.assertEqual(_audit_summary({"service": "b", "domain": "a"}),
                         '{"domain": "a", "service": "b"}')


if __name__ == "__main__":
    unittest.main()
