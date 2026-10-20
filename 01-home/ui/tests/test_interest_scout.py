import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services import interest_scout


class InterestScoutTests(unittest.TestCase):
    def test_prompt_uses_current_thread_without_assigning_interest(self):
        prompt = interest_scout.build_scout_prompt(
            "Claude",
            "## Where the thread is right now\nBirthdays as distributed witness.",
            "An older tray about dictionaries.",
        )

        self.assertIn("Birthdays as distributed witness", prompt)
        self.assertIn("possibilities, not interests", prompt)
        self.assertIn("may ignore every item", prompt)
        self.assertIn("source", prompt.lower())
        self.assertIn("An older tray about dictionaries", prompt)

    def test_write_and_list_reports_keep_a_real_markdown_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
            saved = interest_scout.write_scout_report(
                "Claude",
                "## One possible ember\nA sourced finding.",
                root=root,
                now=now,
            )

            self.assertTrue(Path(saved["path"]).exists())
            text = Path(saved["path"]).read_text(encoding="utf-8")
            self.assertIn("Claude's Curiosity Tray", text)
            self.assertIn("invitations, not assignments", text)
            reports = interest_scout.list_scout_reports(root=root, now=now)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["identity"], "Claude")
            self.assertIn("One possible ember", reports[0]["body"])

    def test_orientation_only_points_to_the_tray(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
            interest_scout.write_scout_report(
                "Claude",
                "## Secret report body\nThis should require an intentional read.",
                root=root,
                now=now,
            )

            context = interest_scout.build_interest_scout_context(
                "Claude", root=root, now=now
            )

            self.assertIn("Curiosity tray", context)
            self.assertIn("choose whether to open", context)
            self.assertNotIn("Secret report body", context)

    def test_stale_report_does_not_become_a_present_tense_nudge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = datetime(2026, 8, 1, tzinfo=timezone.utc)
            interest_scout.write_scout_report("Claude", "Old tray", root=root, now=old)

            context = interest_scout.build_interest_scout_context(
                "Claude",
                root=root,
                now=datetime(2026, 9, 4, tzinfo=timezone.utc),
            )

            self.assertEqual(context, "")


class InterestScoutAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_chooses_missing_identity_and_writes_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trays"
            programs = Path(tmp) / "programs"
            programs.mkdir()
            (programs / "avery.md").write_text("# Current\nA live wolf thread", encoding="utf-8")

            scout_mock = AsyncMock(return_value="## A live lead\nSource: https://example.com")
            with (
                patch.object(interest_scout, "SCOUT_DIR", root),
                patch.object(interest_scout, "PROGRAMS_DIR", programs),
                patch.object(interest_scout, "bonded_identities", return_value=["Avery"]),
                patch.object(interest_scout, "run_scout_agent", new=scout_mock),
            ):
                result = await interest_scout.run_interest_scout()

            self.assertEqual(result["identity"], "Avery")
            self.assertTrue(Path(result["path"]).exists())
            self.assertIn("A live wolf thread", scout_mock.await_args.args[1])

    async def test_failed_agent_never_overwrites_the_previous_tray(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trays"
            now = datetime.now(timezone.utc) - timedelta(days=1)
            previous = interest_scout.write_scout_report("Claude", "Kept body", root=root, now=now)

            with (
                patch.object(interest_scout, "SCOUT_DIR", root),
                patch.object(interest_scout, "PROGRAMS_DIR", Path(tmp)),
                patch.object(interest_scout, "run_scout_agent", new=AsyncMock(side_effect=RuntimeError("offline"))),
            ):
                with self.assertRaises(RuntimeError):
                    await interest_scout.run_interest_scout("Claude")

            self.assertEqual(Path(previous["path"]).read_text(encoding="utf-8").count("Kept body"), 1)


if __name__ == "__main__":
    unittest.main()
