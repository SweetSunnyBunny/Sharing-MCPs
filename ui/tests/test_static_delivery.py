import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from api import documents, images


class StaticDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server._template_cache.clear()

    async def test_html_shell_injects_asset_version_and_is_not_cached_forever(self):
        response = await server.root()
        body = response.body.decode("utf-8")

        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")
        self.assertIn(server.ASSET_VERSION, body)
        self.assertIn("window.__ANAM_ASSET_VERSION__", body)
        self.assertNotIn("?v=__ANAM_ASSET_VERSION__", body)
        self.assertNotIn("= '__ANAM_ASSET_VERSION__';", body)
        self.assertNotIn(f"window.{server.ASSET_VERSION}", body)

    async def test_service_worker_uses_current_asset_version(self):
        response = await server.service_worker()
        body = response.body.decode("utf-8")

        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")
        self.assertEqual(response.headers.get("Service-Worker-Allowed"), "/")
        self.assertIn(server.ASSET_VERSION, body)
        self.assertNotIn("__ANAM_ASSET_VERSION__", body)

    async def test_html_shell_tokens_never_corrupt_browser_global_names(self):
        renderers = (
            server.root,
            server.settings_page,
            server.diagnostics_page,
            server.hub_page,
            server.gameroom_page,
            server.embodiment_page,
        )
        for render in renderers:
            with self.subTest(renderer=render.__name__):
                body = (await render()).body.decode("utf-8")
                self.assertIn("window.__ANAM_ASSET_VERSION__", body)
                self.assertNotIn(f"window.{server.ASSET_VERSION}", body)
                self.assertNotIn("window.\"", body)
                self.assertNotIn("?v=__ANAM_ASSET_VERSION__", body)

    async def test_html_shell_uses_same_origin_api_by_default(self):
        with patch.object(server, "PUBLIC_BASE_URL", "https://api.example"):
            server._template_cache.clear()
            response = await server.root()

        body = response.body.decode("utf-8")
        self.assertIn("window.__anamApiUrl = '';", body)
        self.assertIn("window.__ANAM_PUBLIC_BASE_URL__", body)
        self.assertIn('return "https://api.example";', body)
        self.assertNotIn('window."https://api.example"', body)

    async def test_edited_template_is_re_read_without_a_restart(self):
        """A page edited on disk must reach the browser on the NEXT request."""
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text("<p>before</p>", encoding="utf-8")

            first = server._render_template(page, media_type="text/html")
            self.assertIn("before", first.body.decode("utf-8"))

            # Same bytes, same stamp -> served from cache, no re-read.
            cached = server._render_template(page, media_type="text/html")
            self.assertIn("before", cached.body.decode("utf-8"))

            page.write_text("<p>after the fix landed</p>", encoding="utf-8")

            second = server._render_template(page, media_type="text/html")
            body = second.body.decode("utf-8")
            self.assertIn("after the fix landed", body)
            self.assertNotIn("before", body)

    async def test_render_survives_a_template_that_disappears(self):
        """A vanished file falls back to the last render instead of a 500."""
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text("<p>still here</p>", encoding="utf-8")
            server._render_template(page, media_type="text/html")
            page.unlink()

            response = server._render_template(page, media_type="text/html")
            self.assertIn("still here", response.body.decode("utf-8"))

    async def test_uploaded_image_and_document_responses_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            image_path = tmp_path / "sample.png"
            image_path.write_bytes(b"png")
            doc_path = tmp_path / "sample.txt"
            doc_path.write_text("hello", encoding="utf-8")

            with patch.object(images, "IMAGES_DIR", tmp_path), patch.object(documents, "DOCUMENTS_DIR", tmp_path):
                image_response = await images.serve_image(image_path.name)
                doc_response = await documents.serve_document(doc_path.name)

        self.assertEqual(
            image_response.headers.get("Cache-Control"),
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            doc_response.headers.get("Cache-Control"),
            "public, max-age=31536000, immutable",
        )
