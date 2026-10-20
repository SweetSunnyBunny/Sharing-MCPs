
"""Resolve item uses from the same food, recipe and shop catalogs as the game."""
from copy import deepcopy
import json
from pathlib import Path
import re

def normal(name):
    return re.sub(r'[^a-z0-9]', '', str(name or '').lower())

class Consumables:
    def __init__(self, engine, root, recipes):
        self.specs = {}
        self.home = json.loads((Path(root)/'world/recipes.json').read_text(encoding='utf-8'))['recipes']
        data = json.loads((Path(engine)/'usable_items.json').read_text(encoding='utf-8'))
        for group, items in data['usable_items'].items():
            for name, spec in items.items():
                spec = dict(spec, kind=group)
                if group=='taming' and name in ('Raw Meat','Honey Cake'):spec['edible']=True
                
                if group == 'food' and not (spec.get('heal') or spec.get('effect') == 'heal'):
                    spec['heal'] = 10
                self.specs[normal(name)] = spec
        for recipe in recipes.values():
            name = recipe.get('name', '')
            kind = recipe.get('type')
            benefit = recipe.get('benefit', {})
            if kind == 'companion_treat':
                self.specs[normal(name)] = {'effect': 'feed', 'value': benefit.get('value', 5), 'kind': kind}
            elif kind in ('food', 'potion') and normal(name) not in self.specs:
                effect = benefit.get('type', 'heal')
                self.specs[normal(name)] = {
                    'effect': 'heal' if effect == 'healing' else effect,
                    'value': benefit.get('value', 10), 'kind': kind,
                    'heal': benefit.get('value', 10) if effect == 'healing' else (10 if kind == 'food' else 0),
                    'description': recipe.get('description', ''),
                }
        for path in (Path(engine)/'npcs').glob('*/inventory.json'):
            data = json.loads(path.read_text(encoding='utf-8'))
            def visit(node, category=''):
                if isinstance(node, list):
                    for row in node:
                        if isinstance(row, dict): visit({row.get('name', ''): row}, row.get('type', category))
                elif isinstance(node, dict):
                    for name, row in node.items():
                        if not isinstance(row, dict): continue
                        if 'price' not in row:
                            visit(row, name)
                            continue
                        if normal(name) in self.specs: continue
                        effect = str(row.get('effect', ''))
                        match = re.match(r'heal_(\d+)', effect)
                        if match:
                            self.specs[normal(name)] = dict(row, effect='heal', value=int(match[1]), kind='healing')
                        elif effect == 'cure_poison':
                            self.specs[normal(name)] = dict(row, kind='potion')
                        elif category in ('food', 'foods', 'drink', 'drinks'):
                            self.specs[normal(name)] = dict(row, effect='heal', value=10 if 'food' in category else 5, kind=category)
            visit(data.get('inventory', {}))
            visit(data.get('items', []))
        aliases = {
            'Health Potion (1d4)': {'effect': 'heal', 'dice': '1d4', 'kind': 'healing'},
            'Greater Health Potion (2d4+2)': {'effect': 'heal', 'dice': '2d4+2', 'kind': 'healing'},
            'Potion of Minor Healing': {'effect': 'heal', 'value': 25, 'kind': 'healing'},
            'Healing Salve': {'effect': 'heal', 'dice': '1d4', 'kind': 'healing'},
            'Stamina Potion': {'effect': 'refresh', 'kind': 'potion'},
            'Elixir of Focus': {'effect': 'buff', 'buff': 'all_rolls', 'buff_value': 1, 'duration': 60, 'kind': 'potion'},
            'Potion of Invisibility (Short)': {'effect': 'buff', 'buff': 'stealth', 'buff_value': 3, 'duration': 1, 'kind': 'potion'},
        }
        for name, spec in aliases.items(): self.specs.setdefault(normal(name), spec)
        for name in ('berries','animal treats'):
            self.specs.setdefault(normal(name), {'effect':'feed','value':5,'kind':'companion_treat'})

        for filename,group in (('crops.json','crops'),('forage.json','finds')):
            path=Path(root)/'world'/filename
            if not path.exists():continue
            for name,row in json.loads(path.read_text(encoding='utf-8')).get(group,{}).items():
                if row.get('yields') in ('berries','fruit','roots','vegetables','nuts','grains'):
                    self.specs.setdefault(normal(name),{'effect':'heal','value':5,'kind':'food'})
        self.specs[normal('berries')]={'effect':'heal','value':5,'kind':'food'}

    def resolve(self, name, item=None):
        item = item or {}
        recipe = self.home.get(item.get('recipe'), {})
        spec = deepcopy(self.specs.get(normal(name)))
        if spec is None and recipe.get('type') == 'food':
            spec = {'effect': 'heal', 'value': recipe.get('heal', 10), 'kind': 'food'}
        if spec is None and item.get('type') == 'food':
            spec = {'effect': 'heal', 'value': item.get('benefit', {}).get('value', 10), 'kind': 'food'}
        if spec is None and (recipe.get('uses') or item.get('uses_total')):
            spec = {'effect': 'utility', 'kind': 'utility'}
        if spec:
            total=item.get('uses_total',recipe.get('uses',item.get('servings',spec.get('servings',spec.get('uses',1)))))
            if total and spec.get('kind') not in ('charms','charm'):
                spec['servings']=max(1,min(1000,int(total)))
                spec['remaining']=max(0,min(spec['servings'],int(item.get('uses_left',spec['servings']))))
        return spec

    @staticmethod
    def feedable(spec):
        return bool(spec and (spec.get('kind') in ('companion_treat','food','foods') or spec.get('edible')))
