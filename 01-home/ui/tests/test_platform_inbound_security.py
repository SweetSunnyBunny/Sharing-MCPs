import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.platform_bridge import PlatformBridge, _is_owner_sender, _persist_downloaded_media


class PlatformInboundSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_owner_sender_helper_matches_allowlists(self):
        with patch("services.platform_bridge.ALLOWED_DISCORD_USER_IDS", ["12345"]), patch(
            "services.platform_bridge.ALLOWED_TELEGRAM_USER_IDS", ["99999"]
        ):
            self.assertTrue(_is_owner_sender("12345"))
            self.assertTrue(_is_owner_sender("99999"))
            self.assertFalse(_is_owner_sender("00000"))

    async def test_discord_sender_not_allowlisted_is_rejected(self):
        bridge = PlatformBridge()
        with patch("services.platform_bridge.DISCORD_BOT_TOKENS", {"Avery": "token"}), patch(
            "services.platform_bridge.ALLOWED_DISCORD_USER_IDS", ["12345"]
        ):
            result = await bridge._process_inbound(
                platform="discord",
                identity="Avery",
                chat_id="chat-1",
                sender_id="99999",
                sender_name="Intruder",
                message_id="msg-1",
                text="hello",
            )

        self.assertEqual(result.get("reason"), "sender_not_allowed")
        self.assertFalse(result.get("processed", False))

    async def test_telegram_missing_sender_id_is_rejected(self):
        bridge = PlatformBridge()
        with patch("services.platform_bridge.TELEGRAM_BOT_TOKENS", {"Avery": "token"}):
            result = await bridge._process_inbound(
                platform="telegram",
                identity="Avery",
                chat_id="chat-2",
                sender_id="",
                sender_name="Unknown",
                message_id="msg-2",
                text="hello",
            )

        self.assertEqual(result.get("reason"), "missing_sender_id")
        self.assertFalse(result.get("processed", False))

    async def test_persist_downloaded_media_routes_documents_to_documents_dir(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            images_dir = root / "images"
            docs_dir = root / "documents"
            with patch("services.platform_bridge.IMAGES_DIR", images_dir), patch(
                "services.platform_bridge.DOCUMENTS_DIR", docs_dir
            ):
                result = await _persist_downloaded_media("Avery", ".pdf", b"pdf")
                doc_exists = (docs_dir / result["filename"]).exists()
                image_exists = (images_dir / result["filename"]).exists()

        self.assertEqual(result["type"], "document")
        self.assertTrue(doc_exists)
        self.assertFalse(image_exists)
        self.assertTrue(result["url"].startswith("/api/documents/file/"))
