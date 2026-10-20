
"""Deliberate, same-room help from one healthy owned follower.

An action prepares one bounded roll bonus, without granting loot. The game
consumes it only when the corresponding real attempt rolls. A robot diagnosis
instead reports existing owned-item repair plans and current-room materials.
All writes share the caller's request transaction and retry receipt.
"""
from copy import deepcopy
import hashlib
import math
import time
import uuid

from .item_condition import ensure_condition, material_kind

ACTIONS = frozenset(('companion-track', 'companion-scout', 'companion-forage',
                     'companion-befriend', 'companion-diagnose'))
COOLDOWN = 90

def _key(actor):
    return 'companion-preparation:' + str(actor).strip().lower()

def _identity(game, actor, pet):
    ident = pet.get('instance_id') or pet.get('source_made_id')
    if not ident:

        player = game.players.get_player(actor)
        index = player.get('active_companion_index')
        original = player['companions'][index]
        original.setdefault('instance_id', uuid.uuid4().hex[:12])
        ident = original['instance_id']
        pet['instance_id'] = ident
    return str(ident)

def _following(game, actor):
    pet = game.players.get_active_companion(actor)
    if pet is None:
        return None, 'Choose a companion to walk with you first.'
    health = game.players.companion_health(actor, pet)
    if not health['can_assist']:
        return None, 'Repair your following robot first.' if health['is_robot'] else 'Feed your following companion first.'
    return pet, None

def _clock_key(actor, ident):
    fingerprint = hashlib.sha256(ident.encode()).hexdigest()[:16]
    return f'cooldown:{str(actor).strip().lower()}:companion-ability:{fingerprint}'

def _actions(game, actor, room_id, pet):
    catalog = game.catalogs(actor, room_id)
    bonuses = game.players._companion_bonus_values(pet)
    robot = game.players.companion_health(actor, pet)['is_robot']
    rows = []
    if bonuses.get('tracking', 0) > 0:
        for activity, targets in (('hunt', catalog.get('hunts', [])), ('tame', catalog.get('animals', []))):
            if targets:
                rows.append({'action': 'companion-track', 'choices': {'activity': activity}, 'label': 'Track ' + ('wild quarry' if activity == 'hunt' else 'wild companions'),
                    'icon': '🐾', 'activity': activity, 'bonus': 2, 'description': '+2 on your next ' + activity + ' attempt in this room.'})
    if bonuses.get('perception', 0) > 0 or set(pet.get('special_abilities', [])) & {'night_vision', 'keen_sight', 'silent_flight', 'eagle_eye'}:
        for activity, available in (('gather', bool(catalog.get('gathering'))), ('fish', bool(catalog.get('fishing')))):
            if available:
                rows.append({'action': 'companion-scout', 'choices': {'activity': activity}, 'label': 'Scout ' + ('resources' if activity == 'gather' else 'fishing water'),
                    'icon': '🔭', 'activity': activity, 'bonus': 1, 'description': '+1 on your next ' + activity + ' attempt in this room.'})
    if bonuses.get('gathering', 0) > 0 and catalog.get('gathering'):
        bonus = min(2, max(1, int(bonuses['gathering'])))
        rows.append({'action': 'companion-forage', 'choices': {'activity': 'gather'}, 'label': 'Find useful materials',
            'icon': '🔩' if robot else '🌿', 'activity': 'gather', 'bonus': bonus,
            'description': '+' + str(bonus) + ' on your next gathering attempt here; materials still require a successful roll.'})
    if catalog.get('animals'):
        rows.append({'action': 'companion-befriend', 'choices': {'activity': 'tame'}, 'label': 'Help approach a wild companion',
            'icon': '💛', 'activity': 'tame', 'bonus': 1, 'description': '+1 on your next taming attempt in this room.'})
    if robot:
        rows.append({'action': 'companion-diagnose', 'choices': {}, 'label': 'Diagnose damaged gear',
            'icon': '🛠️', 'description': 'Inspect your damaged items and identify carried or local repair materials.'})
    return rows

def _valid_help(game, actor, room_id, pending):
    if not isinstance(pending, dict) or pending.get('room') != room_id:
        return False
    pet, _ = _following(game, actor)
    if pet is None:
        return False
    return str(pet.get('instance_id') or pet.get('source_made_id') or '') == pending.get('companion_id')

def snapshot(game, actor, room_id):
    pet, reason = _following(game, actor)
    pending = game.store.read(_key(actor))
    result = {'icon': '🐾', 'companion': None, 'actions': [], 'reason': reason,
              'pending': dict(pending, available=_valid_help(game, actor, room_id, pending)) if pending else None}
    if pet is None:
        return result
    ident = _identity(game, actor, pet)
    ready_at = game.store.read(_clock_key(actor, ident), 0)
    remaining = max(0, math.ceil(ready_at - time.time()))
    result.update(companion={'id': ident, 'name': pet.get('custom_name') or pet['name'], 'species': pet['id'],
        'icon': pet.get('emoji', '🐾'), 'source_made_id': pet.get('source_made_id')},
        ready_at=ready_at, ready_in=remaining,
        actions=[dict(row, ready=remaining == 0, ready_in=remaining) for row in _actions(game, actor, room_id, pet)])
    return result

def _targets(game, actor, room_id, activity):
    catalog = game.catalogs(actor, room_id)
    rows = catalog.get({'hunt': 'hunts', 'tame': 'animals', 'gather': 'gathering'}.get(activity, ''), [])
    if activity == 'fish':
        return [{'label': 'Fishing water', 'action': 'fish', 'choices': {}}]
    return [{'label': row.get('name', row.get('id')), 'action': activity,
             'choices': {'target': row.get('id') if activity == 'tame' else row['name']}} for row in rows]

def _diagnosis(game, actor, room_id, args):
    player = game.players.get_player(actor)
    items = game.players.inventory_condition_items(actor) + player['shared']['crafted_items']
    selected = str(args.get('id', args.get('item_id', args.get('target', '')))).strip()
    if selected:
        matches = [item for item in items if str(item.get('id')) == selected or str(item.get('name', '')).casefold() == selected.casefold()]
        if len(matches) != 1:
            raise ValueError('Choose an owned item by its exact ID for this diagnosis.')
        items = matches
    resources = game.catalogs(actor, room_id).get('gathering', [])
    plans = []
    for item in items:
        ensure_condition(item)
        if not item.get('max_durability') or item.get('durability', 0) >= item['max_durability'] or item.get('placed'):
            continue
        plan = game.players.repair_preview(actor, item.get('name', ''), item.get('id'))
        needed = {row['name'].casefold() for row in plan.get('materials', [])}
        categories = {material_kind(name) for name in needed} | {plan.get('material_kind')}
        if 'metal' in categories:
            categories.add('components')
        sources = [{'label': row['name'], 'action': 'gather', 'choices': {'target': row['name']}}
                   for row in resources if row['name'].casefold() in needed or material_kind(row['name']) in categories - {None}]
        plans.append({'id': item['id'], 'name': item.get('name', 'Made thing'),
            'durability': item['durability'], 'max_durability': item['max_durability'],
            'repair': deepcopy(plan), 'local_sources': sources,
            'action': {'action': 'repair', 'choices': {'id': item['id']}}})
    return plans

def execute(game, actor, room_id, action, args):
    if action not in ACTIONS:
        raise ValueError('Choose an available companion action.')
    with game.store.transaction():
        pet, reason = _following(game, actor)
        if pet is None:
            raise ValueError(reason)
        options = [row for row in _actions(game, actor, room_id, pet) if row['action'] == action]
        activity = str(args.get('activity', ''))
        option = next((row for row in options if not activity or row.get('activity') == activity), None)
        if option is None:
            raise ValueError('That ability is not available to this follower in this room.')
        ident = _identity(game, actor, pet)
        name = pet.get('custom_name') or pet['name']
        plans = _diagnosis(game, actor, room_id, args) if action == 'companion-diagnose' else None
        if plans == []:
            return {'success': True, 'changed': False, 'companion': name, 'plans': [], 'message': 'Your carried gear needs no durability repairs.'}
        clock = _clock_key(actor, ident)
        if game.store.read(clock, 0) > time.time():
            raise ValueError('Give your companion a moment; their next ability is ready in ' + str(max(1, math.ceil(game.store.read(clock, 0) - time.time()))) + ' seconds.')
        game.store.write(clock, time.time() + COOLDOWN)
        if plans is not None:
            return {'success': True, 'changed': True, 'companion': name, 'plans': plans,
                    'message': name + ' diagnosed ' + str(len(plans)) + ' damaged item(s). Repairs still use your actual carried materials.'}
        activity = option['activity']
        prepared = {'id': uuid.uuid4().hex[:16], 'companion_id': ident, 'source_made_id': pet.get('source_made_id'),
                    'name': name, 'room': room_id, 'action': activity, 'bonus': option['bonus'], 'ability': action}
        game.store.write(_key(actor), prepared)
        return {'success': True, 'changed': True, 'companion': name, 'assistance': prepared,
                'targets': _targets(game, actor, room_id, activity),
                'message': name + ' is ready to help: +' + str(option['bonus']) + ' on your next ' + activity + ' attempt here. This replaces any previous prepared help.'}

def consume_assistance(game, actor, room_id, action):
    """Call inside the real roll's transaction, after validation and cooldowns.

    Consume on an actual attempt, successful or unsuccessful; validation-only
    refusals must not call this helper. An outer rollback restores the help.
    """
    with game.store.transaction():
        pending = game.store.read(_key(actor))
        if not pending or pending.get('action') != action or not _valid_help(game, actor, room_id, pending):
            return {}
        game.store.write(_key(actor), None)
        return deepcopy(pending)

def restore_assistance(game, actor, prepared):
    """Restore the exact preparation if an engine refused before any roll.

    This is used only inside the same transaction as ``consume_assistance``.
    Never overwrite a different preparation, or manufacture a bonus from input.
    """
    if not isinstance(prepared, dict) or not prepared.get('id'):
        return False
    with game.store.transaction():
        current = game.store.read(_key(actor))
        if current:
            return current.get('id') == prepared['id']
        game.store.write(_key(actor), deepcopy(prepared))
        return True

def clear_released(game,actor,instance_id):
    """Remove only the released animal's preparation; other friends keep theirs."""
    pending=game.store.read(_key(actor))
    if isinstance(pending,dict) and pending.get('companion_id')==instance_id:
        game.store.write(_key(actor),None)
