
"""Shared words and real interaction choices for the room people see.

The room remains the source of scenery and geometry. Optional game catalogs
add only activities that the authoritative game makes available here.
"""
import math
from copy import deepcopy
try:  
    from .fishing_areas import describe as describe_fishing, prop_has_water
except ImportError:
    from fishing_areas import describe as describe_fishing, prop_has_water

def _position(row):
    return {key: row[key] for key in ('x', 'y') if isinstance(row.get(key), (int, float))
            and not isinstance(row[key], bool) and math.isfinite(row[key])}

def _action(action, choices=None, label=None, required=None):
    value = {'tool': 'commons_act', 'action': action, 'choices': choices or {}}
    if label:
        value['label'] = label
    if required:
        value['required_fields'] = required
    return value

def _move(destination, label=None):
    value = {'tool': 'commons_move', 'destination': destination}
    if label:
        value['label'] = label
    return value

def landmark_destination(room, row):
    """An authored point within the room can be approached on nearby floor."""
    point = _position(row)
    rows = room.get('rows', [])
    if len(point) != 2 or not row.get('id') or not 0 <= point['y'] < len(rows):
        return False
    return 0 <= point['x'] < len(rows[math.floor(point['y'])])

def _described(row, kind):
    label = row.get('label') or row.get('name') or row.get('id') or 'Unnamed thing'
    return {'id': row.get('id'), 'label': label, 'name': label,
            'kind': row.get('kind') or kind,
            'description': row.get('description') or row.get('note') or '',
            **_position(row)}

def _npc_actions(npc):
    target = {'target': npc['id']}
    actions = [_action('npc-talk', target.copy(), 'Talk', ['message']),
               _action('flirt', target.copy(), 'Flirt')]
    if npc.get('shop_available'):
        actions.append(_action('shop', target.copy(), 'Browse shop'))
    if npc.get('story_available'):
        actions.append(_action('story-start', target.copy(), 'Begin or continue a story'))
    if npc.get('backstory_available'):
        actions.append(_action('backstory', target.copy(), 'Ask about their history'))
    if npc['id'] == 'takoda':
        actions += [_action('hear-story', target.copy(), 'Listen to a campfire tale'),
                    _action('tell-story', target.copy(), 'Tell a campfire tale', ['message'])]
    return actions

def _terminal_actions(terminal):
    target={'target':terminal['name']}
    actions=[_action('hack',target.copy(),'Recover terminal cache')]
    labels={'intel':'Read character intel','steal':'Steal credits','give':'Give credits'}
    for mode,label in labels.items():
        if mode in terminal.get('operations',[]):
            actions.append(_action('hack',{**target,'mode':mode},label,
                ['character']+(['amount'] if mode in ('steal','give') else [])))
    return actions

def room_experience(room, catalogs=None, holiday=None):
    """A pure, credential-free scene shared by local and remote readers."""
    catalog = catalogs or {}
    npc_rows = catalog.get('npcs', room.get('npcs', []))
    npcs = {row['id']: row for row in npc_rows if row.get('id')}
    terminals = {row['name']: row for row in catalog.get('hacking', []) if row.get('name')}
    fixtures, exits, landmarks = [], [], []
    for prop in room.get('props', []):
        npc = npcs.get(prop.get('game_npc'))
        if npc is None:
            npc = next((row for row in npc_rows if str(prop.get('label', '')).casefold()
                        == str(row.get('name', '')).casefold()), None)
        authored = prop.get('action') if isinstance(prop.get('action'), dict) else None
        kind = ('exit' if prop.get('to') else 'npc' if npc else
                'seat' if authored and authored.get('name') == 'sit' else
                'water' if prop.get('fishable') else 'object')
        fixture = _described(prop, kind)
        if prop.get('note'):
            fixture['note'] = prop['note']
        fixture['actions'] = [_move(prop['id'], 'Go to this fixture')] if prop.get('id') else []
        if prop.get('owner'):
            fixture['owner'] = prop['owner']
        if authored:
            fixture['authored_action'] = {key: authored[key] for key in ('name', 'label', 'spots') if key in authored}
            if authored.get('name') == 'sit' and prop.get('id'):
                fixture['actions'] = [_move(prop['id'], 'Sit here')]
        if prop.get('to'):
            destination = prop['to']
            fixture.update(to=destination, actions=[_move(destination, 'Follow this exit')])
            exits.append({'id': destination, 'fixture_id': prop.get('id'),
                          'label': fixture['label'], 'name': fixture['label'],
                          'kind': fixture['kind'], 'description': fixture['description'],
                          **_position(prop), 'action': _move(destination)})
        if npc:
            fixture['npc_id'] = npc['id']
            fixture['actions'] += _npc_actions(npc)
        terminal = terminals.get(prop.get('game_terminal') or prop.get('label'))
        if terminal:
            fixture['terminal_id']=terminal['name']
            fixture['actions'] += _terminal_actions(terminal)
        if prop.get('fishable') and catalog.get('fishing') and prop_has_water(room, prop):
            fixture['actions'].append(_action('fish', label='Fish here'))
        fixtures.append(fixture)
    for row in room.get('landmarks', []):
        landmark = _described(row, 'scenery')

        if landmark_destination(room, row):
            landmark['navigation'] = _move(row['id'], 'Approach this landmark')
        landmarks.append(landmark)

    def entities(rows, kind, action, key, description_keys):
        result = []
        for row in rows:
            target = row.get(key)
            if not target:
                continue
            label = row.get('name') or row.get('label') or row.get('title') or str(target).replace('_', ' ').title()
            value = {'id': target, 'name': label, 'kind': kind,
                     'description': next((row[field] for field in description_keys if row.get(field)), ''),
                     'action': _action(action, {'target': target})}
            for field in ('emoji', 'difficulty', 'rarity', 'tip', 'role'):
                if row.get(field) is not None:
                    value[field] = row[field]
            result.append(value)
        return result

    tameable = entities(catalog.get('animals', []), 'wild animal', 'tame', 'id', ('line', 'description'))
    quarry = entities(catalog.get('hunts', []), 'unnamed wild quarry', 'hunt', 'name', ('line', 'description'))
    resources = entities(catalog.get('gathering', []), 'resource', 'gather', 'name', ('description', 'flavor'))
    hackable = entities(catalog.get('hacking', []), 'terminal', 'hack', 'name', ('flavor', 'description'))
    for value in hackable:
        value['actions']=_terminal_actions(terminals[value['id']])
    characters = entities(npc_rows, 'npc', 'npc-talk', 'id', ('description', 'role'))
    for npc, source in zip(characters, (row for row in npc_rows if row.get('id'))):
        npc['action']['required_fields'] = ['message']
        npc['actions'] = _npc_actions(source)
    quests = entities(catalog.get('quests', []), 'quest', 'quest-accept', 'quest_id', ('description', 'objective'))
    activities = []
    for ident, label, rows in (('tame', 'Meet wild creatures', tameable),
                               ('hunt', 'Hunt unnamed wild quarry', quarry),
                               ('gather', 'Gather resources', resources),
                               ('hack', 'Use terminals', hackable),
                               ('npc-talk', 'Talk and trade with people', characters),
                               ('quest-accept', 'Accept local quests', quests)):
        if rows:
            activities.append({'id': ident, 'label': label, 'choices_from': {
                'tame': 'wildlife.tameable', 'hunt': 'wildlife.quarry', 'gather': 'resources',
                'hack': 'terminals', 'npc-talk': 'npcs', 'quest-accept': 'quests'}[ident]})
    if catalog.get('fishing'):
        activities.append({'id': 'fish', 'label': 'Fish in the water', 'action': _action('fish'),
                           'setup': catalog.get('fishing_setup', {})})
    if catalog.get('archery'):
        activities.append({'id': 'archery', 'label': 'Practise archery', 'action': _action('archery')})
    if room.get('rest_station') or room.get('id') in ('hearth', 'bedroom', 'nest'):
        activities.append({'id': 'rest', 'label': 'Rest and recover', 'action': _action('rest')})
    scene={'id': room.get('id'), 'name': room.get('name', room.get('id')),
            'description': room.get('description') or room.get('subtitle') or '',
            'subtitle': room.get('subtitle') or '', 'region': room.get('region', 'home'),
            'landmarks': landmarks, 'fixtures': fixtures, 'exits': exits,
            'activities': activities, 'wildlife': {'tameable': tameable, 'quarry': quarry},
            'resources': resources, 'terminals': hackable, 'npcs': characters, 'quests': quests}
    if (room.get('fishing_paint') or room.get('fishing_zones') or room.get('water')
            or any(prop.get('fishable') for prop in room.get('props', []))):
        scene['fishing_water'] = describe_fishing(room)
        scene['fishing_water']['available'] = bool(catalog.get('fishing'))
    if hackable:
        accounts=catalog.get('terminal_characters',[])
        scene['terminal_network']={**catalog.get('terminal_setup',{}),
            'characters':accounts[:20],'characters_total':len(accounts),
            'characters_next_offset':20 if len(accounts)>20 else None,
            'character_help':{'tool':'commons_help','topic':'catalog','section':'terminal_characters',
                              'hint':'Choose an account id; intel uses character, transfers also use whole-credit amount.'}}
    if holiday is not None:

        scene['holiday'] = {key: deepcopy(holiday[key]) for key in
            ('date', 'timezone', 'season', 'mode', 'current', 'next', 'snow', 'outdoors')
            if key in holiday}
        current = holiday.get('current')
        description = current.get('description', '') if current else ''
        if holiday.get('snow'):
            description += ' Gentle Christmas snow falls outdoors and settles lightly around the map edges.'
        scene['seasonal_description'] = description
    return scene
