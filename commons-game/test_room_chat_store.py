"""Real SQLite room history retains speech and publishes outcomes atomically."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'server'))
from adventure.room_chat import CHAT_KEEP, append_activity, read, write
from adventure.store import Store

class RoomChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = Path(self.temp.name) / 'world'
        self.chat = self.world / 'chat'
        self.chat.mkdir(parents=True)
        self.private = Path(self.temp.name) / 'private'
        self.store = Store(self.private, self.world)

    def tearDown(self):
        self.temp.cleanup()

    def legacy(self, room, data):
        path = self.chat / (room + '.json')
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def activity(self, event='cast-one', store=None, room='hearth'):
        return append_activity(store or self.store, room, 'Tester', 'Cast a line; the fish slipped away.',
                               'fish', 'failed', event)

    def test_lazy_migration_preserves_words_ids_and_metadata_without_editing_file(self):
        old = {'lines': [{'id': 8, 'by': 'Admin', 'text': 'A fish! 🐟\nEvery word stays.',
                          'at': '2026-10-04 08:13', 'mentions': ['Morgan'], 'custom': {'keep': True}}],
               'closed': True, 'next_id': 9, 'other': {'authored': 'exactly this'}}
        path = self.legacy('hearth', old)
        original = path.read_bytes()
        self.assertEqual(read(self.store, 'hearth'), old)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.read('chat:hearth'), old)
        self.assertIsNone(self.store.read('house:hearth.json'))
        path.write_text('{bad later migration snapshot', encoding='utf-8')
        self.assertEqual(read(Store(self.private, self.world), 'hearth'), old)

    def test_room_namespace_never_collides_with_room_or_house_document(self):
        self.store.write('house:hearth.json', {'keep': 'house'})
        (self.world / 'hearth.json').write_text('{"room":{"name":"Hearth"}}', encoding='utf-8')
        self.activity()
        self.assertEqual(self.store.read('house:hearth.json'), {'keep': 'house'})
        self.assertEqual(json.loads((self.world / 'hearth.json').read_text()), {'room': {'name': 'Hearth'}})
        self.assertIsNone(self.store.chat_room(self.world / 'hearth.json'))
        self.assertEqual(self.store.chat_room(self.chat / 'hearth.json'), 'hearth')

    def test_event_replay_is_durable_and_stays_deduplicated_after_unsay(self):
        first = self.activity()
        reopened = Store(self.private, self.world)
        self.assertEqual(self.activity(store=reopened), first)
        self.assertEqual(len(read(reopened, 'hearth')['lines']), 1)
        book = read(reopened, 'hearth')
        book['lines'] = []
        write(reopened, 'hearth', book)
        self.assertEqual(self.activity(store=reopened), first)
        self.assertEqual(read(reopened, 'hearth')['lines'], [])
        self.assertEqual(self.activity('cast-two', store=reopened)['id'], 2)

    def test_rollback_reverts_action_line_dedup_and_lazy_migration_together(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.write('player:tester', {'credits': 10})
                self.activity()
                raise RuntimeError('Simulated game flush failure')
        self.assertIsNone(self.store.read('player:tester'))
        self.assertIsNone(self.store.read('chat:hearth'))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM room_activity_events').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM migrations WHERE id='chat:hearth'").fetchone()[0], 0)
        self.assertEqual(self.activity()['id'], 1)

    def test_concurrent_speech_and_activity_use_one_clock_across_store_instances(self):
        stores = [Store(self.private, self.world) for _ in range(4)]
        def publish(index):
            store = stores[index % len(stores)]
            if index % 2:
                return self.activity('cast-' + str(index), store=store)['id']
            
            with store.transaction():
                book = store.chat_read(self.chat / 'hearth.json', {})
                line_id = int(book.get('next_id', 1))
                book.setdefault('lines', []).append({'id': line_id, 'by': 'Speaker',
                                                     'text': 'Hello ' + str(index), 'at': 'now'})
                book['next_id'] = line_id + 1
                store.chat_write(self.chat / 'hearth.json', book)
                return line_id
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(publish, range(60)))
        book = read(self.store, 'hearth')
        self.assertEqual(sorted(ids), list(range(1, 61)))
        self.assertEqual([line['id'] for line in book['lines']], list(range(1, 61)))
        self.assertEqual(book['next_id'], 61)
        self.assertEqual(sum(line.get('kind') == 'activity' for line in book['lines']), 30)

    def test_simultaneous_event_retries_publish_only_once(self):
        stores = [Store(self.private, self.world) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.activity(store=stores[i % 4]), range(24)))
        self.assertTrue(all(line == results[0] for line in results))
        self.assertEqual(len(read(self.store, 'hearth')['lines']), 1)

    def test_retention_is_bounded_without_reusing_ids_or_resurrecting_trimmed_events(self):
        old = {'lines': [{'id': i, 'by': 'Old', 'text': 'Kept words ' + str(i)} for i in range(1, CHAT_KEEP + 11)],
               'next_id': CHAT_KEEP + 11}
        self.legacy('hearth', old)
        self.assertEqual(len(read(self.store, 'hearth')['lines']), CHAT_KEEP + 10)
        first = self.activity()
        self.assertEqual(first['id'], CHAT_KEEP + 11)
        self.assertEqual(len(read(self.store, 'hearth')['lines']), CHAT_KEEP)
        with self.store.transaction():
            for i in range(CHAT_KEEP):
                self.activity('more-' + str(i))
        self.assertNotIn(first, read(self.store, 'hearth')['lines'])
        self.assertEqual(self.activity(), first)
        book = read(self.store, 'hearth')
        self.assertEqual(book['next_id'], CHAT_KEEP * 2 + 12)
        self.assertEqual(len(book['lines']), CHAT_KEEP)

    def test_stale_clock_and_unsay_cannot_reuse_existing_id(self):
        self.legacy('hearth', {'lines': [{'id': 70, 'by': 'Other', 'text': 'My line'}], 'next_id': 1})
        book = self.store.chat_read(self.chat / 'hearth.json')
        self.assertEqual(book['next_id'], 71)
        self.assertEqual(self.activity()['id'], 71)
        write(self.store, 'hearth', {'lines': [], 'next_id': 1})
        self.assertEqual(self.activity('again')['id'], 72)

    def test_activity_line_has_only_public_summary_fields_and_rejects_objects(self):
        line = self.activity()
        self.assertEqual(set(line), {'id', 'by', 'text', 'at', 'ts', 'kind', 'action', 'outcome'})
        self.assertEqual((line['kind'], line['action'], line['outcome']), ('activity', 'fish', 'failed'))
        self.assertNotIn('cast-one', json.dumps(read(self.store, 'hearth')))
        with self.assertRaises(ValueError):
            append_activity(self.store, 'hearth', 'Tester', {'private': 'inventory'}, 'fish', 'success', 'bad')
        with self.assertRaises(ValueError):
            append_activity(self.store, 'hearth', 'Tester', 'Summary', 'fish', {'credits': 123}, 'bad')

    def test_invalid_rooms_and_legacy_paths_do_not_import_anything(self):
        other = Path(self.temp.name) / 'other' / 'chat' / 'hearth.json'
        other.parent.mkdir(parents=True)
        other.write_text('{"lines":[{"text":"private"}]}', encoding='utf-8')
        for room in ('../hearth', 'HEARTH', '', 'x' * 41, None):
            with self.subTest(room=room), self.assertRaises(ValueError):
                read(self.store, room)
        for path in (other, self.world / 'hearth.json', self.chat / 'nested' / 'hearth.json', self.chat / 'other.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                read(self.store, 'hearth', legacy_path=path)
        self.assertIsNone(self.store.read('chat:hearth'))
        self.assertIsNone(self.store.chat_room(other))

    def test_corrupt_legacy_data_is_not_silently_replaced(self):
        path = self.chat / 'hearth.json'
        for malformed in ('{bad', '[]', '{"lines":{}}', '{"lines":["word"]}'):
            path.write_text(malformed, encoding='utf-8')
            with self.subTest(malformed=malformed), self.assertRaises((ValueError, json.JSONDecodeError)):
                self.activity()
            self.assertEqual(path.read_text(), malformed)
            self.assertIsNone(self.store.read('chat:hearth'))

if __name__ == '__main__':
    unittest.main()
