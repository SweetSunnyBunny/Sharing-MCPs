
"""Room speech and activity share one transactional, persistent history.

Legacy room books are imported on first use without rewriting their files.
Activity deduplication is kept separately from the public book, so a replay
cannot republish a trimmed or withdrawn event or disclose request details.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

CHAT_KEEP = 500
TEXT_MAX = 2000
_ROOM = re.compile(r'[a-z0-9_-]{1,40}\Z')
_LABEL = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')

def _room_id(room_id):
    if not isinstance(room_id, str) or not _ROOM.fullmatch(room_id):
        raise ValueError('That is not a valid room id.')
    return room_id

def room_for_path(store, path):
    """Accept only a direct file in this store's world/chat directory."""
    try:
        path = Path(path)
        if path.suffix != '.json' or not _ROOM.fullmatch(path.stem):
            return None
        expected = store.world.resolve() / 'chat'

        if path.parent.resolve() != expected or path.resolve().parent != expected:
            return None
        if path.resolve().name != path.name:
            return None
        return path.stem
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

def _legacy_path(store, room_id, legacy_path):
    path = Path(legacy_path) if legacy_path is not None else store.world / 'chat' / (room_id + '.json')
    if room_for_path(store, path) != room_id:
        raise ValueError('That path is not the requested room chat file.')
    return path

def _book(data):
    if not isinstance(data, dict) or not isinstance(data.get('lines', []), list):
        raise ValueError('The existing room chat book is not valid.')
    if any(not isinstance(row, dict) for row in data.get('lines', [])):
        raise ValueError('The existing room chat lines are not valid.')
    return data

def _positive_int(value):
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
        return number if number > 0 and str(number) == str(value) else 0
    except (TypeError, ValueError, OverflowError):
        return 0

def _next_id(data):
    highest = max((_positive_int(row.get('id')) for row in data.get('lines', [])), default=0)
    return max(1, _positive_int(data.get('next_id')), highest + 1)

def read(store, room_id, legacy_path=None):
    """Read/migrate one room; retain authored words and unknown metadata."""
    room_id = _room_id(room_id)
    path = _legacy_path(store, room_id, legacy_path)
    key = 'chat:' + room_id
    with store.transaction() as db:
        row = db.execute('SELECT body FROM documents WHERE id=?', (key,)).fetchone()
        if row:
            data = _book(json.loads(row[0]))
            data['next_id'] = _next_id(data)
            return data
        
        data = _book(json.loads(path.read_text(encoding='utf-8-sig'))) if path.exists() else {'lines': [], 'next_id': 1}
        data['next_id'] = _next_id(data)
        store.write(key, data)
        db.execute('INSERT OR IGNORE INTO migrations(id) VALUES (?)', (key,))
        return data

def write(store, room_id, data, legacy_path=None):
    """Replace a room book within the caller's transaction, retaining its clock."""
    room_id = _room_id(room_id)
    _legacy_path(store, room_id, legacy_path)
    data = deepcopy(_book(data))
    with store.transaction():
        previous = read(store, room_id, legacy_path)

        data['next_id'] = max(_next_id(previous), _next_id(data))
        data['lines'] = data.get('lines', [])[-CHAT_KEEP:]
        store.write('chat:' + room_id, data)

def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('The activity ' + name + ' is not valid.')
    return value

def append_activity(store, room_id, by, text, action, outcome, event_id, legacy_path=None):
    """Publish a minimal outcome once, atomically with its actual game action."""
    room_id = _room_id(room_id)
    _legacy_path(store, room_id, legacy_path)
    by = _text(by, 'author', 40)
    text = _text(text, 'text', TEXT_MAX)
    event_id = _text(event_id, 'event id', 256)
    if not isinstance(action, str) or not _LABEL.fullmatch(action):
        raise ValueError('The activity action is not valid.')
    if not isinstance(outcome, str) or not _LABEL.fullmatch(outcome):
        raise ValueError('The activity outcome is not valid.')
    with store.transaction() as db:
        replay = db.execute('SELECT line FROM room_activity_events WHERE room=? AND event_id=?',
                            (room_id, event_id)).fetchone()
        if replay:
            return json.loads(replay[0])
        data = read(store, room_id, legacy_path)
        now = datetime.now()
        line = {'id': _next_id(data), 'by': by, 'text': text,
                'at': now.strftime('%Y-%m-%d %H:%M'), 'ts': int(now.timestamp()),
                'kind': 'activity', 'action': action, 'outcome': outcome}
        data.setdefault('lines', []).append(line)
        data['next_id'] = line['id'] + 1
        write(store, room_id, data, legacy_path)
        db.execute('INSERT INTO room_activity_events(room,event_id,line) VALUES (?,?,?)',
                   (room_id, event_id, json.dumps(line, ensure_ascii=False)))
        return line
