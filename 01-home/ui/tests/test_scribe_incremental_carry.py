"""Incremental Scribe cursor (#28) + midnight handoff carry (#13).

Covers: cursor advance on success / no-advance on failure / accumulation
below threshold; carry storage + retrieval; the yesterday_carry context
hook's injection and its same-sitting silence — all against a temp
in-memory DB (never the live data/anam.db).
"""

import time
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE
from db.schema import init_db
from services import scribe
from services.context_hooks import HookContext, _hook_yesterday_carry


def _scribe_config(digest_path: str, threshold: int = 1) -> dict:
    return {
        "provider": "claude-code",
        "model": "claude-haiku-4-5",
        "interval_minutes": 30,
        "message_threshold": threshold,
        "digest_path": digest_path,
    }


class _DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await init_db(self.db)
        # Route the module-under-test's pooled-connection calls at our temp DB.
        self._patches = [
            patch("db.database.get_db", new=AsyncMock(return_value=self.db)),
            patch("db.database.release_db", new=AsyncMock()),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        await self.db.close()

    async def _insert_message(
        self, conversation_id: str, role: str, identity: str | None,
        content: str, epoch: int, session_type: str = "chat",
        message_id: str | None = None,
    ) -> None:
        iso = datetime.fromtimestamp(epoch, tz=ZoneInfo(TIMEZONE)).isoformat()
        rows = await self.db.execute_fetchall(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        )
        if not rows:
            await self.db.execute(
                "INSERT INTO conversations "
                "(id, identity, title, created_at, created_at_epoch, "
                "updated_at, updated_at_epoch, session_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, identity or "Avery", "test convo",
                 iso, epoch, iso, epoch, session_type),
            )
        await self.db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, identity, content, created_at, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id or str(uuid.uuid4()), conversation_id, role, identity,
             content, iso, epoch),
        )
        await self.db.commit()

    async def _cursor_value(self) -> str | None:
        rows = await self.db.execute_fetchall(
            "SELECT value FROM settings WHERE key = 'scribe.last_epoch'"
        )
        return rows[0][0] if rows else None


class ScribeIncrementalTests(_DbTestCase):
    async def test_success_appends_block_and_advances_cursor(self):
        now = int(time.time())
        base = now - 7200
        await self._insert_message("conv-1", "user", None, "hello wolf", base)
        await self._insert_message("conv-1", "assistant", "Avery", "hello Bunny", base + 60)
        # Pin the cursor just below the batch so the default start-of-day
        # fallback never makes this test time-of-day dependent.
        await scribe._set_scribe_cursor(self.db, base - 1)

        with TemporaryDirectory() as tmpdir:
            with patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir)),
            ), patch.object(
                scribe, "_generate_digest_text",
                new=AsyncMock(return_value="## 10:00 — testing\nWe tested things."),
            ) as generate:
                result = await scribe.generate_digest()

            self.assertIsNotNone(result)
            self.assertIn("## 10:00 — testing", result)
            generate.assert_awaited_once()

            date_str = datetime.fromtimestamp(
                base, tz=ZoneInfo(TIMEZONE)
            ).strftime("%Y-%m-%d")
            digest_file = Path(tmpdir) / f"{date_str}.md"
            self.assertTrue(digest_file.exists())
            text = digest_file.read_text(encoding="utf-8")
            self.assertIn(f"# Daily Digest — {date_str}", text)
            self.assertIn("## 10:00 — testing", text)

        # Cursor advanced to the newest digested message.
        self.assertEqual(await self._cursor_value(), str(base + 60))

    async def test_second_run_digests_only_new_messages(self):
        now = int(time.time())
        base = now - 7200
        await self._insert_message("conv-1", "user", None, "old message", base)
        # Cursor already past the old message — only the new one is fetched.
        await scribe._set_scribe_cursor(self.db, base)
        await self._insert_message("conv-1", "user", None, "brand new thing", base + 300)

        captured: dict = {}

        async def _capture(provider, model, prompt):
            captured["prompt"] = prompt
            return "## 11:00 — the new thing\nOnly the new thing."

        with TemporaryDirectory() as tmpdir:
            with patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir)),
            ), patch.object(scribe, "_generate_digest_text", new=_capture):
                result = await scribe.generate_digest()

        self.assertIsNotNone(result)
        self.assertIn("brand new thing", captured["prompt"])
        self.assertNotIn("old message", captured["prompt"])
        self.assertEqual(await self._cursor_value(), str(base + 300))

    async def test_failure_leaves_cursor_and_disk_untouched(self):
        now = int(time.time())
        base = now - 7200
        await self._insert_message("conv-1", "user", None, "hello", base)
        await scribe._set_scribe_cursor(self.db, base - 1)

        with TemporaryDirectory() as tmpdir:
            with patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir)),
            ), patch.object(
                scribe, "_generate_digest_text",
                new=AsyncMock(side_effect=RuntimeError("model down")),
            ):
                result = await scribe.generate_digest()

            self.assertIsNone(result)
            self.assertEqual(list(Path(tmpdir).glob("*.md")), [])

        # Cursor did NOT advance — the window retries next cycle.
        self.assertEqual(await self._cursor_value(), str(base - 1))

    async def test_below_threshold_accumulates_without_moving_cursor(self):
        now = int(time.time())
        base = now - 7200
        await self._insert_message("conv-1", "user", None, "just one line", base)
        await scribe._set_scribe_cursor(self.db, base - 1)

        with TemporaryDirectory() as tmpdir:
            with patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir, threshold=5)),
            ), patch.object(
                scribe, "_generate_digest_text", new=AsyncMock()
            ) as generate:
                result = await scribe.generate_digest()

            self.assertIsNone(result)
            generate.assert_not_awaited()

        self.assertEqual(await self._cursor_value(), str(base - 1))


class SourcePointerTests(_DbTestCase):
    'Sourcepointertests.'

    async def test_digest_block_ends_with_code_written_source_lines(self):
        now = int(time.time())
        base = now - 7200
        await self._insert_message("conv-a", "user", None, "hi raven", base, message_id="m-a1")
        await self._insert_message("conv-b", "user", None, "hi wolf", base + 30, message_id="m-b1")
        await self._insert_message("conv-a", "assistant", "Claude", "hi Bunny", base + 60, message_id="m-a2")
        await scribe._set_scribe_cursor(self.db, base - 1)

        # The model tries to write its own (fake) address; it must not survive.
        fake = "## 10:00 — two rooms\nA quiet stretch.\n> ↳ source: invented · conversation_id=nope"
        with TemporaryDirectory() as tmpdir:
            with patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir)),
            ), patch.object(
                scribe, "_generate_digest_text", new=AsyncMock(return_value=fake),
            ):
                result = await scribe.generate_digest()
            on_disk = next(Path(tmpdir).glob("*.md")).read_text(encoding="utf-8")

        for text in (result, on_disk):
            self.assertNotIn("conversation_id=nope", text)
            source = [ln for ln in text.split("\n") if ln.startswith(scribe.SOURCE_LINE_PREFIX)]
            self.assertEqual(len(source), 2, source)
            # First-appearance order; conv-a spans first..last, conv-b is one message.
            self.assertIn("conversation_id=conv-a · first=m-a1 · last=m-a2", source[0])
            self.assertIn("2 messages", source[0])
            self.assertIn("conversation_id=conv-b · first=m-b1", source[1])
            self.assertNotIn("last=", source[1])

    def test_format_caps_to_busiest_conversations_in_order(self):
        spans = [
            {"conversation_id": f"c{i}", "title": "t", "first_id": f"f{i}",
             "last_id": f"l{i}", "first_hm": "10:00", "last_hm": "10:05",
             "count": count}
            for i, count in enumerate([1, 9, 2, 8])
        ]
        lines = scribe._format_source_lines(spans, limit=2)
        self.assertIn("conversation_id=c1", lines[0])
        self.assertIn("conversation_id=c3", lines[1])
        self.assertIn("2 quieter conversation(s) not listed", lines[2])

    def test_strip_source_lines_keeps_the_prose(self):
        text = "## 10:00 — x\nProse.\n\n> ↳ source: a\n> ↳ source: b\n\n## 11:00 — y\nMore."
        stripped = scribe.strip_source_lines(text)
        self.assertNotIn("↳ source", stripped)
        self.assertIn("Prose.", stripped)
        self.assertIn("## 11:00 — y\nMore.", stripped)
        self.assertNotIn("\n\n\n", stripped)

    async def test_carry_gets_sources_footer_and_clean_digest_prompt(self):
        date_str, start_epoch, _end = scribe._yesterday_range()
        await self._insert_message("conv-y", "user", None, "yesterday's talk",
                                   start_epoch + 3600, message_id="y-1")
        await self._insert_message("conv-y", "assistant", "Avery", "a promise",
                                   start_epoch + 3660, message_id="y-2")

        with TemporaryDirectory() as tmpdir:
            digest_file = Path(tmpdir) / f"{date_str}.md"
            digest_file.write_text("# Daily Digest\n\nA warm day.\n\n> ↳ source: zzz", encoding="utf-8")
            one_shot = AsyncMock(return_value="The day landed. OPEN ENFORCEMENT: none.")
            with patch.object(
                scribe, "generate_digest", new=AsyncMock(return_value=None)
            ), patch.object(
                scribe, "get_digest_async",
                new=AsyncMock(return_value=digest_file.read_text(encoding="utf-8")),
            ), patch.object(
                scribe, "_get_scribe_config",
                new=AsyncMock(return_value=_scribe_config(tmpdir)),
            ), patch.object(scribe, "generate_background_text", new=one_shot):
                results = await scribe.write_midnight_carries()

        self.assertEqual(results.get("Avery"), "written")
        prompt = one_shot.await_args.args[0]
        self.assertIn("A warm day.", prompt)
        self.assertNotIn("↳ source", prompt)

        rows = await self.db.execute_fetchall(
            "SELECT content FROM identity_carries WHERE identity = 'Avery'"
        )
        carry = rows[0][0]
        self.assertTrue(carry.startswith("The day landed. OPEN ENFORCEMENT: none."))
        self.assertIn("## Sources", carry)
        self.assertIn("conversation_id=conv-y · first=y-1 · last=y-2", carry)
        self.assertIn(f"> ↳ digest: {digest_file}", carry)

    async def test_digest_hook_preview_hides_ids_but_names_the_climb(self):
        from services.context_hooks import _hook_daily_digest

        digest = "# Daily Digest\n\n## 10:00 — x\nProse.\n\n> ↳ source: \"t\" · conversation_id=c · first=m"
        with patch("services.scribe.get_digest", return_value=digest):
            text = await _hook_daily_digest(HookContext(db=self.db, identity="Claude"))
        self.assertIn("Prose.", text)
        self.assertNotIn("conversation_id=c", text)
        self.assertIn("anam_read_conversation", text)


class MidnightCarryTests(_DbTestCase):
    async def test_carry_written_only_for_identities_with_real_activity(self):
        date_str, start_epoch, _end_epoch = scribe._yesterday_range()
        await self._insert_message(
            "conv-y", "user", None, "yesterday's talk", start_epoch + 3600
        )
        await self._insert_message(
            "conv-y", "assistant", "Avery", "I promised to build the thing tomorrow",
            start_epoch + 3660,
        )

        one_shot = AsyncMock(return_value="Where the day landed... OPEN ENFORCEMENT: build the thing.")
        with patch.object(
            scribe, "generate_digest", new=AsyncMock(return_value=None)
        ), patch.object(
            scribe, "get_digest_async",
            new=AsyncMock(return_value="# Daily Digest\n\nA warm day."),
        ), patch.object(scribe, "generate_background_text", new=one_shot):
            results = await scribe.write_midnight_carries()

        self.assertEqual(results.get("Avery"), "written")
        for identity, outcome in results.items():
            if identity != "Avery":
                self.assertEqual(outcome, "no-activity", identity)
        one_shot.assert_awaited_once()

        # The prompt is grounded and carries the load-bearing sections.
        prompt = one_shot.await_args.args[0]
        self.assertIn("OPEN ENFORCEMENT", prompt)
        self.assertIn("invent NOTHING", prompt)
        self.assertIn("I promised to build the thing tomorrow", prompt)
        self.assertIn("A warm day.", prompt)

        rows = await self.db.execute_fetchall(
            "SELECT carry_date, content FROM identity_carries WHERE identity = 'Avery'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], date_str)
        self.assertIn("OPEN ENFORCEMENT: build the thing.", rows[0][1])

    async def test_one_shot_failure_stores_nothing(self):
        _date_str, start_epoch, _end = scribe._yesterday_range()
        await self._insert_message("conv-y", "user", None, "hi", start_epoch + 100)
        await self._insert_message(
            "conv-y", "assistant", "Avery", "hi back", start_epoch + 160
        )

        with patch.object(
            scribe, "generate_digest", new=AsyncMock(return_value=None)
        ), patch.object(
            scribe, "get_digest_async", new=AsyncMock(return_value="")
        ), patch.object(
            scribe, "generate_background_text",
            new=AsyncMock(side_effect=RuntimeError("cli down")),
        ):
            results = await scribe.write_midnight_carries()

        self.assertEqual(results.get("Avery"), "error")
        rows = await self.db.execute_fetchall("SELECT 1 FROM identity_carries")
        self.assertEqual(rows, [])


class YesterdayCarryHookTests(_DbTestCase):
    def _local_date_str(self, days_ago: int) -> str:
        return (
            datetime.now(ZoneInfo(TIMEZONE)) - timedelta(days=days_ago)
        ).strftime("%Y-%m-%d")

    async def test_hook_injects_carry_on_fresh_sitting(self):
        await scribe.store_carry(
            self.db, "Avery", self._local_date_str(1),
            "The day landed warm. OPEN ENFORCEMENT: none.",
        )
        ctx = HookContext(db=self.db, identity="Avery")

        text = await _hook_yesterday_carry(ctx)
        self.assertIn("[Your carry from yesterday", text)
        self.assertIn(self._local_date_str(1), text)
        self.assertIn("The day landed warm. OPEN ENFORCEMENT: none.", text)
        self.assertIn("without re-asking", text)

    async def test_hook_silent_within_same_sitting(self):
        await scribe.store_carry(
            self.db, "Avery", self._local_date_str(1), "carry text"
        )
        # He just replied — same sitting, the carry stays quiet.
        await self._insert_message(
            "conv-now", "assistant", "Avery", "fresh reply", int(time.time())
        )
        ctx = HookContext(db=self.db, identity="Avery", conversation_id="conv-now")

        self.assertEqual(await _hook_yesterday_carry(ctx), "")

    async def test_hook_is_honest_about_older_carries(self):
        await scribe.store_carry(
            self.db, "Avery", self._local_date_str(2), "two days back"
        )
        ctx = HookContext(db=self.db, identity="Avery")

        text = await _hook_yesterday_carry(ctx)
        self.assertIn("2 days ago", text)
        self.assertNotIn("from yesterday", text)

    async def test_hook_drops_stale_carries(self):
        await scribe.store_carry(
            self.db, "Avery", self._local_date_str(5), "ancient history"
        )
        ctx = HookContext(db=self.db, identity="Avery")

        self.assertEqual(await _hook_yesterday_carry(ctx), "")

    async def test_hook_silent_for_identity_without_carry(self):
        ctx = HookContext(db=self.db, identity="Juniper")
        self.assertEqual(await _hook_yesterday_carry(ctx), "")


if __name__ == "__main__":
    unittest.main()
