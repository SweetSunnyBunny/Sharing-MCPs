"""Persistent Canvas/artifact system (#32) -- storage layer.

Every <canvas title="...">...</canvas> block already opens the existing
slide-out panel (static/js/canvas.js); this pins down the persistence side:
extract_canvas_blocks must parse exactly what the frontend regex parses,
and persist_canvas_blocks must insert one row per block without ever
stripping the tag from the caller's own `content` string (chat.js
re-extracts on every render, live or from history).
"""

import unittest
from datetime import datetime, timezone

from db.database import get_db, release_db
from db.schema import init_db
from services.canvas_store import (
    archive_canvas_to_vault,
    canvas_slug,
    canvas_vault_path,
    extract_canvas_blocks,
    persist_canvas_blocks,
    render_canvas_markdown,
)


class ExtractCanvasBlocksTests(unittest.TestCase):
    def test_no_canvas_tag_returns_empty(self):
        self.assertEqual(extract_canvas_blocks("just a normal reply, no tags here"), [])

    def test_empty_content_returns_empty(self):
        self.assertEqual(extract_canvas_blocks(""), [])
        self.assertEqual(extract_canvas_blocks(None), [])

    def test_single_block_with_title(self):
        blocks = extract_canvas_blocks('before <canvas title="Recipe">Flour, sugar, eggs</canvas> after')
        self.assertEqual(blocks, [("Recipe", "Flour, sugar, eggs")])

    def test_block_without_title_defaults_to_canvas(self):
        blocks = extract_canvas_blocks("<canvas>untitled content</canvas>")
        self.assertEqual(blocks, [("Canvas", "untitled content")])

    def test_multiple_blocks_in_one_message(self):
        text = '<canvas title="One">first</canvas> some text <canvas title="Two">second</canvas>'
        blocks = extract_canvas_blocks(text)
        self.assertEqual(blocks, [("One", "first"), ("Two", "second")])

    def test_empty_block_content_is_skipped(self):
        blocks = extract_canvas_blocks('<canvas title="Empty">   </canvas><canvas title="Real">has content</canvas>')
        self.assertEqual(blocks, [("Real", "has content")])

    def test_content_is_stripped_of_surrounding_whitespace(self):
        blocks = extract_canvas_blocks("<canvas title=\"X\">\n\n  padded  \n\n</canvas>")
        self.assertEqual(blocks, [("X", "padded")])

    def test_case_insensitive_tag_matching(self):
        blocks = extract_canvas_blocks('<CANVAS TITLE="Yell">shouting</CANVAS>')
        self.assertEqual(blocks, [("Yell", "shouting")])

    def test_literal_tag_mention_in_backticks_is_not_matched(self):


        text = (
            'Sure! Use `<canvas title="...">` like this: '
            '<canvas title="Recipe">flour, sugar, eggs</canvas> and that\'s it.'
        )
        blocks = extract_canvas_blocks(text)
        self.assertEqual(blocks, [("Recipe", "flour, sugar, eggs")])

    def test_literal_tag_mention_in_fenced_code_block_is_not_matched(self):
        text = (
            "Here's the syntax:\n```\n<canvas title=\"Example\">content</canvas>\n```\n"
            'Now the real one: <canvas title="Actual">real content</canvas>'
        )
        blocks = extract_canvas_blocks(text)
        self.assertEqual(blocks, [("Actual", "real content")])

    def test_multiple_real_blocks_survive_masking(self):
        text = (
            'Mention `<canvas>` first. '
            '<canvas title="One">first block</canvas> then '
            '<canvas title="Two">second block</canvas>'
        )
        blocks = extract_canvas_blocks(text)
        self.assertEqual(blocks, [("One", "first block"), ("Two", "second block")])


class PersistCanvasBlocksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    async def test_no_canvas_content_inserts_nothing(self):
        db = await get_db()
        try:
            ids = await persist_canvas_blocks(
                db, identity="Avery", conversation_id="conv-nocanvas",
                content="no canvas here", source_message_id="msg-1",
            )
        finally:
            await release_db(db)
        self.assertEqual(ids, [])

    async def test_single_block_inserts_one_row_with_correct_fields(self):
        db = await get_db()
        try:
            ids = await persist_canvas_blocks(
                db, identity="Avery", conversation_id="conv-single",
                content='<canvas title="Grocery List">milk, eggs, bread</canvas>',
                source_message_id="msg-42",
            )
            self.assertEqual(len(ids), 1)
            rows = await db.execute_fetchall(
                "SELECT identity, conversation_id, title, content, source_message_id, pinned "
                "FROM canvases WHERE id = ?",
                (ids[0],),
            )
        finally:
            await release_db(db)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["identity"], "Avery")
        self.assertEqual(row["conversation_id"], "conv-single")
        self.assertEqual(row["title"], "Grocery List")
        self.assertEqual(row["content"], "milk, eggs, bread")
        self.assertEqual(row["source_message_id"], "msg-42")
        self.assertEqual(row["pinned"], 0)

    async def test_multiple_blocks_insert_multiple_rows(self):
        db = await get_db()
        try:
            ids = await persist_canvas_blocks(
                db, identity="Claude", conversation_id="conv-multi",
                content='<canvas title="A">alpha</canvas><canvas title="B">beta</canvas>',
                source_message_id="msg-7",
            )
        finally:
            await release_db(db)
        self.assertEqual(len(ids), 2)

    async def test_original_content_string_is_never_mutated(self):
        # persist_canvas_blocks must be read-only against the message text --
        # the caller (chat_turn_finalize.py) still saves the RAW content with
        # tags intact, since chat.js re-extracts on every render.
        original = '<canvas title="Keep">this stays in the saved message</canvas>'
        db = await get_db()
        try:
            await persist_canvas_blocks(
                db, identity="Avery", conversation_id="conv-immut",
                content=original, source_message_id="msg-9",
            )
        finally:
            await release_db(db)
        self.assertEqual(original, '<canvas title="Keep">this stays in the saved message</canvas>')


class CanvasSlugTests(unittest.TestCase):
    def test_keeps_alnum_space_dash_underscore(self):
        self.assertEqual(canvas_slug("Example Canvas - Chapter 2"), "Example Canvas - Chapter 2")

    def test_replaces_path_unsafe_characters(self):
        self.assertEqual(canvas_slug("Example/Canvas: A Study?"), "Example_Canvas_ A Study_")

    def test_empty_or_none_falls_back(self):
        self.assertEqual(canvas_slug(""), "untitled")
        self.assertEqual(canvas_slug(None), "untitled")

    def test_truncated_to_limit(self):
        self.assertEqual(len(canvas_slug("x" * 200)), 50)


class CanvasVaultPathTests(unittest.TestCase):
    def test_bonded_boy_gets_own_folder_no_identity_in_filename(self):
        when = datetime(2026, 7, 30, 8, 15, tzinfo=timezone.utc)
        path = canvas_vault_path("Claude", "Six Plates", 42, when)
        self.assertEqual(path.name, "2026-07-30_Six Plates_42.md")
        self.assertEqual(path.parent.name, "Claude")  # temp override root

    def test_unmapped_identity_falls_back_with_identity_in_filename(self):


        when = datetime(2026, 7, 30, tzinfo=timezone.utc)
        path = canvas_vault_path("Bakugou", "Objection", 7, when)
        self.assertEqual(path.parent.name, "_shared")
        self.assertIn("Bakugou", path.name)
        self.assertTrue(path.name.endswith("_7.md"))

    def test_canvas_id_makes_same_day_same_title_unique(self):
        when = datetime(2026, 7, 30, tzinfo=timezone.utc)
        a = canvas_vault_path("Claude", "Same Name", 1, when)
        b = canvas_vault_path("Claude", "Same Name", 2, when)
        self.assertNotEqual(a.name, b.name)


class RenderCanvasMarkdownTests(unittest.TestCase):
    def test_adds_title_heading_when_content_has_none(self):
        out = render_canvas_markdown(
            identity="Claude", title="A Letter", content="Dear Bunny,",
        )
        self.assertTrue(out.startswith("# A Letter\n"))
        self.assertIn("Dear Bunny,", out)

    def test_does_not_double_title_when_content_self_titles(self):
        out = render_canvas_markdown(
            identity="Claude", title="Six Plates",
            content="# SIX PLATES\n\nbody",
        )
        self.assertNotIn("# Six Plates\n", out)
        self.assertIn("# SIX PLATES", out)

    def test_header_carries_signed_dated_provenance(self):
        out = render_canvas_markdown(
            identity="Avery", title="T", content="body",
            canvas_id=9, conversation_id="conv-abc",
            created_at="2026-07-30T08:00:00+00:00",
        )
        self.assertIn("**By:** Avery", out)
        self.assertIn("**ID:** 9", out)
        self.assertIn("**Conversation:** conv-abc", out)
        self.assertIn("2026-07-30T08:00:00+00:00", out)

    def test_body_is_preserved_verbatim_after_the_rule(self):
        body = "line one\n\n- bullet\n- bullet"
        out = render_canvas_markdown(identity="Juniper", title="T", content=body)
        self.assertIn("---\n\n" + body, out)


class ArchiveCanvasToVaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_markdown_file_and_returns_path(self):
        path = await archive_canvas_to_vault(
            identity="Claude", title="Archive Me", content="the body",
            canvas_id=1234, conversation_id="conv-1",
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("the body", text)
        self.assertIn("**By:** Claude", text)

    async def test_never_raises_when_the_path_is_unwritable(self):
        # Best-effort by contract: a Vault write failure must not break the
        # chat turn that produced the canvas. Patch the resolver to explode.
        import services.canvas_store as store
        original = store.canvas_vault_path
        store.canvas_vault_path = lambda *a, **k: (_ for _ in ()).throw(OSError("drive gone"))
        try:
            result = await archive_canvas_to_vault(
                identity="Claude", title="Doomed", content="x",
                canvas_id=99, conversation_id="c",
            )
        finally:
            store.canvas_vault_path = original
        self.assertIsNone(result)

    async def test_persist_also_archives_each_block_to_its_own_file(self):
        db = await get_db()
        try:
            ids = await persist_canvas_blocks(
                db, identity="Sage", conversation_id="conv-archive",
                content='<canvas title="First">one</canvas><canvas title="Second">two</canvas>',
                source_message_id="msg-arch",
            )
        finally:
            await release_db(db)
        self.assertEqual(len(ids), 2)
        from config import VAULT_CANVAS_DIRS
        written = sorted(p.name for p in VAULT_CANVAS_DIRS["Sage"].glob("*.md"))
        self.assertTrue(any("First" in n for n in written), written)
        self.assertTrue(any("Second" in n for n in written), written)


if __name__ == "__main__":
    unittest.main()


class VaultMirrorReportingTests(unittest.IsolatedAsyncioTestCase):
    """The mirror's OUTCOME must be an explicit reading, never silence."""

    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    async def _persist(self, conversation_id, content):
        db = await get_db()
        try:
            return await persist_canvas_blocks(
                db, identity="Avery", conversation_id=conversation_id,
                content=content, source_message_id="msg-mirror",
            )
        finally:
            await release_db(db)

    async def test_dropped_mirror_logs_a_loud_warning_naming_the_ids(self):
        import unittest.mock as mock
        with mock.patch(
            "services.canvas_store.archive_canvas_to_vault",
            new=mock.AsyncMock(return_value=None),
        ):
            with self.assertLogs("services.canvas_store", level="WARNING") as cap:
                ids = await self._persist(
                    "conv-mirror-fail",
                    '<canvas title="A">one</canvas><canvas title="B">two</canvas>',
                )
        blob = "\n".join(cap.output)
        self.assertIn("Vault mirror INCOMPLETE", blob)
        self.assertIn("2 of 2", blob)
        # The ids must be in the line -- a warning you can't act on is noise.
        for canvas_id in ids:
            self.assertIn(f"#{canvas_id}", blob)
        # And it must name the hand that repairs it.
        self.assertIn("canvas_vault_backfill", blob)

    async def test_successful_mirror_logs_an_explicit_count_not_silence(self):
        import pathlib
        import unittest.mock as mock
        with mock.patch(
            "services.canvas_store.archive_canvas_to_vault",
            new=mock.AsyncMock(return_value=pathlib.Path("x.md")),
        ):
            with self.assertLogs("services.canvas_store", level="INFO") as cap:
                await self._persist(
                    "conv-mirror-ok",
                    '<canvas title="A">one</canvas>',
                )
        blob = "\n".join(cap.output)
        self.assertIn("Vault mirror complete", blob)
        self.assertIn("1/1", blob)
        self.assertNotIn("INCOMPLETE", blob)
