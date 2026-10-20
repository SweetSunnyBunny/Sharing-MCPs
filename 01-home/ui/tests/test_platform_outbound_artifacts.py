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

    async def test_discord_upload_failure_falls_back_to_public_link(self):
        with TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "film.mp4"
            video.write_bytes(b"mp4")
            client = AsyncMock()
            client.post.side_effect = RuntimeError("413 too large")
            bridge = PlatformBridge()
            bridge._client = client
            bridge._discord_request_json = AsyncMock(return_value={"id": "55"})

            with patch("services.platform_bridge.DISCORD_BOT_TOKENS", {"Rowan": "token"}):
                ids = await bridge._send_discord_message(
                    identity="Rowan",
                    chat_id="chat",
                    text="",
                    artifacts=[{
                        "kind": "document",
                        "path": str(video),
                        "filename": "film.mp4",
                        "content_type": "video/mp4",
                        "public_url": "https://example.com/api/images/file/film.mp4",
                    }],
                )

        self.assertEqual(ids, ["55"])
        payload = bridge._discord_request_json.await_args.kwargs["payload"]
        self.assertEqual(payload, {"content": "https://example.com/api/images/file/film.mp4"})


class ServedFileLinkTests(unittest.TestCase):
    'Servedfilelinktests.'

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.images = Path(self._tmp.name) / "images"
        self.documents = Path(self._tmp.name) / "documents"
        self.images.mkdir()
        self.documents.mkdir()
        self._patches = [
            patch("api.images.IMAGES_DIR", self.images),
            patch("services.platform_bridge.DOCUMENTS_DIR", self.documents),
            patch("services.platform_bridge.PUBLIC_BASE_URL", "https://example.com"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _collect(self, text, already=None):
        from services.platform_bridge import _collect_served_file_artifacts

        return _collect_served_file_artifacts(text, already=already)

    def test_relative_image_link_becomes_upload_and_leaves_the_words(self):
        (self.images / "Rowan_Morning_2026-10-08.png").write_bytes(b"png")
        text, found = self._collect(
            "Good morning, Bunny!\n\n![MorningMessage](/api/images/file/Rowan_Morning_2026-10-08.png)\n\nYou did it."
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "image")
        self.assertEqual(found[0]["filename"], "Rowan_Morning_2026-10-08.png")
        self.assertNotIn("/api/", text)
        self.assertIn("Good morning, Bunny!", text)
        self.assertIn("You did it.", text)

    def test_public_video_link_keeps_its_title(self):
        (self.images / "Rowan_paint_drop_dance.mp4").write_bytes(b"mp4")
        text, found = self._collect(
            "Made you this: [Paint Drop Dance](https://example.com/api/images/file/Rowan_paint_drop_dance.mp4)"
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "document")
        self.assertEqual(found[0]["content_type"], "video/mp4")
        self.assertEqual(text, "Made you this: **Paint Drop Dance**")

    def test_bare_public_link_is_attached(self):
        (self.images / "Claude_film.mp4").write_bytes(b"mp4")
        text, found = self._collect("Here: https://example.com/api/images/file/Claude_film.mp4")
        self.assertEqual([a["filename"] for a in found], ["Claude_film.mp4"])
        self.assertEqual(text, "Here:")

    def test_other_hosts_and_missing_files_are_left_alone(self):
        original = (
            "![x](https://example.com/api/images/file/a.png) "
            "![y](/api/images/file/not_there.png)"
        )
        text, found = self._collect(original)
        self.assertEqual(found, [])
        self.assertEqual(text, original)

    def test_unsafe_names_are_refused(self):
        (self.images / "_dedup_manifest.json").write_text("{}", encoding="utf-8")
        original = "[m](/api/images/file/_dedup_manifest.json) [t](/api/documents/file/..%2Fsecret.txt)"
        text, found = self._collect(original)
        self.assertEqual(found, [])
        self.assertEqual(text, original)

    def test_too_big_becomes_a_bare_link(self):
        (self.images / "huge.mp4").write_bytes(b"0123456789")
        with patch("services.platform_bridge._PLATFORM_ATTACHMENT_MAX_BYTES", 5):
            text, found = self._collect("[Big Film](/api/images/file/huge.mp4)")
        self.assertEqual(found, [])
        self.assertEqual(text, "**Big Film**\nhttps://example.com/api/images/file/huge.mp4")

    def test_already_registered_file_is_not_uploaded_twice(self):
        (self.images / "rowan_20261007_072903_f33df1fa943a.png").write_bytes(b"png")
        already = [{"path": "C:/elsewhere/original.png", "served_name": "rowan_20261007_072903_f33df1fa943a.png"}]
        text, found = self._collect(
            "![MorningMessage_HardDays](/api/images/file/rowan_20261007_072903_f33df1fa943a.png)",
            already=already,
        )
        self.assertEqual(found, [])
        self.assertEqual(text, "")

    def test_documents_route(self):
        (self.documents / "notes.md").write_text("hi", encoding="utf-8")
        text, found = self._collect("[My notes](/api/documents/file/notes.md)")
        self.assertEqual([a["kind"] for a in found], ["document"])
        self.assertEqual(text, "**My notes**")


if __name__ == "__main__":
    unittest.main()
