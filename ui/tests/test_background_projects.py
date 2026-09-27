import unittest

from services.background_projects import (
    advance_project_context,
    build_project_context,
    default_project_squad,
    evaluate_project_response,
    normalize_contract,
    parse_contract,
)


class BackgroundProjectContractTests(unittest.TestCase):
    def test_continue_advances_but_preserves_contract(self):
        contract = normalize_contract(
            outcome="Ship it",
            verification="Tests pass",
            max_turns=3,
            project_id="project-1",
        )
        context = build_project_context("Do the thing", contract)
        cleaned, decision = evaluate_project_response(
            context,
            'Progress made. <project_status state="continue">One test remains.</project_status>',
        )

        self.assertEqual(cleaned, "Progress made.")
        self.assertTrue(decision.should_continue)
        advanced = parse_contract(advance_project_context(context))
        self.assertEqual(advanced["turn"], 2)
        self.assertEqual(advanced["project_id"], "project-1")

    def test_missing_marker_is_bounded_at_turn_limit(self):
        contract = normalize_contract(max_turns=2, turn=2)
        context = build_project_context("Do the thing", contract)
        cleaned, decision = evaluate_project_response(context, "I forgot the marker.")

        self.assertEqual(cleaned, "I forgot the marker.")
        self.assertEqual(decision.state, "blocked")
        self.assertFalse(decision.should_continue)
        self.assertIn("2-turn limit", decision.reason)

    def test_complete_never_reschedules(self):
        context = build_project_context("Do it", normalize_contract(max_turns=5))
        _, decision = evaluate_project_response(
            context,
            '<project_status state="complete">Verified.</project_status>',
        )
        self.assertEqual(decision.state, "complete")
        self.assertFalse(decision.should_continue)

    def test_squad_survives_bounded_continuation(self):
        squad = default_project_squad("Claude")
        contract = normalize_contract(
            project_id="project-squad",
            max_turns=4,
            squad=squad,
        )
        context = build_project_context("Build the useful thing", contract)

        advanced = parse_contract(advance_project_context(context))

        self.assertEqual(advanced["squad"], squad)
        self.assertEqual(advanced["turn"], 2)
        self.assertIn("available squad", context.lower())

    def test_default_squad_keeps_the_bonded_identity_as_lead(self):
        squad = default_project_squad("River")

        self.assertEqual([seat["role"] for seat in squad], ["Scout", "Lead", "Review", "Handoff"])
        self.assertEqual(squad[1]["agent"], "River")
        self.assertEqual(squad[0]["agent"], "research-runner")


if __name__ == "__main__":
    unittest.main()
