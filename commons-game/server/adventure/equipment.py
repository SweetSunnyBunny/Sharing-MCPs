
"""Shared item classification for the bag, made things and worn equipment.

Names fill the older catalogs' missing slots; an authored specific slot wins.
Legacy armor/accessory slots are refined only for recognisable body pieces.
"""
from functools import lru_cache
import json
from pathlib import Path
import re

ENGINE = Path(__file__).parent / 'engine'
EQUIPMENT_SLOTS = {
    'weapon': 'Weapon', 'armor': 'Torso', 'head': 'Head', 'hands': 'Hands',
    'boots': 'Feet', 'cloak': 'Cloak', 'belt': 'Belt', 'charm': 'Charm',
    'accessory': 'Accessory',
}
SLOT_ALIASES = {'torso': 'armor', 'body': 'armor', 'feet': 'boots',
                'gloves': 'hands', 'helmet': 'head', 'neck': 'accessory'}
CONSUMABLE_TYPES = frozenset(('food', 'companion_treat', 'potion', 'medicine',
                             'consumable', 'ammunition', 'material', 'decoration'))

def item_name(item):
    return str(item.get('name') or item.get('what') or item.get('item') or '')

def fishing_tool(item):
    name = item_name(item).lower()
    benefit = item.get('benefit') or {}
    return ('fishing' in name and ('rod' in name or 'pole' in name)) or (
        benefit.get('type') == 'unlock_activity' and benefit.get('value') == 'fishing')

def gathering_tool(item):
    return item.get('category') == 'tools' or item_name(item).casefold() in (
        'gathering basket', 'hatchet', 'pickaxe', 'shovel', 'gold pan', 'hammer')

def item_slot(item):
    """The wearable slot, or None for consumables, materials and carried tools."""
    name = item_name(item).lower()
    kind = item.get('type', '')
    if fishing_tool(item) or kind in CONSUMABLE_TYPES or item.get('uses_total') or item.get('uses'):
        return None
    explicit = SLOT_ALIASES.get(item.get('slot'), item.get('slot'))
    if item.get('passive') or kind == 'passive' or (
            kind == 'gear' and 'slot' in item and item['slot'] is None):
        return None
    if explicit in EQUIPMENT_SLOTS and explicit not in ('armor', 'accessory'):
        return explicit

    for pattern, slot in (
        (r'\b(cloak|cape|poncho)\b', 'cloak'),
        (r'\b(boots?|shoes?|slippers?|greaves)\b', 'boots'),
        (r'\b(helmet|helm|hat|hood|cap)\b', 'head'),
        (r'\b(gloves?|gauntlets?|bracers?)\b', 'hands'),
        (r'\b(belt|sash)\b', 'belt'),
        (r'\b(charm|amulet|talisman)\b', 'charm'),
        (r'\b(armor|armour|breastplate|cuirass|vest|hauberk|mail)\b', 'armor'),
        (r'\b(sword|longsword|shortsword|longbow|shortbow|bow|blade|dagger|knife|axe|hatchet|spear|rifle|pistol|revolver|shotgun|blaster|weapon|staff)\b', 'weapon'),
        (r'\b(necklace|ring|badge|brooch|backpack|satchel|quiver|pack|bandana|shield)\b', 'accessory'),
    ):
        if re.search(pattern, name):
            return slot
    if explicit in EQUIPMENT_SLOTS:
        return explicit
    if kind in ('armor', 'armour'):
        return 'armor'
    if kind == 'weapon':
        return 'weapon'
    if kind in ('accessory', 'gear', 'equipment'):
        return 'accessory'
    return None

def item_broken(item):
    return isinstance(item.get('durability'), (int, float)) and item['durability'] <= 0

def legacy_slot(item, old_slot):
    slot = item_slot(item)
    if slot:
        return slot
    kind = item.get('type')
    if (fishing_tool(item) or kind in CONSUMABLE_TYPES or item.get('uses_total')
            or item.get('uses') or item.get('passive') or kind == 'passive'
            or (kind == 'gear' and 'slot' in item and item['slot'] is None)):
        return None

    return SLOT_ALIASES.get(old_slot, old_slot)

def item_bonus_values(item):
    values = dict(item.get('bonuses') or {})
    benefit = item.get('benefit') or {}
    if benefit.get('type') and benefit['type'] not in values:
        values[benefit['type']] = benefit.get('value', 0)
    
    stat = item.get('stat_bonus')
    if isinstance(stat, str):
        match = re.fullmatch(r'\+?(\d+)\s+(attack|damage|defense|hunting|gathering|stealth|crafting|fishing)', stat.strip(), re.I)
        if match:
            values.setdefault(match[2].lower(), int(match[1]))
    return values

@lru_cache(maxsize=1)
def equipment_catalog():
    """Static definitions from all live regional recipes, shops and rewards."""
    found = {}

    def add(name, spec):
        if name:
            key = str(name).casefold()
            found[key] = {**found.get(key, {}), **spec, 'name': str(name)}

    def visit_shop(value, category=''):
        if not isinstance(value, dict):
            return
        for name, row in value.items():
            if not isinstance(row, dict):
                continue
            if 'price' in row:
                add(name, dict(row, category=category))
            else:
                visit_shop(row, name)

    for path in (ENGINE / 'npcs').glob('*/inventory.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        visit_shop(data.get('inventory', {}))
        for row in data.get('items', []):
            add(row.get('name'), row)
        for name, spec in data.get('crafting', {}).get('recipes', {}).items():
            add(name, spec)
        for group in data.get('services', {}).values():
            for spec in group.get('repairs', group.get('upgrades', {})).values():
                add(spec.get('result'), spec)
    for path in ENGINE.glob('crafting_recipes*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        for spec in data.get('recipes', data).values():
            if isinstance(spec, dict) and spec.get('name'):
                add(spec['name'], spec)
    rewards = json.loads((ENGINE / 'quest_reward_items.json').read_text(encoding='utf-8'))
    for region in rewards.get('unique_items', {}).values():
        for spec in region.values():
            add(spec.get('name'), spec)
    weapons = json.loads((ENGINE / 'weapons_config.json').read_text(encoding='utf-8'))
    for weapon_id, spec in weapons.get('weapons', {}).items():
        if weapon_id != 'fists':
            add(spec['name'], dict(spec, slot='weapon', weapon_id=weapon_id))
    usable = json.loads((ENGINE / 'usable_items.json').read_text(encoding='utf-8'))
    for group in usable.get('usable_items', {}).values():
        for name, spec in group.items():
            current = found.get(name.casefold(), {'name': name})
            if (item_slot(current) == 'charm' and spec.get('effect') == 'buff'
                    and isinstance(spec.get('buff_value'), (int, float))
                    and not isinstance(spec['buff_value'], bool)):

                bonuses = dict(current.get('bonuses') or {})
                bonuses.setdefault(spec['buff'], spec['buff_value'])
                add(name, {'bonuses': bonuses})
    return found

def definition(item):
    return {**equipment_catalog().get(item_name(item).casefold(), {}), **item}
