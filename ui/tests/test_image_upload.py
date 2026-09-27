import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from starlette.datastructures import UploadFile
from starlette.requests import Request

from api import images


_ONE_BY_ONE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9f0AAAAASUVORK5CYII="
)


class ImageUploadTests(unittest.IsolatedAsyncioTestCase):
    def _request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/images/upload",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "scheme": "http",
                "server": ("testserver", 80),
            }
        )

    async def test_upload_image_accepts_valid_png(self):
        req = self._request()
        upload = UploadFile(filename="tiny.png", file=BytesIO(_ONE_BY_ONE_PNG))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(images, "IMAGES_DIR", tmp_path):
                response = await images.upload_image(req, upload, "Rowan")
                payload = json.loads(response.body)
                exists = (tmp_path / payload["filename"]).exists()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(exists)
        self.assertEqual(payload["url"].startswith("/api/images/file/"), True)

    async def test_upload_image_rejects_bad_magic_bytes(self):
        req = self._request()
        upload = UploadFile(filename="fake.png", file=BytesIO(b"not-a-png"))

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(images, "IMAGES_DIR", Path(tmp)):
                response = await images.upload_image(req, upload, "Rowan")

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match", payload["error"])


if __name__ == "__main__":
    unittest.main()
