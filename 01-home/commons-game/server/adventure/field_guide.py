
"""A private, earned field guide; reading a catalog does not discover its world.

The authenticated transport calls ``visit`` after updating the person's real
presence, and Game.execute calls ``observe`` inside its outcome transaction.
Entries come from authored room data and resolved outcomes, never story prompts.
Milestones grant cosmetic titles only, once each.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re

ACTIONS = frozenset(('field-guide-title',))
MILESTONES = (
    ('paths-three', 'Trail Reader', 'Visit three places.', 'places', 3, '🧭'),
    ('paths-ten', 'Commons Wayfarer', 'Visit ten places.', 'places', 10, '🗺️'),
    ('clues-ten', 'Keen Observer', 'Discover ten landmarks.', 'landmarks', 10, '🔎'),
    ('fish-first', 'Riverfriend', 'Catch your first fish.', 'catches', 1, '🐟'),
    ('fish-five', 'Water Watcher', 'Catch five different fish species.', 'fish', 5, '🎣'),
    ('quarry-three', 'Patient Tracker', 'Hunt three different wild quarry.', 'hunted', 3, '🐾'),
    ('bond-first', 'Kin Keeper', 'Bond with your first wild companion.', 'tamed', 1, '💛'),
    ('bonds-three', 'Creature Friend', 'Bond with three different wild species.', 'tamed', 3, '🐾'),
    ('recipe-first', 'Maker', 'Complete your first recipe.', 'recipes', 1, '🧵'),
    ('recipes-five', 'Craftsperson', 'Complete five different recipes.', 'recipes', 5, '🛠️'),
)

def _now():
    return datetime.now(timezone.utc).isoformat()

def _key(actor):
    return 'field-guide:' + str(actor).strip().lower()

def _book(game, actor):
    book = game.store.read(_key(actor), {})
    for field in ('places', 'landmarks', 'animals', 'fish', 'recipes', 'milestones'):
        book.setdefault(field, {})
    book.setdefault('selected_title', None)
    return book

def _counts(book):
    animals = book['animals'].values()
    return {'places': len(book['places']), 'landmarks': len(book['landmarks']),
            'animals': len(book['animals']), 'fish': len(book['fish']),
            'catches': sum(row.get('catches', 0) for row in book['fish'].values()),
            'hunted': sum(bool(row.get('hunted')) for row in animals),
            'tamed': sum(bool(row.get('tamed')) for row in book['animals'].values()),
            'recipes': len(book['recipes'])}

def _unlock(book):
    counts = _counts(book)
    unlocked = []
    for ident, title, description, metric, required, icon in MILESTONES:
        if counts[metric] >= required and ident not in book['milestones']:
            row = {'id': ident, 'title': title, 'description': description,
                   'icon': icon, 'earned_at': _now()}
            book['milestones'][ident] = row
            unlocked.append(row)
    return unlocked

def _name_key(name):
    return re.sub(r'[^a-z0-9]+', '-', str(name).casefold()).strip('-')

def _animal(book, row, room_id):
    name = str(row.get('name') or row.get('id') or '').strip()
    if not name:
        return None
    ident = _name_key(name)
    entry = book['animals'].setdefault(ident, {
        'id': ident, 'name': name, 'icon': row.get('emoji', row.get('icon', '🐾')),
        'first_seen': _now(), 'rooms': [], 'hunted': 0, 'tamed': 0})
    if room_id not in entry['rooms']:
        entry['rooms'].append(room_id)
    return entry

def visit(game, actor, room_id):
    """Record only the actor's current signed location, never a remote look.

    Presence belongs to this store's world. A missing disposable presence file
    is deliberately not permission to consult the real household's presence.
    """
    path = game.store.world / 'presence.json'
    if not path.exists():
        return {'changed': False, 'unlocked': []}
    presence = json.loads(path.read_text(encoding='utf-8-sig'))
    player = game.players.get_player(actor)
    names = {str(actor).casefold(), str(player.get('name', '')).casefold()}
    here = next((row for who, row in presence.items() if str(who).casefold() in names), None)
    if not isinstance(here, dict) or here.get('room') != room_id:
        return {'changed': False, 'unlocked': []}
    with game.store.transaction():
        book = _book(game, actor)
        before = deepcopy(book)
        room = game.room(room_id)
        catalog = game.catalogs(actor, room_id)
        place = book['places'].setdefault(room_id, {'id': room_id, 'first_visited': _now()})
        place.update(name=room.get('name', room_id), region=room.get('region', 'home'),
                     description=room.get('description', ''),
                     resources=[str(row['name']) for row in catalog.get('gathering', []) if row.get('name')])
        for row in room.get('landmarks', []):
            label = str(row.get('label') or row.get('name') or row.get('id') or '').strip()
            if not label:
                continue
            ident = room_id + ':' + str(row.get('id') or _name_key(label))
            landmark = book['landmarks'].setdefault(ident, {'id': ident, 'discovered_at': _now()})
            landmark.update(room=room_id, label=label, kind=row.get('kind', 'landmark'),
                            description=str(row.get('description', '')))
        for row in catalog.get('hunts', []) + catalog.get('animals', []):
            _animal(book, row, room_id)
        unlocked = _unlock(book)
        changed = before != book
        if changed:
            game.store.write(_key(actor), book)
        return {'changed': changed, 'unlocked': unlocked}

def _choice(rows, target):
    wanted = str(target).casefold().strip()
    exact = [row for row in rows if wanted in (str(row.get('id', '')).casefold(), str(row.get('name', '')).casefold())]
    if exact:
        return exact[0]
    candidates = [row for row in rows if wanted and wanted in str(row.get('name', '')).casefold()]
    return candidates[0] if len(candidates) == 1 else None

def observe(game, actor, room_id, action, args, result):
    """Add knowledge from actual outcomes, before the request receipt commits."""
    if not isinstance(result, dict) or result.get('success') is not True:
        return {'changed': False, 'unlocked': []}
    if action not in ('fish', 'hunt', 'tame', 'craft', 'home-craft'):
        return {'changed': False, 'unlocked': []}
    with game.store.transaction():
        book = _book(game, actor)
        before = deepcopy(book)
        target = args.get('target', '')
        if action == 'fish':
            catch = result.get('catch')
            if not isinstance(catch, dict) or not catch.get('id') or not catch.get('species'):
                return {'changed': False, 'unlocked': []}
            weight = catch.get('lb')
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
                return {'changed': False, 'unlocked': []}
            species = str(catch['species'])
            entry = book['fish'].setdefault(species, {'name': species, 'icon': '🐟', 'rarity': catch.get('rarity', 'common'),
                'first_caught': catch.get('at', _now()), 'catches': 0, 'record_lb': 0, 'rooms': [], 'catch_ids': []})
            if str(catch['id']) not in entry['catch_ids']:
                entry['catch_ids'].append(str(catch['id']))
                entry['catches'] += 1
                if weight > entry['record_lb']:
                    entry.update(record_lb=weight, record_at=catch.get('at', _now()), record_room=room_id)
                if room_id not in entry['rooms']:
                    entry['rooms'].append(room_id)
        elif action in ('hunt', 'tame'):
            catalog = game.catalogs(actor, room_id)
            row = _choice(catalog.get('hunts' if action == 'hunt' else 'animals', []), target)
            if row:
                entry = _animal(book, row, room_id)
                entry['hunted' if action == 'hunt' else 'tamed'] += 1
        else:
            catalog = game.catalogs(actor, room_id)
            recipes = {**catalog.get('recipes', {}), **catalog.get('home_recipes', {})}
            ident = str((result.get('item', {}).get('recipe_id') if isinstance(result.get('item'), dict) else '') or target)
            row = recipes.get(ident)
            if row is None:
                matches = [(key, row) for key, row in recipes.items()
                           if str(row.get('name', row.get('makes', ''))).casefold() == str(target).casefold()]
                if len(matches) == 1:
                    ident, row = matches[0]
            if row:
                entry = book['recipes'].setdefault(ident, {'id': ident, 'name': row.get('name', row.get('makes', ident)),
                    'icon': '🛠️', 'learned_at': _now(), 'completed': 0, 'materials': deepcopy(row.get('materials', row.get('needs', {})))})
                entry['completed'] += 1
        unlocked = _unlock(book)
        changed = before != book
        if changed:
            game.store.write(_key(actor), book)
        return {'changed': changed, 'unlocked': unlocked}

def snapshot(game, actor):
    """Only this person's discovered entries, with goals and cosmetic titles."""
    book = _book(game, actor)
    counts = _counts(book)
    earned = book['milestones']
    community_titles=game.players.get_player(actor).get('community_titles',[])
    return {'icon': '📖', 'counts': counts,
            **{key: [({k: v for k, v in row.items() if k != 'catch_ids'}) for row in book[key].values()]
               for key in ('places', 'landmarks', 'animals', 'fish', 'recipes')},
            'milestones': [{**earned.get(ident, {}), 'id': ident, 'title': title, 'description': description,
                            'icon': icon, 'earned': ident in earned, 'progress': min(required, counts[metric]), 'required': required}
                           for ident, title, description, metric, required, icon in MILESTONES],
            'titles': [row['title'] for row in earned.values()]+community_titles, 'selected_title': book['selected_title'],
            'title_actions': [{'action': 'field-guide-title', 'choices': {'target': row['id']}, 'label': row['title']}
                              for row in earned.values()] + [{'action':'field-guide-title','choices':{'target':'community:'+title},'label':title} for title in community_titles]
                              + [{'action': 'field-guide-title', 'choices': {'target': ''}, 'label': 'No title'}]}

def execute(game, actor, room_id, action, args):
    if action not in ACTIONS:
        raise ValueError('Choose an available field guide action.')
    with game.store.transaction():
        book = _book(game, actor)
        target = str(args.get('target', '')).strip()
        milestone = book['milestones'].get(target)
        if target.startswith('community:') and target[10:] in game.players.get_player(actor).get('community_titles',[]):
            milestone={'title':target[10:]}
        if target and not milestone:
            raise ValueError('Choose a title you have earned.')
        title = milestone['title'] if milestone else None
        changed = book['selected_title'] != title
        book['selected_title'] = title
        if changed:
            game.store.write(_key(actor), book)
        return {'success': True, 'changed': changed, 'title': title,
                'message': 'Title selected: ' + title + '.' if title else 'Your field guide title is cleared.'}
