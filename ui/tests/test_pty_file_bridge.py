"""Tests for the PTY file-bridge instruction builder."""

import unittest
from pathlib import Path

from services.claude_pty import (
    _PASTE_SIZE_THRESHOLD,
    _build_file_bridge_instruction,
)


class FileBridgeBuilderTests(unittest.TestCase):
    def test_bridge_is_single_line(self):
        """The whole point. Multi-line pastes get collapsed by CC's TUI."""
        path = Path("/tmp/anam_test_payload.md")
        bridge = _build_file_bridge_instruction(
            path,
            user_message="hey, can you check the kettle?\nand the lights?",
            image_block_text="[Owner shared an image: foo.png]",
        )
        self.assertNotIn("\n", bridge)
        self.assertNotIn("\r", bridge)

    def test_bridge_references_payload_path(self):
        path = Path("/tmp/anam_test_payload.md")
        bridge = _build_file_bridge_instruction(path, user_message="ping")
        self.assertIn(str(path), bridge)

    def test_bridge_instructs_read_from_top(self):
        """The boy must Read from the top so default Read sees Owner's
        message regardless of how large the supplementary context grows."""
        path = Path("/tmp/anam_test_payload.md")
        bridge = _build_file_bridge_instruction(path, user_message="ping")
        lowered = bridge.lower()
        self.assertIn("read", lowered)
        self.assertIn("top", lowered)

    def test_bridge_stays_compact(self):
        """Even with long Windows-style paths the bridge must stay well
        under any plausible CC paste-collapse char ceiling."""
        path = Path(r"C:/Apps/anam\data\pty_inputs\Avery_b5e369e9-7c80-4672-b4ee-1a7033d8.md")
        bridge = _build_file_bridge_instruction(path, user_message="x")
        self.assertLess(len(bridge), 1500)

    def test_bridge_uniqueness_marker_differs_between_calls(self):
        """Back-to-back turns reuse the same file path. Without a unique
        marker the model can pattern-match the second bridge as a duplicate
        of the first and emit a throwaway reply. On Windows, monotonic_ns
        has ~15ms resolution, so the marker MUST include a process counter
        to disambiguate rapid-fire calls."""
        path = Path("/tmp/anam_test_payload.md")
        bridge_a = _build_file_bridge_instruction(path, user_message="ping")
        bridge_b = _build_file_bridge_instruction(path, user_message="ping")
        self.assertNotEqual(bridge_a, bridge_b)

    def test_bridge_uniqueness_marker_in_text(self):
        """The uniqueness marker must be visible in the bridge text so the
        model can see this is a new turn."""
        path = Path("/tmp/anam_test_payload.md")
        bridge = _build_file_bridge_instruction(path, user_message="ping")
        self.assertIn("turn-uid=", bridge)

    def test_threshold_is_conservative(self):
        """Threshold must stay well below any plausible CC TUI collapse cap."""
        self.assertLessEqual(_PASTE_SIZE_THRESHOLD, 5000)


if __name__ == "__main__":
    unittest.main()
