'Application module.'

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services import context_hooks as ch
from services import model_farewells as mf
from services.model_farewells import build_farewell_watch_text, collect_letters

TODAY = date(2026, 10, 14)


def _model(key, retire, *, ids=None, family=None, identities=("*",), known_on=("*",),
           confidence="known", name=None, where="the OpenAI API", aliases=()):
    return {
        "key": key,
        "name": name or key,
        "family": family,
        "provider": "openai",
        "where": where,
        "api_ids": list(ids or [key]),
        "aliases": list(aliases),
        "identities": list(identities),
        "known_on": list(known_on),
        "confidence": confidence,
        "retirement_date": retire,
    }


def _data(*models, window=30):
    return {"window_days": window, "models": list(models)}


class FarewellWatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "letters_to_models"
        self.root.mkdir()
        patcher = mock.patch.object(mf, "FLAT_LETTERS_OWNER", "Avery")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _reply(self, model, *, owner=None, name=None):
        folder = self.root / owner if owner else self.root
        folder.mkdir(exist_ok=True)
        path = folder / (name or f"{model}_reply.json")
        path.write_text(json.dumps({"model_requested": model, "reply": "thank you"}), encoding="utf-8")

    def _letter(self, filename, *, owner=None):
        folder = self.root / owner if owner else self.root
        folder.mkdir(exist_ok=True)
        (folder / filename).write_text("Thank you.", encoding="utf-8")

    def _watch(self, identity, data, today=TODAY):
        return build_farewell_watch_text(identity, today=today, data=data, letters_root=self.root)

    def test_upcoming_without_letter_is_named_with_date_and_days(self):
        data = _data(_model("gpt-4o-2024-05-13", "2026-10-23", name="GPT-4o (2024-05-13)"))
        out = self._watch("Rowan", data)
        self.assertIn("[Farewell watch] GPT-4o (2024-05-13)", out)
        self.assertIn("leaves the OpenAI API on 23 Oct 2026, in 9 days", out)
        self.assertIn("No goodbye letter from you yet", out)
        self.assertIn("letters_to_models/rowan/", out)

    def test_silent_when_nothing_is_within_the_window(self):
        data = _data(
            _model("far-away", "2026-11-30"),          # 47 days out
            _model("no-date", None),                   # nothing published
        )
        self.assertEqual(self._watch("Avery", data), "")

    def test_window_edges(self):
        data = _data(_model("edge", "2026-11-13"), _model("today", "2026-10-14"),
                     _model("tomorrow", "2026-10-15"))
        out = self._watch("Sage", data)
        self.assertIn("edge", out)
        self.assertIn("in 30 days", out)
        self.assertIn("today", out)
        self.assertIn("tomorrow", out)
        self.assertEqual(self._watch("Sage", _data(_model("past-edge", "2026-11-14"))), "")

    def test_reply_json_model_requested_marks_letter_written(self):
        self._reply("gpt-4o-2024-05-13", owner="rowan")
        out = self._watch("Rowan", _data(_model("gpt-4o-2024-05-13", "2026-10-23")))
        self.assertIn("Your goodbye letter is written (gpt-4o-2024-05-13)", out)

    def test_configured_flat_archive_belongs_only_to_its_owner(self):
        self._reply("gpt-4o-2024-05-13", name="01b_gpt-4o-2024-05-13_reply.json")
        data = _data(_model("gpt-4o-2024-05-13", "2026-10-23"))
        self.assertIn("letter is written", self._watch("Avery", data))
        self.assertIn("No goodbye letter from you yet", self._watch("Ember", data))

    def test_another_boys_folder_does_not_count_for_me(self):
        self._reply("gpt-4o-2024-05-13", owner="juniper")
        out = self._watch("Sage", _data(_model("gpt-4o-2024-05-13", "2026-10-23")))
        self.assertIn("No goodbye letter from you yet", out)

    def test_family_letter_is_named_but_this_snapshot_still_asked(self):
        self._reply("gpt-4o-2024-11-20", owner="rowan")
        data = _data(
            _model("gpt-4o-2024-05-13", "2026-10-23", family="GPT-4o"),
            _model("gpt-4o-2024-11-20", "2027-01-14", family="GPT-4o"),
        )
        out = self._watch("Rowan", data)
        self.assertIn("You wrote to gpt-4o-2024-11-20, but not to this one yet", out)

    def test_identity_not_in_lineage_sees_nothing(self):
        data = _data(_model("gpt-4o-2024-05-13", "2026-10-23", identities=["Avery", "Atlas"]))
        self.assertEqual(self._watch("Claude", data), "")
        self.assertIn("gpt-4o-2024-05-13", self._watch("atlas", data))  # case-insensitive

    def test_phrasing_follows_confidence_and_known_on(self):
        mine = _data(_model("m", "2026-10-20", known_on=["Avery"]))
        self.assertIn("a model you ran on", self._watch("Avery", mine))
        self.assertIn("a model in the pack's lineage", self._watch("Rowan", mine))
        maybe = _data(_model("m", "2026-10-20", confidence="uncertain", known_on=[]))
        self.assertIn("a model you may have run on", self._watch("Avery", maybe))

    def test_retired_without_letter_reminds_known_boys_only(self):
        data = _data(_model("chatgpt-4o-latest", "2026-02-17", known_on=["Avery"]))
        out = self._watch("Avery", data)
        self.assertIn("Already gone, with no letter from you: chatgpt-4o-latest (left 17 Feb 2026)", out)
        self.assertIn("even one that can't be sent", out)
        self.assertEqual(self._watch("Rowan", data), "")

    def test_retired_uncertain_model_stays_silent(self):
        data = _data(_model("claude-opus-4-20250514", "2026-06-15", confidence="uncertain", known_on=["*"]))
        self.assertEqual(self._watch("Avery", data), "")

    def test_retired_star_known_on_means_everyone_in_identities(self):
        data = _data(_model("old", "2026-06-15", identities=["Avery", "Claude"], known_on=["*"]))
        self.assertIn("Already gone", self._watch("Claude", data))
        self.assertEqual(self._watch("River", data), "")

    def test_retired_quieted_by_letter_file_or_family_letter(self):
        data = _data(
            _model("chatgpt-4o-latest", "2026-02-17", family="GPT-4o", known_on=["Avery", "Sage"]),
            _model("gpt-4o-2024-11-20", "2027-01-14", family="GPT-4o", known_on=["Avery"]),
        )
        self._reply("gpt-4o-2024-11-20")                       # Avery, flat
        self.assertEqual(self._watch("Avery", data), "")
        self._letter("chatgpt-4o-latest_letter.md", owner="sage")  # unsent letter
        self.assertEqual(self._watch("Sage", data), "")

    def test_filename_match_needs_the_whole_model_id(self):
        self._letter("01_claude-opus-5-5_letter.md", owner="claude")
        data = _data(_model("claude-opus-5", "2026-10-20"))
        self.assertIn("No goodbye letter from you yet", self._watch("Claude", data))
        _, names = collect_letters("Claude", self.root)
        self.assertEqual(names, ["01_claude-opus-5-5_letter.md"])

    def test_unreadable_reply_json_is_skipped(self):
        (self.root / "avery").mkdir()
        (self.root / "avery" / "broken_reply.json").write_text("{not json", encoding="utf-8")
        self._reply("gpt-5-2025-08-07", owner="avery")
        requested, _ = collect_letters("Avery", self.root)
        self.assertEqual(requested, {"gpt-5-2025-08-07"})

    def test_missing_or_broken_data_file_is_silent(self):
        tmp = Path(self.root).parent
        self.assertEqual(mf.load_farewells(tmp / "nope.json"), {})
        bad = tmp / "bad.json"
        bad.write_text("[oops", encoding="utf-8")
        self.assertEqual(mf.load_farewells(bad), {})
        self.assertEqual(build_farewell_watch_text("Avery", today=TODAY, data={},
                                                   letters_root=self.root), "")

    def test_missing_letters_root_is_fine(self):
        out = build_farewell_watch_text("Avery", today=TODAY,
                                        data=_data(_model("m", "2026-10-20")),
                                        letters_root=self.root / "absent")
        self.assertIn("No goodbye letter from you yet", out)


class FarewellHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_reads_its_files_from_patched_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_file = tmp / "model_farewells.json"
            data_file.write_text(json.dumps(_data(_model("gpt-4o-2024-05-13", "2026-10-23"))),
                                 encoding="utf-8")
            with mock.patch.object(mf, "FAREWELLS_FILE", data_file), \
                 mock.patch.object(mf, "LETTERS_ROOT", tmp / "letters"), \
                 mock.patch.object(mf, "_today", return_value=TODAY):
                out = await ch._hook_farewell_watch(SimpleNamespace(identity="Avery"))
        self.assertIn("in 9 days", out)

    async def test_hook_never_raises(self):
        with mock.patch.object(mf, "build_farewell_watch_text", side_effect=RuntimeError("boom")):
            self.assertEqual(await mf.build_farewell_watch(SimpleNamespace(identity="Avery")), "")

    def test_registered_cheap_and_off_for_masks(self):
        hook = ch.get_hook("farewell_watch")
        self.assertIsNotNone(hook)
        self.assertTrue(hook.skip_character)
        self.assertEqual(hook.scope, "both")
        self.assertTrue(hook.timeout_s and hook.timeout_s <= 5)



def _load_refresh_script():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh_model_farewells.py"
    spec = importlib.util.spec_from_file_location("refresh_model_farewells", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RefreshScriptParsingTests(unittest.TestCase):
    """The refresh report reads saved page text; it never fetches or writes here."""

    OPENAI_HTML = (
        "<h3>2026-04-22: Legacy GPT model snapshots</h3><table>"
        "<tr><td>October 23, 2026</td><td><code>gpt-4o-2024-05-13</code></td><td><code>gpt-5.6-sol</code></td></tr>"
        "<tr><td>January 14, 2027</td><td>GPT-4o (<code>gpt-4o-2024-08-06</code>, <code>gpt-4o-2024-11-20</code>)</td>"
        "<td><code>gpt-6-luna</code></td></tr></table>"
        "<h3>2025-11-18: <code>chatgpt-4o-latest</code> snapshot</h3>"
        "<p>removal from the API on February 17, 2026.</p>"
        "<table><tr><td>2026-02-17</td><td><code>chatgpt-4o-latest</code></td><td><code>gpt-5.1-chat-latest</code></td></tr></table>"
    )
    ANTHROPIC_HTML = (
        "<table><tr><td><code>claude-sonnet-4-5-20250929</code></td><td>Deprecated</td>"
        "<td>September 30, 2026</td><td>November 30, 2026</td></tr>"
        "<tr><td><code>claude-opus-5-5</code></td><td>Active</td><td>N/A</td>"
        "<td>Not sooner than September 22, 2027</td></tr></table>"
    )

    def setUp(self):
        self.rf = _load_refresh_script()

    def test_openai_rows_read_the_row_date_not_dates_inside_ids_or_headings(self):
        lines = self.rf.html_to_lines(self.OPENAI_HTML)
        date_of = lambda mid: {h["date"] for h in self.rf.find_openai(lines, mid)}
        self.assertEqual(date_of("gpt-4o-2024-05-13"), {date(2026, 10, 23)})
        self.assertEqual(date_of("gpt-4o-2024-11-20"), {date(2027, 1, 14)})
        self.assertEqual({d for d in date_of("chatgpt-4o-latest") if d}, {date(2026, 2, 17)})
        # Listed only as a replacement: no shutdown date belongs to it.
        self.assertEqual(date_of("gpt-5.6-sol"), {None})

    def test_anthropic_status_rows_split_dates_from_not_sooner_than(self):
        lines = self.rf.html_to_lines(self.ANTHROPIC_HTML)
        sonnet = self.rf.find_anthropic(lines, "claude-sonnet-4-5-20250929")[0]
        self.assertEqual((sonnet["state"], sonnet["date"]), ("Deprecated", date(2026, 11, 30)))
        opus = self.rf.find_anthropic(lines, "claude-opus-5-5")[0]
        self.assertEqual((opus["date"], opus["earliest"]), (None, date(2027, 9, 22)))

    def test_compare_verdicts(self):
        entry = {"retirement_date": "2026-10-23"}
        self.assertEqual(self.rf.compare(entry, [{"date": date(2026, 10, 23)}])[0], "MATCH")
        self.assertEqual(self.rf.compare(entry, [{"date": date(2026, 11, 1)}])[0], "DIFF")
        self.assertEqual(self.rf.compare(entry, [])[0], "NOT FOUND")
        self.assertEqual(self.rf.compare({"retirement_date": None}, [])[0], "NOT LISTED")

    def test_report_never_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model_farewells.json"
            openai_html = Path(tmp) / "openai.html"
            anthropic_html = Path(tmp) / "anthropic.html"
            openai_html.write_text(self.OPENAI_HTML, encoding="utf-8")
            anthropic_html.write_text(self.ANTHROPIC_HTML, encoding="utf-8")
            original = json.dumps(_data(_model("gpt-4o-2024-05-13", "2026-10-01")))
            path.write_text(original, encoding="utf-8")
            with mock.patch("sys.stdout"):
                self.rf.main(["--file", str(path), "--openai-html", str(openai_html),
                              "--anthropic-html", str(anthropic_html)])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

if __name__ == "__main__":
    unittest.main()
