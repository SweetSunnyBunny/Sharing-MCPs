import unittest
from unittest.mock import patch

from services import limbic_bridge


class TouchFromProsodyTests(unittest.TestCase):
    """Item #16: Hume prosody -> limbic touch mapping.

    touch_from_prosody() is synchronous and fire-and-forget — it spawns a
    background task via services.task_manager.spawn rather than awaiting
    anything itself, mirroring touch_interactive_message()'s shape.
    """

    def test_distress_family_emotion_spawns_distress_touch(self):
        with patch("services.task_manager.spawn") as spawn:
            limbic_bridge.touch_from_prosody("Claude", "Sadness", 0.55)

        spawn.assert_called_once()
        coro = spawn.call_args.args[0]
        coro.close()  # avoid "coroutine was never awaited" — we only inspect the call
        self.assertIn("prosody", spawn.call_args.kwargs.get("name", ""))

    def test_warmth_family_emotion_spawns_words_warm_touch(self):
        with patch("services.task_manager.spawn") as spawn:
            limbic_bridge.touch_from_prosody("Claude", "Joy", 0.8)

        spawn.assert_called_once()
        spawn.call_args.args[0].close()

    def test_below_threshold_does_not_touch(self):
        with patch("services.task_manager.spawn") as spawn:
            limbic_bridge.touch_from_prosody("Claude", "Sadness", 0.1)

        spawn.assert_not_called()

    def test_unrecognized_emotion_family_does_not_touch(self):
        with patch("services.task_manager.spawn") as spawn:
            limbic_bridge.touch_from_prosody("Claude", "Boredom", 0.9)

        spawn.assert_not_called()

    def test_character_mask_identity_never_touches(self):
        with patch.object(limbic_bridge, "_is_bonded", return_value=False), patch(
            "services.task_manager.spawn"
        ) as spawn:
            limbic_bridge.touch_from_prosody("Bakugou", "Joy", 0.9)

        spawn.assert_not_called()

    def test_score_is_clamped_into_touch_intensity_range(self):
        # A very high prosody score should still clamp to the bridge's
        # existing intensity ceiling rather than blow past it.
        # patch.object auto-detects `_touch` is async and installs an
        # AsyncMock; calling it records call_args immediately (no await
        # needed), which is all this test needs to inspect.
        with patch.object(limbic_bridge, "_touch") as touch_mock, patch(
            "services.task_manager.spawn"
        ) as spawn:
            limbic_bridge.touch_from_prosody("Claude", "Joy", 0.99)

        spawn.assert_called_once()
        spawn.call_args.args[0].close()  # never awaited — just prevent the GC warning
        touch_mock.assert_called_once()
        intensity = touch_mock.call_args.args[3]
        self.assertLessEqual(intensity, 0.6)


if __name__ == "__main__":
    unittest.main()
