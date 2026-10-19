
"""Durable objects and carried-material repair plans, without save mutations."""
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from .equipment import definition, fishing_tool, gathering_tool, item_name, item_slot

@lru_cache(maxsize=1)
def shiny_names():
    path = Path(__file__).resolve().parents[2] / 'world/shinies.json'
    if not path.exists():
        return frozenset()
    rows = json.loads(path.read_text(encoding='utf-8')).get('shinies', {})
    return frozenset(name.casefold() for name, row in rows.items()
                     if row.get('yields') == 'shiny')

def durable_item(item):
    spec = definition(item)
    if item_name(spec).casefold() in shiny_names():
        return False
    if spec.get('type') in ('food', 'medicine', 'potion', 'companion_treat',
                            'consumable', 'ammunition', 'material', 'decoration'):
        return False
    if spec.get('uses_total') or spec.get('uses'):
        return False
    return bool(item_slot(spec) or fishing_tool(spec) or gathering_tool(spec) or
                spec.get('type') in ('tool', 'companion') or
                ('durability' in item and 'max_durability' in item))

def ensure_condition(item):
    """Fill missing condition only; an existing zero stays broken."""
    if not durable_item(item):
        return item
    spec = definition(item)
    slot = item_slot(spec)
    default = (40 if spec.get('type') == 'companion' else
               25 if fishing_tool(spec) else
               {'weapon': 60, 'armor': 80}.get(slot, 40))
    authored_max = spec.get('max_durability', spec.get('durability', default))
    maximum = item.get('max_durability', authored_max)
    if not isinstance(maximum, (int, float)) or isinstance(maximum, bool) or not math.isfinite(maximum) or maximum <= 0:
        maximum = default
    item['max_durability'] = maximum
    current = item.get('durability', spec.get('durability', maximum))
    if not isinstance(current, (int, float)) or isinstance(current, bool) or not math.isfinite(current):
        current = maximum
    item['durability'] = min(maximum, max(0, current))
    return item

def material_kind(name):
    normal = re.sub(r'[_-]', ' ', name.casefold())
    if name.casefold() in shiny_names() or re.search(r'\b(shin(?:y|ies)|component|components|circuit|servo|chip|wiring|wire|cell|scrap|plating|mechanical|electronic|weapon system)\b', normal):
        return 'components'
    if re.search(r'\b(cloth|fabric|fiber|fibre|linen|thread|wool|cotton|silk|hide|hides|pelt|pelts|leather|skin|skins|fur)\b', normal):
        return 'cloth or animal skins'
    if re.search(r'\b(wood|firewood|branches?|sticks?|timber|logs?)\b', normal):
        return 'wood'
    if re.search(r'\b(metal|iron|steel|copper|ore|ingots?)\b', normal):
        return 'metal'
    return None

def repair_kind(spec):
    name = item_name(spec).lower()
    if spec.get('type') == 'companion' or re.search(r'\b(droid|robot|bot|drone|exo|exosuit)\b', name):
        return 'components'
    slot = item_slot(spec)
    if fishing_tool(spec) or ('bow' in name and slot == 'weapon'):
        return 'wood'
    if slot in ('cloak', 'head', 'hands', 'boots', 'belt') or re.search(r'\b(leather|hide|cloth|pelt|fur|weave|suit|vest)\b', name):
        return 'cloth or animal skins'
    if slot in ('charm', 'accessory'):
        return 'components'
    return 'metal'

def inventory_counts(inventory):
    counts = Counter()
    for item in inventory:
        name = item_name(item) if isinstance(item, dict) else str(item)
        qty = int(item.get('quantity', 1)) if isinstance(item, dict) else 1
        counts[name] += max(0, qty)
    return counts

def repair_plan(item, inventory, recipe=None, factor=.25, reduction=0):
    """Allocate exact materials first, then compatible raw carried substitutes.

    Recipe ingredient counts remain proportional to damage. Flexible materials
    cannot consume another wearable or a robot body as scrap.
    """
    current, maximum = item.get('durability'), item.get('max_durability')
    kind = repair_kind(definition(item))
    empty = {'available': False, 'reason': '', 'materials': [], 'material_kind': kind, 'spent': {}}
    if current is None or not maximum:
        return dict(empty, reason='This item does not need durability repairs.')
    if item.get('placed'):
        return dict(empty, reason='Pick this item up before repairing it.')
    if current >= maximum:
        return dict(empty, reason='Already at full durability.')
    ratio = (maximum - current) / maximum
    ingredients = (recipe or {}).get('materials') or {}
    requirements = {name: max(1, int(max(1, int(amount * factor * ratio)) * (1-reduction)))
                    for name, amount in ingredients.items() if isinstance(amount, (int, float)) and amount > 0}
    if not requirements:
        requirements = {kind: max(1, int(math.ceil((maximum-current)/20) * (1-reduction)))}
    remaining = inventory_counts(inventory)
    raw = {name for name in remaining if not durable_item({'name': name})}
    spent = Counter()
    rows = []
    for required_name, amount in requirements.items():
        category = material_kind(required_name) or (kind if not ingredients else None)
        candidates = [name for name in remaining if name.casefold() == required_name.casefold()]

        if category:
            candidates += [name for name in remaining if name in raw and name not in candidates and
                           (material_kind(name) == category or
                            category == 'metal' and material_kind(name) == 'components')]
        owned = sum(remaining[name] for name in candidates)
        needed = amount
        for name in candidates:
            take = min(needed, remaining[name])
            remaining[name] -= take
            spent[name] += take
            needed -= take
        rows.append({'name': required_name, 'required': amount, 'owned': owned})
    missing = [row for row in rows if row['owned'] < row['required']]
    reason = ('Missing repair materials: ' + ', '.join(
        row['name']+' (need '+str(row['required'])+', have '+str(row['owned'])+')' for row in missing)) if missing else ''
    return {'available': not missing, 'reason': reason, 'materials': rows, 'material_kind': kind,
            'spent': {name: qty for name, qty in spent.items() if qty}}
