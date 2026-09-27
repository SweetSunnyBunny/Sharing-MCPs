import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from services.github_handoff_loader import (
    build_github_handoff_context,
    list_github_handoff_refs,
    load_latest_github_handoffs,
    render_github_handoffs,
)


class GithubHandoffLoaderTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_reads_latest_remote_notes_without_touching_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "user.email", "atlas@example.test")
            self._git(repo, "config", "user.name", "Atlas Test")

            note_dir = repo / "programs" / "explorations" / "atlas"
            note_dir.mkdir(parents=True)
            first = note_dir / "2026-07-21-first.md"
            first.write_text("first doorway", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "first")

            second = note_dir / "2026-07-22-second.md"
            second.write_text("second doorway", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "second")

            handoffs = load_latest_github_handoffs(
                "Atlas",
                limit=2,
                repo_root=repo,
                remote="",
                branch="HEAD",
                refresh=False,
            )

            self.assertEqual(
                [handoff.path for handoff in handoffs],
                [
                    "programs/explorations/atlas/2026-07-22-second.md",
                    "programs/explorations/atlas/2026-07-21-first.md",
                ],
            )
            rendered = render_github_handoffs(handoffs)
            self.assertIn("second doorway", rendered)
            self.assertIn("first doorway", rendered)

    def test_non_positive_limit_skips_git(self):
        self.assertEqual(load_latest_github_handoffs("Atlas", limit=0), [])

    def test_manifest_uses_current_files_and_exact_blob_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "user.email", "atlas@example.test")
            self._git(repo, "config", "user.name", "Atlas Test")
            note_dir = repo / "programs" / "explorations" / "atlas"
            note_dir.mkdir(parents=True)
            retired = note_dir / "2026-07-20-retired.md"
            retired.write_text("retired", encoding="utf-8")
            live = note_dir / "2026-07-21-live.md"
            live.write_text("first version", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "first")
            retired.unlink()
            live.write_text("second version", encoding="utf-8")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "--quiet", "-m", "second")

            refs = list_github_handoff_refs(
                "Atlas", repo_root=repo, remote="", branch="HEAD", refresh=False,
            )

            self.assertEqual([ref.path for ref in refs], ["programs/explorations/atlas/2026-07-21-live.md"])
            self.assertRegex(refs[0].blob_oid, r"^[0-9a-f]{40,64}$")

    def test_receipt_context_replays_until_qualia_confirms_then_uses_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state_path = repo / "data" / "receipts.json"
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "user.email", "atlas@example.test")
            self._git(repo, "config", "user.name", "Atlas Test")
            note_dir = repo / "programs" / "explorations" / "atlas"
            note_dir.mkdir(parents=True)
            older = note_dir / "2026-07-21-older.md"
            older.write_text("older body", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "older")
            newest = note_dir / "2026-07-22-newest.md"
            newest.write_text("newest body", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "newest")

            calls: list[str] = []

            async def lookup(ref):
                calls.append(ref.path)
                if ref.path.endswith("newest.md"):
                    return (
                        f'Semantic search: "{ref.receipt_token}"\n'
                        f"1. [observation [qualia_notice]] receipt {ref.receipt_token}\n"
                        "   id=41234 score=1"
                    )
                return f'Semantic search: "{ref.receipt_token}"\nNo results'

            context = asyncio.run(build_github_handoff_context(
                "Atlas",
                repo_root=repo,
                remote="",
                branch="HEAD",
                refresh=False,
                receipt_state_path=state_path,
                receipt_lookup=lookup,
            ))

            self.assertIn("older body", context)
            self.assertNotIn("newest body", context)
            self.assertIn("2026-07-22-newest.md -> Qualia observation #41234", context)
            self.assertIn("Qualia receipt token:", context)
            self.assertEqual(len(calls), 2)

            calls.clear()
            asyncio.run(build_github_handoff_context(
                "Atlas",
                repo_root=repo,
                remote="",
                branch="HEAD",
                refresh=False,
                receipt_state_path=state_path,
                receipt_lookup=lookup,
            ))
            self.assertEqual(calls, ["programs/explorations/atlas/2026-07-21-older.md"])

            newest.write_text("newest body, revised", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "revise newest")
            calls.clear()
            revised = asyncio.run(build_github_handoff_context(
                "Atlas",
                repo_root=repo,
                remote="",
                branch="HEAD",
                refresh=False,
                receipt_state_path=state_path,
                receipt_lookup=lambda ref: asyncio.sleep(0, result=""),
            ))
            self.assertIn("newest body, revised", revised)

    def test_query_echo_alone_is_not_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state_path = repo / "data" / "receipts.json"
            self._git(repo, "init", "--quiet")
            self._git(repo, "config", "user.email", "atlas@example.test")
            self._git(repo, "config", "user.name", "Atlas Test")
            note_dir = repo / "programs" / "explorations" / "atlas"
            note_dir.mkdir(parents=True)
            note = note_dir / "2026-07-22-note.md"
            note.write_text("must remain full", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "--quiet", "-m", "note")

            async def echoed_query_only(ref):
                return f'Semantic search: "exact token {ref.receipt_token}"\nNo results'

            context = asyncio.run(build_github_handoff_context(
                "Atlas",
                repo_root=repo,
                remote="",
                branch="HEAD",
                refresh=False,
                receipt_state_path=state_path,
                receipt_lookup=echoed_query_only,
            ))
            self.assertIn("must remain full", context)
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
