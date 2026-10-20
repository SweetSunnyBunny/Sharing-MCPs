
"""Finite shared projects and authored consequences, saved in private SQLite.

The caller uses Game.execute's transaction and receipt. Contributions escrow
only carried raw materials; withdrawing returns the contributor's own units.
Completed projects never reset, and their services and rewards are bounded.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from .servings import food_units

ACTIONS = frozenset(('projects', 'project-contribute', 'project-withdraw', 'project-service'))
PROJECTS = (
    {'id': 'hearth-feast', 'title': 'A Table for Everyone', 'icon': '🥧',
     'region': 'home', 'room': 'hearth', 'needs': {'berries': 8, 'wood': 4},
     'description': 'Provision a shared berry feast, with fuel for the oven.',
     'clue': 'Forage berries and fallen wood in the Woods, then bring them to the Kitchen.',
     'completed_description': 'A berry feast waits on the kitchen table. A small card thanks the people who provisioned it.',
     'service': {'title': 'Share a slice', 'icon': '🥧', 'servings': 24, 'heal': 10}},
    {'id': 'village-waystation', 'title': 'The Village Waystation', 'icon': '🏮',
     'region': 'heros_journey', 'room': 'village-square',
     'needs': {'Oak Branch': 6, 'Wildflower': 6},
     'description': 'Repair the village supply shelter and mark its path with flowers.',
     'clue': 'Gather oak branches and wildflowers in the Enchanted Forest.',
     'completed_description': 'A repaired supply shelter stands beside the village path, with fresh flower markers and the builders’ names.',
     'effects': {'gathering_bonus': 1},
     'effect_rooms': ['enchanted-forest-main', 'enchanted-forest-north', 'enchanted-forest-right',
                      'fey-wilds-main', 'fey-wilds-north1', 'fey-wilds-north2', 'wizards-tower-outside']},
    {'id': 'frontier-supply-cache', 'title': 'Supplies for the Trail', 'icon': '🧳',
     'region': 'dusty_trails', 'room': 'dusty-trails-hub',
     'needs': {'wood': 6, 'Rabbit Pelt': 4},
     'description': 'Pack fuel and insulating pelts for travelers crossing the frontier.',
     'clue': 'Hunt wild rabbits on Dusty Trails and bring fallen wood from the Home Woods; the saloon hosts the cache.',
     'completed_description': 'A weatherproof trail cache rests beside the saloon. A stitched tag names the travelers who supplied it.',
     'service': {'title': 'Warm up by the trail stove', 'icon': '🔥', 'servings': 20, 'heal': 10}},
    {'id': 'salvage-repair-station', 'title': 'Wake the Repair Station', 'icon': '🤖',
     'region': 'black_serenity', 'room': 'scavengers-reach',
     'needs': {'Scrap Metal': 8, 'Circuit Board': 4, 'Power Cell': 2},
     'description': 'Restore a communal repair station among the wreck piles.',
     'clue': 'Gather intact boards, cells and scrap here on Scavengers Reach.',
     'completed_description': 'The repaired station hums between the wreck piles. Its lit workbench bears a plate naming every contributor.',
     'effects': {'repair_material_discount': 0.1},
     'effect_rooms': ['scavengers-reach', 'the-forge']},
)

_EVENT_ANCHORS = {'home': 'hearth', 'heros_journey': 'village-square',
                  'dusty_trails': 'dusty-trails-hub', 'black_serenity': 'ships-bridge'}

def _now():
    return datetime.now(timezone.utc).isoformat()

def _normal(value):
    return re.sub('[^a-z0-9]', '', str(value).lower())

def _quantity(value, maximum=100):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Choose a positive whole quantity.')
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise ValueError('Choose a positive whole quantity.') from None
    if str(amount) != str(value) or not 1 <= amount <= maximum:
        raise ValueError('Choose a positive whole quantity.')
    return amount

def _spec(target):
    wanted = _normal(target)
    result = next((row for row in PROJECTS if wanted in (_normal(row['id']), _normal(row['title']))), None)
    if not result:
        raise ValueError('Choose a project from the shared noticeboard.')
    return result

def _state(game, spec):
    return game.store.read('community-project:' + spec['id'],
        {'id': spec['id'], 'progress': {}, 'contributors': {}, 'completed': False,
         'revision': 0, 'service_claims': {}, 'service_used': 0})

def _matches(game, label, material):
    if _normal(label) == _normal(material):
        return True
    if material not in ('berries', 'wood'):
        return False
    if material == 'wood' and _normal(label) in ('firewood', 'firewoodbundle', 'oakbranch'):
        return True
    categories = getattr(game, '_community_material_categories', None)
    if categories is None:
        categories = {}
        for filename, key in (('crops.json', 'crops'), ('forage.json', 'finds')):
            path = game.root / 'world' / filename
            if path.exists():
                rows = json.loads(path.read_text(encoding='utf-8')).get(key, {})
                categories.update({_normal(name): row.get('yields') for name, row in rows.items()})
        game._community_material_categories = categories
    return categories.get(_normal(label)) == material

def _count_inventory(game, player, name):
    return sum(max(0, int(row.get('quantity', 1))) if isinstance(row, dict) else 1
               for row in player['shared']['inventory']
               if _matches(game, row.get('name', '') if isinstance(row, dict) else row, name))

def _take(game, player, material, amount):
    """Keep original unit metadata and remaining food portions in the escrow."""
    shared = player['shared']
    portions = food_units(shared, game.consumables)
    units = []
    for row in list(shared['inventory']):
        name = row.get('name', '') if isinstance(row, dict) else str(row)
        if not _matches(game, name, material):
            continue
        count = max(0, int(row.get('quantity', 1))) if isinstance(row, dict) else 1
        taken = min(amount - len(units), count)
        if not taken:
            continue
        for _ in range(taken):
            unit = deepcopy(row)
            if isinstance(unit, dict):
                unit['quantity'] = 1
            entry = {'inventory': unit}
            if portions.get(name):
                entry['food'] = portions[name].pop(0)
            units.append(entry)
        if isinstance(row, dict) and count > 1:
            if taken < count:
                row['quantity'] = count - taken
            else:
                shared['inventory'].remove(row)
        else:
            shared['inventory'].remove(row)
        if len(units) == amount:
            break
    if len(units) != amount:
        raise ValueError('You do not carry that many of this material.')
    return units

def _return(player, units):
    for unit in units:
        row = deepcopy(unit['inventory'])
        player['shared']['inventory'].append(row)
        if unit.get('food'):
            name = row.get('name', '') if isinstance(row, dict) else str(row)
            player['shared'].setdefault('food_servings', {}).setdefault(name, []).append(deepcopy(unit['food']))

def _public(game, actor, room_id, spec, state):
    player = game.players.get_player(actor)
    own = state['contributors'].get(actor, {})
    service = spec.get('service')
    return {key: deepcopy(spec[key]) for key in ('id', 'title', 'icon', 'region', 'room', 'description', 'clue')} | {
        'completed': bool(state['completed']), 'completed_at': state.get('completed_at'),
        'completed_description': spec['completed_description'] if state['completed'] else '',
        'requirements': [{'material': name, 'required': qty, 'contributed': state['progress'].get(name, 0),
                          'remaining': max(0, qty - state['progress'].get(name, 0)),
                          'owned': _count_inventory(game, player, name), 'mine': len(own.get('units', {}).get(name, []))}
                         for name, qty in spec['needs'].items()],
        'contributors': [{'name': row['name'], 'quantity': sum(len(v) for v in row.get('units', {}).values())}
                         for row in state['contributors'].values() if any(row.get('units', {}).values())],
        'reward': {'xp': 12, 'title': 'Commons Contributor'},
        'benefits': deepcopy(spec.get('effects', {})),
        'effects': deepcopy(spec.get('effects', {})) if state['completed'] else {},
        'can_contribute': not state['completed'] and room_id == spec['room'],
        'can_withdraw': not state['completed'] and room_id == spec['room'] and any(own.get('units', {}).values()),
        'service': ({**service, 'remaining': max(0, service['servings'] - state.get('service_used', 0)),
                     'claimed': actor in state.get('service_claims', {}),
                     'available': state['completed'] and room_id == spec['room']
                         and actor not in state.get('service_claims', {})
                         and state.get('service_used', 0) < service['servings']} if service else None)}

def snapshot(game, actor, room_id):
    _, _, region, _ = game.context(actor, room_id)
    return {'projects': [_public(game, actor, room_id, spec, _state(game, spec)) for spec in PROJECTS
                         if spec['region'] == region],
            'effects': room_effects(game, room_id),
            'instructions': 'Contribute carried materials at the project’s room. Your own materials can be withdrawn until completion. Contributions and rewards survive leaving.'}

def execute(game, actor, room_id, action, args):
    if action == 'projects':
        return {'success': True, 'message': 'The shared noticeboard.', **snapshot(game, actor, room_id), 'changed': False}
    if action not in ACTIONS:
        raise ValueError('Unknown community action.')
    player, _, region, _ = game.context(actor, room_id)
    spec = _spec(args.get('target', args.get('project')))
    if room_id != spec['room']:
        raise ValueError('Bring this contribution to ' + spec['room'].replace('-', ' ').title() + '.')
    state = _state(game, spec)
    if action == 'project-service':
        service = spec.get('service')
        if not state['completed'] or not service:
            raise ValueError('This project has no completed service to use.')
        if actor in state['service_claims']:
            return {'success': False, 'message': 'You have already shared in this project’s provisions.', 'changed': False}
        if state['service_used'] >= service['servings']:
            return {'success': False, 'message': 'The shared provisions have all been used.', 'changed': False}
        old_hp = player['shared']['hp']
        player['shared']['hp'] = min(player['shared']['max_hp'], old_hp + service['heal'])
        state['service_claims'][actor] = _now()
        state['service_used'] += 1
        state['revision'] += 1
        game.store.write('community-project:' + spec['id'], state)
        return {'success': True, 'changed': True, 'project': spec['id'], 'title': spec['title'],
                'service': service['title'], 'healed': player['shared']['hp'] - old_hp,
                'message': service['title'] + '. ' + str(player['shared']['hp'] - old_hp) + ' HP restored; '
                           + str(service['servings'] - state['service_used']) + ' shared portions remain.'}
    if state['completed']:
        raise ValueError('This project is complete; its materials are already in use.')
    material = args.get('material')
    if material:
        material = next((name for name in spec['needs'] if _normal(name) == _normal(material)), None)
        if not material:
            raise ValueError('Choose a material this project actually needs.')
    elif action == 'project-contribute':
        material = next((name for name, qty in spec['needs'].items()
                         if state['progress'].get(name, 0) < qty and _count_inventory(game, player, name)), None)
    else:
        material = next((name for name, units in state['contributors'].get(actor, {}).get('units', {}).items() if units), None)
    if not material:
        raise ValueError('Choose a needed carried material.' if action == 'project-contribute' else 'You have no contribution to withdraw.')
    amount = _quantity(args.get('quantity', 1))
    contributor = state['contributors'].setdefault(actor, {'name': player['name'], 'units': {}})
    units = contributor['units'].setdefault(material, [])
    if action == 'project-contribute':
        if amount > spec['needs'][material] - state['progress'].get(material, 0):
            raise ValueError('That quantity exceeds what the project still needs.')
        if amount > _count_inventory(game, player, material):
            raise ValueError('You do not carry that many of this material.')
        units.extend(_take(game, player, material, amount))
        state['progress'][material] = state['progress'].get(material, 0) + amount
    else:
        if amount > len(units):
            raise ValueError('You can withdraw only your own contributed materials.')
        _return(player, units[:amount])
        del units[:amount]
        state['progress'][material] -= amount
    state['revision'] += 1
    completed = all(state['progress'].get(name, 0) >= qty for name, qty in spec['needs'].items())
    if completed:
        state.update(completed=True, completed_at=_now())
        for owner, row in state['contributors'].items():
            if not any(row['units'].values()):
                continue
            game.players.add_xp(owner, 12, 'Shared project: ' + spec['title'], False)
            earned = game.players.get_player(owner).setdefault('community_titles', [])
            if 'Commons Contributor' not in earned:
                earned.append('Commons Contributor')
    game.store.write('community-project:' + spec['id'], state)
    return {'success': True, 'changed': True, 'project': spec['id'], 'title': spec['title'],
            'material': material, 'quantity': amount, 'completed': completed,
            'message': ('Contributed ' if action == 'project-contribute' else 'Withdrew your ') + str(amount) + ' ' + material
                       + ' for ' + spec['title'] + ('. The project is complete!' if completed else '.'),
            'community': snapshot(game, actor, room_id)}

def room_effects(game, room_id):
    room = game.room(room_id)
    region = room.get('region', 'home')
    if region == 'hub':
        region = 'home'
    result = []
    for spec in PROJECTS:
        state = _state(game, spec)
        if state['completed'] and room_id in [spec['room'], *spec.get('effect_rooms', [])]:
            result.append({'id': spec['id'], 'kind': 'project', 'icon': spec['icon'], 'title': spec['title'],
                           'description': spec['completed_description'] if room_id == spec['room'] else
                               'Support from ' + spec['title'] + ' helps travelers here. The completed project is at '
                               + spec['room'].replace('-', ' ').title() + '.',
                           'room': spec['room'],
                           'contributors': [row['name'] for row in state['contributors'].values() if any(row.get('units', {}).values())],
                           'effects': deepcopy(spec.get('effects', {}))})
    history = game.store.read('community-events:' + region, [])
    result.extend(deepcopy(row) for row in history if row['room'] == room_id)
    return result

def effects(game, room_id):
    rows = room_effects(game, room_id)
    region = game.room(room_id).get('region', 'home')
    if region == 'hub':
        region = 'home'

    regional = game.store.read('community-events:' + region, [])
    return {'gathering_bonus': min(1, sum(int(row.get('effects', {}).get('gathering_bonus', 0)) for row in rows) + bool(regional)),
            'repair_material_discount': min(0.1, max((float(row.get('effects', {}).get('repair_material_discount', 0)) for row in rows), default=0))}

def record_event(game, event, room_id_or_world):
    """Create a bounded commemorative fixture only for a resolved success.

    Store an ID receipt separately from the last twelve public commemorations;
    trimming public history can never make a replay create another fixture.
    The record is authored from the region, not from generated NPC narration.
    """
    if not event.get('resolved') or event.get('resolution') != 'success' or not event.get('id'):
        return None
    if room_id_or_world in _EVENT_ANCHORS:
        region, room_id = room_id_or_world, _EVENT_ANCHORS[room_id_or_world]
    else:
        room_id = room_id_or_world
        region = game.room(room_id).get('region', 'home')
        if region == 'hub':
            region = 'home'
    key = 'community-event-receipt:' + str(event['id'])
    old = game.store.read(key)
    if old:
        return old
    title = re.sub(r'[\x00-\x1f\x7f]', '', str(event.get('title', 'A regional adventure')))[:120]
    names = [str(row.get('name', actor))[:80] for actor, row in event.get('participants', {}).items()
             if row.get('contributions', 0) > 0][:32]
    description = {'heros_journey': 'A flower-ringed commemorative lantern burns beside the village supply shelter.',
                   'dusty_trails': 'A brass trail marker beside the saloon records the region’s successful adventure.',
                   'black_serenity': 'A restored signal beacon on the bridge displays a commemorative crew plate.'
                   }.get(region, 'A commemorative card on the Hearth mantel records a shared adventure.')
    row = {'id': 'event:' + str(event['id']), 'kind': 'event', 'title': title, 'icon': '🏅',
           'room': room_id, 'region': region, 'description': description, 'contributors': names,
           'completed_at': _now(), 'effects': {'gathering_bonus': 1}}
    history = game.store.read('community-events:' + region, [])
    history.append(row)
    game.store.write('community-events:' + region, history[-12:])
    game.store.write(key, row)
    return row
