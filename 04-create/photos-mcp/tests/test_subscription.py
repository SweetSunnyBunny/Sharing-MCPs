"""Subscription selection and image validation without an account request."""
import importlib.util, io, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("photos_subscription_tests",ROOT/"server.py")
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class SubscriptionTests(unittest.TestCase):
    def test_subscription_needs_no_api_key_and_saves_valid_png(self):
        image=io.BytesIO();Image.new("RGB",(2,2)).save(image,"PNG")
        provider=SimpleNamespace(generate=lambda *args:(image.getvalue(),{}))
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"PHOTOS_CHAT_DIR":folder},clear=True),patch.object(p,"_subscription_road",return_value=provider),patch.object(p,"_request_image",side_effect=AssertionError("paid fallback")):
            result=p.photo_generate("A blue square",subject="one",road="subscription")
            self.assertTrue(result.startswith("Saved ("),result)
            self.assertEqual(len(list(Path(folder).glob("*.png"))),1)

    def test_subscription_failure_never_falls_back_to_api(self):
        def failed(*args):raise TimeoutError("ambiguous completion")
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"PHOTOS_CHAT_DIR":folder},clear=True),patch.object(p,"_subscription_road",return_value=SimpleNamespace(generate=failed)),patch.object(p,"_request_image",side_effect=AssertionError("paid fallback")):
            self.assertIn("no API fallback",p.photo_generate("A scene",road="subscription"))
            self.assertEqual(list(Path(folder).glob("*.png")),[])

    def test_bad_subscription_bytes_are_not_saved(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"PHOTOS_CHAT_DIR":folder},clear=True),patch.object(p,"_subscription_road",return_value=SimpleNamespace(generate=lambda *a:(b"not an image",{}))):
            self.assertIn("no valid PNG",p.photo_generate("A scene",road="subscription"))
            self.assertEqual(list(Path(folder).glob("*.png")),[])

if __name__=="__main__":unittest.main()
