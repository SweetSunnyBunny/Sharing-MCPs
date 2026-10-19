"""Portable installation, fresh-state privacy and real starter-game contracts."""
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'server'))
from adventure.game import Game
from adventure.players import Players
from adventure.store import Store


class StarterGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.world = self.base / 'world'
        self.world.mkdir()
        self.store = Store(self.base / 'private', self.world)

    def tearDown(self):
        self.temp.cleanup()

    def test_every_room_has_a_serializable_playable_catalog(self):
        count = 0
        for path in (ROOT / 'world').glob('*.json'):
            room = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(room, dict) or not room.get('rows'):
                continue
            with self.subTest(room=path.stem):
                state = Game(self.store, ROOT).snapshot('visitor', path.stem)
                json.dumps(state)
                self.assertIn('inventory', state)
            count += 1
        self.assertEqual(count, 41)

    def test_new_players_have_no_preowned_pets_or_saved_progress(self):
        for who in ('admin', 'visitor'):
            player = Players(self.store).get_player(who)
            self.assertEqual(player['companions'], [])
            self.assertEqual(player.get('family_pets', []), [])
            self.assertEqual(player['shared']['gold_cents'], 5000)
        self.assertEqual(self.store.house_read(self.world / 'companions.json', {}).get('companions', []), [])

    def test_action_retries_and_failed_transactions_preserve_state(self):
        action = lambda: Game(self.store, ROOT).execute('visitor', 'hearth', 'rest', {}, 'same-request')
        first = action()
        self.assertEqual(first, action())
        with self.assertRaises(ValueError):
            Game(self.store, ROOT).execute('visitor', 'hearth', 'fish', {}, 'same-request')
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.write('sentinel', 'uncommitted')
                raise RuntimeError('rollback')
        self.assertIsNone(self.store.read('sentinel'))

    def test_fishing_resolves_a_real_catch_and_release(self):
        with patch('adventure.game.random.randint', return_value=20):
            caught = Game(self.store, ROOT).execute('visitor', 'garden', 'fish', {}, uuid.uuid4().hex)
        self.assertTrue(caught['result']['success'])
        released = Game(self.store, ROOT).execute('visitor', 'garden', 'release', {}, uuid.uuid4().hex)
        self.assertIn('catch', released['result'])
        self.assertIsNone(Game(self.store, ROOT).snapshot('visitor', 'garden')['held_fish'])

    def test_initialization_is_idempotent_and_respects_custom_admin(self):
        state = self.base / 'installation'
        env = dict(os.environ, COMMONS_STATE_ROOT=str(state), COMMONS_ADMIN_NAME='Host',
                   COMMONS_COMPANIONS='Maple,Cedar', PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, '-B', str(ROOT / 'setup_commons.py'), '--init']
        subprocess.run(command, env=env, capture_output=True, check=True)
        key = (state / 'master/host.key').read_bytes()
        subprocess.run(command, env=env, capture_output=True, check=True)
        self.assertEqual(key, (state / 'master/host.key').read_bytes())
        self.assertTrue((state / 'private/maple.key').is_file())
        self.assertTrue((state / 'private/cedar.key').is_file())
        self.assertFalse((ROOT / 'host.key').exists())


class StarterHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('starter_http_host', ROOT / 'server/commons_server.py')
        self.host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.host)
        self.host._reroot(self.base / 'private', self.base / 'master', self.base / 'world')
        (self.base / 'world/game-config.json').write_text('{}', encoding='utf-8')
        self.key = 'isolated-test-administrator-key'
        (self.base / 'master/admin.key').write_text(self.key, encoding='utf-8')
        self.server = self.host.HearthHTTPServer(('127.0.0.1', 0), self.host.HearthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_public_configuration_has_no_credentials(self):
        with urlopen(self.url + '/public-config.js') as response:
            text = response.read().decode()
        config = json.loads(text.removeprefix('window.COMMONS_CONFIG=').removesuffix(';'))
        self.assertEqual(config['adminName'], 'admin')
        self.assertNotIn(self.key, text)
        self.assertNotIn('API_KEY', text)

    def test_authenticated_gameplay_retries_are_atomic(self):
        with self.assertRaises(HTTPError) as failure:
            urlopen(self.url + '/api/game/state?as=admin&key=wrong')
        self.assertEqual(failure.exception.code, 403)
        body = {'by': 'admin', 'key': self.key, 'action': 'rest', 'args': {}, 'request_id': 'starter-http-retry'}
        request = Request(self.url + '/api/game/action', data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            first = json.load(response)
        with urlopen(request) as response:
            self.assertEqual(first, json.load(response))

    def test_private_files_are_not_static_downloads(self):
        for path in ('/.env', '/server/settings.py', '/world/notes.json', '/%2eenv', '/world/baskets.json'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as failure:
                urlopen(self.url + path)
            self.assertIn(failure.exception.code, (403, 404))

    def test_unconfigured_notifications_fail_closed(self):
        with patch.dict(os.environ, {'COMMONS_ANAM_ENABLED': 'false', 'COMMONS_WRIST_GATE_DIR': ''}):
            self.assertEqual(self.host._anam_key(), '')
            self.assertNotEqual(self.host._wrist_gate('visitor', datetime(2026, 1, 1, 12))[0], 0)


if __name__ == '__main__':
    unittest.main()
