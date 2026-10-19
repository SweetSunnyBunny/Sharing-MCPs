
"""Storage adapter for the imported mechanics; one player, wallet and basket."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import uuid
import random
import hashlib
import math
import re
from datetime import datetime

from .engine.multi_world_player_manager import MultiWorldPlayerManager
from . import companion_care
from .equipment import (EQUIPMENT_SLOTS, SLOT_ALIASES, definition,
                        fishing_tool, gathering_tool, item_broken, item_bonus_values,
                        item_name as item_label, item_slot, legacy_slot)
from .item_condition import durable_item, ensure_condition, inventory_counts, repair_plan

ENGINE = Path(__file__).parent / 'engine'
SHARED = frozenset(('hp', 'max_hp', 'inventory', 'perishable_inventory',
    'crafted_items', 'equipped', 'equipment', 'equipped_weapon', 'active_buffs', 'item_conditions'))

class WorldState(dict):
    """Regional quest state with shared player attributes, without duplication."""
    def __init__(self, region, shared):
        super().__init__((k, v) for k, v in region.items() if k not in SHARED and k != 'currency')
        self.shared = shared

    def __getitem__(self, key):
        if key == 'currency':
            return self.shared['gold_cents'] / 100
        if key in SHARED:
            return self.shared[key]
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key == 'currency':
            self.shared['gold_cents'] = int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        elif key in SHARED:
            self.shared[key] = value
        else:
            super().__setitem__(key, value)

    def __contains__(self, key):
        return key == 'currency' or (key in SHARED and key in self.shared) or super().__contains__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def update(self, *args, **kwargs):
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

class Players(MultiWorldPlayerManager):
    def __init__(self, store):
        self.store = store
        self.loaded = set()
        config = json.loads((ENGINE / 'worlds_config.json').read_text(encoding='utf-8'))
        config['worlds']['home'] = {'name': 'Commons', 'channels': [], 'npcs': []}
        for world in config['worlds'].values():
            world['currency'] = {'name': 'gold', 'symbol': 'g', 'starting_amount': 0, 'decimal': True}
        super().__init__(store.path.parent / 'unused-player-files', config)
        dusty_recipes=json.loads((ENGINE / 'crafting_recipes_dusty_trails.json').read_text(encoding='utf-8'))
        self.crafting_config['recipes'].update(dusty_recipes.get('recipes', dusty_recipes))
        native_path=self.store.world/'wilds.json'
        if not native_path.exists():native_path=Path(__file__).resolve().parents[2]/'world/wilds.json'
        self.native_wilds=json.loads(native_path.read_text(encoding='utf-8')) if native_path.exists() else {}
        native=self.native_wilds.get('animals',{})
        for ident,spec in native.items():
            current=self.companions_config['companions'].get(ident,{})
            bonuses={**current.get('bonuses',{}),**spec.get('bonuses',{})}
            self.companions_config['companions'][ident]={**spec,**current,'bonuses':bonuses,
                'name':current.get('name',spec.get('name',ident.replace('_',' ').title())),
                'emoji':current.get('emoji',spec.get('emoji','🐾')),
                'description':current.get('description',spec.get('line',''))}
        for ident,recipe in self.crafting_config['recipes'].items():
            if recipe.get('type')!='companion':continue
            authored=item_bonus_values(recipe)
            current=self.companions_config['companions'].get(ident,{})
            self.companions_config['companions'][ident]={
                'name':recipe['name'],'emoji':'🤖','description':recipe.get('description',''),
                'starting_loyalty':75,'personality':'loyal','acquisition':['craft'],
                **current,'bonuses':{**authored,**current.get('bonuses',{})}}

    def _load_players(self):
        with self.store.transaction() as db:
            return {key[7:]: json.loads(body) for key, body in db.execute("SELECT id,body FROM documents WHERE id LIKE 'player:%'")}

    def _load_single_player(self, player_id):
        if player_id in self.players:
            return self.players[player_id]
        return self.store.read('player:' + player_id)

    def _save_player(self, player_id):

        pass

    def get_player(self, player_id, player_name=None):
        player_id = str(player_id).strip().lower()
        player = super().get_player(player_id, player_name)
        if player_id in self.loaded:
            return player
        self.loaded.add(player_id)
        defaults = self._init_world_data('home')
        shared = player.setdefault('shared', {k: deepcopy(defaults.get(k)) for k in SHARED if k in defaults})
        shared.setdefault('gold_cents', 5000)
        shared.setdefault('equipment', {'weapon': None, 'armor': None, 'accessory': None})
        shared.setdefault('equipped_weapon', None)
        baskets = self.store.house_read(self.store.world / 'baskets.json', {})
        basket = baskets.setdefault(player_id, {'name': player_name or player_id, 'picked': {}, 'made': [], 'equipment': {}})
        shared['inventory'] = [name for name, count in basket.get('picked', {}).items() for _ in range(max(0, int(count)))]

        shared.setdefault('item_conditions', deepcopy(basket.get('inventory_item_conditions', {})))
        self._sync_item_conditions(shared)

        shared['crafted_items'] = []
        for made in basket.get('made', []):
            item = deepcopy(made)
            item.setdefault('name', item.get('what', 'Made thing'))
            item.setdefault('id', uuid.uuid4().hex[:12])
            ensure_condition(item)
            shared['crafted_items'].append(item)
        shared.setdefault('perishable_inventory', [])
        animals = self.store.house_read(self.store.world / 'companions.json', {'companions': []})
        owned = [a for a in animals.get('companions', []) if str(a.get('owner', '')).lower() == player_id]
        player['companions'] = []
        player['family_pets'] = []
        player['active_companion_index'] = None
        for a in owned:
            species=self._companion_id(a.get('animal'))
            game=deepcopy(a.get('game',{}))

            for field in ('hp','max_hp','is_robot','last_pet','last_interaction'):
                if field in a:game.setdefault(field,deepcopy(a[field]))
            spec = self._companion_details(dict(game,id=species))
            player['companions'].append(dict(spec, id=species, instance_id=a['id'],
                name=spec.get('name', a.get('animal', 'Animal')), custom_name=a.get('given_name'),
                loyalty=a.get('loyalty', 50), personality=a.get('personality', 'gentle'),
                adopted_at=a.get('tamed_at'), last_interaction=spec.get('last_interaction') or a.get('tamed_at'), _house=a))
            companion_care.recover_pet_interaction(player['companions'][-1])
            self.companion_health(player_id,player['companions'][-1])
            if a.get('following') and not a.get('family_body'):
                player['active_companion_index'] = len(player['companions']) - 1
        self._migrate_to_multi_companion(player,player_id)
        preferences=animals.get('companion_follow_preferences',{})
        if isinstance(preferences,dict) and player_id in preferences:
            player['companion_follow_choice']=preferences[player_id]
        self._ensure_family_companion(player,player_id)
        if 'companion_follow_choice' in player:
            chosen=player['companion_follow_choice']
            if chosen in {pet['instance_id'] for pet in player['family_pets']}:
                active=player.get('active_companion_index')
                chosen=(player['companions'][active]['instance_id']
                    if isinstance(active,int) and 0<=active<len(player['companions']) else None)
                player['companion_follow_choice']=chosen
            player['active_companion_index']=next((i for i,pet in enumerate(player['companions'])
                if chosen is not None and pet.get('instance_id')==chosen),None)
        player['companion_slots'] = max(10, len(player['companions']) + 1)
        for region in config_worlds(self):
            player['worlds'][region] = WorldState(player['worlds'].get(region, self._init_world_data(region)), shared)
        self._basket_cache = getattr(self, '_basket_cache', {})
        self._basket_cache[player_id] = basket
        by_id={i['id']:i for i in shared['crafted_items']}
        previous_refs=shared.get('equipment',{})
        shared['equipped']={slot:None for slot in EQUIPMENT_SLOTS}
        shared['equipment']={}
        for old_slot, ident in basket.get('equipment',{}).items():
            item=by_id.get(ident)
            if not item:continue
            slot=legacy_slot(definition(item),old_slot)
            if slot in EQUIPMENT_SLOTS and not shared['equipped'].get(slot):
                shared['equipped'][slot]=item['name']
                shared['equipment'][slot]={'source':'made','id':ident}
        for old_slot,name in basket.get('inventory_equipment',{}).items():
            if name not in shared['inventory']:continue
            slot=legacy_slot(definition({'name':name}),old_slot)
            if slot in EQUIPMENT_SLOTS and not shared['equipped'].get(slot):
                shared['equipped'][slot]=name
                ref=basket.get('inventory_equipment_refs',{}).get(old_slot) or previous_refs.get(old_slot,{})
                shared['equipment'][slot]={'source':'inventory','name':name,
                    **({'id':ref['id']} if ref.get('id') else {})}

        weapon=shared.get('equipped_weapon')
        if not shared['equipped']['weapon'] and weapon and weapon!='fists':
            name=self.weapons_config['weapons'].get(weapon,{}).get('name')
            if name in shared['inventory']:
                shared['equipped']['weapon']=name
                shared['equipment']['weapon']={'source':'inventory','name':name}
        self._sync_weapon_choice(player_id)
        return player

    def flush(self):
        baskets = self.store.house_read(self.store.world / 'baskets.json', {})
        animals = self.store.house_read(self.store.world / 'companions.json', {'companions': []})
        for who in self.loaded:
            player = self.players[who]
            shared = player['shared']
            self._sync_item_conditions(shared)
            basket = self._basket_cache[who]
            counts = Counter()
            for item in shared['inventory']:
                if isinstance(item, dict):
                    counts[item.get('name', '')] += int(item.get('quantity', 1))
                else:
                    counts[str(item)] += 1
            basket['picked'] = {name: qty for name, qty in counts.items() if name and qty > 0}
            made = []
            for item in shared['crafted_items']:
                ensure_condition(item)
                item.setdefault('id', uuid.uuid4().hex[:12])
                item.setdefault('what', item.get('name', 'Made thing'))
                item.setdefault('made_by', player['name'])
                made.append(item)
            basket['made'] = made
            self._resolve_equipment(who)
            basket['equipment']={slot:ref['id'] for slot,ref in shared['equipment'].items()
                if ref.get('source')=='made'}
            basket['inventory_equipment']={slot:ref['name'] for slot,ref in shared['equipment'].items()
                if ref.get('source')=='inventory'}
            basket['inventory_equipment_refs']={slot:deepcopy(ref) for slot,ref in shared['equipment'].items()
                if ref.get('source')=='inventory'}
            basket['inventory_item_conditions']=deepcopy(shared['item_conditions'])
            baskets[who] = basket
            animals['companions'] = [a for a in animals.get('companions', []) if str(a.get('owner', '')).lower() != who]
            for i, a in enumerate(player.get('companions', [])+player.get('family_pets', [])):
                health=self.companion_health(who,a)
                row = deepcopy(a.get('_house', {}))
                row.update(id=a.get('instance_id') or uuid.uuid4().hex[:12], owner=who,
                    animal=a['id'], given_name=a.get('custom_name'), loyalty=a.get('loyalty', 50),
                    personality=a.get('personality', 'gentle'),
                    following=bool(a.get('_house',{}).get('family_body')) or i == player.get('active_companion_index'),
                    tamed_at=a.get('adopted_at'), room=a.get('tamed_location', row.get('room', 'woods')))
                row.update(hp=health['hp'],max_hp=health['max_hp'],is_robot=health['is_robot'])
                if a.get('last_pet'):row['last_pet']=deepcopy(a['last_pet'])
                row['game']={k:v for k,v in a.items() if k not in ('_house','instance_id')}
                animals['companions'].append(row)
            if 'companion_follow_choice' in player:
                animals.setdefault('companion_follow_preferences',{})[who]=player['companion_follow_choice']
            saved = deepcopy(player)
            saved['shared'].pop('inventory', None)
            saved['shared'].pop('crafted_items', None)
            saved.pop('companions', None)
            saved.pop('family_pets', None)
            self.store.write('player:' + who, saved)
        self.store.house_write(self.store.world / 'baskets.json', baskets)
        self.store.house_write(self.store.world / 'companions.json', animals)

    def check_loyalty_decay(self, player_id, is_bot=False):
        return []

    def _migrate_to_multi_companion(self,player,player_id=None):
        player.setdefault('companions',[])
        player.setdefault('family_pets',[])
        player.setdefault('active_companion_index',None)
        legacy=player.pop('companion',None)
        if legacy:
            name=legacy.get('name','Companion')
            ident=legacy.get('id') or next((k for k,v in self.companions_config['companions'].items() if v['name']==name),name.lower().replace(' ','_'))
            spec=self.companions_config['companions'].get(ident,{})
            player['companions'].append({**spec,**legacy,'id':ident})
            if player['active_companion_index'] is None:player['active_companion_index']=len(player['companions'])-1
        for pet in player['companions']+player['family_pets']:
            pet.setdefault('instance_id',pet.get('_house',{}).get('id') or uuid.uuid4().hex[:12])

    def _ensure_family_companion(self,player,player_id):
        """Family pets have care and identity, apart from selected gameplay helpers."""
        pets=player['companions'];active=player.get('active_companion_index')
        selected=pets[active] if isinstance(active,int) and not isinstance(active,bool) and 0<=active<len(pets) else None
        ordinary=[];family=list(player.get('family_pets',[]))
        for pet in pets:
            if pet.get('_house',{}).get('family_body'):family.append(pet)
            else:ordinary.append(pet)
        if not any(pet is selected for pet in ordinary):
            selected=next((pet for pet in reversed(ordinary) if pet.get('_house',{}).get('following')),None)
        player['companions']=ordinary
        player['active_companion_index']=next((i for i,pet in enumerate(ordinary) if pet is selected),None)
        player['family_pets']=family
        if 'companion_follow_choice' not in player and player['active_companion_index'] is not None:
            player['companion_follow_choice']=ordinary[player['active_companion_index']]['instance_id']

    def check_companion_flee_or_attack(self, player_id):
        return {'action': None}

    def _companion_id(self, value):
        normal=lambda text:re.sub(r'[^a-z0-9]','',str(text or '').lower())
        wanted=normal(value)
        for ident,spec in self.companions_config['companions'].items():
            names=[ident,spec.get('name'),*spec.get('aliases',[])]
            if wanted and any(normal(name)==wanted for name in names):return ident
        return value

    def _companion_details(self, companion):
        ident=self._companion_id(companion.get('id'))
        spec=self.companions_config['companions'].get(ident,{})
        result={**spec,**companion,'id':ident}
        result['bonuses']={**spec.get('bonuses',{}),**companion.get('bonuses',{})}
        result.setdefault('name',str(ident or 'Companion').replace('_',' ').title())
        result.setdefault('emoji','🐾')
        result.setdefault('description','')
        companion_care.ensure_health(result,spec)
        return result

    def companion_body(self, player_id, companion):
        source=companion.get('source_made_id')
        return next((item for item in self.get_player(player_id)['shared']['crafted_items']
                     if item.get('id')==source),None) if source else None

    def companion_health(self, player_id, companion):
        body=self.companion_body(player_id,companion)
        health=companion_care.ensure_health(companion,self.companions_config['companions'].get(companion.get('id'),{}),body)
        materials=companion_care.repair_materials(self.get_player(player_id)['shared']['inventory']) if health['is_robot'] else []
        reason=('This robot body is no longer in your bag.' if companion.get('source_made_id') and body is None else
                'Put this robot in your bag before repairing it.' if body and body.get('placed') else
                'Already at full health.' if not health['hurt'] else
                'Carry shiny bits or mechanical parts to repair this robot.' if not materials else None)
        return dict(health,repair_materials=materials,repair_available=bool(health['is_robot'] and not reason),repair_reason=reason)

    def care_companion(self, player_id, identifier=None):
        player=self.get_player(player_id)
        pets=player.get('companions',[])
        all_pets=pets+player.get('family_pets',[])
        if identifier is None or identifier=='':
            index=player.get('active_companion_index')
            if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(pets):
                raise ValueError('Choose a companion to care for.')
            pet=pets[index]
        else:
            pet=next((pet for pet in all_pets if str(pet.get('instance_id'))==str(identifier)),None)
            if pet is None and str(identifier).isdigit():
                index=int(identifier)-1
                pet=pets[index] if 0<=index<len(pets) else None
            if pet is None:
                wanted=str(identifier).strip().casefold()
                matches=[row for row in all_pets if wanted in (
                    str(row.get('custom_name') or '').casefold(),str(row.get('name') or '').casefold())]
                if len(matches)>1:raise ValueError('Several companions have that name. Choose their stable id.')
                pet=matches[0] if matches else None
            if pet is None:raise ValueError('Choose one of your own companions.')
        self.companion_health(player_id,pet)
        return pet

    def companion_release_info(self,player_id,pet):
        """Only animals that can be met again in the actual wild catalog leave."""
        if pet.get('_house',{}).get('family_body'):
            return {'available':False,'reason':'Family pets stay with their family.','befriend_locations':[]}
        if self.companion_health(player_id,pet)['is_robot']:
            return {'available':False,'reason':'Robots are repaired or carried, not released into the wild.','befriend_locations':[]}
        info=self.companions_config.get('taming_info',{}).get(pet['id'],{})

        regional_tame=bool(info.get('can_tame') and info.get('locations') and pet['id']!='fox')
        native=self.native_wilds.get('animals',{}).get(pet['id'],{})
        native_places=[place for place,animals in self.native_wilds.get('wilds',{}).items()
                       if native and pet['id'] in animals and native.get('can_tame',True)]
        available=bool(regional_tame or native_places)
        return {'available':available,'reason':None if available else 'This companion cannot currently be befriended again in the wild.',
                'befriend_locations':list(dict.fromkeys((info.get('locations',[]) if regional_tame else [])+native_places))}

    def release_companion(self,player_id,identifier):
        pet=self.care_companion(player_id,identifier)
        if not isinstance(identifier,str) or identifier!=pet.get('instance_id'):
            raise ValueError('Release an owned animal using its exact stable id.')
        info=self.companion_release_info(player_id,pet)
        if not info['available']:raise ValueError(info['reason'])
        player=self.get_player(player_id);pets=player['companions']
        index=next(i for i,row in enumerate(pets) if row is pet)
        active=player.get('active_companion_index')
        selected=active==index or player.get('companion_follow_choice')==pet['instance_id']
        pets.pop(index)
        if selected:
            player['active_companion_index']=None
            player['companion_follow_choice']=None
        elif isinstance(active,int) and active>index:player['active_companion_index']=active-1
        return {'success':True,'released':True,'companion':companion_care.identity(pet),'species':pet['id'],
                'was_active':selected,'befriend_locations':info['befriend_locations'],
                'message':f"{pet.get('custom_name') or pet.get('name')} returned to the wild. A future meeting begins a new bond."}

    def injure_following_companion(self, player_id, damage):
        if damage<=0:return None
        player=self.get_player(player_id)
        index=player.get('active_companion_index')
        if index is None:return None
        pet=player['companions'][index]
        if not self._companion_availability(player_id,pet)[0]:return None
        health=self.companion_health(player_id,pet)
        if not health['can_assist']:return None
        return companion_care.injure(pet,max(1,math.ceil(damage/3)),
            self.companions_config['companions'].get(pet['id'],{}),self.companion_body(player_id,pet))

    def get_active_companion(self, player_id):
        player=self.get_player(player_id)
        self._migrate_to_multi_companion(player,player_id)
        index=player.get('active_companion_index')
        companions=player.get('companions',[])
        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(companions):return None
        available,_=self._companion_availability(player_id,companions[index])
        if not available:
            player['active_companion_index']=None
            return None
        return self._companion_details(companions[index])

    def _companion_availability(self, player_id, companion):
        source=companion.get('source_made_id')
        if not source:return True,None
        body=next((item for item in self.get_player(player_id)['shared']['crafted_items'] if item.get('id')==source),None)
        if not body:return False,'This crafted companion body is no longer in your bag.'
        if body.get('placed'):return False,'Pick up this crafted companion before walking together.'
        if item_broken(body):return False,'Repair this crafted companion before walking together.'
        return True,None

    def get_all_companions(self, player_id):
        player=self.get_player(player_id)
        self._migrate_to_multi_companion(player,player_id)
        self.get_active_companion(player_id)
        return [dict(self._companion_details(companion),is_active=i==player.get('active_companion_index'))
            for i,companion in enumerate(player.get('companions',[]))]

    def _companion_bonus_values(self, companion):
        aliases={'attack_bonus':'attack','damage_bonus':'damage','hunting_bonus':'hunting',
            'hunt_bonus':'hunting','taming_bonus':'taming'}
        values={}
        for key,value in companion.get('bonuses',{}).items():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):continue
            canonical=aliases.get(key,key)
            if canonical not in values or key==canonical:values[canonical]=max(0,value)
        return values

    def get_companion_assistance(self, player_id, companion=None):
        selected=self.get_active_companion(player_id)
        preview=companion is not None
        companion=self._companion_details(companion) if preview else selected
        bonuses=self._companion_bonus_values(companion) if companion else {}
        available,unavailable_reason=self._companion_availability(player_id,companion) if companion else (False,None)
        health=self.companion_health(player_id,companion) if companion else None
        if health and not health['can_assist']:
            unavailable_reason='Needs repairs before helping again.' if health['is_robot'] else 'Needs food and care before helping again.'
            bonuses={key:0 for key in bonuses}
        active=bool(selected)
        if preview:
            if 'is_active' in companion:active=bool(companion['is_active'])
            elif companion.get('instance_id'):
                active=bool(selected and companion['instance_id']==selected.get('instance_id'))
            else:active=bool(selected and companion.get('id')==selected.get('id')
                and companion.get('custom_name')==selected.get('custom_name'))
        active=active and available
        return {**bonuses,'active':active,'available':available,'loyal':True,
            'name':(companion.get('custom_name') or companion.get('name')) if companion else None,
            'attack':bonuses.get('attack',0),'damage':bonuses.get('damage',0),
            'hunting':bonuses.get('hunting',0),'taming':bonuses.get('taming',0),
            'gathering':bonuses.get('gathering',0),'repair':bonuses.get('repair',0),
            'luck':bonuses.get('luck',0),'fishing':bonuses.get('fishing',0),
            'hacking':bonuses.get('hacking',0),'healing':bonuses.get('healing',0),
            'loyalty_requirement':companion.get('loyalty_requirement',0) if companion else 0,
            'reason':unavailable_reason or ('Following you' if active else 'Resting' if companion else 'No following companion')}

    def switch_active_companion(self, player_id, companion_identifier):
        player=self.get_player(player_id)
        companions=player.get('companions',[])
        try:pet=self.care_companion(player_id,companion_identifier)
        except ValueError as error:return {'success':False,'message':str(error)}
        if pet.get('_house',{}).get('family_body'):
            return {'success':False,'message':'Cloud is your pet and follows alongside your chosen companion.'}
        index=next(i for i,row in enumerate(companions) if row is pet)
        available,reason=self._companion_availability(player_id,companions[index])
        if not available:return {'success':False,'message':reason}
        player['active_companion_index']=index
        player['companion_follow_choice']=companions[index]['instance_id']
        self._save_player(player_id)
        companion=self.get_active_companion(player_id)
        return {'success':True,'companion':companion,'message':(companion.get('custom_name') or companion['name'])+' is following you.'}

    def rest_companion(self,player_id):
        player=self.get_player(player_id)
        active=player.get('active_companion_index')
        pet=player['companions'][active] if isinstance(active,int) and 0<=active<len(player['companions']) else None
        player['active_companion_index']=None
        player['companion_follow_choice']=None
        return {'success':True,'companion':companion_care.identity(pet) if pet else None,
                'message':'Your companion is resting.'}

    def get_companion_bonuses(self, player_id):
        companion=self.get_active_companion(player_id)

        health=self.companion_health(player_id,companion) if companion else None
        return {'bonuses':self._companion_bonus_values(companion) if companion and health['can_assist'] else {},
            'penalties':{},'is_loyal':True}

    def modify_companion_loyalty(self, player_id, amount, reason=''):
        result=super().modify_companion_loyalty(player_id,amount,reason)

        result['crossed_threshold']=False
        return result

    def activate_made_companion(self, player_id, item_id):
        source,item=self._owned_item(player_id,item_id=item_id)
        if source!='made':return {'success':False,'message':'Choose one of your crafted companions.'}
        recipe=self.crafting_config['recipes'].get(item.get('recipe_id'),definition(item))
        if recipe.get('type')!='companion' and item.get('type')!='companion':
            return {'success':False,'message':'This made thing is not a companion.'}
        if item.get('placed'):return {'success':False,'message':'Pick this companion up before activating it.'}
        if item_broken(item):return {'success':False,'message':'Repair this companion before activating it.'}
        player=self.get_player(player_id)
        instance='crafted-'+hashlib.sha256((str(player_id).lower()+':'+item['id']).encode()).hexdigest()[:16]
        index=next((i for i,pet in enumerate(player['companions'])
            if pet.get('source_made_id')==item['id'] or pet.get('instance_id')==instance),None)
        if index is None:

            ident=item.get('recipe_id') or next((key for key,value in self.companions_config['companions'].items()
                if value.get('name')==item['name']),None)
            if not ident:return {'success':False,'message':'This companion has no activation definition.'}
            spec=self.companions_config['companions'].get(ident,{})
            now=datetime.now().isoformat()
            player['companions'].append(self._companion_details(dict(spec,id=ident,instance_id=instance,
                source_made_id=item['id'],custom_name=None,loyalty=spec.get('starting_loyalty',75),
                adopted_at=now,last_interaction=now)))
            index=len(player['companions'])-1
        player['active_companion_index']=index
        player['companion_follow_choice']=player['companions'][index]['instance_id']
        self._save_player(player_id)
        companion=self.get_active_companion(player_id)
        return {'success':True,'companion':companion,'assistance':self.get_companion_assistance(player_id),
            'message':(companion.get('custom_name') or companion['name'])+' is following you.'}

    def companion_combat_recovery(self, player_id, world_id, encounter_id=None):
        companion=self.get_active_companion(player_id)
        healing=self.get_companion_bonuses(player_id)['bonuses'].get('healing',0)
        if not companion or healing<=0:return None
        player=self.get_player(player_id)
        receipts=player.setdefault('companion_recovery_encounters',[])
        if encounter_id and encounter_id in receipts:return None
        before=self.get_hp(player_id,world_id)
        if before['hp']>=before['max_hp']:return None
        healed=self.heal(player_id,world_id,int(healing),companion.get('custom_name') or companion['name'])
        if encounter_id:
            receipts.append(encounter_id)
            del receipts[:-50]
        self._save_player(player_id)
        return {**healed,'name':companion.get('custom_name') or companion['name'],
            'companion_id':companion['id'],'encounter_id':encounter_id}

    def _dungeon_encounter_id(self, player_id):
        party=self.get_party(player_id)
        dungeon=(party or {}).get('current_dungeon')
        if not dungeon:return None
        return 'dungeon:'+str(party.get('leader_id'))+':'+str(dungeon.get('started_at'))+':'+str(
            dungeon.get('room_index',0))+':'+str(dungeon.get('in_bonus_room',False))+':'+str(
            dungeon.get('active_room',{}).get('room_id'))

    def _apply_dungeon_damage(self, player_id, world_id, amount, source='unknown'):
        result=super()._apply_dungeon_damage(player_id,world_id,amount,source)
        result['item_wear']=self.wear_equipment(player_id,world_id,weapon=False,armor=True) if result['damage']>0 else []
        injury=self.injure_following_companion(player_id,result['damage'])
        if injury:result['companion_injury']=injury
        encounter=self._dungeon_encounter_id(player_id)
        if encounter:
            recovery=self.companion_combat_recovery(player_id,world_id,encounter)
            if recovery and recovery.get('healed'):
                result['companion_recovery']=recovery
                result['hp']=recovery['hp']
                result['knocked_out']=recovery['hp']<=0
                result['message']+=' '+recovery['name']+' restores '+str(recovery['healed'])+' HP.'
        return result

    def take_damage(self, player_id, world_id, amount, source='unknown'):
        result=super().take_damage(player_id,world_id,amount,source)
        if str(player_id) in getattr(self,'_hunting_actors',set()):
            result['item_wear']=self.wear_equipment(player_id,world_id,weapon=False,armor=True) if result['damage']>0 else []
            injury=self.injure_following_companion(player_id,result['damage'])
            if injury:
                result['companion_injury']=injury
                result['message']+=' '+injury['message']
            self._combat_care_events.setdefault(str(player_id),[]).append(result)
        return result

    def add_xp(self, player_id, amount, source='', apply_rp_bonus=True):
        p=self.get_player(player_id)
        old_hp,old_max=p['shared']['hp'],p['shared']['max_hp']
        result=super().add_xp(player_id,amount,source,apply_rp_bonus)

        bonus=sum(r.get('max_hp_bonus',0) for r in result.get('rewards',[]))
        p['shared']['max_hp']=old_max+bonus
        p['shared']['hp']=min(old_hp+bonus,old_max+bonus)
        return result

    def _sync_item_conditions(self, shared):
        """Keep one stable condition per carried durable copy, preserving wear."""
        conditions=shared.setdefault('item_conditions',{})
        counts=inventory_counts(shared.get('inventory',[]))
        refs=shared.get('equipment',{})
        worn={ref.get('id') for ref in refs.values() if isinstance(ref,dict)}
        for name,quantity in counts.items():
            if not durable_item({'name':name}):continue
            rows=[row for row in conditions.values() if row.get('name')==name]
            
            rows.sort(key=lambda row:row.get('id') not in worn)
            for row in rows[quantity:]:conditions.pop(row['id'],None)
            for _ in range(max(0,quantity-len(rows))):
                ident='bag-'+uuid.uuid4().hex[:12]
                conditions[ident]=ensure_condition({'id':ident,'name':name})
        for ident,row in list(conditions.items()):
            if not counts.get(row.get('name')) or not durable_item(row):
                conditions.pop(ident,None)
            else:
                row['id']=ident
                ensure_condition(row)

    def inventory_condition_items(self, player_id, name=None):
        shared=self.get_player(player_id)['shared']
        self._sync_item_conditions(shared)
        return [row for row in shared['item_conditions'].values()
            if name is None or row['name'].casefold()==str(name).casefold()]

    def _owned_item(self, player_id, name='', item_id=None):
        shared=self.get_player(player_id)['shared']
        self._sync_item_conditions(shared)
        if not item_id and any(str(i.get('id'))==str(name) for i in shared['crafted_items']):
            item_id=str(name)
        if not item_id and str(name) in shared.get('item_conditions',{}):
            item_id=str(name)
        if item_id:
            item=next((i for i in shared['crafted_items'] if i.get('id')==item_id),None)
            if item:return 'made',ensure_condition(item)
            item=shared['item_conditions'].get(item_id)
            return ('inventory',item) if item else (None,None)
        query=str(name).casefold()

        for ref in shared.get('equipment',{}).values():
            if ref.get('name','').casefold()==query and ref.get('id'):
                if ref.get('source')=='inventory':
                    item=shared['item_conditions'].get(ref['id'])
                else:item=next((i for i in shared['crafted_items'] if i.get('id')==ref['id']),None)
                if item:return ref['source'],ensure_condition(item)
                return None,None
        item=next((i for i in shared['crafted_items'] if i.get('name','').casefold()==query),None)
        if item:
            item.setdefault('id',uuid.uuid4().hex[:12])
            return 'made',ensure_condition(item)
        rows=self.inventory_condition_items(player_id,name)
        if rows:
            return 'inventory',max(rows,key=lambda row:row['durability'])
        carried=next((i for i in shared['inventory'] if
            (item_label(i) if isinstance(i,dict) else str(i)).casefold()==query),None)
        if carried is not None:
            return 'inventory',carried if isinstance(carried,dict) else {'name':carried}
        return None,None

    def item_capabilities(self, player_id, item_name='', item_id=None):
        source,item=self._owned_item(player_id,item_name,item_id)
        if not item:
            return {'slot':None,'equippable':False,'tool':None,'automatic':False,'broken':False,'kind':'item'}
        spec=definition(item)
        slot=item_slot(spec)
        broken=item_broken(item)
        rod=fishing_tool(spec)
        gathering=gathering_tool(spec)
        result={'slot':slot,'equippable':bool(slot and not broken and not item.get('placed')),
            'tool':'fishing' if rod else 'gathering' if gathering else None,
            'automatic':bool((rod or gathering) and not broken and not item.get('placed')),
            'broken':broken,'kind':spec.get('type') or ('weapon' if slot=='weapon' else 'gear' if slot else 'item')}
        if 'durability' in item:
            result['condition']={'current':item['durability'],'max':item['max_durability'],'broken':broken}
            result['repair']=self._repair_preview(player_id,item)
        return result

    def _resolve_equipment(self, player_id):
        """Resolve saved stable IDs, and accept legacy name-only slot writes."""
        shared=self.get_player(player_id)['shared']
        equipped=shared.setdefault('equipped',{})
        refs=shared.setdefault('equipment',{})
        resolved=[]
        for slot,name in list(equipped.items()):
            if not name:
                refs.pop(slot,None)
                continue
            ref=refs.get(slot) or {}
            source,item=None,None
            if ref.get('source')=='made':
                source,item=self._owned_item(player_id,item_id=ref.get('id'))
                if item and item.get('name')!=name:source,item=None,None
            elif ref.get('source')=='inventory':
                if ref.get('id'):
                    source,item=self._owned_item(player_id,item_id=ref['id'])
                    if item and item.get('name')!=name:source,item=None,None
                else:
                    rows=self.inventory_condition_items(player_id,name)
                    if rows:source,item='inventory',max(rows,key=lambda row:row['durability'])
                    elif name in shared['inventory']:source,item='inventory',{'name':name}
            if not item:

                if ref.get('id') and name==ref.get('name',name):
                    equipped[slot]=None;refs.pop(slot,None);continue
                source,item=self._owned_item(player_id,name)
            if not item or item.get('placed'):
                equipped[slot]=None;refs.pop(slot,None);continue
            canonical=legacy_slot(definition(item),slot)
            if canonical not in EQUIPMENT_SLOTS:
                equipped[slot]=None;refs.pop(slot,None);continue
            if canonical!=slot:
                if equipped.get(canonical):
                    equipped[slot]=None;refs.pop(slot,None);continue
                equipped[canonical]=name;equipped[slot]=None;refs.pop(slot,None)
            slot=canonical
            refs[slot]=({'source':'made','id':item['id'],'name':item['name']} if source=='made'
                        else {'source':'inventory','name':item_label(item),
                              **({'id':item['id']} if item.get('id') else {})})
            resolved.append((slot,definition(item),source))
        return resolved

    def _sync_weapon_choice(self, player_id):
        shared=self.get_player(player_id)['shared']
        weapon=next((i for slot,i,_ in self._resolve_equipment(player_id) if slot=='weapon'),None)
        shared['equipped_weapon']=(weapon.get('weapon_id') or 'made:'+str(weapon.get('id',weapon['name']))) if weapon and not item_broken(weapon) else 'fists'

    def equip_item(self, player_id, world_id, item_name, slot=None, item_id=None):
        source,item=self._owned_item(player_id,item_name,item_id)
        if not item:return {'success':False,'message':'You do not own that item.'}
        spec=definition(item)
        actual=item_slot(spec)
        if not actual:
            message='Fishing rods work automatically while carried.' if fishing_tool(spec) else 'This item belongs in your bag, not an equipment slot.'
            return {'success':False,'message':message}
        if item_broken(item):return {'success':False,'message':'Repair this broken item before equipping it.'}
        if item.get('placed'):return {'success':False,'message':'Pick this item up before equipping it.'}
        requested=SLOT_ALIASES.get(slot,slot)

        if requested and requested!=actual and not (requested in ('armor','accessory') and actual!='weapon'):
            return {'success':False,'message':'This item belongs in the '+actual+' slot.'}
        shared=self.get_player(player_id)['shared']
        self._resolve_equipment(player_id)
        old=shared['equipped'].get(actual)
        shared['equipped'][actual]=item_label(item)
        shared['equipment'][actual]=({'source':'made','id':item['id'],'name':item_label(item)} if source=='made'
                                    else {'source':'inventory','name':item_label(item),
                                          **({'id':item['id']} if item.get('id') else {})})
        self._sync_weapon_choice(player_id)
        self._save_player(player_id)
        return {'success':True,'slot':actual,'equipped':item_label(item),'unequipped':old,
            'message':'Equipped '+item_label(item)+' in '+EQUIPMENT_SLOTS[actual].lower()+'.'}

    def unequip_item(self, player_id, world_id, slot):
        slot=SLOT_ALIASES.get(slot,slot)
        if slot not in EQUIPMENT_SLOTS:return {'success':False,'message':'Unknown equipment slot.'}
        shared=self.get_player(player_id)['shared']
        self._resolve_equipment(player_id)
        old=shared['equipped'].get(slot)
        if not old:return {'success':False,'message':'Nothing equipped in '+slot+'.'}
        shared['equipped'][slot]=None
        shared['equipment'].pop(slot,None)
        self._sync_weapon_choice(player_id)
        self._save_player(player_id)
        return {'success':True,'unequipped':old,'slot':slot,'message':'Unequipped '+old+'.'}

    def equip_weapon(self, player_id, world_id, weapon_name):
        return self.equip_item(player_id,world_id,weapon_name,'weapon')

    def unequip_weapon(self, player_id, world_id):
        shared=self.get_player(player_id)['shared']
        shared['equipped']['weapon']=None
        shared['equipment'].pop('weapon',None)
        shared['equipped_weapon']='fists'
        self._save_player(player_id)
        return {'success':True,'message':'Using bare hands.'}

    def get_equipped_weapon(self, player_id, world_id):
        selected=next((spec for slot,spec,_ in self._resolve_equipment(player_id)
            if slot=='weapon' and not item_broken(spec)),None)
        if not selected:
            self.get_player(player_id)['shared']['equipped_weapon']='fists'
            return dict(self.weapons_config['weapons']['fists'],weapon_id='fists')
        name=selected['name'].lower()
        template='stick'
        for markers,ident in ((('longbow',),'longbow'),(('bow',),'shortbow'),
                (('knife','dagger'),'dagger'),(('spear',),'hunting_spear'),
                (('sword','blade'),'longsword'),(('axe','hatchet'),'greataxe')):
            if any(marker in name for marker in markers):
                template=ident
                break
        weapon=dict(self.weapons_config['weapons'].get(selected.get('weapon_id',template),self.weapons_config['weapons']['fists']))
        for key in ('type','attack_bonus','damage_bonus','damage_die','requires_ammo','ammo_type','crit_range','effects'):
            if key in selected:weapon[key]=selected[key]
        
        if weapon.get('type') in ('weapon','gear','equipment','armor','tool'):
            weapon['type']=self.weapons_config['weapons'].get(template,{}).get('type','blade')
        values=item_bonus_values(selected)
        for key in ('attack','damage'):
            if isinstance(values.get(key),(int,float)):
                weapon[key+'_bonus']=weapon.get(key+'_bonus',0)+values[key]
        weapon.update(name=selected['name'],weapon_id=selected.get('weapon_id') or 'made:'+str(selected.get('id',selected['name'])))
        self.get_player(player_id)['shared']['equipped_weapon']=weapon['weapon_id']
        return weapon

    def get_equipment_bonuses(self, player_id, world_id):
        keys=('attack','defense','gathering','hunting','crafting','stealth','price_bonus',
            'inventory_capacity','ranged_bonus','wolf_hunting','intimidation','tracking',
            'social','magic_rolls','focus','fishing')
        result=dict.fromkeys(keys,0)
        result.update(sources=[],special_effects={})
        aliases={'hunting_bonus':'hunting','gathering_bonus':'gathering','gather_bonus':'gathering','crafting_bonus':'crafting'}

        def add(values,slot=None,name=None,special=None):
            normal={}
            for key,value in values.items():
                canonical=aliases.get(key,key)
                
                if canonical not in normal or key==canonical:normal[canonical]=value
            for key,value in normal.items():
                if isinstance(value,bool):result['special_effects'][key]=value
                elif isinstance(value,(int,float)):
                    result[key]=result.get(key,0)+value
                    if name:result['sources'].append({'slot':slot,'item':name,'type':key,'value':value})
            result['special_effects'].update(special or {})

        for slot,spec,_ in self._resolve_equipment(player_id):
            if item_broken(spec):continue
            values=item_bonus_values(spec)
            
            if slot=='weapon':
                values.pop('attack',None);values.pop('damage',None)
            add(values,slot,spec['name'],spec.get('special_effects'))
        titles=self.get_player_titles(player_id,world_id)
        if titles.get('success'):add(titles.get('total_bonuses',{}))
        checked=set()
        for carried in self.get_player(player_id)['shared']['inventory']:
            spec=definition(carried if isinstance(carried,dict) else {'name':str(carried)})
            name=spec['name']
            if name.casefold() in checked:continue
            if spec.get('passive') or spec.get('type')=='passive':
                add(spec.get('passive_bonuses',{}),'inventory (passive)',name,spec.get('special_effects'))
                checked.add(name.casefold())
            elif spec.get('type')=='gear' and 'slot' in spec and spec['slot'] is None:
                add(spec.get('bonuses',{}),'inventory (gear)',name,spec.get('special_effects'))
                checked.add(name.casefold())
        add(self.get_status_effect_bonuses(player_id))

        taming=sum(effect.get('value',0) for effect in self.get_active_status_effects(player_id)
            if effect.get('type') in ('taming','buff_taming','buff_all'))
        result['taming']=result.get('taming',0)+taming
        result['hunting_bonus']=result['hunting']
        return result

    def _repair_preview(self, player_id, item):
        recipe=self.crafting_config['recipes'].get(item.get('recipe_id'),definition(item))
        repair=self.crafting_config.get('repair_costs',{}).get(item.get('type'),{})
        factor=repair.get('materials_factor',.25)
        assistance=self.get_companion_assistance(player_id)
        communal=min(.1,max(0,getattr(self,'community_repair_discount',0)))
        reduction=min(.5,assistance.get('repair',0)*.1+communal)
        preview=repair_plan(item,self.get_player(player_id)['shared']['inventory'],recipe,factor,reduction)
        preview['assistance']={'name':assistance['name'],'bonus':assistance.get('repair',0),
            'material_reduction_percent':round(reduction*100),'community_reduction_percent':round(communal*100)}
        return preview

    def repair_preview(self, player_id, item_name='', item_id=None):
        _,item=self._owned_item(player_id,item_name,item_id)
        if not item:return {'available':False,'reason':'You do not own that item.','materials':[]}
        return self._repair_preview(player_id,item)

    def repair_item(self, player_id, world_id, item_name, item_id=None):
        source,item=self._owned_item(player_id,item_name,item_id)
        if not item:return {'success':False,'message':'You do not own that item to repair.'}
        preview=self._repair_preview(player_id,item)
        if not preview['available']:return {'success':False,'message':preview['reason'],'repair':preview}
        for name,amount in preview['spent'].items():
            self.remove_item(player_id,world_id,name,amount)
        maximum=item['max_durability']
        item['durability']=maximum
        self._sync_weapon_choice(player_id)
        self._save_player(player_id)
        return {'success':True,'id':item['id'],'durability':maximum,'max_durability':maximum,
            'materials_spent':preview['spent'],'repair_assistance':preview['assistance'],
            'message':'Repaired '+item['name']+' to '+str(maximum)+'/'+str(maximum)+' durability.'}

    def damage_item(self, player_id, world_id, item_name, damage=1, item_id=None):
        source,item=self._owned_item(player_id,item_name,item_id)
        if not item or 'durability' not in item:return {'broken':False,'wear':0}
        before=item['durability']
        item['durability']=max(0,before-max(0,damage))
        self._sync_weapon_choice(player_id)
        self._save_player(player_id)
        result={'id':item['id'],'name':item['name'],'source':source,'broken':item['durability']==0,
            'before':before,'after':item['durability'],'wear':before-item['durability'],
            'durability':item['durability'],'max_durability':item['max_durability']}
        if result['broken'] and before>0:result['message']=item['name']+' broke. It can be repaired.'
        return result

    def wear_equipment(self, player_id, world_id, weapon=True, armor=False, amount=1):
        slots=({'weapon'} if weapon else set())
        if armor:slots.update(slot for slot in EQUIPMENT_SLOTS if slot!='weapon')
        worn=list(self._resolve_equipment(player_id))
        results=[]
        for slot,item,_ in worn:
            if slot in slots and not item_broken(item):
                changed=self.damage_item(player_id,world_id,item['name'],amount,item_id=item.get('id'))
                if changed.get('wear'):
                    results.append(dict(changed,slot=slot))
        return results

    def remove_item(self, player_id, world_id, item_name, quantity=1, item_id=None):
        shared=self.get_player(player_id)['shared']
        self._sync_item_conditions(shared)
        if quantity<=0:return False
        rows=self.inventory_condition_items(player_id,item_name)
        if item_id and not any(row['id']==item_id for row in rows):return False
        if not super().remove_item(player_id,world_id,item_name,quantity):return False

        worn={ref.get('id') for ref in shared.get('equipment',{}).values()}
        rows.sort(key=lambda row:(row['id']!=item_id if item_id else row['id'] in worn))
        for row in rows[:quantity]:shared['item_conditions'].pop(row['id'],None)
        self._resolve_equipment(player_id)
        return True

    def transfer_item(self, giver_id, receiver_id, world_id, item_name, quantity=1, item_id=None):
        if str(giver_id).strip().lower()==str(receiver_id).strip().lower():
            return {'success':False,'message':'That item is already yours.'}
        giver=self.get_player(giver_id)
        receiver=self.get_player(receiver_id)
        self._sync_item_conditions(giver['shared'])
        self._sync_item_conditions(receiver['shared'])
        rows=self.inventory_condition_items(giver_id,item_name)
        worn={ref.get('id') for ref in giver['shared'].get('equipment',{}).values()}
        rows.sort(key=lambda row:(row['id']!=item_id if item_id else row['id'] in worn))
        transferred=deepcopy(rows[:max(0,quantity)])
        if not self.remove_item(giver_id,world_id,item_name,quantity,item_id):
            return {'success':False,'message':'You do not own that quantity or item instance.'}
        canonical=transferred[0]['name'] if transferred else item_name
        for _ in range(quantity):receiver['shared']['inventory'].append(canonical)
        receiver['shared']['item_conditions'].update({row['id']:row for row in transferred})
        self._sync_item_conditions(receiver['shared'])
        self._save_player(giver_id);self._save_player(receiver_id)
        return {'success':True,'message':'Gave '+(str(quantity)+'x ' if quantity>1 else '')+canonical+' to '+receiver['name']+'!',
            'item_ids':[row['id'] for row in transferred]}

    def adopt_companion(self,player_id,companion_id):
        p=self.get_player(player_id)
        spec=self.companions_config['companions'].get(companion_id)
        if not spec or spec.get('secret_reward') or spec.get('adoptable') is False:
            return {'success':False,'message':'This companion is earned through its own story.'}
        now=datetime.now().isoformat()
        p['companions'].append(dict(spec,id=companion_id,custom_name=None,
            instance_id=uuid.uuid4().hex[:12],loyalty=spec.get('starting_loyalty',50),adopted_at=now,last_interaction=now))
        if p.get('active_companion_index') is None and 'companion_follow_choice' not in p:
            p['active_companion_index']=len(p['companions'])-1
        return {'success':True,'message':spec['name']+' has joined you.'}

    def get_weather(self,world_id):

        now=datetime.now();period=now.hour*3+now.minute//20
        seed=hashlib.sha256(f'{world_id}:{now.date()}:{period}'.encode()).hexdigest()
        options=self.WEATHER_TYPES.get(world_id,self.WEATHER_TYPES['heros_journey'])
        chosen=random.Random(seed).choices(options,weights=[v['weight'] for v in options])[0]
        return dict(chosen,effects=self.WEATHER_EFFECTS.get(chosen['name'],{}),world_id=world_id)

    def _quest_chains(self,world_id):
        path=ENGINE/f'quests_{world_id.replace("-","_")}.json'
        return json.loads(path.read_text(encoding='utf-8-sig')).get('quest_chains',{}) if path.exists() else {}

    def get_party(self,player_id):
        party=super().get_party(player_id)
        quest=(party or {}).get('current_quest')
        if quest and not quest.get('world_id'):

            player=self.get_player(player_id)
            worlds=[wid for wid,data in player['worlds'].items() if data.get('on_party_quest')]
            if len(worlds)!=1:
                worlds=[]
                for path in ENGINE.glob('quests_*.json'):
                    data=json.loads(path.read_text(encoding='utf-8-sig'))
                    if any(q.get('quest_id')==quest.get('quest_id') for q in data.get('quest_pool',[])):
                        worlds.append(path.stem.removeprefix('quests_'))
            if len(worlds)==1:quest['world_id']=worlds[0]
        return party

    def _party_quest_here(self,player_id,world_id):
        quest=(self.get_party(player_id) or {}).get('current_quest')
        return bool(quest and quest.get('world_id')==world_id)

    def start_quest(self,player_id,world_id,quest_data):
        if self._party_quest_here(player_id,world_id):
            return {'success':False,'message':'Finish your party quest in this region before accepting a solo quest.'}
        return super().start_quest(player_id,world_id,quest_data)

    def start_party_quest(self,leader_id,world_id,quest):
        return super().start_party_quest(leader_id,world_id,dict(quest,world_id=world_id))

    def _recover_chain_cursor(self,player_id,world_id):
        """Recover unpaid next stages from completed history; never pay on read."""
        player=self.get_player(player_id)
        statuses=player.setdefault('quest_chains',{})
        history=player['worlds'][world_id].get('quest_history',[])
        for ident,chain in self._quest_chains(world_id).items():
            status=statuses.get(ident)
            if status and status.get('completed'):continue
            completed={q.get('quest_id') for q in history
                if q.get('is_chain_quest') and q.get('chain_id')==ident and q.get('completed_at')}
            count=0
            for quest in chain['quests']:
                if quest['quest_id'] not in completed:break
                count+=1
            if not count:continue
            status=statuses.setdefault(ident,{'current_step':0,'completed':False})
            if count==len(chain['quests']):

                status['pending_completion']=True
                status['current_step']=count-1
            else:status['current_step']=max(status.get('current_step',0),count)
            status.setdefault('started_at',next(q['completed_at'] for q in history if q.get('chain_id')==ident and q.get('completed_at')))

    def get_available_quest_chains(self,player_id,world_id):
        self._recover_chain_cursor(player_id,world_id)
        result=super().get_available_quest_chains(player_id,world_id)
        statuses=self.get_player(player_id).get('quest_chains',{})
        for chain in result:
            chain['pending_completion']=bool(statuses.get(chain['chain_id'],{}).get('pending_completion'))
        return result

    def start_quest_chain(self,player_id,world_id,chain_id):
        if self._party_quest_here(player_id,world_id):
            return {'success':False,'message':'Finish your party quest in this region before continuing a storyline.'}
        self._recover_chain_cursor(player_id,world_id)
        player=self.get_player(player_id)
        status=player.get('quest_chains',{}).get(chain_id,{})
        if status.get('pending_completion') and not player['worlds'][world_id].get('current_quest'):
            result=self.advance_quest_chain(player_id,world_id,chain_id)
            if result.get('success'):status.pop('pending_completion',None)
            return result
        result=super().start_quest_chain(player_id,world_id,chain_id)
        if result.get('success'):
            quest=player['worlds'][world_id]['current_quest']
            quest['world_id']=world_id
            quest['reward_currency']=round(quest.get('reward_currency',0)*{'dusty_trails':2,'black_serenity':.5}.get(world_id,1),2)
        return result

    def complete_quest(self,player_id,world_id):
        quest=deepcopy(self.get_player(player_id)['worlds'][world_id].get('current_quest'))
        result=super().complete_quest(player_id,world_id)
        if result.get('success') and quest and quest.get('is_chain_quest'):
            status=self.get_player(player_id).get('quest_chains',{}).get(quest['chain_id'],{})

            if not status.get('completed') and status.get('current_step',0)<quest['chain_step']:
                outcome=self.advance_quest_chain(player_id,world_id,quest['chain_id'])
                result['chain_progress']=outcome
                result['message']+='\n'+outcome.get('message','')
        return result

    def _load_dungeon_config(self,world_id):
        config=super()._load_dungeon_config(world_id)
        scale={'dusty_trails':2,'black_serenity':.5}.get(world_id,1)
        def adjust(node):
            if isinstance(node,dict):
                for k,v in node.items():
                    if k in ('currency','reward_currency') and isinstance(v,(int,float)):node[k]=round(v*scale,2)
                    else:adjust(v)
            elif isinstance(node,list):
                for n in node:adjust(n)
        adjust(config)
        return config

    def transform(self,player_id,form_id):
        
        if form_id=='human':
            self.get_player(player_id)['current_form']='human'
            return {'success':True,'message':'Returned to your everyday form.'}
        return super().transform(player_id,form_id)

    def rename_companion(self, player_id, companion_identifier, new_name):
        new_name = new_name.strip()
        if not new_name:
            raise ValueError('Give your companion a name.')
        pet=self.care_companion(player_id,companion_identifier)
        if pet.get('_house',{}).get('family_body')=='cloud':
            return {'success':False,'message':'Cloud keeps the name the family knows.'}
        pet['custom_name']=new_name
        self._save_player(player_id)
        return {'success':True,'companion':companion_care.identity(pet),
                'message':'Your companion is now named '+new_name+'.'}

    def tame_companion(self,*args,**kwargs):
        actor=args[0] if args else kwargs.get('player_id')
        player=self.get_player(actor)
        explicit='companion_follow_choice' in player
        active=player.get('active_companion_index')
        result=super().tame_companion(*args,**kwargs)
        if explicit:player['active_companion_index']=active
        if result.get('success'):
            result['message']=result['message'].split('\n⚠️')[0]
        return result

def config_worlds(manager):
    return manager.worlds_config['worlds'].keys()
