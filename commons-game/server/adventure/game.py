

"""Authenticated game actions shared by the website and companion tools.

The transport supplies the verified actor and actual room. Catalogs, rolls,
prices and rewards come from the server; clients submit choices only.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import time
import uuid
import re

from .players import Players, ENGINE
from .recipes import available_here
from .consumables import Consumables, normal
from .equipment import EQUIPMENT_SLOTS, definition, fishing_tool, item_broken
from .item_condition import ensure_condition
from .servings import food_units, discard_empty_made
from . import activity, companion_care, hacking, guidance, field_guide, companion_actions, community, dungeon_sessions
from room_experience import room_experience
from holidays import holiday_state
from fishing_areas import has_overrides, has_water
from .stories import story_action, dungeon_ability, settle_event
from .engine.multi_world_quest_handler import MultiWorldQuestHandler

def _named_catalog_choice(rows, requested):
    """Resolve an exact catalog name, or one unambiguous natural shorthand."""
    wanted = str(requested or '').strip().lower()
    exact = next((row for row in rows if str(row.get('name', '')).lower() == wanted), None)
    if exact or not wanted:
        return exact
    matches = [row for row in rows
               if wanted in str(row.get('name', '')).lower().split()
               or wanted in str(row.get('name', '')).lower()]
    return matches[0] if len(matches) == 1 else None

def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else default

def nearby_names(room):
    return {str(npc.get('name','')).casefold() for npc in room.get('npcs',[])}

def positive(value, maximum=1000):
    if isinstance(value, bool):
        raise ValueError('Choose a positive whole quantity.')
    n = int(value)
    if str(n) != str(value) or not 1 <= n <= maximum:
        raise ValueError('Choose a positive whole quantity.')
    return n

def approach_bonus(description):
    """The Home approach rule, shared by the browser and text actions."""
    words=str(description or '').lower()
    categories={
        'gentle':('slow','gentle','calm','quiet','soft','patient','crouch','kneel'),
        'food':('meat','food','treat','offer','feed','fish','berry','honey'),
        'knowledge':('scent','territory','pack','alpha','trust','bond','instinct'),
        'respect':('eye','gaze','bow','respect','submit','lower'),
        'voice':('whisper','hum','sing','murmur','coo','voice','song')}
    bonus=sum((2 if key in ('gentle','food','knowledge') else 1)
              for key,terms in categories.items() if any(term in words for term in terms))
    return min(4,bonus) or (1 if len(words)>10 else 0)

class Game:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        self.players = Players(store)
        self.quests = MultiWorldQuestHandler(self.players)
        self.worlds = read(ENGINE/'worlds_config.json')['worlds']
        self.consumables = Consumables(ENGINE, self.root, self.players.crafting_config['recipes'])

    def room(self, room_id):
        if not isinstance(room_id, str) or not room_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in room_id):
            raise ValueError('Unknown location.')

        path = self.store.world/f'{room_id}.json'
        room = read(path if path.exists() else self.root/'world'/f'{room_id}.json')
        if not room or 'rows' not in room:
            raise ValueError('Unknown location.')
        return room

    def context(self, actor, room_id):
        room = self.room(room_id)
        region = room.get('region', 'home')
        if region == 'hub':
            region = 'home'
        self.active_region=region
        self.players.community_repair_discount=community.effects(self,room_id).get('repair_material_discount',0)
        return self.players.get_player(actor, actor.title()), room, region, room.get('game_location', room_id)

    def assisted_engine(self,actor,room_id,action,invoke):
        """Consume prepared help only when the engine calculates an actual attempt."""
        name='get_combat_bonuses' if action=='hunt' else 'get_gathering_bonuses'
        original=getattr(self.quests,name);prepared=None
        def calculate(*args,**kwargs):
            nonlocal prepared
            value=deepcopy(original(*args,**kwargs))
            if prepared is None:prepared=companion_actions.consume_assistance(self,actor,room_id,'gather' if action=='collect' else action)
            bonus=prepared.get('bonus',0)
            if action!='hunt':
                shared=community.effects(self,room_id)['gathering_bonus']
                bonus+=shared
                if shared:value['bonus_sources']['community']=shared
            value['attack' if action=='hunt' else 'bonus']+=bonus
            if prepared:value['bonus_sources']['prepared_companion']=prepared
            return value
        setattr(self.quests,name,calculate)
        try:
            result=invoke()
            if prepared:
                if not (result.get('rolls') or 'roll' in result):companion_actions.restore_assistance(self,actor,prepared)
                else:result['prepared_assistance']=prepared
            return result
        finally:setattr(self.quests,name,original)

    def catalogs(self, actor, room_id):
        """[commons:actions] Discover only choices executable in this actual room."""
        player, room, region, loc = self.context(actor, room_id)
        wildlife_loc = room.get('wildlife_location', loc)
        hunts = self.quests.hunting_config.get('hunting_areas', {}).get(region, {}).get(wildlife_loc, {}).get('animals', [])
        if room.get('hunting'):
            native = read(self.root/'world/hunting.json', {})
            hunts = [dict(native['quarry'][name], name=name) for name in native.get('grounds', {}).get(room['hunting'], [])]
        areas=self.quests.gathering_config.get('gathering_areas', {}).get(region,{})
        gathers=areas.get(room_id,areas.get(loc,{})).get('resources',[])
        taming = self.players.companions_config.get('taming_info', {})
        animals = [{'id':key, **self.players.companions_config['companions'].get(key, {}), 'difficulty':v.get('difficulty',10), 'tip':v.get('taming_tip','')}
            for key, v in taming.items() if key != 'fox' and v.get('can_tame') and wildlife_loc in v.get('locations', [])]
        if room.get('wilds'):
            path=self.store.world/'wilds.json'
            wilds=read(path if path.exists() else self.root/'world/wilds.json',{})
            ground='the wild wood' if room['wilds'] is True else room['wilds']
            animals=[{'id':key,'name':key.title(),**wilds['animals'][key]} for key in wilds.get('wilds',{}).get(ground,[])]
        animals=[dict(animal,assistance=self.players.get_companion_assistance(actor,dict(animal,is_active=False)))
                 for animal in animals]
        quests=read(ENGINE/f'quests_{region}.json', {}).get('quest_pool', [])
        nearby={n['name'].lower() for n in room.get('npcs',[])}
        quests=[q for q in quests if str(q.get('given_by','')).lower() in nearby]
        hack=read(ENGINE/'hacking_config.json',{}).get('hacking_areas',{}).get(region,{}).get(loc,{})
        terminals,terminal_characters,terminal_setup=hacking.catalog(self,actor,room_id,hack.get('systems',[]))
        stories=read(ENGINE/'storylines.json',{})
        histories=read(ENGINE/'backstory_config.json',{}).get('npcs',{})
        npcs=[]
        for npc in room.get('npcs',[]):
            stock=read(ENGINE/'npcs'/npc['id']/'inventory.json',{})
            npcs.append(dict(npc,story_available=npc['id'] in stories,
                backstory_available=npc['id'] in histories,
                shop_available=bool(stock.get('inventory') or stock.get('items') or stock.get('services'))))
        return {'hunts':hunts,'gathering':gathers,'animals':animals,'quests':quests,
            'fishing': bool(self.room_fish_pool(room, region, loc)),
            'archery': bool(room.get('range')),
            'fishing_setup': self.fishing_setup(actor, region),
            'hunting_setup': self.quests.get_combat_bonuses(actor, region),
            'taming_setup': self.taming_setup(actor,region),
            'recipes':{k:v for k,v in self.players.crafting_config.get('recipes',{}).items() if available_here(v,region,room_id)},
            'home_recipes':{k:v for k,v in read(self.root/'world/recipes.json',{}).get('recipes',{}).items() if available_here(v,region,room_id)},
            'npcs':npcs, 'forms':{k:v for k,v in self.players.transformations_config.get('forms',{}).items() if k=='human' or player.get('character_type','all') in v.get('available_to',[])},
            'character_types':sorted({t for f in self.players.transformations_config['forms'].values() for t in f.get('available_to',[]) if t!='all'}),
            'chains':guidance.chain_steps(self,actor,room_id,region),
            'roles':self.players.dungeon_rules.get('roles',{}),
            'dungeons':read(ENGINE/f'dungeons_{region}.json',{}).get('dungeons',[]),
            'hacking':terminals,
            **({'terminal_characters':terminal_characters,'terminal_setup':terminal_setup} if terminals else {})}

    def snapshot(self, actor, room_id):
        """[commons:ai] Return the same playable state to browser and tool readers."""
        p,r,w,loc=self.context(actor,room_id)
        self.players.get_active_companion(actor)
        for pet in p.get('companions',[])+p.get('family_pets',[]):self.players.companion_health(actor,pet)
        shared=p['shared']

        for item in shared['crafted_items']:
            item.setdefault('id',uuid.uuid4().hex[:12])
            item.setdefault('name',item.get('what','Made thing'))
            ensure_condition(item)
        discard_empty_made(shared,self.consumables)
        food_units(shared,self.consumables)
        catalog=self.catalogs(actor,room_id)
        holiday=holiday_state(self.store,room=r)
        holiday['can_manage']=actor=='admin'
        inv=Counter()
        for item in shared['inventory']:
            inv[item.get('name','') if isinstance(item,dict) else item] += item.get('quantity',1) if isinstance(item,dict) else 1
        state={'ok':True,'name':p['name'],'room':room_id,'region':r.get('region','home'),
            'gold':shared['gold_cents']/100,'hp':shared['hp'],'max_hp':shared['max_hp'],
            'level':p.get('level',1),'xp':p.get('xp',0),'inventory':dict(inv),
            'made':shared['crafted_items'],'perishables':shared.get('perishable_inventory',[]),
            'equipment':shared.get('equipped',{}),'form':p.get('current_form','human'),
            'equipment_refs':shared.get('equipment',{}),
            'equipment_slots':EQUIPMENT_SLOTS,
            'inventory_condition_items':[dict(item,actions=self.item_actions(actor,item['name'],item))
                for item in self.players.inventory_condition_items(actor)],
            'item_actions':{name:self.item_actions(actor,name) for name in inv},
            'made_actions':{item['id']:self.item_actions(actor,item.get('name',item.get('what','')),item)
                            for item in shared['crafted_items']},
            'companions':[dict({k:v for k,v in a.items() if k!='_house'},
                family_body=a.get('_house',{}).get('family_body'),
                release=self.players.companion_release_info(actor,a),
                care=self.players.companion_health(actor,a),
                is_active=i==p.get('active_companion_index'),
                assistance=self.players.get_companion_assistance(actor,dict(a,is_active=i==p.get('active_companion_index'))))
                for i,a in enumerate(p.get('companions',[]))],
            'family_pets':[dict({k:v for k,v in a.items() if k!='_house'},
                family_body=a.get('_house',{}).get('family_body'),is_family_pet=True,following=True,
                care=self.players.companion_health(actor,a),release=self.players.companion_release_info(actor,a))
                for a in p.get('family_pets',[])],
            'active_companion_index':p.get('active_companion_index'),
            'companion_follow_preferences':({actor:p['companion_follow_choice']}
                if 'companion_follow_choice' in p else {}),
            'quest':p['worlds'][w].get('current_quest'),'achievements':p.get('achievements',[]),
            'reputation':self.players.get_reputation(actor,w), 'party':self.players.get_party(actor),
            'dungeon':dict(self.players.get_dungeon_status(actor),data=self.players.get_party_dungeon(actor)),
            'character_type':p.get('character_type','all'),
            'mount':self.quests.get_mount_speed_bonus(actor,w),
            'relationships':self.players.get_all_relationships(actor),
            'status_effects':self.players.get_active_status_effects(actor),
            'catalog':catalog,'environment':room_experience(r,catalog,holiday=holiday),
            'holiday':holiday,
            'regions':read(self.root/'world/regions.json',{}).get('regions',{}),
            'exits':[{'room':d['to'],'label':d.get('label',d['to']),'arrival':d.get('arriveAt')} for d in r.get('props',[]) if d.get('to')],
            'competitive':p.get('competitive',False),
            'party_invites':p.get('pending_party_invites',[]),
            'duels':[d for d in self.store.documents('duel:') if actor in d['players'] and d['status']!='finished' and (d['status']=='active' or d['expires']>time.time())],
            'event':self.store.read('world-event:'+w),
            'weather':self.players.get_weather(w),
            'held_fish':self.held_fish(actor),
            'journal':self.store.read('journal:'+actor,[])[-100:]}
        state['belongings']=guidance.belongings(state)
        state['guidance']=guidance.snapshot(self,actor,room_id,state)
        field_guide.visit(self,actor,room_id)
        state['field_guide']=field_guide.snapshot(self,actor)
        state['companion_abilities']=companion_actions.snapshot(self,actor,room_id)
        state['community']=community.snapshot(self,actor,room_id)
        world=guidance.rooms(self)
        for project in state['community']['projects']:
            project['route']=guidance.route(self,room_id,project['room'],world) or []
        state['dungeon_session']=dungeon_sessions.snapshot(self,actor,room_id)
        state['environment']['changes']=community.room_effects(self,room_id)
        return state

    def item_actions(self, actor, name, item=None):
        info=self.players.item_capabilities(actor,name,(item or {}).get('id'))
        spec=self.consumables.resolve(name,item)
        rows=food_units(self.players.get_player(actor)['shared'],self.consumables).get(name,[]) if not item else []
        if spec and rows:spec=dict(spec,remaining=rows[0]['uses_left'],servings=rows[0]['uses_total'])
        remaining=spec.get('remaining',1) if spec else 0
        info.update(usable=bool(spec and spec['effect']!='feed' and remaining>0 and not info.get('equippable')),
                    feedable=bool(self.consumables.feedable(spec) and remaining>0),
                    heal=(spec.get('heal',spec.get('value',0) if spec.get('effect') in ('heal','cure_poison_heal') else 0) if spec else 0),
                    remaining=remaining,uses_total=spec.get('servings',0) if spec else 0,
                    servings_left=sum(row['uses_left'] for row in rows) if rows else remaining,
                    units=len(rows) if rows else (1 if item else 0))
        info['feed_heal']=self.pet_food_healing(spec) if spec and info['feedable'] else 0
        if item and definition(item).get('type')=='companion':
            info.update(activatable=not item_broken(item) and not item.get('placed'),usable=False,feedable=False)
            ident=item.get('recipe_id') or next((key for key,row in self.players.companions_config['companions'].items()
                if normal(row.get('name',''))==normal(name)),None)
            info['assistance']=self.players.get_companion_assistance(actor,
                dict(definition(item),id=ident,is_active=False))
        return info

    @staticmethod
    def pet_food_healing(spec):
        return int(spec.get('heal',spec.get('value',10) if spec.get('effect') in ('heal','cure_poison_heal') else 10)) or 10

    def taming_setup(self,actor,world,words=''):
        player=self.players.get_player(actor)
        form=self.players.get_current_form(actor).get('bonuses',{}).get('taming',0)
        effects=sum(effect.get('value',0) for effect in self.players.get_active_status_effects(actor)
                    if effect.get('type') in ('taming','buff_taming','buff_all'))
        legacy=sum(buff.get('value',0) for buff in player.get('active_buffs',[]) if buff.get('type')=='taming')
        equipment=self.players.get_equipment_bonuses(actor,world).get('taming',0)-effects
        companion=self.players.get_companion_assistance(actor)
        approach=approach_bonus(words)
        sources={'form':form,'equipment':equipment,'status_effects':effects+legacy,
                 'approach':approach,'companion':companion}
        return {'total_bonus':form+equipment+effects+legacy+approach+companion['taming'],
                'bonus_sources':sources}

    def _use_source(self, actor, room_id, target='', item_id=None, share=False):
        p=self.players.get_player(actor)
        owner=actor
        item=None
        if not item_id and any(str(i.get('id'))==str(target) for i in p['shared']['crafted_items']):
            item_id=str(target)
        if item_id:
            if share:
                baskets=self.store.house_read(self.store.world/'baskets.json',{})
                owner=next((who for who,row in baskets.items() if any(str(i.get('id'))==str(item_id)
                          for i in row.get('made',[]))),None)
                if owner is None:raise ValueError('No such made thing.')
            holder=self.players.get_player(owner)
            item=next((i for i in holder['shared']['crafted_items'] if str(i['id'])==str(item_id)),None)
            if item is None:raise ValueError('That is not in your basket.')
            if owner!=actor and (item.get('placed') or {}).get('room')!=room_id:
                raise PermissionError('That food has not been set out to share in this room.')
            target=item.get('name',item.get('what',''))
        else:
            carried=[i.get('name','') if isinstance(i,dict) else i for i in p['shared']['inventory']]
            target=next((name for name in carried if normal(name)==normal(target)),target)
            if target not in carried:
                matches=[i for i in p['shared']['crafted_items'] if normal(i.get('name',i.get('what')))==normal(target)]
                if len(matches)!=1:raise ValueError('Choose an owned item, using its id for a made thing.')
                item=matches[0]
                target=item.get('name',item.get('what',''))
        spec=self.consumables.resolve(target,item)
        if not spec:raise ValueError('This item has no use action.')
        if item is None:
            rows=food_units(self.players.get_player(owner)['shared'],self.consumables).get(target,[])
            if rows:spec=dict(spec,servings=rows[0]['uses_total'],remaining=rows[0]['uses_left'])
        return owner,item,target,spec

    def _consume(self, owner, item, target, spec, actor, world):
        if item is None:
            rows=food_units(self.players.get_player(owner)['shared'],self.consumables).get(target,[])
            if rows:
                rows[0]['uses_left']-=1
                if rows[0]['uses_left']>0:return
                rows.pop(0)
            if not self.players.remove_item(owner,world,target):raise ValueError('You do not own this item.')
        elif spec.get('servings'):
            if spec['remaining']<=0:raise ValueError('There is none left.')
            item.update(uses_total=spec['servings'],uses_left=spec['remaining']-1)
            now=datetime.now(timezone.utc)
            item.setdefault('shared',[]).append({'by':self.players.get_player(actor)['name'],
                'on':now.strftime('%Y-%m-%d'),'at':now.strftime('%Y-%m-%d %H:%M')})
            if item['uses_left']==0:self.players.get_player(owner)['shared']['crafted_items'].remove(item)
        else:
            self.players.get_player(owner)['shared']['crafted_items'].remove(item)

    def use_item(self, actor, room_id, target='', item_id=None, share=False, feed=False, companion=None):
        p,_,world,_=self.context(actor,room_id)
        owner,item,target,spec=self._use_source(actor,room_id,target,item_id,share)
        effect=spec.get('effect','utility')
        if feed:
            if not self.consumables.feedable(spec):raise ValueError('That item is not companion food.')
            pet=self.players.care_companion(actor,companion)
            care=self.players.companion_health(actor,pet)
            if care['is_robot']:raise ValueError('Repair your robot with carried shiny bits or mechanical parts.')
            amount=self.pet_food_healing(spec)
            result=companion_care.feed(pet,amount)
            self._consume(owner,item,target,spec,actor,world)
            pet['loyalty']=min(100,pet.get('loyalty',50)+(int(spec.get('value',5)) if effect=='feed' else 5))
            return dict(result,loyalty=pet['loyalty'],companion=companion_care.identity(pet))
        if effect=='feed':raise ValueError('Feed this treat to a companion.')
        if self.players.item_capabilities(owner,target,(item or {}).get('id')).get('equippable'):
            raise ValueError('Wear this item to use its benefits.')
        self._consume(owner,item,target,spec,actor,world)
        amount=int(spec.get('heal',spec.get('value',0) if effect in ('heal','cure_poison_heal') else 0))
        if spec.get('dice'):
            match=re.fullmatch(r'(\d+)d(\d+)(?:\+(\d+))?',spec['dice'])
            amount=sum(random.randint(1,int(match[2])) for _ in range(int(match[1])))+int(match[3] or 0)
        healing=self.players.heal(actor,world,amount,target) if amount else {'hp':p['shared']['hp'],'max_hp':p['shared']['max_hp'],'healed':0}
        if effect in ('cure_poison','cure_poison_heal'):self.players.remove_status_effect(actor,'poison')
        if effect=='refresh':p['cooldowns']={}
        if effect in ('buff','daily_buff','heal_buff','combat_buff','cooldown_reduction','luck','light'):
            buff=spec.get('buff',effect)
            self.players.add_status_effect(actor,{'all_rolls':'buff_all','gathering':'buff_gathering','fey_gathering':'buff_gathering',
                'strength':'buff_attack','courage':'buff_attack','morale':'buff_all','morning_energy':'buff_all'}.get(buff,str(buff)),
                int(spec.get('duration',60)),int(spec.get('buff_value',spec.get('value',1))),target)
        message=spec.get('flavor') or ('Used '+target+'.')
        if amount:
            if not healing.get('healed'):
                message+=f" {'You enjoyed a serving. ' if spec.get('servings') else ''}Your HP remains {healing['hp']}/{healing['max_hp']}."
            else:message+=' '+healing.get('message',f"Restored {healing['healed']} HP.")
        return dict(healing,success=True,message=message,
                    exhausted=bool(item and item.get('uses_left')==0),**({'made':item} if item else {}))

    def cooldown(self, actor, activity, seconds):
        key=f'cooldown:{actor}:{activity}'
        until=self.store.read(key,0)
        if until>time.time():
            raise ValueError(f'Wait {max(1,int(until-time.time()))} seconds before trying again.')
        effects=self.players.get_active_status_effects(actor)
        reduction=max([e.get('value',0) for e in effects if e['type']=='cooldown_reduction']+[0])
        self.store.write(key,time.time()+seconds*max(.25,1-reduction/100))

    def fish_pool(self, world, loc):
        return self.players.fishing_config.get('fishing_spots',{}).get(world,{}).get(loc,{}).get('fish',[])

    def room_fish_pool(self, room, world, loc):

        if has_overrides(room) and not has_water(room):
            return []
        if room.get('water'):
            native=read(self.root/'world/fish.json',{})
            return [dict(native['fish'][s],name=s,difficulty=10) for s in native.get('waters',{}).get(room['water'],[])]
        pool=self.fish_pool(world,room.get('fishing_pool',loc))
        if pool:
            return pool
        
        if room.get('fishing_zones') or any(p.get('fishable') for p in room.get('props',[])) or has_overrides(room):
            fallback={'dusty_trails':'dusty-river','heros_journey':'enchanted-forest','black_serenity':'lower-decks'}.get(world)
            if fallback:
                return self.fish_pool(world,fallback)
            native=read(self.root/'world/fish.json',{})
            return [dict(native['fish'][s],name=s,difficulty=10) for s in native.get('waters',{}).get('the river',[])]
        return []

    def held_fish(self, actor):
        held=self.store.read('held-fish:'+actor)
        if held:return held
        book=self.store.house_read(self.store.world/'catches.json',{'casts':[]})
        return next((c for c in book.get('casts',[]) if str(c.get('by','')).lower()==actor and c.get('state')=='held'),None)

    def fishing_setup(self, actor, world, bait=''):
        p=self.players.get_player(actor)
        inventory=p['shared']['inventory']
        condition_items=self.players.inventory_condition_items(actor)
        condition_names={item['name'] for item in condition_items}
        items=[definition(i if isinstance(i,dict) else {'name':i}) for i in inventory
               if (i.get('name') if isinstance(i,dict) else i) not in condition_names]
        items += [definition(i) for i in condition_items if not item_broken(i)]
        items += [definition(i) for i in p['shared']['crafted_items']
                  if not item_broken(i) and not i.get('placed')]
        carried=[i.get('name','') for i in items]
        rods={r['name']:r['bonus'] for r in self.players.fishing_config['fishing_gear'].values()}
        rods['Fishing Rod']=rods['Basic Fishing Rod']
        rod_bonuses={normal(name):bonus for name,bonus in rods.items()}
        owned=[i for i in items if fishing_tool(i) and not item_broken(i)]
        chosen=max(owned,key=lambda item:rod_bonuses.get(normal(item['name']),1)) if owned else None
        rod=chosen['name'] if chosen else None
        baits=[dict(b,remaining=p.get('bait_charges',{}).get(b['name'],0)+carried.count(b['name'])*b['uses'])
               for b in self.players.fishing_config['bait'].values()]
        selected=next((b for b in baits if b['name']==bait and b['remaining']>0),None)
        gear=self.players.get_equipment_bonuses(actor,world)
        companion=self.players.get_companion_bonuses(actor)['bonuses']
        bonuses={'rod':rod_bonuses.get(normal(rod),1) if rod else 0,
                 'equipment':gear.get('fishing',0),
                 'luck':gear.get('luck',0),
                 'bait':selected['bonus'] if selected else 0,
                 'companion':companion.get('fishing',0)+companion.get('luck',0)}
        hints=[]
        if not rod:
            hints.append("You're fishing with your hands. Craft a Fishing Rod in Hero's Journey (4 firewood and 2 Thread) for +1, or buy a rod from Victor in the Enchanted Forest.")
        if not selected:
            if any(b['remaining'] for b in baits):hints.append('You have bait, but none is selected. Choose it before your next cast for its bonus.')
            else:hints.append('No bait is helping this attempt. Victor in the Enchanted Forest sells Worms (+1), Live Minnows (+2) and Magic Lures (+3).')
        if rod and bonuses['rod']<2:hints.append('A Quality Fishing Rod adds +2; Victor sells one in the Enchanted Forest. The best carried rod is used automatically.')
        return {'rod':rod,'method':rod or 'Bare hands','bait':selected['name'] if selected else None,
                'rod_id':chosen.get('id') if chosen else None,
                'baits':baits,'bonuses':bonuses,'total_bonus':sum(bonuses.values()),'hints':hints,
                'companion':self.players.get_companion_assistance(actor)}

    def fish(self, actor, room_id, bait=''):
        p,room,world,loc=self.context(actor,room_id)
        held=self.held_fish(actor)
        if held:return {'success':True,'catch':held,'message':'Your catch is still on the line. Keep it or release it.'}
        pool=deepcopy(self.room_fish_pool(room,world,loc))
        if not pool:raise ValueError('There is no fishing water here.')
        self.cooldown(actor,'fish',45)
        setup=self.fishing_setup(actor,world,bait)
        if bait:
            baits={b['name']:b for b in setup['baits']}
            if bait not in baits:raise ValueError('Unknown bait.')
            charges=p.setdefault('bait_charges',{})
            if charges.get(bait,0)<=0:
                if not self.players.remove_item(actor,world,bait):raise ValueError('You do not have that bait.')
                charges[bait]=baits[bait]['uses']
            charges[bait]-=1
        prepared=companion_actions.consume_assistance(self,actor,room_id,'fish')
        if prepared:
            setup['bonuses']['prepared_companion']=prepared['bonus']
            setup['prepared_assistance']=prepared
            setup['total_bonus']+=prepared['bonus']
        fish=random.choices(pool,weights=[f.get('weight',10) for f in pool])[0]
        roll=random.randint(1,20)
        wear=self.players.damage_item(actor,world,setup['rod'],1,item_id=setup['rod_id']) if setup['rod'] else None
        total=roll+setup['total_bonus']
        difficulty=fish.get('difficulty',10)
        details={'roll':roll,'total':total,'difficulty':difficulty,'setup':setup,
                 'item_wear':[wear] if wear and wear.get('wear') else [],
                 'reason':('A natural 1 always slips away.' if roll==1 else
                           'A natural 20 always catches.' if roll==20 else
                           f'Your total was {total}; this fish needed {difficulty}.')}
        if roll!=20 and (roll==1 or total<difficulty):
            return {'success':False,**details,'message':'The fish slips away. '+details['reason']+' '+ ' '.join(setup['hints'])}
        releases=self.store.house_read(self.store.world/'catches.json',{}).get('released',{})
        growth=min(.6,releases.get(fish['name'],0)*.02)
        low,high=fish.get('lb',[.5,4])
        held={'id':'c'+uuid.uuid4().hex[:10],'species':fish['name'],'room':room_id,'lb':round(random.uniform(low,high*(1+growth)),2),
              'value':fish.get('value',2),'rarity':fish.get('rarity','common'),'at':datetime.now(timezone.utc).isoformat(),'on':datetime.now().strftime('%Y-%m-%d')}
        self.store.write('held-fish:'+actor,held)
        book=self.store.house_read(self.store.world/'catches.json',{'casts':[]})
        book.setdefault('casts',[]).append(dict(held,by=p['name'],state='held'))
        self.store.house_write(self.store.world/'catches.json',book)
        return {'success':True,'catch':held,**details,'message':f"Caught {fish['name']}! Keep it or release it."}

    def decide_fish(self,actor,room_id,keep):
        fish=self.held_fish(actor)
        if not fish:raise ValueError('There is no fish waiting on your line.')
        p,r,w,loc=self.context(actor,room_id)
        book=self.store.house_read(self.store.world/'catches.json',{'casts':[]})
        if keep:self.players.add_item(actor,w,fish['species'])
        else:
            releases=self.store.house_read(self.store.world/'catches.json',{}).get('released',{})
            releases[fish['species']]=releases.get(fish['species'],0)+1
            book['released']=releases
        book['casts']=[c for c in book.get('casts',[]) if c.get('id')!=fish['id']]
        book.setdefault('casts',[]).append(dict(fish,by=p['name'],state='kept' if keep else 'released'))
        self.store.house_write(self.store.world/'catches.json',book)
        self.store.write('held-fish:'+actor,None)
        self.players.add_xp(actor,10,'Fishing')
        return {'success':True,'catch':deepcopy(fish),'message':'The catch is in your inventory.' if keep else 'Released. This species can grow larger for the next fisher.'}

    def shop(self, actor, room_id, npc_id):
        _,room,world,loc=self.context(actor,room_id)
        npc=next((n for n in room.get('npcs',[]) if n['id']==npc_id),None)
        if not npc:raise ValueError('That NPC is not here.')
        data=read(ENGINE/'npcs'/npc_id/'inventory.json',{})
        scale={'dusty_trails':2,'black_serenity':.5}.get(world,1)
        stock=[]
        def visit(value,category=''):
            if not isinstance(value,dict):return
            for name,row in value.items():
                if isinstance(row,dict) and 'price' in row:
                    stock.append(dict(row,name=name,category=category,price=round(float(row['price'])*scale,2)))
                elif isinstance(row,dict):visit(row,name)
        visit(data.get('inventory',{}))
        for row in data.get('items',[]):
            if isinstance(row,dict) and 'price' in row:stock.append(dict(row,category=row.get('type','supplies'),price=round(float(row['price'])*scale,2)))
        buy_prices={name:round(float(price)*scale,2) for name,price in data.get('buy_prices',{}).items() if isinstance(price,(int,float))}
        services=[]
        for kind,group in data.get('services',{}).items():
            for name,row in group.get('repairs',group.get('upgrades',{})).items():
                services.append(dict(row,id=kind+':'+name,name=kind.title()+' '+name,
                    inputs=[name,*row.get('components',[])],price=round(row['cost']*scale,2)))
        for name,row in data.get('crafting',{}).get('recipes',{}).items():
            services.append(dict(row,id='make:'+name,name='Forge '+name,result=name,
                inputs=row.get('components',[]),price=round(row['cost']*scale,2)))
        for item in stock:
            item['price']=round(item['price']*self.players.get_price_modifier(actor,world,npc_id),2)
            use=self.consumables.resolve(item['name'])
            if use:
                healing=use.get('heal',use.get('value',0) if use.get('effect') in ('heal','cure_poison_heal') else 0)
                if healing:
                    item['healing']=healing
                    text=item.get('description','')
                    if re.search(r'\b(?:restores?|heals?)\s+\d+\s+HP\b',text,re.I):
                        text=re.sub(r'(\b(?:restores?|heals?)\s+)\d+(\s+HP\b)',
                                    lambda match:match[1]+str(healing)+match[2],text,flags=re.I)
                    else:text=(text+' '+f'Restores {healing} HP per use.').strip()
                    item['description']=text
            if item.get('companion_id'):
                pet=self.players.companions_config['companions'].get(item['companion_id'],{})
                item['emoji']=pet.get('emoji','🐾')
                item['assistance']=self.players.get_companion_assistance(actor,dict(pet,id=item['companion_id'],is_active=False))
                item['description']=pet.get('description',item.get('description',''))+' Bonded companions are protected and keep their loyalty while you are away.'
        return {'npc':npc,'stock':stock,'buy_prices':buy_prices,'services':services}

    def action(self, actor, room_id, action, args):
        p,room,w,loc=self.context(actor,room_id)
        m=self.players
        target=str(args.get('target','')).strip()
        catalog=self.catalogs(actor,room_id)
        if action in guidance.ACTIONS:return guidance.execute(self,actor,room_id,action,args)
        if action in field_guide.ACTIONS:return field_guide.execute(self,actor,room_id,action,args)
        if action in companion_actions.ACTIONS:return companion_actions.execute(self,actor,room_id,action,args)
        if action in community.ACTIONS:return community.execute(self,actor,room_id,action,args)
        if action in dungeon_sessions.ACTIONS:return dungeon_sessions.execute(self,actor,room_id,action,args)
        if action in ('story-start','story-respond','backstory','flirt','tell-story','hear-story'):return story_action(self,actor,room_id,action,args)
        if action=='character-type':
            if target not in catalog['character_types']+['all']:raise ValueError('Choose an available character type.')
            if p.get('character_type','all')!='all':raise ValueError('Your character type is already chosen.')
            m.set_character_type(actor,target)
            return {'success':True,'message':'Character type set to '+target+'.'}
        if action=='fish':return self.fish(actor,room_id,str(args.get('bait','')))
        if action in ('keep','release'):
            if args.get('catch_id') is not None and (self.held_fish(actor) or {}).get('id')!=args['catch_id']:
                raise ValueError('That is not your held catch.')
            return self.decide_fish(actor,room_id,action=='keep')
        if action=='hunt':
            self.cooldown(actor,'hunt',45*(1-self.quests.get_mount_speed_bonus(actor,w).get('speed_bonus',0)))
            quarry=_named_catalog_choice(catalog['hunts'],target)
            if not quarry:raise ValueError('Choose unnamed wild quarry from this hunting ground.')
            if room.get('hunting'):
                
                self.quests.hunting_config.setdefault('hunting_areas',{}).setdefault(w,{})[loc]={'animals':[
                    dict(q,ac=q.get('ac',q['difficulty']),health=q.get('health',max(3,q['difficulty']//2)),damage=q.get('damage',max(0,q['difficulty']-7))) for q in catalog['hunts']]}
            quest=p['worlds'][w].get('current_quest')
            loc=room.get('wildlife_location',loc)
            fn=self.quests.handle_hunt if self.quests._matches_objective(quest,('hunt',),quarry['name'],loc) else self.quests.handle_free_hunt
            result=self.assisted_engine(actor,room_id,'hunt',lambda:fn(actor,p['name'],w,quarry['name'],loc))
            if result.get('success') and room.get('hunting'):
                for name,loot in quarry.get('loot',{}).items():m.add_item(actor,w,name,random.randint(loot['min'],loot['max']))
            return result
        if action=='gather':
            if not any(q['name'].lower()==target.lower() for q in catalog['gathering']):raise ValueError('That resource is not available here.')
            resource=next(q for q in catalog['gathering'] if q['name'].lower()==target.lower())
            self.cooldown(actor,'gather',resource.get('cooldown',30))
            self.quests.gathering_config.setdefault('gathering_areas',{}).setdefault(w,{})[loc]={'resources':catalog['gathering']}
            return self.assisted_engine(actor,room_id,'gather',lambda:self.quests.handle_free_gather(actor,p['name'],w,target,loc))
        if action=='collect':
            quest=p['worlds'][w].get('current_quest')
            if not quest:raise ValueError('Choose a gathering quest first.')
            return self.assisted_engine(actor,room_id,'collect',lambda:self.quests.handle_collect(actor,p['name'],w,quest.get('target_item',''),loc))
        if action=='search':return self.quests.handle_search(actor,p['name'],w,loc)
        if action=='craft':
            if 'cannot_craft' in m.get_current_form(actor).get('restrictions',[]):raise ValueError('Return to a crafting-capable form first.')
            return m.craft_item(actor,w,target)
        if action=='repair':return m.repair_item(actor,w,target,item_id=args.get('id',args.get('item_id')))
        if action=='made-stow':
            source,item=m._owned_item(actor,item_id=str(args.get('id',args.get('item_id',target))))
            if source!='made':raise ValueError('Choose one of your made things.')
            placed=item.get('placed')
            if placed and placed.get('room')!=room_id:raise ValueError('Enter the room where you set it down first.')
            item.pop('placed',None)
            return {'success':True,'id':item['id'],'message':item.get('name',item.get('what','Made thing'))+' is back in your bag.'}
        if action=='process':return m.process_perishable(actor,w,target)
        if action=='equip':
            slot=args.get('slot') or None
            if slot and slot not in EQUIPMENT_SLOTS:raise ValueError('Unknown equipment slot.')
            return m.equip_item(actor,w,target,slot,item_id=args.get('id',args.get('item_id')))
        if action=='unequip':return m.unequip_item(actor,w,target)
        if action=='transform':return m.transform(actor,target)
        if action in ('quest-accept','party-quest-start'):
            quest=next((q for q in catalog['quests'] if q['quest_id']==target),None)
            if not quest:raise ValueError('That quest is not being offered here.')
            quest=deepcopy(quest)
            quest['reward_currency']=round(quest.get('reward_currency',0)*{'dusty_trails':2,'black_serenity':.5}.get(w,1),2)
            if action=='party-quest-start':return m.start_party_quest(actor,w,quest)
            return m.start_quest(actor,w,quest)
        if action in ('quest-complete','party-quest-complete'):
            party=m.get_party(actor)
            quest=(party or {}).get('current_quest') if action=='party-quest-complete' else p['worlds'][w].get('current_quest')
            if not quest or str(quest.get('given_by','')).lower() not in {n['name'].lower() for n in room.get('npcs',[])}:
                raise ValueError('Return to the quest giver to turn this in.')
            if action=='party-quest-complete':return m.complete_party_quest(actor,w)
            return m.complete_quest(actor,w)
        if action=='quest-abandon':return m.abandon_quest(actor,w)
        if action=='quest-chain':
            if not any(c.get('chain_id')==target for c in catalog['chains']):raise ValueError('This quest chain is not available yet.')
            chain=next(c for c in catalog['chains'] if c['chain_id']==target)
            if str(chain.get('given_by','')).casefold() not in nearby_names(room):
                raise ValueError('Return to the quest giver to continue this storyline.')
            return m.start_quest_chain(actor,w,target)
        if action=='daily':
            self.cooldown(actor,'daily:'+w,86400)
            reward={'heros_journey':3,'dusty_trails':2,'black_serenity':2.5}.get(w,1)
            m.add_currency(actor,w,reward)
            return {'success':True,'message':f'A daily welcome: {reward:g} gold.'}
        if action=='shop':return dict(self.shop(actor,room_id,target),success=True)
        if action=='shop-service':
            shop=self.shop(actor,room_id,str(args.get('npc','')))
            service=next((s for s in shop['services'] if s['id']==target),None)
            if not service:raise ValueError('That service is not offered here.')
            for name,qty in Counter(service['inputs']).items():
                if not m.has_item(actor,w,name,qty):raise ValueError('Missing '+str(qty)+' '+name+'.')
            if not m.spend_currency(actor,w,service['price']).get('success'):raise ValueError('You do not have enough gold.')
            for name in service['inputs']:m.remove_item(actor,w,name)
            output=service['result'];stat=re.search(r'\+(\d+)\s+(?:to )?(damage|defense|hacking)',service.get('stat_bonus') or '')
            if stat:
                kind={'damage':'attack'}.get(stat[2],stat[2])
                slot={'attack':'weapon','defense':'armor'}.get(kind,'accessory')
                p['shared']['crafted_items'].append({'name':output,'what':output,'slot':slot,'type':'gear',
                    'bonuses':{kind:int(stat[1])},'description':service.get('description','')})
            else:m.add_item(actor,w,output)
            return {'success':True,'message':f'{output} is ready. Paid {service["price"]:g} gold.'}
        if action=='gamble':
            if loc not in ('the-prancing-pony','long-branch-saloon','mos-eisley-cantina'):raise ValueError('Find a tavern or cantina game table.')
            self.cooldown(actor,'gamble',10)
            return m.gamble(actor,w,positive(args.get('amount',1),1000))
        if action=='npc-talk':
            npc=next((n for n in room.get('npcs',[]) if n['id']==target),None)
            if not npc:raise ValueError('That NPC is not here.')
            message=str(args.get('message','')).strip()
            if not message or len(message)>4000:raise ValueError('Write between 1 and 4000 characters.')
            self.cooldown(actor,'npc',3)
            m.record_npc_interaction(actor,npc['name'],p['name'])
            pending=[j for j in self.store.documents('npc-job:') if j.get('actor')==actor and j.get('status')=='pending']
            if len(pending)>=2:raise ValueError('Wait for your current conversations to finish.')
            job_id=uuid.uuid4().hex
            self.store.write('npc-job:'+job_id,{'id':job_id,'actor':actor,'npc':target,'room':room_id,
                'status':'pending','message':message,'scene':room['name']+' — '+room.get('subtitle','')+
                '\n'+room.get('description','')+
                '\nSeasonal decorations: '+room_experience(room,catalog,
                    holiday=holiday_state(self.store,room=room)).get('seasonal_description','')+
                '\nLandmarks: '+ '; '.join(landmark.get('label','')+': '+landmark.get('description','')
                    for landmark in room.get('landmarks',[]))+
                '\nPaths here: '+', '.join(d.get('label',d['to']) for d in room.get('props',[]) if d.get('to'))+
                '\nAvailable activities: '+', '.join(activity['label'] for activity in room_experience(room,catalog)['activities'])+
                '\n'+m.get_npc_memory_context(actor,npc['name'],p['name']),
                'history_key':f'npc-history:{actor}:{target}'})
            return {'success':True,'job_id':job_id,'message':npc['name']+' is considering your words…'}
        if action in ('buy','sell'):
            npc=str(args.get('npc',''))
            shop=self.shop(actor,room_id,npc)
            item=next((i for i in shop['stock'] if i['name']==target),None)
            qty=positive(args.get('quantity',1),100)
            if action=='sell' and target in shop['buy_prices']:item={'name':target,'price':shop['buy_prices'][target]*2}
            if not item:raise ValueError('That item is not traded in this shop.')
            price=item['price']*qty*(1 if action=='buy' else .5)
            stock=self.store.read('shop:'+npc,{})
            if stock.get('_restocked',0)+86400<time.time():stock={'_restocked':time.time()}
            initial=item.get('max_stock',item.get('stock',100))
            available=stock.get(target,initial)
            if initial==-1:available=1000000
            if action=='buy':
                if available<qty:raise ValueError('There is not enough stock.')
                if p['shared']['gold_cents']<round(price*100):raise ValueError('You do not have enough gold.')
                m.spend_currency(actor,w,price)
                if item.get('companion_id'):
                    if qty!=1:raise ValueError('Welcome one companion at a time.')
                    adopted=m.adopt_companion(actor,item['companion_id'])
                    if not adopted.get('success'):raise ValueError(adopted['message'])
                elif item.get('category')=='lodging':m.restore_full_hp(actor,w)
                else:m.add_item(actor,w,target,qty)
                stock[target]=available-qty
                m.record_purchase_from_npc(actor,shop['npc']['name'],target,price,p['name'])
            else:
                if not m.has_item(actor,w,target,qty):raise ValueError('You do not own that quantity.')
                m.remove_item(actor,w,target,qty)
                m.add_currency(actor,w,price)
                stock[target]=available+qty
            self.store.write('shop:'+npc,stock)
            return {'success':True,'message':f"{'Bought' if action=='buy' else 'Sold'} {qty} × {target} for {price:g} gold."}
        if action=='tame':
            animal=next((a for a in catalog['animals'] if a['id']==target),None)
            if not animal:raise ValueError('That animal is not in these wilds.')
            self.cooldown(actor,'tame',45)
            if room.get('wilds'):
                m.companions_config['companions'].setdefault(target,dict(animal,emoji='🐾',description=animal.get('line',''),starting_loyalty=animal.get('starting_loyalty',50)))
                m.companions_config.setdefault('taming_info',{})[target]={'can_tame':True,'locations':[loc],'difficulty':animal.get('difficulty',10),'failure_effects':['flees']}
            words=str(args.get('words',''))[:4000]
            setup=self.taming_setup(actor,w,words)
            prepared=companion_actions.consume_assistance(self,actor,room_id,'tame')
            if prepared:
                setup['total_bonus']+=prepared['bonus']
                setup['bonus_sources']['prepared_companion']=prepared
            result=m.tame_companion(actor,target,p['name'],room.get('wildlife_location',loc),
                approach_bonus=m.get_equipment_bonuses(actor,w).get('taming',0)+approach_bonus(words)+prepared.get('bonus',0))
            if 'roll' in result:
                result.update(setup=setup,bonus_sources=setup['bonus_sources'],total=result.get('modified_roll'))
            elif prepared:companion_actions.restore_assistance(self,actor,prepared)
            if result.get('success') and 'roll' in result:
                result['quest_progress']=self.quests.record_tame(actor,w,p['name'],target,room.get('wildlife_location',loc))
            if result.get('damage'):result['injury']=m.take_damage(actor,w,result['damage'],'Wild '+target)
            if 'poisoned' in result.get('message',''):m.add_status_effect(actor,'poison',5,2,'Wild '+target)
            return result
        if action=='companion-activate':return m.activate_made_companion(actor,str(args.get('id',args.get('item_id',target))))
        if action=='companion-name':return m.rename_companion(actor,target,str(args.get('name',''))[:60])
        if action=='companion-follow':return m.switch_active_companion(actor,target)
        if action=='companion-rest':return m.rest_companion(actor)
        if action=='companion-pet':return companion_care.pet(m.care_companion(actor,target))
        if action=='companion-release':
            result=m.release_companion(actor,target)
            companion_actions.clear_released(self,actor,result['companion']['instance_id'])
            return result
        if action=='feed':
            return self.use_item(actor,room_id,target,args.get('id',args.get('item_id')),feed=True,companion=args.get('companion'))
        if action=='companion-repair':
            pet=m.care_companion(actor,target)
            body=m.companion_body(actor,pet)
            if pet.get('source_made_id') and body is None:raise ValueError('This robot body is no longer in your bag.')
            if body and body.get('placed'):raise ValueError('Put this robot in your bag before repairing it.')
            return companion_care.repair(pet,p['shared']['inventory'],args.get('material'),args.get('quantity'),
                definition=m.companions_config['companions'].get(pet['id'],{}),body=body)
        if action=='use':
            return self.use_item(actor,room_id,target,args.get('id',args.get('item_id')),share=bool(args.get('share')))
        if action=='rest':
            if not (room.get('rest_station') or room_id in ('hearth','bedroom','nest')):raise ValueError('Rest at home, an inn, or the Crossroads.')
            self.cooldown(actor,'rest',60)
            return m.restore_full_hp(actor,w)
        if action=='hack':
            return hacking.action(self,actor,room_id,args,catalog['hacking'])
        if action=='party-create':return m.create_party(actor,w,p['name'])
        if action=='party-invite':return m.invite_to_party(actor,target.lower(),target)
        if action=='party-join':return m.join_party(actor,p['name'],target or None)
        if action=='party-decline':return m.decline_party_invite(actor,target or None)
        if action=='party-kick':return m.kick_from_party(actor,target.lower())
        if action=='party-leave':return m.leave_party(actor)
        if action=='dungeon-start':
            if not any(d['dungeon_id']==target for d in catalog['dungeons']):raise ValueError('Unknown dungeon in this region.')
            return m.start_dungeon_run(actor,w,target)
        if action=='dungeon-ability':return dungeon_ability(self,actor,w,args)
        if action=='dungeon-recover':return m.roll_downed(actor,w)
        if action=='dungeon-pass':return m.advance_dungeon_turn(actor,w)
        if action=='dungeon-attack':return m.attack_dungeon_target(actor,w,target)
        if action=='dungeon-flee':return m.flee_dungeon(actor,w)
        if action=='dungeon-role':return m.set_dungeon_role(actor,w,target)
        if action=='competitive':
            p['competitive']=bool(args.get('enabled',False))
            return {'success':True,'message':'Competitive play '+('enabled.' if p['competitive'] else 'disabled.')}
        if action in ('duel-challenge','duel-accept','duel-attack','duel-flee','steal'):
            return self.competitive(actor,room_id,action,args)
        if action in ('event-start','event-respond'):
            return self.event(actor,room_id,action,args)
        if action=='gift':
            name=str(args.get('item',''));quantity=positive(args.get('quantity',1),100)
            carried=[item.get('name','') if isinstance(item,dict) else str(item) for item in p['shared']['inventory']]
            name=next((actual for actual in carried if actual.casefold()==name.casefold()),name)
            units=deepcopy(food_units(p['shared'],self.consumables).get(name,[])[:quantity])
            result=m.transfer_item(actor,target.lower(),w,name,quantity)
            if result.get('success') and units:
                food_units(p['shared'],self.consumables)
                receiving=m.get_player(target.lower())['shared']
                rows=food_units(receiving,self.consumables)[name]
                rows[-len(units):]=units
            return result
        if action=='gift-gold':return m.transfer_currency(actor,target.lower(),w,positive(args.get('quantity',1),100000))
        if action=='gift-made':return m.transfer_crafted_item(actor,target.lower(),w,str(args.get('item','')))
        if action=='gift-perishable':return m.transfer_perishable_item(actor,target.lower(),w,str(args.get('item','')),positive(args.get('quantity',1),100))
        raise ValueError('Unknown game action.')

    def competitive(self,actor,room_id,action,args):
        p,room,w,loc=self.context(actor,room_id)
        if room.get('region','home')=='home':raise ValueError('Competitive play stays outside Home.')
        target=str(args.get('target','')).lower()
        if action in ('steal','duel-challenge'):
            if target==actor or not self.store.read('player:'+target):raise ValueError('Choose another player who has entered the game.')
            other=self.players.get_player(target)
            presence=read(self.store.world/'presence.json',{})
            target_room=next((r.get('room') for n,r in presence.items() if n.lower()==target),None)
            if target_room!=room_id:raise ValueError('That player is not here.')
            self.cooldown(actor,action+':'+target,300)
            if action=='steal':
                if not p.get('competitive') or not other.get('competitive'):raise ValueError('Both players must opt in to pickpocketing.')
                roll=random.randint(1,20)
                if roll<13:return {'success':False,'message':'Your attempt was noticed. No gold changed hands.','roll':roll}
                cents=min(other['shared']['gold_cents'],int(other['shared']['gold_cents']*.1))
                other['shared']['gold_cents']-=cents;p['shared']['gold_cents']+=cents
                return {'success':True,'message':f'You lifted {cents/100:g} gold.','roll':roll}
            duel_id=uuid.uuid4().hex[:12]
            duel={'id':duel_id,'players':[actor,target],'room':room_id,'status':'offered','expires':time.time()+120,'turn':actor,'hp':{actor:100,target:100}}
            self.store.write('duel:'+duel_id,duel)
            return {'success':True,'message':'Duel offered. The other player must accept.','duel':duel}
        duel=self.store.read('duel:'+str(args.get('duel','')))
        if not duel or actor not in duel['players'] or duel['room']!=room_id:raise ValueError('That duel is not available to you here.')
        if action in ('duel-accept','duel-attack'):
            present=read(self.store.world/'presence.json',{})
            places={n.lower():v.get('room') for n,v in present.items()}
            if any(places.get(n)!=room_id for n in duel['players']):raise ValueError('Both duelists must be here to continue.')
        if action=='duel-accept':
            if actor!=duel['players'][1] or duel['status']!='offered' or duel['expires']<time.time():raise ValueError('This challenge cannot be accepted.')
            duel['status']='active'
        elif action=='duel-flee':
            if duel['status']!='active':raise ValueError('This duel is not active.')
            duel['status']='finished'
        else:
            if duel['status']!='active' or duel['turn']!=actor:raise ValueError('It is not your turn in an active duel.')
            other=next(n for n in duel['players'] if n!=actor)
            roll=random.randint(1,20)
            damage=random.randint(1,6) if roll>=10 else 0
            duel['hp'][other]=max(0,duel['hp'][other]-damage)
            duel['turn']=other
            if duel['hp'][other]==0:duel['status']='finished'
        self.store.write('duel:'+duel['id'],duel)
        return {'success':True,'duel':duel,'message':f"Duel {duel['status']}: "+', '.join(f'{n}: {hp} HP' for n,hp in duel['hp'].items())}

    def event(self,actor,room_id,action,args):
        p,room,w,loc=self.context(actor,room_id)
        templates=read(ENGINE/'event_templates.json',{})
        scenarios=templates.get('worlds',{}).get(w,{}).get('scenarios',[])
        if not scenarios:raise ValueError('World events take place in the adventure regions.')
        key='world-event:'+w
        event=self.store.read(key)
        if event and not event.get('resolved') and event['expires']<time.time():settle_event(self,key,event,'timeout')
        if action=='event-start':
            if not event or event.get('resolved'):
                prior=self.store.read('event-created:'+w,0)
                if prior+3*86400>time.time():return {'success':True,'message':'The region is resting after its last event.','event':event}
                scenario=random.choice(scenarios)
                event={'id':uuid.uuid4().hex,'room':room_id,'title':scenario['title'],'hook':scenario['hook'],
                    'expires':time.time()+12*3600,'participants':{},'messages':[],'stage':'discovery',
                    'base_xp':random.randint(20,50),'resolved':False}
                self.store.write(key,event);self.store.write('event-created:'+w,time.time())
            return {'success':True,'message':event['title']+'\n'+event['hook'],'event':event}
        if not event or event.get('resolved'):raise ValueError('There is no active event.')
        pending=event.get('narrating')
        if pending:
            job=self.store.read('npc-job:'+pending,{})
            if job.get('status')=='pending':raise ValueError('Wait for the storyteller to answer the last contribution.')
        message=str(args.get('message','')).strip()
        if not message or len(message)>4000:raise ValueError('Describe your contribution in 1–4000 characters.')
        self.cooldown(actor,'event',15)
        person=event['participants'].setdefault(actor,{'name':p['name'],'contributions':0})
        person['contributions']+=1
        event['messages'].append({'by':p['name'],'text':message,'stage':event['stage']})
        job_id=uuid.uuid4().hex;event['narrating']=job_id
        self.store.write(key,event)
        self.store.write('npc-job:'+job_id,{'id':job_id,'actor':actor,'npc':'narrator','room':room_id,'status':'pending',
            'message':p['name']+': '+message,'scene':event['title']+'\n'+event['hook']+'\nCurrent stage: '+event['stage']+
                '. Run this collaborative scene through discovery, confrontation, and resolution. Respond in 2–4 sentences. '+
                'Append [STAGE_ADVANCE] when their actions advance the scene. Append [EVENT_COMPLETE] only when the situation is resolved. '+
                'Acknowledge their contribution, leave player choices open, and never invent rewards.',
            'history_key':'event-history:'+event['id'],'event_key':key,'event_id':event['id']})
        return {'success':True,'message':'Your contribution is recorded. The storyteller is responding…','job_id':job_id}

    def execute(self, actor, room_id, action, args, request_id):
        """Commit action, discoveries, chat and receipt together; retries replay first."""
        if not isinstance(request_id,str) or not 8<=len(request_id)<=100:
            raise ValueError('A unique request id is required.')
        request=json.dumps({'room':room_id,'action':action,'args':args},sort_keys=True)
        with self.store.transaction():
            old=self.store.replay(actor,request_id,request)
            if old is not None:return old
            p,room,w,loc=self.context(actor,room_id)
            wd=p['worlds'][w]
            party=self.players.get_party(actor)
            party_quest=(party or {}).get('current_quest')
            borrowed=(action in ('collect','hunt','search') and wd.get('on_party_quest')
                and not wd.get('current_quest') and bool(party_quest) and party_quest.get('world_id')==w)
            if borrowed:wd['current_quest']=deepcopy(party['current_quest'])
            try:
                before=activity.capture(self,actor,room_id,action,args)
                result=dungeon_sessions.run(self,actor,room_id,action,args,
                    lambda:self.action(actor,room_id,action,args)) if action.startswith('dungeon-') and action not in dungeon_sessions.ACTIONS else self.action(actor,room_id,action,args)
            finally:
                if borrowed:wd['current_quest']=None
            journal=self.store.read('journal:'+actor,[])
            journal.append({'at':datetime.now(timezone.utc).isoformat(),'room':room_id,'action':action,'result':result})
            self.store.write('journal:'+actor,journal)
            guidance.observe(self,actor,room_id,action,args,result)
            field_guide.observe(self,actor,room_id,action,args,result)
            public=activity.publish(self,actor,room_id,action,args,result,before,request_id)
            result={'ok':True,'result':result,'state':self.snapshot(actor,room_id)}
            if public:result['activity']=public

            self.players.flush()
            self.store.receipt(actor,request_id,request,result)
            return result
