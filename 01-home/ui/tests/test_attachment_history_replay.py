import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config as config_module
from services.session_lifecycle import build_messages_array


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute_fetchall(self, _query, _params):
        return list(self._rows)


class AttachmentHistoryReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_messages_array_keeps_document_only_and_image_only_turns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            images_dir = root / "images"
            docs_dir = root / "documents"
            images_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)

            (images_dir / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
            (docs_dir / "notes.md").write_text("# hello\nworld", encoding="utf-8")

            rows = [
                ("assistant", "latest reply", None),
                ("user", "", json.dumps({"images": [{"filename": "pic.png"}]})),
                ("assistant", "earlier reply", None),
                (
                    "user",
                    "",
                    json.dumps(
                        {
                            "documents": [
                                {
                                    "filename": "notes.md",
                                    "original_name": "notes.md",
                                }
                            ]
                        }
                    ),
                ),
            ]

            fake_db = _FakeDb(rows)
            with patch.object(config_module, "IMAGES_DIR", images_dir), patch(
                "services.attachment_context.DOCUMENTS_DIR",
                docs_dir,
            ):
                messages = await build_messages_array(fake_db, "conv-1")

        self.assertEqual(len(messages), 4)
        self.assertIn("Owner shared a document", messages[0]["content"])
        self.assertEqual(messages[2]["role"], "user")
        self.assertIsInstance(messages[2]["content"], list)
        self.assertEqual(messages[2]["content"][0]["type"], "image")
        self.assertIn("/api/images/file/pic.png", messages[2]["content"][0]["source"]["url"])
