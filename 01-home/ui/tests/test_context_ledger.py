import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import context_ledger


class ContextLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "prompts").mkdir()
        (self.root / "prompts" / "codex").mkdir()
        (self.root / "data").mkdir()
        (self.root / "prompts" / "avery.md").write_text(
            "# Avery\n\n" + "A shared paragraph with enough words to cross the detector threshold cleanly. " * 2,
            encoding="utf-8",
        )
        (self.root / "prompts" / "codex" / "anam-contract.md").write_text(
            "stable codex contract", encoding="utf-8"
        )
        (self.root / "CLAUDE.md").write_text("repository rules", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("codex rules", encoding="utf-8")
        self.path_patch = patch.object(context_ledger, "ROOT_DIR", self.root)
        self.ledger_patch = patch.object(context_ledger, "LEDGER_PATH", self.root / "data" / "context_ledger.json")
        self.path_patch.start()
        self.ledger_patch.start()
        context_ledger._PENDING_ORIENTATIONS.clear()

    def tearDown(self):
        self.ledger_patch.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_records_post_cap_hook_rows_and_exact_cross_source_duplicates(self):
        repeated = "A shared paragraph with enough words to cross the detector threshold cleanly. " * 2
        orientation = f"[ONE]\n{repeated}\n\n[TWO]\nsmall"
        context_ledger.remember_orientation(
            identity="Avery",
            conversation_id="conv-1",
            mode="autonomous",
            is_warm_turn=False,
            hooks=[
                {"name": "deep_memory", "text": repeated, "original_chars": len(repeated) + 20, "cap_chars": len(repeated), "cached": False},
                {"name": "hub_dashboard", "text": repeated, "original_chars": len(repeated), "cap_chars": None, "cached": True},
            ],
            assembled_text=orientation,
        )
        context_ledger.record_turn_context(
            identity="Avery",
            conversation_id="conv-1",
            provider="openai",
            orientation_context=orientation,
            mode_rules="",
            skill_context="[LOCAL SKILL CATALOG]\n- one",
            model="test-model",
        )

        report = context_ledger.get_context_ledgers("Avery")["identities"][0]
        self.assertTrue(report["orientation_snapshot_matched"])
        self.assertEqual(report["orientation_chars"], len(repeated) * 2)
        self.assertEqual(report["duplicate_count"], 1)
        self.assertEqual(
            report["duplicates"][0]["sources"],
            ["identity_prompt", "orientation.deep_memory", "orientation.hub_dashboard"],
        )
        row = next(source for source in report["sources"] if source["key"] == "orientation.deep_memory")
        self.assertEqual(row["chars"], len(repeated))
        self.assertEqual(row["original_chars"], len(repeated) + 20)

    def test_hash_mismatch_falls_back_to_combined_orientation(self):
        context_ledger.remember_orientation(
            identity="Avery",
            conversation_id="conv-2",
            mode="interactive",
            is_warm_turn=True,
            hooks=[{"name": "time", "text": "old", "original_chars": 3, "cap_chars": None, "cached": False}],
            assembled_text="old",
        )
        context_ledger.record_turn_context(
            identity="Avery",
            conversation_id="conv-2",
            provider="chatgpt",
            orientation_context="new orientation",
            mode_rules="",
            skill_context="",
        )
        report = json.loads(context_ledger.LEDGER_PATH.read_text(encoding="utf-8"))["identities"]["avery"]
        self.assertFalse(report["orientation_snapshot_matched"])
        self.assertIn("orientation.combined", [row["key"] for row in report["sources"]])

    def test_codex_ledger_records_base_and_contract_hash_manifest(self):
        context_ledger.record_turn_context(
            identity="Avery",
            conversation_id="conv-codex",
            provider="codex",
            orientation_context="fresh orientation",
            mode_rules="mode",
            skill_context="skill",
        )

        report = context_ledger.get_context_ledgers("Avery")["identities"][0]
        rows = {row["key"]: row for row in report["sources"]}
        self.assertEqual(rows["identity_prompt"]["status"], "sent")
        self.assertEqual(len(rows["identity_prompt"]["sha256"]), 64)
        self.assertEqual(rows["codex_anam_contract"]["layer"], "developer")
        self.assertEqual(len(rows["codex_anam_contract"]["sha256"]), 64)
        self.assertEqual(len(rows["codex_anam_contract"]["instruction_fingerprint"]), 64)


class ContextLedgerHubContractTests(unittest.TestCase):
    def test_hub_has_context_tab_and_live_loader(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "hub.html").read_text(encoding="utf-8")
        js = (root / "static" / "js" / "hub.js").read_text(encoding="utf-8")
        self.assertIn('data-tab="context"', html)
        self.assertIn("loadContextLedger", js)
        self.assertIn("/api/hub/context-ledger", js)


if __name__ == "__main__":
    unittest.main()
