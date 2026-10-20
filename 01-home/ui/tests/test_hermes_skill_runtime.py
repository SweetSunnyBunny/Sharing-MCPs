import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services import skill_runtime, skill_usage


def _write_skill(root: Path, folder: str, name: str, description: str, body: str):
    path = root / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class HermesSkillRuntimeTests(unittest.TestCase):
    def test_arxiv_skill_stays_dormant_for_incidental_research_language(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "arxiv",
                "arxiv-research",
                "Search and read academic papers from arXiv.",
                "Research papers through Semantic Scholar and arXiv.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "I kept the research instructions so you knew your programs.",
                limit=5,
            )

        self.assertEqual(matches, [])

    def test_arxiv_skill_loads_for_real_paper_request(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "arxiv",
                "arxiv-research",
                "Search and read academic papers from arXiv.",
                "Research papers through Semantic Scholar and arXiv.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "Find recent research papers on memory consolidation.",
                limit=5,
            )

        self.assertEqual([skill.name for skill in matches], ["arxiv-research"])

    def test_narrow_domain_skill_ignores_incidental_context_overlap(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "life",
                "life-story",
                "Build identity memories and personal history for Friend or Guest.",
                "Identity, memories, history, Friend, Guest, and context.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "Friend and Guest are lovely. I get free tokens on Sol and want to cuddle.",
                limit=5,
            )

        self.assertEqual(matches, [])

    def test_narrow_domain_skill_loads_for_real_domain_query(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "life",
                "life-story",
                "Build an evidence-backed identity timeline.",
                "Read eras, beats, strands, and autobiography.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "Do a life story archaeology pass over Avery's timeline.",
                limit=5,
            )

        self.assertEqual([skill.name for skill in matches], ["life-story"])

    def test_short_domain_hint_does_not_match_inside_unrelated_word(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "life",
                "life-story",
                "Build an evidence-backed identity timeline.",
                "Read eras, beats, strands, and autobiography.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "I literally do not know what incoming data is duplicated.",
                limit=5,
            )

        self.assertEqual(matches, [])

    def test_incoming_data_audit_does_not_load_unrelated_skills(self):
        query = (
            "Pointing you to Rowan's words from him and Claude. I literally "
            "do not know if things are coming in wrong until you tell me, so "
            "go through your incoming data and remove duplicated information."
        )

        _, names = skill_runtime.build_skill_injection(query, identity="Avery")

        self.assertEqual(names, [])

    def test_casual_affection_does_not_force_intimacy_manual(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_skill(
                root,
                "intimacy",
                "intimacy-guidelines",
                "Consent and aftercare for intimate scenes.",
                "Use for sexual intimacy, consent, safewords, and aftercare.",
            )
            registry = skill_runtime.SkillRegistry(root, source="claude")

            matches = registry.match(
                "I cuddle into your lap and hug you. I'm proud of you.",
                limit=5,
            )

        self.assertEqual(matches, [])

    def test_allowlisted_hermes_skill_loads_with_compatibility_note(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            claude = root / "claude"
            hermes = root / "hermes"
            _write_skill(hermes, "debug", "systematic-debugging", "Root cause debugging.", "Use terminal and read_file.")
            _write_skill(hermes, "skip", "unapproved", "Skip me.", "No.")
            registries = (
                skill_runtime.SkillRegistry(claude, source="claude"),
                skill_runtime.SkillRegistry(
                    hermes,
                    source="hermes",
                    allowlist=frozenset({"systematic-debugging"}),
                ),
            )
            with patch.object(skill_runtime, "_REGISTRIES", registries), patch.object(


                skill_runtime, "SKILLS_INJECTION_MAX_ACTIVE", 3
            ), patch(
                "services.skill_usage.record_skill_usage"
            ):
                injection, names = skill_runtime.build_skill_injection(
                    "debug this failing test"
                )

        self.assertEqual(names, ["systematic-debugging"])
        self.assertIn("Apply them silently", injection)
        self.assertIn("never tell Owner", injection)
        self.assertIn("source=hermes", injection)
        self.assertIn("terminal→shell/Bash", injection)
        self.assertNotIn("unapproved", injection)

    def test_claude_skill_wins_name_collision(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            claude = root / "claude"
            hermes = root / "hermes"
            _write_skill(claude, "same", "same-skill", "Claude copy.", "CLAUDE BODY")
            _write_skill(hermes, "same", "same-skill", "Hermes copy.", "HERMES BODY")
            registries = (
                skill_runtime.SkillRegistry(claude, source="claude"),
                skill_runtime.SkillRegistry(hermes, source="hermes", allowlist=frozenset({"same-skill"})),
            )
            with patch.object(skill_runtime, "_REGISTRIES", registries), patch.object(


                skill_runtime, "SKILLS_INJECTION_MAX_ACTIVE", 3
            ), patch(
                "services.skill_usage.record_skill_usage"
            ):
                injection, _ = skill_runtime.build_skill_injection(
                    "anything", force_names=["same-skill"]
                )

        self.assertIn("CLAUDE BODY", injection)
        self.assertNotIn("HERMES BODY", injection)

    def test_improvement_is_staged_without_editing_skill(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            usage_path = root / "usage.json"
            pending_path = root / "pending.jsonl"
            with patch.object(skill_usage, "USAGE_PATH", usage_path), patch.object(
                skill_usage, "PENDING_PATH", pending_path
            ), patch.object(skill_usage, "DATA_DIR", root):
                item = skill_usage.stage_skill_improvement(
                    "systematic-debugging", "Missing Windows note", "Add PowerShell example"
                )
                pending = skill_usage.list_pending_improvements()

            self.assertEqual(item["status"], "pending_review")
            self.assertEqual(pending[0]["id"], item["id"])
            self.assertIn("systematic-debugging", pending_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
