
"""Remember what a character last saw, without storing other people's prose.

Add a stable update ID below when a player-facing feature becomes available.
Room and thing differences come from authorized live views, never Git history.
"""
from datetime import datetime, timezone
import hashlib
import json

from . import room_chat

WORLD_UPDATES = (
    {'id': '20261004-pets-wild', 'date': '2026-10-04', 'icon': '🐾',
     'text': 'Poke an owned creature to pet them. Cloud follows separately; Pets offers feeding and reviewed wild-animal release.',
     'actions': ['companion-pet', 'feed', 'companion-release']},
    {'id': '20261004-ai-hands', 'date': '2026-10-04', 'icon': '💗',
     'text': 'Petting records affection and a fresh interaction. AI actions offer compact changes, friendly pet names and full-state opt-in.',
     'actions': ['pet', 'companion-feed'], 'help': ['actions', 'companions', 'family_pets']},
)

def _now():
    return datetime.now(timezone.utc).isoformat()

def _label(value, fallback='a placed thing'):
    return str(value or fallback).strip()[:100]

def _things(rows):
    """Store IDs, labels, owners and positions; note bodies are fingerprints only."""
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _label(row.get('label') or row.get('what') or row.get('name') or row.get('id'))
        ident = str(row.get('id') or '')[:120]
        if not ident:
            ident = 'spot-' + hashlib.sha256(json.dumps([label, row.get('x'), row.get('y')]).encode()).hexdigest()[:16]
        
        note = '' if row.get('sealed') or row.get('sealed_to') else str(row.get('note') or '')
        result[ident] = {'id': ident, 'name': label, 'by': _label(row.get('owner') or row.get('by'), 'someone'),
                         'x': row.get('x'), 'y': row.get('y'),
                         'note_version': hashlib.sha256(note.encode()).hexdigest() if note else None}
    return result

def _public_thing(row, change):
    return {k: v for k, v in dict(row, change=change).items() if k != 'note_version'}

def look(store, actor, room_id, visible_rooms, things):
    """Advance only this actor's markers after a successful authorized look.

    ``visible_rooms`` must already exclude rooms this character cannot enter.
    Summaries are bounded; returned cursors can fetch the remaining public chat.
    """
    key = 'visit-updates:' + str(actor).strip().lower()
    with store.transaction():
        book = store.read(key, {})
        previous = book.get('rooms', {}).get(room_id)
        known = book.get('known_rooms', {})
        seen_updates = set(book.get('updates', []))
        first = not book
        updates = [dict(row) for row in WORLD_UPDATES if row['id'] not in seen_updates]
        new_rooms = [{'id': ident, 'name': _label(name, ident)}
                     for ident, name in sorted(visible_rooms.items()) if known and ident not in known]
        current = _things(things)
        changes = []
        if previous:
            old = previous.get('things', {})
            for ident, row in current.items():
                if ident not in old:
                    changes.append(_public_thing(row, 'placed'))
                elif row != old[ident]:
                    kind = 'note updated' if row.get('note_version') != old[ident].get('note_version') else 'moved or changed'
                    changes.append(_public_thing(row, kind))
            changes.extend(_public_thing(row, 'removed') for ident, row in old.items() if ident not in current)
        chat = room_chat.read(store, room_id)
        lines = chat.get('lines', [])
        after = int(previous.get('chat_id', 0)) if previous else None
        recent = [row for row in lines if after is not None and int(row.get('id', 0)) > after]
        activity = [{k: row[k] for k in ('id', 'by', 'kind', 'action', 'outcome', 'at') if k in row} |
                    {'text': str(row.get('text', ''))[:240]} for row in recent[-5:]]
        last = max([int(row.get('id', 0)) for row in lines] + [after or 0])
        now = _now()
        out = {'first_tracked_visit': first, 'since': book.get('last_look'),
               'room_last_seen': previous.get('seen_at') if previous else None,
               'updates': updates, 'new_rooms': new_rooms[:12], 'new_rooms_more': len(new_rooms) > 12,
               'things': changes[:12], 'things_more': len(changes) > 12,
               'activity': activity, 'activity_more': len(recent) > 5,
               'listen_from': f'{room_id}:{after}' if after is not None else None,
               'history_gap': bool(after is not None and lines and after < int(lines[0].get('id', 0)) - 1)}
        out['message'] = ('This is your first recorded look; here are the latest Commons additions.' if first else
                          'Since your last look, here is what changed.' if updates or new_rooms or changes or recent else
                          'Nothing new has been observed since your last look.')
        known.update({ident: _label(name, ident) for ident, name in visible_rooms.items()})
        book.update(last_look=now, known_rooms=known, updates=sorted(seen_updates | {row['id'] for row in updates}))
        book.setdefault('rooms', {})[room_id] = {'seen_at': now, 'chat_id': last, 'things': current}
        store.write(key, book)
        return out
