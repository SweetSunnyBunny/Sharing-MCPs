"""Fictional terminal dossiers and conserved transfers in the shared game wallet.

Only an explicit public-field projection leaves this module. NPC descriptions
are authored game text, never conversation prompts, profiles or private notes.
The caller's Store transaction commits wallets, cooldowns and receipts together.
"""
from copy import deepcopy
import json
import math
import random
import time

from .players import ENGINE

CENTS_PER_CREDIT = 50
OPERATIONS = ('recover', 'intel', 'steal', 'give')

def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default

def _cents(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('This game wallet needs repair before credits can move.')
    return value

def _credits(cents):
    return cents // CENTS_PER_CREDIT if cents % CENTS_PER_CREDIT == 0 else cents / CENTS_PER_CREDIT

def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Choose a positive whole number of credits.')
    try:
        amount = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Choose a positive whole number of credits.') from None
    if amount < 1 or str(amount) != str(value):
        raise ValueError('Choose a positive whole number of credits.')
    return amount

def _presence(game):
    return {str(name).lower(): row.get('room') for name, row in
            _read(game.store.world / 'presence.json', {}).items() if isinstance(row, dict)}

def _npc_accounts(game):
    """Join authored public descriptions to NPCs that really inhabit this region."""
    config = _read(ENGINE / 'terminal_characters.json', {}).get('characters', {})
    npcs = {}
    for path in sorted((game.root / 'world').glob('*.json')):
        room = _read(path, {})
        if not isinstance(room, dict) or room.get('region') != 'black_serenity' or not room.get('rows'):
            continue
        for npc in room.get('npcs', []):
            ident = str(npc.get('id', ''))
            if ident not in config:
                continue
            spec = config[ident]
            key = 'npc:' + ident
            default = {'id': key, 'gold_cents': int(spec['starting_credits']) * CENTS_PER_CREDIT}
            ledger = game.store.read('terminal-wallet:' + key, default)
            cents = _cents(ledger.get('gold_cents'))
            npcs[key] = {'id': key, 'name': npc['name'], 'kind': 'npc',
                         'role': npc.get('role', 'Resident'), 'location': room.get('id', path.stem),
                         'description': str(spec.get('description', '')),
                         'gold_cents': cents, 'ledger': ledger}
    return npcs

def _steal_reason(game, actor, room_id, other_id, player, cents, places):
    if other_id == actor:
        return 'Choose another character.'
    if not game.players.get_player(actor).get('competitive') or not player.get('competitive'):
        return 'Both players must enable competitive play before credits can be stolen.'
    if places.get(other_id) != room_id:
        return 'That player must be in this room.'
    if cents // (10 * CENTS_PER_CREDIT) < 1:
        return 'The 10% player limit is less than one whole credit.'
    until = game.store.read(f'cooldown:{actor}:steal:{other_id}', 0)
    if until > time.time():
        return f'Wait {max(1, math.ceil(until - time.time()))} seconds before stealing from this player again.'
    return ''

def catalog(game, actor, room_id, systems):
    """Offer public targets without loading or creating anyone else's character."""
    if not systems:
        return [], [], {}
    player = game.players.get_player(actor)
    terminal_rows = [dict(deepcopy(system), operations=list(OPERATIONS),
                          steal_limit=max(0, int(system.get('rewards', {}).get('credits', [0, 0])[-1])))
                     for system in systems]
    setup = {'credits_balance': _credits(_cents(player['shared']['gold_cents'])),
             'credits_per_gold': 2, 'cooldown_seconds': 60,
             'player_theft_percent': 10, 'player_theft_cooldown_seconds': 300}
    characters = []
    for account in _npc_accounts(game).values():
        cents = account['gold_cents']
        limit = cents // CENTS_PER_CREDIT
        characters.append({key: account[key] for key in ('id', 'name', 'kind', 'role', 'location')})
        characters[-1].update(credits=_credits(cents), stealable=limit > 0,
                              steal_limit=limit, steal_reason='' if limit else 'This account has no whole credits left.',
                              giveable=True, give_reason='')
    places = _presence(game)
    for ident, other in sorted(game.players.players.items()):
        if ident == actor or not game.store.read('player:' + ident):
            continue
        cents = _cents(other.get('shared', {}).get('gold_cents', 5000))
        reason = _steal_reason(game, actor, room_id, ident, other, cents, places)
        characters.append({'id': 'player:' + ident, 'name': str(other.get('name', ident)),
                           'kind': 'player', 'role': 'Player character', 'location': places.get(ident),
                           'credits': _credits(cents), 'competitive': bool(other.get('competitive')),
                           'stealable': not reason, 'steal_limit': cents // (10 * CENTS_PER_CREDIT),
                           'steal_reason': reason, 'giveable': True, 'give_reason': ''})
    return terminal_rows, characters, setup

def _account(game, actor, character):
    if not isinstance(character, str) or not character:
        raise ValueError('Choose a character account from this terminal\'s catalog.')
    if character == 'player:' + actor:
        raise ValueError('Choose another character.')
    npc = _npc_accounts(game).get(character)
    if npc:
        return npc
    if not character.startswith('player:'):
        raise ValueError('That character account does not exist.')
    ident = character[7:]
    existing = game.store.read('player:' + ident)
    if not ident or not existing or ident != ident.strip().lower():
        raise ValueError('Choose another player who has entered the game.')

    other = game.players.get_player(ident)
    if 'gold_cents' in existing.get('shared', {}):
        other['shared']['gold_cents'] = _cents(existing['shared']['gold_cents'])
    return {'id': character, 'name': other['name'], 'kind': 'player',
            'role': 'Player character', 'location': _presence(game).get(ident),
            'gold_cents': _cents(other['shared']['gold_cents']), 'player': other}

def _write_account(game, account, cents):
    cents = _cents(cents)
    if account['kind'] == 'player':
        account['player']['shared']['gold_cents'] = cents
    else:
        game.store.write('terminal-wallet:' + account['id'], {'id': account['id'], 'gold_cents': cents})
    account['gold_cents'] = cents

def _intel(account):
    """Deliberately project fields; never return an entire stored player dict."""
    result = {key: account.get(key) for key in ('name', 'role', 'location')}
    result['credits'] = _credits(account['gold_cents'])
    if account['kind'] == 'npc':
        result['description'] = account['description']
    else:
        player = account['player']
        result.update(level=player.get('level', 1), character_type=player.get('character_type', 'all'),
                      form=player.get('current_form', 'human'), hp=player['shared']['hp'],
                      max_hp=player['shared']['max_hp'], competitive=bool(player.get('competitive')),
                      description='A registered player character exploring the Commons.')
    return result

def action(game, actor, room_id, args, systems):
    player, room, world, loc = game.context(actor, room_id)
    if room.get('region') != 'black_serenity':
        raise ValueError('Hacking terminals are in the futuristic area.')
    terminal = next((row for row in systems if row.get('name') == args.get('target')), None)
    if not terminal:
        raise ValueError('That terminal is not here.')
    mode = args.get('mode', 'recover')
    if mode not in OPERATIONS:
        raise ValueError('Choose recover, intel, steal or give.')
    account = None if mode == 'recover' else _account(game, actor, args.get('character'))
    amount = _amount(args.get('amount')) if mode in ('steal', 'give') else None

    existing = game.store.read('player:' + actor)
    actor_cents = _cents(existing.get('shared', {}).get('gold_cents', player['shared']['gold_cents'])
                         if existing else player['shared']['gold_cents'])
    transfer_cents = amount * CENTS_PER_CREDIT if amount else 0
    if mode == 'give' and transfer_cents > actor_cents:
        raise ValueError('You do not have enough credits for that transfer.')
    if mode == 'steal':
        if transfer_cents > account['gold_cents']:
            raise ValueError('That character does not have enough credits.')
        maximum = min(account['gold_cents'] // CENTS_PER_CREDIT, terminal['steal_limit'])
        if account['kind'] == 'player':
            ident = account['id'][7:]
            reason = _steal_reason(game, actor, room_id, ident, account['player'], account['gold_cents'], _presence(game))
            if reason:
                raise ValueError(reason)
            maximum = min(maximum, account['gold_cents'] // (10 * CENTS_PER_CREDIT))
        if amount > maximum:
            raise ValueError(f'This terminal can steal at most {maximum} whole credits from that account per breach.')
    
    player['shared']['gold_cents'] = actor_cents
    if mode != 'give':
        until = game.store.read(f'cooldown:{actor}:hack', 0)
        if until > time.time():
            raise ValueError(f'Wait {max(1, math.ceil(until - time.time()))} seconds before trying again.')
        game.cooldown(actor, 'hack', 60)
        if mode == 'steal' and account['kind'] == 'player':
            
            game.store.write(f'cooldown:{actor}:steal:{account["id"][7:]}', time.time() + 300)
        roll = random.randint(1, 20)
        bonus = game.players.get_equipment_bonuses(actor, world).get('hacking', 0)
        if roll + bonus < terminal['difficulty']:
            return {'mode': mode, 'success': False,
                    'message': 'Access refused: ' + terminal.get('failure_effect', 'logged'),
                    'roll': roll, 'bonus': bonus, 'total': roll + bonus}
    else:
        roll = bonus = None
    result = {'mode': mode, 'success': True}
    if roll is not None:
        result.update(roll=roll, bonus=bonus, total=roll + bonus)
    if mode == 'intel':
        result.update(intel=_intel(account), message=f'Character dossier opened: {account["name"]}.')
    elif mode in ('steal', 'give'):
        sign = 1 if mode == 'steal' else -1
        player['shared']['gold_cents'] += sign * transfer_cents
        _write_account(game, account, account['gold_cents'] - sign * transfer_cents)
        result.update(transfer={'credits': amount,
                                'from': account['id'] if mode == 'steal' else 'player:' + actor,
                                'to': 'player:' + actor if mode == 'steal' else account['id'],
                                'actor_credits': _credits(player['shared']['gold_cents']),
                                'target_credits': _credits(account['gold_cents'])},
                      message=(f'Stole {amount} credits from {account["name"]}.' if mode == 'steal' else
                               f'Gave {amount} credits to {account["name"]}.'))
    else:
        rewards = terminal.get('rewards', {})
        lo, hi = rewards.get('credits', [0, 0])
        credits = random.randint(lo, hi)
        game.players.add_currency(actor, world, credits * .5)
        if rewards.get('items') and random.random() < rewards.get('item_chance', 0):
            game.players.add_item(actor, world, random.choice(rewards['items']))
        result.update(recovered_credits=credits,
                      message=f'Terminal opened. Recovered {credits} credits ({credits * .5:g} gold).')
    if mode != 'give':
        result['quest_progress'] = game.quests.record_hack(actor, world, player['name'], terminal['name'], loc)
        result['achievements'] = game.players.record_hack(actor)
        game.players.add_xp(actor, 15, 'Hacking')
    return result
