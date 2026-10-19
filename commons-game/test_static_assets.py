"""Real HTTP checks for CDN ownership and private-file URL aliases."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent

class StaticAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("static_test_host", ROOT / "server/commons_server.py")
        self.host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.host)
        self.host.RESERVED_NAMES = frozenset({"admin", "rowan", "avery"})
        self.host.COMMONS_ROOT = self.base / "commons"
        self.host.CDN_ROOT = self.base / "CDN/commons"
        self.world = self.host.COMMONS_ROOT / "world"
        self.host._reroot(self.base / "private", self.base / "master", world=self.world)
        self.host.UPLOAD_DIR = self.host.CDN_ROOT / "uploads"
        self.write(self.host.COMMONS_ROOT / "index.html", b"Commons scene")
        self.write(self.host.COMMONS_ROOT / "play.html", b"Commons game")
        self.write(self.world / "hearth.json", b'{"id":"hearth","rows":["..."],"props":[]}')
        self.write(self.world / "notes.json", b'[{"text":"private note sentinel"}]')
        self.write(self.world / "baskets.json", b'{"secret":"private inventory sentinel"}')
        self.write(self.host.COMMONS_ROOT / "keys/commons_private/keys.json", b'"private key sentinel"')
        self.write(self.host.COMMONS_ROOT / "index.html.bak", b"private backup sentinel")
        self.write(self.host.COMMONS_ROOT / "art/sample.png", b"stale local image")
        self.write(self.host.CDN_ROOT / "art/sample.png", b"canonical CDN image")
        self.server = self.host.HearthHTTPServer(("127.0.0.1", 0), self.host.HearthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def request(self, path, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_private_files_blocked_for_get_head_and_encoded_aliases(self):
        for enabled in (False, True):
            if enabled:
                self.write(self.world / "game-config.json", b"{}")
            for method in ("GET", "HEAD"):
                for path in ("/world/notes.json", "/%77orld/notes.json", "/world//baskets.json",
                             "/world/./baskets.json", "/world/x/../baskets.json", "/world%5Cbaskets.json",
                             "/world/nested/baskets.json", "/world/nested/made-archive.json",
                             "/keys/commons_private/keys.json", "/%6beys/commons_private/keys.json",
                             "/index.html.bak", "/server/commons_server.py", "/world/", "/art/"):
                    with self.subTest(enabled=enabled, method=method, path=path):
                        status, content, _ = self.request(path, method)
                        self.assertIn(status, (403, 404))
                        self.assertNotIn(b"sentinel", content)

    def test_cdn_images_and_world_geometry_work_without_local_art(self):
        for method in ("GET", "HEAD"):
            status, content, headers = self.request("/art/sample.png", method)
            self.assertEqual(status, 200)
            self.assertEqual(int(headers["Content-Length"]), len(b"canonical CDN image"))
            self.assertEqual(content, b"canonical CDN image" if method == "GET" else b"")
        (self.host.COMMONS_ROOT / "art/sample.png").unlink()
        self.assertEqual(self.request("/art/sample.png")[1], b"canonical CDN image")
        self.assertEqual(self.request("/world/hearth.json")[0], 200)
        self.assertEqual(self.request("/")[1], b"Commons scene")

    def test_versioned_public_assets_have_bounded_browser_reuse(self):
        self.write(self.host.COMMONS_ROOT / "assets/game.js", b"window.game = true;")
        self.write(self.host.COMMONS_ROOT / "assets/game.css", b"canvas {display:block}")
        self.write(self.host.CDN_ROOT / "item-art/pie.png", b"pie image")
        for path in ("/assets/game.js?v=20261004", "/assets/game.css?theme=dark&v=manual-2",
                     "/art/sample.png?v=abc123", "/item-art/pie.png?%76=manual-1"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, content, headers = self.request(path, method)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Cache-Control"], "public, max-age=600")
                    self.assertNotIn("immutable", headers["Cache-Control"])
                    self.assertTrue(headers["Last-Modified"])
                    if method == "HEAD":
                        self.assertEqual(content, b"")
        for query in ("", "?v=", "?v=%20", "?t=123", "?version=manual-1"):
            for method in ("GET", "HEAD"):
                with self.subTest(query=query, method=method):
                    self.assertEqual(self.request("/art/sample.png" + query, method)[2]["Cache-Control"],
                                     "no-cache")

    def test_conditional_assets_retain_their_versioned_cache_policy(self):
        for path, policy in (("/art/sample.png?v=abc123", "public, max-age=600"),
                             ("/art/sample.png", "no-cache")):
            modified = self.request(path)[2]["Last-Modified"]
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, content, headers = self.request(path, method,
                        headers={"If-Modified-Since": modified})
                    self.assertEqual(status, 304)
                    self.assertEqual(content, b"")
                    self.assertEqual(headers["Cache-Control"], policy)

    def test_html_geometry_and_public_json_stay_fresh_even_with_versions(self):
        self.write(self.host.COMMONS_ROOT / "assets/catalog.json", b'{"items":[]}')
        for path in ("/?v=1", "/index.html?v=1", "/play.html?v=1",
                     "/world/hearth.json?v=1", "/assets/catalog.json?v=1"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, _, headers = self.request(path, method)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Cache-Control"], "no-store")
        updated = b'{"id":"hearth","rows":["...."],"props":[]}'
        self.write(self.world / "hearth.json", updated)
        self.assertEqual(self.request("/world/hearth.json?v=1")[1], updated)

    def test_denied_and_missing_static_files_are_not_fresh_cached(self):
        for path in ("/assets/missing.js?v=1", "/art/missing.png?v=1",
                     "/world/notes.json?v=1", "/world%5Cbaskets.json?v=1",
                     "/art/sample.png.bak?v=1", "/item-art/private.json?v=1"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, content, headers = self.request(path, method)
                    self.assertIn(status, (403, 404))
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertNotIn(b"sentinel", content)

    def test_all_api_responses_are_private_and_not_stored(self):
        self.write(self.world / "game-config.json", b"{}")
        for path in ("/api/health", "/api/health?v=1", "/api/missing.js?v=1",
                     "/api/agent/missing?v=1", "/api/game/state?as=avery&key=wrong&v=1"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    _, content, headers = self.request(path, method)
                    self.assertEqual(headers["Cache-Control"], "private, no-store")
                    self.assertNotIn(b"sentinel", content)
        _, _, headers = self.request("/api/game/action?v=1", "POST",
                                    {"as": "avery", "key": "wrong", "action": "eat"})
        self.assertEqual(headers["Cache-Control"], "private, no-store")

    def test_room_backdrops_get_content_versions_without_source_or_revision_edits(self):
        from unittest.mock import patch
        import asset_paths
        image = self.host.CDN_ROOT / "art/sample.png"
        room = self.world / "hearth.json"
        stored = {"id": "hearth", "rows": ["..."], "props": [],
                  "backdrop": "/art/sample.png?palette=warm&v=old#focus"}
        original = json.dumps(stored, indent=2).encode()
        self.write(room, original)
        revision = self.host._editor_revision(room)
        asset_paths._image_digest.cache_clear()
        with patch("asset_paths.open", wraps=open, create=True) as source_open:
            status, raw, headers = self.request("/world/hearth.json")
            rendered = json.loads(raw)
            expected = hashlib.sha256(image.read_bytes()).hexdigest()[:12]
            url = urlsplit(rendered["backdrop"])
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(parse_qs(url.query), {"palette": ["warm"], "v": [expected]})
            self.assertEqual(url.fragment, "focus")
            head_status, head_body, head_headers = self.request("/world/hearth.json?v=anything", "HEAD")
            self.assertEqual(head_status, 200)
            self.assertEqual(head_body, b"")
            self.assertEqual(int(head_headers["Content-Length"]), len(raw))
            self.assertEqual(source_open.call_count, 1)
            old_stat = image.stat()
            updated = b"updated!! CDN image"  
            self.assertEqual(len(updated), old_stat.st_size)
            image.write_bytes(updated)
            os.utime(image, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1000000))
            _, refreshed, _ = self.request("/world/hearth.json",
                headers={"If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
            fresh_version = parse_qs(urlsplit(json.loads(refreshed)["backdrop"]).query)["v"][0]
            self.assertEqual(fresh_version, hashlib.sha256(updated).hexdigest()[:12])
            self.assertNotEqual(fresh_version, expected)
            self.assertEqual(source_open.call_count, 2)
        self.assertEqual(room.read_bytes(), original)
        self.assertEqual(self.host._editor_revision(room), revision)
        self.assertEqual(stored["backdrop"], "/art/sample.png?palette=warm&v=old#focus")

    def test_backdrop_versioning_resolves_only_known_canonical_images(self):
        self.write(self.host.CDN_ROOT / "assets/adventure/backgrounds/room.png", b"room image")
        image_hash = hashlib.sha256(b"canonical CDN image").hexdigest()[:12]
        for url in ("/art/sample.png", "art/sample.png", "/art/sample.png?v=old&v=older",
                    "https://cdn.example.com/commons/art/sample.png?v=old"):
            with self.subTest(url=url):
                rendered = self.host._public_room_geometry({"backdrop": url})
                parsed = urlsplit(rendered["backdrop"])
                self.assertEqual(parse_qs(parsed.query)["v"], [image_hash])
        self.assertIn("?v=", self.host._public_room_geometry(
            {"backdrop": "/assets/adventure/backgrounds/room.png"})["backdrop"])
        for url in ("https://outside.example/art/sample.png", "//cdn.example.com/commons/art/sample.png",
                    "http://cdn.example.com/commons/art/sample.png",
                    "https://cdn.example.com/other/art/sample.png",
                    "https://stranger@cdn.example.com/commons/art/sample.png",
                    "/art/missing.png", "/art/../private.png", "/art/%2e%2e/private.png",
                    "/art%5Csample.png", "/world/hearth.json", "/art/sample.png.bak", None):
            with self.subTest(url=url):
                data = {"backdrop": url}
                self.assertIs(self.host._public_room_geometry(data), data)
        raw = b'{"id":"hearth", "rows":["..."], "props":[]}'
        self.write(self.world / "hearth.json", raw)
        self.assertEqual(self.request("/world/hearth.json")[1], raw)

    def test_item_art_uses_cdn_but_does_not_publish_reports(self):
        self.write(self.host.CDN_ROOT / "item-art/pie.png", b"new item image")
        self.write(self.host.CDN_ROOT / "item-art/private.json", b"private audit sentinel")
        self.write(self.host.CDN_ROOT / "item-art/owned.csv", b"private audit sentinel")
        for method in ("GET", "HEAD"):
            self.assertEqual(self.request("/item-art/pie.png?v=1", method)[0], 200)
            for path in ("/item-art/private.json", "/item-art/owned.csv", "/item-art/"):
                self.assertEqual(self.request(path, method)[0], 404)
        self.assertEqual(self.request("/item-art/pie.png")[1], b"new item image")

    def test_map_atlas_reads_canonical_cdn_documents_only(self):
        self.write(self.host.CDN_ROOT / "maps/README.md", b"Canonical public map guide")
        self.write(self.host.CDN_ROOT / "maps/atlas.json", b'{"rooms":["hearth"]}')
        self.write(self.host.COMMONS_ROOT / "maps/README.md", b"stale local guide")
        self.write(self.host.CDN_ROOT / "maps/private.md", b"private report sentinel")
        for method in ("GET", "HEAD"):
            for path, expected in (("/maps/README.md", b"Canonical public map guide"),
                                   ("/maps/atlas.json", b'{"rooms":["hearth"]}')):
                status, content, headers = self.request(path, method)
                self.assertEqual(status, 200)
                self.assertEqual(int(headers["Content-Length"]), len(expected))
                self.assertEqual(content, expected if method == "GET" else b"")
            for path in ("/maps/private.md", "/maps/README.md.bak", "/maps/"):
                self.assertEqual(self.request(path, method)[0], 404)

    def test_editor_art_survives_save_without_changing_npc_identity(self):
        from copy import deepcopy
        self.write(self.host.COMMONS_ROOT / 'assets/art-catalog.json',
                   b'{"npcs":{"neighbor":{},"bran":{}}}')
        live = {'rows':['...']*3, 'legend':{'.':{'solid':False}}, 'spawn':{'x':1,'y':1},
                'backdrop':'/maps/old.png', 'props':[{'id':'npc-neighbor','game_npc':'neighbor',
                'x':1,'y':1,'w':1,'h':1,'label':'Eamon','invisible':True}]}
        incoming = deepcopy(live)
        incoming['backdrop']='/assets/adventure/backgrounds/cargo_bay.png?v=123'
        incoming['props'][0].update(npc_art='bran',npc_scale=1.4,game_npc='bran')
        saved = self.host._editor_prepare_room(incoming,live)
        self.assertEqual(saved['backdrop'],incoming['backdrop'])
        self.assertEqual(saved['props'][0]['game_npc'],'neighbor')
        self.assertEqual(saved['props'][0]['npc_art'],'bran')
        self.assertEqual(saved['props'][0]['npc_scale'],1.4)
        for bad in ('https://example.com/art.png','//example.com/art.png',
                    '/assets/%2e%2e/private.png','javascript:alert(1)'):
            invalid=deepcopy(incoming);invalid['backdrop']=bad
            with self.assertRaises(ValueError):self.host._editor_prepare_room(invalid,live)
        for field,value in (('npc_scale',float('nan')),('npc_scale',4),('npc_art','missing')):
            invalid=deepcopy(incoming);invalid['props'][0][field]=value
            with self.assertRaises(ValueError):self.host._editor_prepare_room(invalid,live)
        self.assertTrue(self.host._art_ok('/item-art/strawberry_pie.png?v=123'))

    def test_editor_seat_positions_validate_and_survive(self):
        from copy import deepcopy
        live={'rows':['.....']*5,'legend':{'.':{'solid':False}},'spawn':{'x':1,'y':1},'props':[]}
        incoming=deepcopy(live)
        incoming['props']=[{'id':'chair','label':'chair','x':1,'y':2,'w':2,'h':2,
                            'action':{'name':'sit','spots':[{'x':2,'y':2.5,'dir':'left'}]}}]
        saved=self.host._editor_prepare_room(incoming,live)
        self.assertEqual(saved['props'][0]['action']['spots'],incoming['props'][0]['action']['spots'])
        for key,value in [('x',float('nan')),('y',8),('dir','sideways')]:
            bad=deepcopy(incoming);bad['props'][0]['action']['spots'][0][key]=value
            with self.assertRaises(ValueError):self.host._editor_prepare_room(bad,live)
        del incoming['props'][0]['action']
        self.assertNotIn('action',self.host._editor_prepare_room(incoming,saved)['props'][0])

    def test_existing_made_art_grows_without_rewriting_save(self):
        from unittest.mock import patch
        basket={'tester':{'name':'Tester','made':[{'id':'pie','what':'strawberry pie',
          'placed':{'room':'kitchen','x':30,'y':16,'w':.9,'h':.65}},
          {'id':'custom','what':'custom art','placed':{'room':'kitchen','x':20,'y':16,'w':4,'h':3}}]}}
        before=json.dumps(basket)
        with patch.object(self.host,'_basket_all',return_value=basket):
            rows=self.host._placed_made()
        self.assertEqual((rows[0]['w'],rows[0]['h']),(2.25,1.65))
        self.assertEqual((rows[1]['w'],rows[1]['h']),(4,3))
        self.assertEqual(json.dumps(basket),before)

    def test_upload_is_stored_in_cdn_and_returned_url_reads_same_bytes(self):
        key = "isolated-key-at-least-sixteen"
        self.write(self.host.KEYS_FILE, json.dumps({"avery": key}).encode())
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        status, body, _ = self.request("/api/upload", "POST", {
            "by": "avery", "key": key, "data": base64.b64encode(raw).decode()})
        self.assertEqual(status, 200, body)
        url = json.loads(body)["url"]
        self.assertEqual((self.host.CDN_ROOT / url.lstrip("/")).read_bytes(), raw)
        self.assertFalse((self.host.COMMONS_ROOT / url.lstrip("/")).exists())
        self.assertEqual(self.request(url)[1], raw)
        self.assertEqual(self.request(url, "HEAD")[0], 200)

    def test_nonfinite_positions_do_not_corrupt_presence(self):
        key = "isolated-key-at-least-sixteen"
        self.write(self.host.KEYS_FILE, json.dumps({"avery": key}).encode())
        for value in (float("nan"), float("inf"), float("-inf")):
            status, _, _ = self.request("/api/here", "POST", {
                "by": "avery", "key": key, "room": "hearth", "x": value, "y": 1})
            self.assertEqual(status, 400)
        self.assertFalse(self.host.HERE_FILE.exists())

if __name__ == "__main__":
    unittest.main()
