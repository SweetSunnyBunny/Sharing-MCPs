"""Offline adapter contract tests: no provider, personal media or frame access."""
import asyncio
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx
from PIL import Image

PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("photos_public", PACKAGE / "server.py")
photos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(photos)


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(stream, format="PNG")
    return stream.getvalue()


class PhotosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for p in [patch.object(photos, "ROOT", self.root), patch.dict(os.environ, {}, clear=True)]:
            p.start()
            self.addCleanup(p.stop)

    def configured(self):
        return patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-value", "OPENAI_IMAGE_MODEL": "gpt-image-2.5-flare"})

    def reference(self, filename="reference.png"):
        path = self.root / filename
        path.write_bytes(png_bytes())
        return path

    def test_three_registered_tools_and_signature(self):
        tools = asyncio.run(photos.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, {"photo_generate", "photo_to_frame", "photo_frame_list"})
        generate = next(t for t in tools if t.name == "photo_generate")
        self.assertEqual(generate.inputSchema["required"], ["prompt"])
        self.assertEqual(generate.inputSchema["properties"]["identity"]["default"], "Image")

    def test_defaults_and_relative_paths_are_package_local(self):
        self.assertEqual(photos._destination("chat"), self.root / "output" / "images")
        with patch.dict(os.environ, {"PHOTOS_CHAT_DIR": "custom/images"}):
            self.assertEqual(photos._destination("chat"), self.root / "custom" / "images")

    def test_explicit_absolute_destination(self):
        directory = self.root / "application" / "data" / "images"
        with patch.dict(os.environ, {"PHOTOS_CHAT_DIR": str(directory)}):
            self.assertEqual(photos._destination("chat"), directory)

    def test_frame_tools_require_explicit_configuration(self):
        with self.configured(), patch.object(photos, "_request_image") as request:
            self.assertIn("PHOTOS_FRAME_DIR", photos.photo_generate("test", to="frame"))
            self.assertIn("PHOTOS_FRAME_DIR", photos.photo_to_frame("not-read.png"))
            self.assertIn("PHOTOS_FRAME_DIR", photos.photo_frame_list())
        request.assert_not_called()

    def test_missing_credentials_or_model_refuse_without_request(self):
        for values in [{}, {"OPENAI_API_KEY": "offline-test-value"}, {"OPENAI_API_KEY": "REPLACE_WITH_YOUR_OWN_API_KEY", "OPENAI_IMAGE_MODEL": "gpt-image-2.5-flare"}]:
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True), patch.object(photos, "_request_image") as request:
                self.assertIn("set your own", photos.photo_generate("test"))
                request.assert_not_called()

    def test_empty_prompt_and_bad_arguments_never_generate(self):
        with self.configured(), patch.object(photos, "_request_image") as request:
            for kwargs in [{"prompt": ""}, {"prompt": "test", "to": "unknown"}, {"prompt": "test", "size": "giant"}, {"prompt": "test", "quality": "unknown"}, {"prompt": "test", "identity": "../escape"}]:
                self.assertTrue(photos.photo_generate(**kwargs).startswith("Refused:"))
        request.assert_not_called()

    def test_world_feed_receipt_filename_and_png_contract(self):
        job = "a1b2c3d4e5f6070890abcdef123456789"
        directory = self.root / "app" / "data" / "images"
        with self.configured(), patch.dict(os.environ, {"PHOTOS_CHAT_DIR": str(directory)}), patch.object(photos, "_request_image", return_value=png_bytes()) as request:
            result = photos.photo_generate("test scene", identity="Worldfeed", subject=job, size="square", quality="medium")
        match = re.search(r"^Saved \([^\n]+\): ([^\r\n]+)", result)
        self.assertIsNotNone(match)
        path = Path(match.group(1))
        self.assertEqual(path.parent, directory)
        self.assertRegex(path.name, re.compile(r"Worldfeed_" + job + r"_\d{4}-\d{2}-\d{2}\.png", re.I))
        with Image.open(path) as image:
            image.verify()
        self.assertEqual(request.call_args.args[1]["size"], "1024x1024")
        self.assertEqual(request.call_args.args[1]["quality"], "medium")

    def test_existing_file_is_not_overwritten_or_regenerated(self):
        with self.configured(), patch.object(photos, "_request_image", return_value=png_bytes()) as request:
            self.assertTrue(photos.photo_generate("test", subject="same").startswith("Saved"))
            self.assertIn("already exists", photos.photo_generate("test", subject="same"))
        self.assertEqual(request.call_count, 1)

    def test_reference_uploads_have_generic_filenames(self):
        ref = self.reference("custom-local-filename.png")
        with self.configured(), patch.object(photos, "_request_image", return_value=png_bytes()) as request:
            result = photos.photo_generate("new scene", reference_paths=[str(ref)])
        self.assertTrue(result.startswith("Saved"))
        payload, references = request.call_args.args[1:]
        self.assertEqual(payload["input_fidelity"], "high")
        self.assertEqual(references[0][0], "image[]")
        self.assertEqual(references[0][1][0], "reference-1.png")

    def test_image_two_omits_unsupported_fidelity_switch(self):
        with self.configured(), patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "gpt-image-2"}), patch.object(photos, "_request_image", return_value=png_bytes()) as request:
            photos.photo_generate("test", reference_paths=[str(self.reference())])
        self.assertNotIn("input_fidelity", request.call_args.args[1])

    def test_reference_validation_precedes_request(self):
        fake = self.root / "fake.png"
        fake.write_text("not an image")
        with self.configured(), patch.object(photos, "_request_image") as request:
            self.assertTrue(photos.photo_generate("test", reference_paths=[str(fake)]).startswith("Refused:"))
            self.assertTrue(photos.photo_generate("test", reference_paths=[str(fake)] * 5).startswith("Refused:"))
        request.assert_not_called()

    def test_generation_request_and_validated_response(self):
        response = MagicMock()
        response.json.return_value = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
        with patch.object(photos.httpx, "Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            self.assertEqual(photos._request_image("offline-key", {"model": "example"}, []), png_bytes())
            call = client.return_value.__enter__.return_value.post.call_args
        self.assertEqual(call.args[0], "https://api.openai.com/v1/images/generations")
        self.assertEqual(call.kwargs["json"], {"model": "example"})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer offline-key")
        response.raise_for_status.assert_called_once()

    def test_edit_request_uses_multipart(self):
        response = MagicMock()
        response.json.return_value = {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
        refs = photos._reference_images([str(self.reference())])
        with patch.object(photos.httpx, "Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            photos._request_image("offline-key", {"n": 1, "model": "example"}, refs)
            call = client.return_value.__enter__.return_value.post.call_args
        self.assertEqual(call.args[0], "https://api.openai.com/v1/images/edits")
        self.assertEqual(call.kwargs["data"]["n"], "1")
        self.assertEqual(call.kwargs["files"], refs)
        self.assertNotIn("json", call.kwargs)

    def test_invalid_provider_image_never_gets_saved(self):
        response = MagicMock()
        for result in [{}, {"data": []}, {"data": [{"b64_json": "!invalid"}]}, {"data": [{"b64_json": base64.b64encode(b"not png").decode()}]}]:
            response.json.return_value = result
            with self.subTest(result=result), self.configured(), patch.object(photos.httpx, "Client") as client:
                client.return_value.__enter__.return_value.post.return_value = response
                self.assertIn("no valid PNG", photos.photo_generate("test"))
        self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_provider_http_error_redacts_body_and_does_not_retry(self):
        response = httpx.Response(401, request=httpx.Request("POST", photos.API_ROOT), text="secret diagnostic")
        error = httpx.HTTPStatusError("secret diagnostic", request=response.request, response=response)
        with self.configured(), patch.object(photos, "_request_image", side_effect=error) as request:
            result = photos.photo_generate("test")
        self.assertIn("HTTP 401", result)
        self.assertNotIn("secret diagnostic", result)
        self.assertEqual(request.call_count, 1)

    def test_timeout_is_not_retried_or_reported_saved(self):
        with self.configured(), patch.object(photos, "_request_image", side_effect=httpx.ReadTimeout("test")) as request:
            result = photos.photo_generate("test")
        self.assertIn("may have completed", result)
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("Saved", result)

    def test_frame_copy_and_list_use_only_configured_fixture_directory(self):
        frame = self.root / "frame"
        with patch.dict(os.environ, {"PHOTOS_FRAME_DIR": str(frame)}):
            result = photos.photo_to_frame(str(self.reference()), subject="Example")
            self.assertIn("Copied to configured frame folder", result)
            self.assertIn("1 images", photos.photo_frame_list())
            self.assertIn("already exists", photos.photo_to_frame(str(self.reference()), subject="Example"))

    def test_no_local_configuration_files_are_read_for_key(self):
        (self.root / ".env").write_text("OPENAI_API_KEY=local-file-value\nOPENAI_IMAGE_MODEL=example\n")
        with patch.object(photos, "_request_image") as request:
            self.assertIn("set your own", photos.photo_generate("test"))
        request.assert_not_called()

    def test_examples_are_valid_generic_configuration(self):
        config = json.loads((PACKAGE / "mcp-servers.example.json").read_text())
        self.assertEqual(set(config["mcpServers"]), {"photos"})
        self.assertEqual(config["mcpServers"]["photos"]["env"]["OPENAI_API_KEY"], "REPLACE_WITH_YOUR_OWN_API_KEY")


if __name__ == "__main__":
    unittest.main()
