
"""Voluntary pause and absence controls around the existing dungeon engine.

No presence timestamp plays, passes, or injures another person's character.
Only that person can mark themselves away. Combat skips their turn and enemy
targeting until they return; ordinary party reward rules remain intact.
"""
from copy import deepcopy
from datetime import datetime, timezone

ACTIONS = frozenset(('dungeon-pause', 'dungeon-resume', 'dungeon-away', 'dungeon-return'))
COMBAT_ACTIONS = frozenset(('dungeon-attack', 'dungeon-ability', 'dungeon-pass', 'dungeon-recover'))

def _now():
    return datetime.now(timezone.utc).isoformat()

def _current(dungeon):
    order = dungeon.get('turn_order', [])
    if not order:
        return None
    return order[int(dungeon.get('turn_index', 0)) % len(order)]

def _session(party, dungeon):
    session = dungeon.setdefault('session', {
        'paused': False, 'away': {}, 'roster_order': list(dungeon.get('turn_order', [])),
        'created_at': dungeon.get('started_at', _now()), 'updated_at': _now(),
        'history': [], 'revision': 0})
    session.setdefault('away', {})
    session.setdefault('roster_order', list(dungeon.get('turn_order', [])))
    session.setdefault('history', [])
    session.setdefault('revision', 0)
    members = {row['player_id'] for row in party.get('members', [])}
    session['away'] = {actor: stamp for actor, stamp in session['away'].items() if actor in members}
    session['roster_order'] = [actor for actor in session['roster_order'] if actor in members]
    for row in party.get('members', []):
        if row['player_id'] not in session['roster_order']:
            session['roster_order'].append(row['player_id'])
    return session

def _roster(party, dungeon, preferred=None):
    session = _session(party, dungeon)
    current = preferred or _current(dungeon)
    previous = list(dungeon.get('turn_order', []))
    available = [actor for actor in session['roster_order'] if actor not in session['away']]
    if current not in available and previous:
        start = previous.index(current) if current in previous else 0
        current = next((previous[(start + offset) % len(previous)]
                        for offset in range(1, len(previous) + 1)
                        if previous[(start + offset) % len(previous)] in available), None)
    dungeon['turn_order'] = available
    dungeon['turn_index'] = available.index(current) if current in available else 0
    if not available:
        session.update(paused=True, pause_reason='Everyone has voluntarily stepped away.', paused_turn=current)
    return available

def _history(session, actor, action):
    session['history'].append({'at': _now(), 'by': actor, 'action': action})
    session['history'] = session['history'][-20:]
    session['updated_at'] = _now()
    session['revision'] += 1

def snapshot(game, actor, room_id):
    party = game.players.get_party(actor)
    dungeon = (party or {}).get('current_dungeon')
    if not dungeon:
        return {'active': False, 'controls': [], 'description': 'An expedition waits safely between turns. Existing dungeon size and role rules apply.'}
    
    session = dungeon.get('session', {})
    away = session.get('away', {})
    members = party.get('members', [])
    turn = _current(dungeon)
    controls = ['dungeon-return'] if actor in away else ['dungeon-away']
    if session.get('paused'):
        if any(row['player_id'] not in away for row in members):
            controls.append('dungeon-resume')
    else:
        controls.append('dungeon-pause')
    return {'active': True, 'paused': bool(session.get('paused')), 'pause_reason': session.get('pause_reason', ''),
            'name': dungeon.get('name'), 'world': dungeon.get('world_id'),
            'room_index': dungeon.get('room_index', 0), 'room_count': len(dungeon.get('rooms', [])),
            'turn': turn, 'turn_name': next((row['name'] for row in members if row['player_id'] == turn), None),
            'away': [{'id': row['player_id'], 'name': row['name']} for row in members if row['player_id'] in away],
            'available': [{'id': row['player_id'], 'name': row['name']} for row in members if row['player_id'] not in away],
            'self_away': actor in away, 'controls': controls,
            'updated_at': session.get('updated_at', dungeon.get('started_at')), 'revision': session.get('revision', 0),
            'description': 'Pause saves the exact turn, enemies and health. Away is voluntary: you leave combat safely until you return. Party membership and ordinary shared rewards remain.'}

def execute(game, actor, room_id, action, args):
    if action not in ACTIONS:
        raise ValueError('Unknown dungeon session action.')
    _, _, region, _ = game.context(actor, room_id)
    party = game.players.get_party(actor)
    dungeon = (party or {}).get('current_dungeon')
    if not dungeon:
        raise ValueError('Your party has no active dungeon session.')
    if not any(row['player_id'] == actor for row in party.get('members', [])):
        raise ValueError('You are not a member of this expedition.')
    if region != dungeon.get('world_id'):
        raise ValueError('Return to the expedition’s region before changing its session.')
    
    target = args.get('target')
    if action in ('dungeon-away', 'dungeon-return') and target and str(target).lower() != actor:
        raise ValueError('Only that player can change their own away status.')
    session = _session(party, dungeon)
    changed = True
    if action == 'dungeon-pause':
        if session.get('paused'):
            changed = False
        else:
            session.update(paused=True, paused_by=actor, pause_reason='The party paused its expedition.', paused_turn=_current(dungeon))
        message = 'The expedition is paused. Its turn, enemies and health are saved.'
    elif action == 'dungeon-resume':
        if not _roster(party, dungeon):
            raise ValueError('Someone must return from away status before resuming.')
        if session.get('paused'):
            _roster(party, dungeon, session.get('paused_turn'))
            session.update(paused=False, pause_reason='')
        else:
            changed = False
        message = 'The expedition resumes from its saved turn.'
    elif action == 'dungeon-away':
        if actor in session['away']:
            changed = False
        else:
            prior_turn = _current(dungeon)
            session['away'][actor] = _now()
            _roster(party, dungeon)
            if not dungeon['turn_order']:
                session['paused_turn'] = prior_turn
        message = 'You stepped away safely. Your character will not act or be targeted while away.'
        if session.get('paused'):
            message += ' The expedition is paused.'
    else:
        if actor not in session['away']:
            changed = False
        else:
            session['away'].pop(actor)
            _roster(party, dungeon)
        message = 'You returned to the expedition.' + (' Resume when the party is ready.' if session.get('paused') else '')
    if changed:
        _history(session, actor, action)
    return {'success': True, 'changed': changed, 'message': message, 'dungeon_session': snapshot(game, actor, room_id)}

def run(game, actor, room_id, action, args, invoke):
    """Wrap existing dungeon dispatch without modifying the imported engine.

    ``invoke`` takes no arguments and runs the original start/attack/ability/
    pass/recovery/role/flee operation. Enemy phases temporarily see only active
    members; the canonical party roster is restored even if combat raises.
    Rewards and run cleanup see the full party, preserving existing rules.
    """
    if action in ACTIONS:
        return execute(game, actor, room_id, action, args)
    party = game.players.get_party(actor)
    dungeon = (party or {}).get('current_dungeon')
    if not dungeon:
        result = invoke()
        created = game.players.get_party_dungeon(actor)
        if created and result.get('success'):
            current_party = game.players.get_party(actor)
            _session(current_party, created)
            result['dungeon_session'] = snapshot(game, actor, room_id)
        return result
    _, _, region, _ = game.context(actor, room_id)
    if region != dungeon.get('world_id'):
        raise ValueError('Return to the expedition’s region before acting in it.')
    session = _session(party, dungeon)
    if action in COMBAT_ACTIONS:
        if session.get('paused'):
            raise ValueError('The expedition is paused. Resume it before taking a turn.')
        if actor in session['away']:
            raise ValueError('You are away from combat. Return before taking a turn.')
        _roster(party, dungeon)
        if action == 'dungeon-ability' and str(args.get('target', '')).lower() in session['away']:
            raise ValueError('That companion has stepped away from this encounter.')
    if action == 'dungeon-role' and actor in session['away']:
        raise ValueError('Return to combat before changing your role.')

    manager = game.players
    original_phase = manager._advance_dungeon_turn
    had_instance_phase = '_advance_dungeon_turn' in manager.__dict__
    instance_phase = manager.__dict__.get('_advance_dungeon_turn')

    def phase(active_party, world):
        roster = active_party.get('members', [])
        active_party['members'] = [row for row in roster if row['player_id'] not in session['away']]
        try:
            return original_phase(active_party, world)
        finally:
            active_party['members'] = roster

    if action in COMBAT_ACTIONS:
        active_party = {**party, 'members': [row for row in party['members'] if row['player_id'] not in session['away']]}
        dungeon['party_bonuses'] = manager._compute_party_synergy(active_party, dungeon['world_id'])
        manager._advance_dungeon_turn = phase
    try:
        result = invoke()
    finally:
        if action in COMBAT_ACTIONS:
            if had_instance_phase:
                manager._advance_dungeon_turn = instance_phase
            else:
                manager.__dict__.pop('_advance_dungeon_turn', None)
    if result.get('success'):
        _history(session, actor, action)
        if result.get('dungeon_complete') or result.get('dungeon_failed') or action == 'dungeon-flee':
            key = 'dungeon-session-history:' + party['party_id']
            history = game.store.read(key, [])
            history.append({'name': dungeon.get('name'), 'dungeon_id': dungeon.get('dungeon_id'),
                            'world': dungeon.get('world_id'), 'completed': bool(result.get('dungeon_complete')),
                            'ended_at': _now(), 'session': deepcopy(session)})
            game.store.write(key, history[-12:])
        result['dungeon_session'] = snapshot(game, actor, room_id)
    return result
