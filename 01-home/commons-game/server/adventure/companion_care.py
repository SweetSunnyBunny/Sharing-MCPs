
"""Protected companion health and care, using one owned robot body when present.

These helpers mutate the caller's owned record and inventory only. The game
request boundary commits both, including its existing retry receipt.
"""
from functools import lru_cache
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import uuid

ROBOT_IDS = frozenset(('maintenance_droid', 'scrap_spider', 'service_droid',
                      'combat_droid', 'repair_bot', 'medical_drone'))

def _normal(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())

def _number(value, fallback, minimum=0, maximum=1000):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        return fallback
    return max(minimum, min(maximum, value))

def _maximum(value, fallback):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 1):
        return fallback
    return min(1000, value)

def is_robot(companion, definition=None, body=None):
    spec = definition or {}
    care_type = spec.get('care_type', companion.get('care_type'))
    if care_type:
        return care_type == 'robot'
    ident = str(spec.get('id', companion.get('id', ''))).lower()
    return (ident in ROBOT_IDS or bool(companion.get('source_made_id'))
            or bool(spec.get('is_robot', companion.get('is_robot')))
            or bool(body and body.get('type') == 'companion')
            or 'U0001f916' in str(spec.get('emoji', companion.get('emoji', ''))))

def ensure_health(companion, definition=None, body=None):
    """Migrate an old companion gently; a crafted robot's body is authoritative."""
    spec = definition or {}
    robot = is_robot(companion, spec, body)
    maximum = int(_maximum(spec.get('max_hp', companion.get('max_hp')),
                           40 if robot else 30))
    hp = int(_number(companion.get('hp'), maximum, maximum=maximum))
    companion.update(hp=hp, max_hp=maximum, is_robot=robot,
                     care_type='robot' if robot else 'animal')
    if robot and body is not None:
        body_max = _maximum(body.get('max_durability'), 40)
        
        durability = _number(body.get('durability'), body_max * hp / maximum,
                             maximum=body_max)
        body.update(max_durability=body_max, durability=round(durability, 6))
        companion['hp'] = min(maximum, max(0, round(maximum * durability / body_max)))
    return health_status(companion)

def health_status(companion, definition=None, body=None):
    if definition is not None or body is not None or 'hp' not in companion or 'max_hp' not in companion:
        return ensure_health(companion, definition, body)
    robot = is_robot(companion)
    maximum = int(_maximum(companion.get('max_hp'), 40 if robot else 30))
    hp = int(_number(companion.get('hp'), maximum, maximum=maximum))
    return {'hp': hp, 'max_hp': maximum, 'is_robot': robot,
            'care_type': 'robot' if robot else 'animal', 'hurt': hp < maximum,
            'broken': hp <= 0, 'can_assist': hp > 0}

def _set_hp(companion, hp, body=None):
    companion['hp'] = max(0, min(companion['max_hp'], int(hp)))
    if companion['is_robot'] and body is not None:
        body['durability'] = round(body['max_durability'] * companion['hp'] / companion['max_hp'], 6)

def _name(companion):
    return companion.get('custom_name') or companion.get('name') or 'Your companion'

def identity(companion):
    """Public, per-copy identity for a witnessed care action."""
    ident=companion.get('instance_id') or companion.get('_house',{}).get('id')
    return {'id':ident,'instance_id':ident,'name':_name(companion),
            'family_body':companion.get('family_body') or companion.get('_house',{}).get('family_body')}

def recover_pet_interaction(companion):
    """[commons:companions] Recover a witnessed older pat, never invent a new one.

    Before the hydration fix, a saved pet event survived while its interaction
    date reverted to the adoption date. Epoch events compare absolutely;
    legacy naive ISO dates keep the old engine's local-time semantics.
    """
    event=companion.get('last_pet')
    timestamp=event.get('time') if isinstance(event,dict) else None
    if (isinstance(timestamp,bool) or not isinstance(timestamp,(int,float))
            or timestamp<0 or timestamp>time.time() or not math.isfinite(timestamp)):
        return False
    try:observed=datetime.fromtimestamp(timestamp,timezone.utc)
    except (ValueError,OverflowError,OSError):return False
    current=companion.get('last_interaction')
    if isinstance(current,str):
        try:
            if datetime.fromisoformat(current).timestamp()>=timestamp:return False
        except (ValueError,OverflowError,OSError):pass
    companion['last_interaction']=observed.isoformat()
    return True

def pet(companion):
    """[commons:companions] One heart per pat; loyalty grows once per UTC day.

    The request transaction persists this event and its receipt together. A
    retry reuses both, while repeated real pats remain affectionate without
    repeatedly increasing the companion's gameplay contribution.
    """
    now=time.time();stamp=datetime.fromtimestamp(now,timezone.utc)
    event={'id':uuid.uuid4().hex,'time':now}
    companion['last_pet']=event
    companion['last_interaction']=stamp.isoformat()
    count=min(10**9,int(_number(companion.get('affection_count'),0,maximum=10**9))+1)
    companion['affection_count']=count
    loyalty=_number(companion.get('loyalty'),50,maximum=100)
    gain=1 if companion.get('last_affection_day')!=stamp.date().isoformat() and loyalty<100 else 0
    companion['loyalty']=min(100,loyalty+gain)
    companion['last_affection_day']=stamp.date().isoformat()
    reception=_pet_reception(companion)
    return {'success':True,'name':_name(companion),'companion':identity(companion),
            'pet_event':event,'hearts':1,'affection_count':count,'loyalty_gained':gain,
            'reception':reception,'last_interaction':companion['last_interaction'],
            'message':f"You give {_name(companion)} a gentle pat. ♥ {reception}"}

def _pet_reception(companion):
    """Small, species-appropriate acknowledgment, with personality as fallback."""
    name=_name(companion);species=_normal(companion.get('id'))
    if is_robot(companion):return f"{name}'s lights glow softly in acknowledgment."
    lines={'wolf':'leans gently into your hand, tail giving a small sweep.',
        'cat':'leans against your hand with a soft purr.',
        'rabbit':'twitches their nose and settles beside you.',
        'owl':'blinks slowly, feathers settling comfortably.',
        'hawk':'settles their wings and watches you calmly.',
        'raven':'tilts their head and offers a quiet, companionable croak.',
        'hedgehog':'uncurls a little and sniffs your hand.',
        'squirrel':'pauses beside you, whiskers twitching curiously.',
        'badger':'nudges your hand and settles close.'}
    if species in lines:return name+' '+lines[species]
    personality=_normal(companion.get('personality'))
    if personality in ('curious','playful'):return f"{name} tilts their head and comes a little closer."
    if personality in ('proud','independent','aloof'):return f"{name} gives you a quiet, watchful look and stays close."
    return f"{name} relaxes beside you, enjoying the gentle attention."

def injure(companion, amount, definition=None, body=None):
    """Leave a hurt follower owned and recoverable, even at zero HP."""
    before = ensure_health(companion, definition, body)
    damage = min(before['hp'], math.ceil(_number(amount, 0)))
    _set_hp(companion, before['hp'] - damage, body)
    after = health_status(companion)
    message = f"{_name(companion)} took {damage} damage. ({after['hp']}/{after['max_hp']} HP)"
    if after['broken']:
        message += ' Needs repairs before helping again.' if after['is_robot'] else ' Needs food and care before helping again.'
    return dict(after, damage=damage, name=_name(companion), message=message)

def feed(companion, heal_amount=10, definition=None, body=None):
    before = ensure_health(companion, definition, body)
    if before['is_robot']:
        return {'success': False, **before,
                'message': 'Repair this robot with carried shiny bits or mechanical parts.'}
    amount = int(_number(heal_amount, 10))
    _set_hp(companion, before['hp'] + amount)
    after = health_status(companion)
    healed = after['hp'] - before['hp']
    return dict(after, success=True, healed=healed, name=_name(companion),
                message=f"{_name(companion)} enjoyed a serving and recovered {healed} HP. ({after['hp']}/{after['max_hp']} HP)")

@lru_cache(maxsize=1)
def repair_material_catalog():
    """Only real salvage definitions and the Home shiny category are accepted."""
    groups = {
        8: ('shiny', 'Shiny Bits'),
        12: ('Scrap Metal', 'Droid Component', 'Metal Plating',
             'Servo Motor', 'Salvage Wiring'),
        16: ('Circuit Board', 'Circuit Boards'),
    }
    catalog = {_normal(name): value for value, names in groups.items() for name in names}
    path = Path(__file__).resolve().parents[2] / 'world' / 'shinies.json'
    if path.exists():
        for name, row in json.loads(path.read_text(encoding='utf-8')).get('shinies', {}).items():
            if row.get('yields') == 'shiny':
                catalog[_normal(name)] = 8
    return catalog

def _inventory_name(item):
    return str(item.get('name') or item.get('what') or '') if isinstance(item, dict) else str(item)

def _inventory_quantity(item):
    value = item.get('quantity', 1) if isinstance(item, dict) else 1
    return int(_number(value, 0, maximum=1000000))

def repair_materials(inventory):
    """Expose exact carried names, quantities and healing for care controls."""
    catalog = repair_material_catalog()
    choices = {}
    for item in inventory:
        name = _inventory_name(item)
        value = catalog.get(_normal(name))
        count = _inventory_quantity(item)
        if value and count:
            key = _normal(name)
            row = choices.setdefault(key, {'name': name, 'quantity': 0, 'heal_per_unit': value})
            row['quantity'] += count
    return list(choices.values())

def repair(companion, inventory, material=None, quantity=None, definition=None, body=None):
    """Spend one salvage unit by default; partial repairs never require a rebuild."""
    before = ensure_health(companion, definition, body)
    if not before['is_robot']:
        return {'success': False, **before, 'message': 'Feed this companion food to heal it.'}
    if not before['hurt']:
        return {'success': False, **before, 'message': _name(companion) + ' is already at full health.'}
    if quantity is None:
        quantity = 1
    elif isinstance(quantity, str) and re.fullmatch(r'[1-9][0-9]*', quantity):
        quantity = int(quantity)
    if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
            or not math.isfinite(quantity) or int(quantity) != quantity or not 1 <= quantity <= 10):
        return {'success': False, **before, 'message': 'Repair with between 1 and 10 material units.'}
    choices = repair_materials(inventory)
    wanted = _normal(material) if material is not None else None
    choice = next((row for row in choices if wanted is None or _normal(row['name']) == wanted), None)
    if not choice:
        return {'success': False, **before,
                'message': 'Carry shiny bits, scrap metal, circuits or mechanical parts to repair this robot.'}
    requested = min(int(quantity), math.ceil((before['max_hp'] - before['hp']) / choice['heal_per_unit']))
    if choice['quantity'] < requested:
        return {'success': False, **before, 'message': f"Need {requested} {choice['name']} to make that repair."}
    left = requested
    spent = {}
    for index in range(len(inventory) - 1, -1, -1):
        item = inventory[index]
        if _normal(_inventory_name(item)) != _normal(choice['name']):
            continue
        count = _inventory_quantity(item)
        used = min(count, left)
        if not used:
            continue
        name = _inventory_name(item)
        spent[name] = spent.get(name, 0) + used
        if used == count:
            inventory.pop(index)
        else:
            item['quantity'] = count - used
        left -= used
        if left == 0:
            break
    _set_hp(companion, before['hp'] + requested * choice['heal_per_unit'], body)
    after = health_status(companion)
    healed = after['hp'] - before['hp']
    return dict(after, success=True, healed=healed, name=_name(companion),
                materials_spent=spent,
                message=f"Repaired {_name(companion)} for {healed} HP. ({after['hp']}/{after['max_hp']} HP)")
