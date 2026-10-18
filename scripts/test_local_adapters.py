"""Offline contracts for newly packaged adapters; no app or device requests."""
import importlib.util, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def load(package):
    spec=importlib.util.spec_from_file_location(package.replace("-","_"),ROOT/package/"server.py")
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module

class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{"ANAM_ROOT":self.temp.name,"ANAM_API_KEY":"offline-key","ANAM_COMPANIONS":"Moss,Fern"},clear=True)
        self.env.start();self.addCleanup(self.env.stop)

    def test_watch_requires_identity_and_keeps_missing_gate_closed(self):
        w=load("watch-mcp")
        with patch.object(w,"_api",side_effect=AssertionError("must not send")):
            self.assertIn("Refused",w.watch_tap("Hello"))
            self.assertIn("GATE SAYS",w.watch_tap("Hello",identity="Moss"))
        self.assertEqual(w._anam_key(),"offline-key")

    def test_crosstalk_uses_configured_roster_and_explicit_sender(self):
        c=load("crosstalk-mcp")
        self.assertEqual(c.BONDED,["moss","fern"])
        self.assertEqual(c._anam_key(),("offline-key",""))
        self.assertIn("identity is required",c.reach_brother("Fern","Hello"))
        self.assertIn("cannot reach yourself",c.reach_brother("Moss","Hello",identity="Moss"))

    def test_touch_accepts_direct_key_and_requires_sms_contact(self):
        t=load("anam-touch-mcp")
        self.assertEqual(t._key(),"offline-key")
        with patch.object(t,"_post",side_effect=AssertionError("must not send")):
            self.assertIn("specify a contact",t.pack_text("Moss","Hello"))

    def test_home_assistant_initializes_without_a_token_or_request(self):
        h=load("home-assistant-mcp")
        self.assertEqual(h.HA_TOKEN,"")
        self.assertEqual(h._safe_path("states","light.room/a"),"states/light.room%2Fa")

if __name__=="__main__":unittest.main()
