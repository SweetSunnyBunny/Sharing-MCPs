import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import images


class ImageSecurityTests(unittest.TestCase):
    def test_informative_filename_uses_12_hex_entropy(self):
        name = images.informative_filename("Avery", ".png")
        stem = Path(name).stem
        suffix = Path(name).suffix
        entropy = stem.rsplit("_", 1)[-1]

        self.assertEqual(suffix, ".png")
        self.assertEqual(len(entropy), 12)
        self.assertRegex(entropy, r"^[0-9a-f]{12}$")

    def test_allowed_source_path_requires_real_containment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            allowed = root / "allowed"
            sibling = root / "allowed-evil"
            inside_file = allowed / "pic.png"
            sibling_file = sibling / "pic.png"
            allowed.mkdir(parents=True, exist_ok=True)
            sibling.mkdir(parents=True, exist_ok=True)
            inside_file.write_bytes(b"ok")
            sibling_file.write_bytes(b"no")

            with patch.object(images, "ALLOWED_IMAGE_SOURCES", [allowed]):
                self.assertTrue(images._is_allowed_source_path(inside_file.resolve()))
                self.assertFalse(images._is_allowed_source_path(sibling_file.resolve()))

