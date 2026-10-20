
"""Transactional game and legacy-house documents, with safe lazy migration."""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
import threading

MIGRATED = frozenset(('baskets.json', 'plantings.json', 'companions.json',
    'catches.json', 'hunts.json', 'placeables.json', 'made-archive.json',
    'rook.json', 'archery.json', 'howls.json'))

class Store:
    def __init__(self, private: Path, world: Path):
        self.path = Path(private) / 'commons-game.sqlite3'
        self.world = Path(world)
        self.lock = threading.RLock()
        self.local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    actor TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
                    response TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(actor,request_id));
                CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS room_activity_events (
                    room TEXT NOT NULL, event_id TEXT NOT NULL, line TEXT NOT NULL,
                    PRIMARY KEY(room,event_id));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        return db

    @contextmanager
    def transaction(self):
        """[commons:saves] Nested callers share one atomic save and receipt boundary."""
        if getattr(self.local, 'db', None) is not None:
            yield self.local.db
            return
        with self.lock:
            db = self.connect()
            self.local.db = db
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self.local.db = None
                db.close()

    def read(self, key, default=None):
        with self.transaction() as db:
            row = db.execute('SELECT body FROM documents WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def write(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT INTO documents(id,body) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                       (key, json.dumps(value, ensure_ascii=False)))

    def documents(self, prefix):
        with self.transaction() as db:
            return [json.loads(body) for key,body in db.execute(
                'SELECT id,body FROM documents WHERE substr(id,1,?)=?', (len(prefix),prefix))]

    def house_read(self, path, default):
        path = Path(path)
        key = 'house:' + path.name
        with self.transaction() as db:
            row = db.execute('SELECT body FROM documents WHERE id=?', (key,)).fetchone()
            if row:
                return json.loads(row[0])
            
            value = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default
            self.write(key, value)
            db.execute('INSERT OR IGNORE INTO migrations(id) VALUES (?)', (key,))
            return value

    def house_write(self, path, value):
        self.write('house:' + Path(path).name, value)

    def chat_room(self, path):
        """Identify a chat file by its full location, never by basename."""
        from .room_chat import room_for_path
        return room_for_path(self, path)

    def chat_read(self, path, default=None):
        from .room_chat import read
        room = self.chat_room(path)
        if room is None:
            raise ValueError('That path is not a room chat file.')
        return read(self, room, legacy_path=path)

    def chat_write(self, path, value):
        from .room_chat import write
        room = self.chat_room(path)
        if room is None:
            raise ValueError('That path is not a room chat file.')
        return write(self, room, value, legacy_path=path)

    def replay(self, actor, request_id, request):
        with self.transaction() as db:
            row = db.execute('SELECT request,response FROM receipts WHERE actor=? AND request_id=?', (actor, request_id)).fetchone()
            if row:
                if row[0] != request:
                    raise ValueError('This request id was already used for another action.')
                return json.loads(row[1])

    def receipt(self, actor, request_id, request, response):
        with self.transaction() as db:
            db.execute('INSERT INTO receipts(actor,request_id,request,response) VALUES (?,?,?,?)',
                       (actor, request_id, request, json.dumps(response, ensure_ascii=False)))
