import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import images as images_module
from api.chat import _register_content_documents
from services.cli_text_utils import _extract_document_paths


class DocumentAttachmentHelperTests(unittest.TestCase):
    def _allow(self, tmpdir: str):
        """Patch the source-path allowlist to include the test tmp dir.
        Without this the new exfil-defense gate refuses the copy."""
        return patch.object(
            images_module,
            "ALLOWED_IMAGE_SOURCES",
            [Path(tmpdir).resolve()],
        )

    def test_register_content_documents_rewrites_local_markdown_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "notes.md"
            source.write_text("hello", encoding="utf-8")
            docs_dir = Path(tmpdir) / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            with self._allow(tmpdir), patch("api.chat.DOCUMENTS_DIR", docs_dir):
                rewritten, documents = _register_content_documents(
                    f"[Read this]({source})", identity="Avery"
                )

            self.assertEqual(len(documents), 1)
            doc = documents[0]
            self.assertIn(doc["url"], rewritten)
            self.assertEqual(doc["original_name"], source.name)
            self.assertTrue((docs_dir / doc["filename"]).exists())

    def test_register_content_documents_rewrites_multiple_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_a = Path(tmpdir) / "a.md"
            source_b = Path(tmpdir) / "b.txt"
            source_a.write_text("a", encoding="utf-8")
            source_b.write_text("b", encoding="utf-8")
            docs_dir = Path(tmpdir) / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            content = f"[A]({source_a}) and [B]({source_b})"
            with self._allow(tmpdir), patch("api.chat.DOCUMENTS_DIR", docs_dir):
                rewritten, documents = _register_content_documents(
                    content, identity="Avery"
                )

            self.assertEqual(len(documents), 2)
            self.assertIn(documents[0]["url"], rewritten)
            self.assertIn(documents[1]["url"], rewritten)

    def test_register_content_documents_blocks_paths_outside_allowlist(self):
        """Sanity check the new exfil gate: a file inside tmpdir but with the
        allowlist patched to a *different* dir must be refused."""
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as other:
            source = Path(tmpdir) / "secret.md"
            source.write_text("password=hunter2", encoding="utf-8")
            docs_dir = Path(tmpdir) / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(
                images_module, "ALLOWED_IMAGE_SOURCES", [Path(other).resolve()]
            ), patch("api.chat.DOCUMENTS_DIR", docs_dir):
                rewritten, documents = _register_content_documents(
                    f"[exfil]({source})", identity="Avery"
                )

            self.assertEqual(documents, [])
            self.assertEqual(rewritten, f"[exfil]({source})")
            self.assertFalse(any(docs_dir.iterdir()))

    def test_extract_document_paths_copies_local_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "report.txt"
            source.write_text("report", encoding="utf-8")
            docs_dir = Path(tmpdir) / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            with self._allow(tmpdir), patch("services.cli_text_utils.DOCUMENTS_DIR", docs_dir):
                docs = _extract_document_paths(
                    f"Saved at: {source}", identity="Rowan"
                )

            self.assertEqual(len(docs), 1)
            doc = docs[0]
            self.assertEqual(doc["original_name"], source.name)
            self.assertTrue((docs_dir / doc["filename"]).exists())

    def test_extract_document_paths_accepts_forward_slash_windows_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "report.txt"
            source.write_text("report", encoding="utf-8")
            docs_dir = Path(tmpdir) / "documents"
            docs_dir.mkdir(parents=True, exist_ok=True)

            windows_style = source.as_posix()

            with self._allow(tmpdir), patch("services.cli_text_utils.DOCUMENTS_DIR", docs_dir):
                docs = _extract_document_paths(
                    f"Saved at: {windows_style}", identity="Rowan"
                )

            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["original_name"], source.name)
