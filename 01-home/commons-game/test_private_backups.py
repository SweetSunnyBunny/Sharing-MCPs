"""Private snapshots verify committed WAL saves and isolated restore drills."""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'server'))
from adventure import backups
from adventure.store import Store
from tools import backup_commons

class PrivateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / 'commons'
        self.world = self.root / 'world'
        self.world.mkdir(parents=True)
        self.private = base / 'commons_private'
        self.store = Store(self.private, self.world)
        (self.world / 'hearth.json').write_text(json.dumps({'name': 'Test Hearth', 'rows': ['...']}), encoding='utf-8')
        (self.private / 'keys.json').write_text(json.dumps({'tester': 'SECRET-TEST-KEY'}), encoding='utf-8')
        master = base / 'commons_master'
        master.mkdir()
        (master / 'master_key.txt').write_text('SECRET-TEST-MASTER', encoding='utf-8')
        self.store.write('chat:hearth', {'lines': [{'id': 1, 'by': 'Tester', 'text': 'A saved conversation.'}]})
        self.store.write('house:baskets.json', {'tester': {'picked': {'pie': 2}, 'made': [{'id': 'one-sword', 'what': 'Longsword'}]}})
        self.store.write('player:tester', {'shared': {'hp': 91}, 'name': 'Tester'})

    def tearDown(self):
        self.temp.cleanup()

    def rewrite(self, archive, modify):
        with zipfile.ZipFile(archive) as original:
            files = {name: original.read(name) for name in original.namelist()}
        modify(files)
        changed = self.private / ('tampered-' + archive.name)
        with zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as saved:
            for name, data in files.items():
                saved.writestr(name, data)
        return changed

    def test_snapshot_verifies_chat_inventory_keys_and_isolated_restore_without_live_changes(self):
        archive = backups.create_snapshot(self.store, self.root)
        verified = backups.verify_snapshot(archive)
        self.assertTrue(archive.resolve().is_relative_to(self.private.resolve()))
        self.assertEqual(verified['database']['integrity'], 'ok')
        self.assertEqual(verified['database']['chat_lines'], 1)
        self.assertEqual((verified['database']['inventory_units'], verified['database']['made_items']), (2, 1))
        restored = backups.restore_drill(archive)
        self.assertEqual(restored['restore_drill'], 'passed')
        self.assertTrue(restored['isolated'])
        self.assertEqual(self.store.read('player:tester')['shared']['hp'], 91)
        with zipfile.ZipFile(archive) as saved:
            self.assertEqual(saved.read('commons_master/master_key.txt'), b'SECRET-TEST-MASTER')
            self.assertIn('commons_private/keys.json', saved.namelist())
            self.assertFalse(any('-wal' in name or '-shm' in name for name in saved.namelist()))
        self.assertNotIn('SECRET-TEST', json.dumps(verified))
        self.assertNotIn('SECRET-TEST', json.dumps(restored))

    def test_committed_wal_contents_are_in_snapshot_and_concurrent_records_stay_consistent(self):

        with closing(self.store.connect()) as live:
            live.execute("INSERT INTO documents(id,body) VALUES ('counter-a','0'),('counter-b','0')")
            live.commit()
            def update():
                for count in range(1, 25):
                    with self.store.transaction():
                        self.store.write('counter-a', count)
                        self.store.write('counter-b', count)
            writer = threading.Thread(target=update)
            writer.start()
            archive = backups.create_snapshot(self.store, self.root)
            writer.join()
        with zipfile.ZipFile(archive) as saved, tempfile.TemporaryDirectory() as temp:
            dbpath = Path(temp) / 'copy.sqlite3'
            dbpath.write_bytes(saved.read('commons_private/commons-game.sqlite3'))
            with closing(sqlite3.connect(dbpath)) as copy:
                values = dict(copy.execute("SELECT id,body FROM documents WHERE id IN ('counter-a','counter-b')"))
                self.assertEqual(values['counter-a'], values['counter-b'])
                self.assertGreaterEqual(int(values['counter-a']), 0)

    def test_checksum_detects_modified_world_file(self):
        archive = backups.create_snapshot(self.store, self.root)
        tampered = self.rewrite(archive, lambda files: files.update({'world/hearth.json': b'{"name":"changed"}'}))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            backups.verify_snapshot(tampered)

    def test_sqlite_integrity_is_checked_even_if_a_tamperer_updates_manifest_hash(self):
        archive = backups.create_snapshot(self.store, self.root)
        def damage(files):
            name = 'commons_private/commons-game.sqlite3'
            files[name] = b'this is not a sqlite database'
            manifest = json.loads(files['manifest.json'])
            manifest['files'][name].update(bytes=len(files[name]), sha256=hashlib.sha256(files[name]).hexdigest())
            files['manifest.json'] = json.dumps(manifest).encode()
        tampered = self.rewrite(archive, damage)
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backups.verify_snapshot(tampered)

    def test_unsafe_paths_cannot_escape_restore_drill(self):
        archive = backups.create_snapshot(self.store, self.root)
        tampered = self.rewrite(archive, lambda files: files.update({'../escape.txt': b'no'}))
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            backups.restore_drill(tampered)
        self.assertFalse((self.root.parent / 'escape.txt').exists())

    def test_backup_never_recursively_includes_archives_or_publishes_under_checkout(self):
        archive = backups.create_snapshot(self.store, self.root)
        second = backups.create_snapshot(self.store, self.root)
        with zipfile.ZipFile(second) as saved:
            self.assertFalse(any('migration_backups' in name or 'routine_backups' in name for name in saved.namelist()))
        unsafe = Store(self.root / 'private', self.world)
        with self.assertRaisesRegex(ValueError, 'outside'):
            backups.create_snapshot(unsafe, self.root)
        self.assertTrue(archive.exists())

    def test_invalid_source_save_does_not_leave_a_published_or_partial_archive(self):
        with self.store.transaction() as db:
            db.execute("UPDATE documents SET body='invalid-json' WHERE id='player:tester'")
        with self.assertRaises(ValueError):
            backups.create_snapshot(self.store, self.root)
        self.assertEqual(list((self.private / 'migration_backups').iterdir()), [])

    def test_daily_archive_has_verified_marker_and_retention_leaves_manual_recovery(self):
        manual = backups.create_snapshot(self.store, self.root)
        archives = [backups.create_snapshot(self.store, self.root, routine=True) for _ in range(3)]
        directory = self.private / 'routine_backups'
        unrelated = directory / 'keep-my-note.txt'
        unrelated.write_text('keep', encoding='utf-8')
        removed = backups.prune_routine(self.private, retain=1)
        self.assertEqual(len(removed), 2)
        self.assertTrue(archives[-1].exists())
        self.assertTrue(manual.exists())
        self.assertTrue(unrelated.exists())
        marker = json.loads((directory / 'status.json').read_text())
        self.assertTrue(marker['verified'])
        self.assertEqual(marker['restore_drill'], 'passed')

    def test_timer_refuses_disposable_paths_and_starts_live_only_without_a_startup_snapshot(self):
        with patch.object(backups.threading, 'Thread') as thread:
            self.assertFalse(backups.start_if_live(self.store, self.root))
            self.assertFalse(backups.start_if_live(self.store, ROOT))
            thread.assert_not_called()
        live_root = backups.CANONICAL_ROOT.resolve()
        fake = SimpleNamespace(world=live_root / 'world', path=live_root.parent / 'commons_private' / 'commons-game.sqlite3')
        with patch.object(backups, '_threads', {}), patch.object(backups.threading, 'Thread') as thread, patch.object(backups, 'create_snapshot') as create:
            thread.return_value.is_alive.return_value = True
            self.assertTrue(backups.start_if_live(fake, live_root))
            self.assertTrue(backups.start_if_live(fake, live_root))
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs['daemon'])
            create.assert_not_called()
        mismatched = SimpleNamespace(world=self.world, path=fake.path)
        self.assertFalse(backups.start_if_live(mismatched, live_root))

    def test_callable_backup_path_is_preserved_and_prints_only_private_archive_path(self):
        output = io.StringIO()
        with patch.object(backup_commons, 'ROOT', self.root), patch.object(backup_commons, 'PRIVATE', self.private), redirect_stdout(output):
            archive = backup_commons.backup()
        self.assertEqual(Path(output.getvalue().strip()), archive)
        self.assertNotIn('SECRET-TEST', output.getvalue())
        self.assertTrue(backups.verify_snapshot(archive)['verified'])

if __name__ == '__main__':
    unittest.main()
