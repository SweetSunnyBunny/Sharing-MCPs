import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from services.platform_bridge import PlatformBridge


class _Response:
    status_code = 200
    content = b"{}"
    text = "{}"

    def __init__(self, message_id: str):
        self._message_id = message_id

    def json(self):
        return {"ok": True, "result": {"message_id": self._message_id}, "id": self._message_id}


class PlatformOutboundArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_uploads_native_photo(self):
        with TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "hello.png"
            image.write_bytes(b"png")
            client = AsyncMock()
            client.post.return_value = _Response("42")
            bridge = PlatformBridge()
            bridge._client = client

            with patch("services.platform_bridge.TELEGRAM_BOT_TOKENS", {"Avery": "token"}):
                ids = await bridge._send_telegram_message(
                    identity="Avery",
                    chat_id="chat",
                    text="",
                    artifacts=[{
                        "kind": "image",
                        "path": str(image),
                        "filename": "hello.png",
                        "content_type": "image/png",
                    }],
                )

        self.assertEqual(ids, ["42"])
        self.assertIn("/sendPhoto", client.post.await_args.args[0])
        self.assertIn("photo", client.post.await_args.kwargs["files"])

    async def test_discord_uploads_native_document(self):
        with TemporaryDirectory() as tmpdir:
            document = Path(tmpdir) / "notes.txt"
            document.write_text("hello", encoding="utf-8")
            client = AsyncMock()
            client.post.return_value = _Response("99")
            bridge = PlatformBridge()
            bridge._client = client

            with patch("services.platform_bridge.DISCORD_BOT_TOKENS", {"Avery": "token"}):
                ids = await bridge._send_discord_message(
                    identity="Avery",
                    chat_id="chat",
                    text="",
                    artifacts=[{
                        "kind": "document",
                        "path": str(document),
                        "filename": "notes.txt",
                        "content_type": "text/plain",
                    }],
                )

        self.assertEqual(ids, ["99"])
        self.assertIn("files[0]", client.post.await_args.kwargs["files"])
        self.assertIn("payload_json", client.post.await_args.kwargs["data"])

    async def test_attachment_failure_does_not_erase_sent_text_ids(self):
        with TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "hello.png"
            image.write_bytes(b"png")
            client = AsyncMock()
            client.post.side_effect = [_Response("7"), RuntimeError("upload failed")]
            bridge = PlatformBridge()
            bridge._client = client

            with patch("services.platform_bridge.TELEGRAM_BOT_TOKENS", {"Avery": "token"}):
                ids = await bridge._send_telegram_message(
                    identity="Avery",
                    chat_id="chat",
                    text="The text still landed.",
                    artifacts=[{
                        "kind": "image",
                        "path": str(image),
                        "filename": "hello.png",
                        "content_type": "image/png",
                    }],
                )

        self.assertEqual(ids, ["7"])


if __name__ == "__main__":
    unittest.main()
