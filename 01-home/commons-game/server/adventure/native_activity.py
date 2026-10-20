
"""Public outcome summaries for the original Home actions.

Only named, visible facts are copied into chat. Request bodies, private notes,
inventory snapshots and error payloads never become a public activity line.
"""
from copy import deepcopy
import re

from .room_chat import append_activity

ROUTES = frozenset(('/api/forage', '/api/plant', '/api/tend', '/api/harvest',
    '/api/loose', '/api/craft', '/api/name-companion', '/api/follow-companion',
    '/api/rook', '/api/place-made', '/api/place', '/api/howl', '/api/here',
    '/api/outfit'))

def clean(value, limit=100):
    """Keep a label on one bounded line, including player-authored labels."""
    return ' '.join(str(value or '').split())[:limit]

def number(value, default=0):
    try:
        return max(0, min(int(value), 1000000))
    except (ValueError, TypeError, OverflowError):
        return default

def _person(ns, by):
    return next((row for name, row in (ns['_read_json'](ns['HERE_FILE'], {}) or {}).items()
                 if ns['_norm'](name) == ns['_norm'](by) and isinstance(row, dict)), {})

def before(ns, route, body):
    """Read the minimum durable state needed to recognize a real change."""
    by = clean(body.get('by', body.get('as')), 40)
    out = {'presence': deepcopy(_person(ns, by))}
    if route == '/api/place':
        out['thing'] = deepcopy(next((r for r in ns['_read_json'](ns['PLACE_FILE'], [])
            if isinstance(r, dict) and r.get('id') == body.get('id')), {}))
    if route == '/api/place-made':
        baskets = ns['_read_json'](ns['BASKET_FILE'], {})
        mine = next((row for name, row in baskets.items() if ns['_norm'](name) == ns['_norm'](by)), {})
        out['made'] = deepcopy(next((row for row in mine.get('made', [])
            if isinstance(row, dict) and row.get('id') == body.get('id')), {}))
    if route in ('/api/tend', '/api/harvest'):
        out['planting'] = deepcopy(next((r for r in ns['_read_json'](ns['GROW_FILE'], [])
            if isinstance(r, dict) and r.get('id') == body.get('id')), {}))
    if route == '/api/follow-companion':
        rows = ns['_read_json'](ns['COMPANIONS_FILE'], {}).get('companions', [])
        out['companion'] = deepcopy(next((r for r in rows if r.get('id') == body.get('id')), {}))
    return out

def _room(ns, ident):
    ident = str(ident or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9_-]{1,40}', ident):
        return None
    data = ns['_read_json'](ns['WORLD_DIR'] / (ident + '.json'), None)
    return ident if isinstance(data, dict) and data.get('rows') else None

def publish(store, ns, route, body, reply, status, previous, event_id):
    """Append one actual outcome after the handler, in its save transaction."""
    if route not in ROUTES or not isinstance(reply, dict) or status in (401, 403) or status >= 500:
        return None
    by = clean(body.get('by', body.get('as')), 40)
    if not by or not ns['_verify'](by, body.get('key')):
        return None
    presence = _person(ns, by)
    room = _room(ns, presence.get('room'))
    planting = reply.get('planting') or previous.get('planting') or {}
    if route in ('/api/plant', '/api/tend', '/api/harvest'):
        room = _room(ns, planting.get('room')) or room
    if route == '/api/place-made':
        room = _room(ns, (reply.get('made') or {}).get('placed', {}).get('room')) or room
    if route == '/api/place':
        ident = reply.get('id') or body.get('id')
        thing = next((r for r in ns['_read_json'](ns['PLACE_FILE'], [])
                      if isinstance(r, dict) and r.get('id') == ident), previous.get('thing', {}))
        room = _room(ns, thing.get('room')) or room
    if not room:
        return None

    failed = status >= 400 or reply.get('ok') is False
    if failed:
        if status != 409:
            return None
        error = str(reply.get('error', ''))
        if 'already' in error.lower():
            return None
        if route == '/api/harvest' and error == 'not ready yet':
            phrase = 'tried harvesting, but the crop was not ripe yet.'
        elif route == '/api/craft' and body.get('recipe') in ns['_recipes']() and error.startswith('you need '):
            phrase = 'tried crafting, but did not have the ingredients to finish the item.'
        else:
            return None
    else:
        if reply.get('already') or reply.get('already_today') or planting.get('already_today'):
            return None
        if route == '/api/forage':
            found = reply.get('found') or {}
            phrase = 'gathered %s ×%s.' % (clean(found.get('find'), 80) or 'materials', number(found.get('handful'), 1))
        elif route == '/api/plant':
            phrase = 'planted %s%s.' % (clean(planting.get('crop'), 80) or 'a plant',
                ' in ' + clean(planting['bed'], 60) if planting.get('bed') else '')
        elif route == '/api/tend':
            phrase = 'watered %s.' % (clean(planting.get('crop'), 80) or 'a plant')
        elif route == '/api/harvest':
            phrase = 'harvested %s.' % (clean(reply.get('crop') or planting.get('crop'), 80) or 'a crop')
        elif route == '/api/loose':
            phrase = 'shot an archery end: %s/%s points.' % (number(reply.get('score')), number(reply.get('out_of'), 27))
            if number(reply.get('score')) == 0:
                phrase += ' All three arrows missed.'
                failed = True
        elif route == '/api/craft':
            made = reply.get('made') or {}
            phrase = 'crafted %s%s.' % (clean(made.get('what'), 80) or 'an item',
                ' for ' + clean(made['for'], 40) if made.get('for') else '')
        elif route in ('/api/name-companion', '/api/follow-companion'):
            companion = reply.get('companion') or {}
            name = clean(companion.get('given_name'), 40) or 'their companion'
            if route == '/api/name-companion':
                phrase = 'named their companion %s.' % name
            else:
                following = bool(reply.get('following'))
                if bool(previous.get('companion', {}).get('following')) == following:
                    return None
                phrase = ('set off with %s.' if following else 'let %s rest.') % name
        elif route == '/api/rook':
            phrase = "collected Rook's gift: %s." % (clean(reply.get('shiny'), 80) or 'a shiny')
        elif route == '/api/place-made':
            old = previous.get('made', {}).get('placed') or {}
            placed = (reply.get('made') or {}).get('placed') or {}
            if old and all(old.get(k) == placed.get(k) for k in ('room', 'x', 'y')):
                return None
            phrase = 'set out %s.' % (clean((reply.get('made') or {}).get('what'), 80) or 'a made item')
        elif route == '/api/place':
            action = body.get('action')
            if action == 'move':
                old = previous.get('thing', {})
                if all(old.get(k) == thing.get(k) for k in ('room', 'x', 'y', 'art', 'in_inventory')):
                    return None
            label = clean(thing.get('label'), 80) or 'a thing'
            template = {'place': 'placed %s.', 'move': 'moved %s.', 'remove': 'picked up %s.'}.get(action)
            if not template:return None
            phrase = template % label
        elif route == '/api/howl':
            phrase = 'joined the howl.' if reply.get('joined') else 'raised a howl.'
        elif route == '/api/outfit':
            if previous.get('presence', {}).get('avatar') == presence.get('avatar'):
                return None
            phrase = 'changed into %s.' % (clean((reply.get('outfit') or {}).get('label'), 80) or 'another outfit')
        elif route == '/api/here':
            old = previous.get('presence', {})
            if old.get('room') != presence.get('room'):
                phrase = 'arrived.'
            elif (old.get('action') == 'sit') != (presence.get('action') == 'sit'):
                phrase = 'sat down.' if presence.get('action') == 'sit' else 'stood up.'
            else:
                return None
        else:
            return None
    action = route.rsplit('/', 1)[-1]
    return append_activity(store, room, by, (by + ' ' + phrase)[:400], action,
        'failure' if failed else 'success', event_id, ns['CHAT_DIR'] / (room + '.json'))
