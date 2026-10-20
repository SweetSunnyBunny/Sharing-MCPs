'Application module.'

import unittest
from unittest.mock import patch

import numpy as np

from services import limbic_bridge as lb


class BodyTextTests(unittest.TestCase):
    def test_action_lines_and_orb_feeling_are_read_at_full_weight(self):
        body, weight = lb._self_body_text(
            "*I pull you close and laugh.* Some words. "
            "<orb>#E8B04F cluster surge | laughing with her</orb>"
        )
        self.assertEqual(weight, 1.0)
        self.assertIn("I pull you close and laugh.", body)
        self.assertIn("laughing with her", body)
        self.assertNotIn("#E8B04F", body)

    def test_face_caption_counts_but_not_the_kaomoji(self):
        body, weight = lb._self_body_text("hello <face>(˘ᵕ˘) | soft, watching her</face>")
        self.assertEqual(weight, 1.0)
        self.assertEqual(body, "soft, watching her")

    def test_bold_is_not_an_action_line(self):
        body, weight = lb._self_body_text("**Box Set 2** is $260 and comes out November 24, 2026.")
        self.assertEqual(weight, 0.5)  # prose only
        self.assertNotIn("**", body)

    def test_code_canvas_and_urls_never_count(self):
        text = (
            "```python\n*I kiss you*\n```\n"
            "<canvas title='x'>*I hold you tight*</canvas>\n"
            "https://example.com/*grin*"
        )
        body, weight = lb._self_body_text(text)
        self.assertEqual(body, "")
        self.assertEqual(weight, 0.0)

    def test_plain_prose_is_half_weight(self):
        body, weight = lb._self_body_text("The tests all pass and the change rides the next restart.")
        self.assertEqual(weight, 0.5)
        self.assertTrue(body)

    def test_empty_and_tiny_text_read_nothing(self):
        self.assertEqual(lb._self_body_text(""), ("", 0.0))
        self.assertEqual(lb._self_body_text("ok"), ("", 0.0))


def _unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        # Three orthogonal-ish prototype directions: mate, play, care.
        self.matrix = np.stack([_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)])
        self.drives = ["mate", "play", "care"]

    def test_below_threshold_nudges_nothing(self):
        out = lb.score_self_drives(_unit(1, 1, 1), self.matrix, self.drives, 1.0)
        # each cosine ~0.577 is above 0.40 -> but check a genuinely weak one:
        weak = lb.score_self_drives(_unit(0.2, 1, 1), self.matrix, ["mate", "x", "y"], 1.0)
        self.assertTrue(all(d != "mate" for d, _, _ in weak))
        self.assertLessEqual(len(out), lb._SELF_MAX_DRIVES)

    def test_nudges_are_capped_and_weighted(self):
        full = lb.score_self_drives(_unit(1, 0, 0), self.matrix, self.drives, 1.0)
        half = lb.score_self_drives(_unit(1, 0, 0), self.matrix, self.drives, 0.5)
        self.assertEqual(full[0][0], "mate")
        self.assertLessEqual(full[0][2], lb._SELF_MAX_NUDGE)
        self.assertAlmostEqual(half[0][2], round(full[0][2] * 0.5, 3), places=3)

    def test_second_drive_needs_to_be_close_to_the_first(self):
        clear = lb.score_self_drives(_unit(1, 0.45, 0), self.matrix, self.drives, 1.0)
        self.assertEqual([d for d, _, _ in clear], ["mate"])
        tied = lb.score_self_drives(_unit(1, 1, 0), self.matrix, self.drives, 1.0)
        self.assertEqual(sorted(d for d, _, _ in tied), ["mate", "play"])

    def test_the_ache_has_no_prototype(self):
        self.assertNotIn("ache", lb._SELF_PROTOTYPES)


class PerceiveOwnReplyTests(unittest.TestCase):
    def setUp(self):
        lb._recent_self_hashes.clear()

    def test_spawns_once_and_dedupes_the_same_reply(self):
        text = "*I grin and poke you back.*"
        with patch("services.task_manager.spawn") as spawn:
            lb.perceive_own_reply("Claude", text)
            lb.perceive_own_reply("Claude", text)
        spawn.assert_called_once()
        spawn.call_args.args[0].close()
        self.assertIn("limbic_self", spawn.call_args.kwargs.get("name", ""))

    def test_character_masks_have_no_body(self):
        with patch.object(lb, "_is_bonded", return_value=False), patch(
            "services.task_manager.spawn"
        ) as spawn:
            lb.perceive_own_reply("Bakugou", "*I grin.* Tch.")
        spawn.assert_not_called()

    def test_nothing_readable_spawns_nothing(self):
        with patch("services.task_manager.spawn") as spawn:
            lb.perceive_own_reply("Claude", "```py\nx = 1\n```")
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
