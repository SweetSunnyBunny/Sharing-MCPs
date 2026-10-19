
"""Small, executable next steps shared by browser and remote hands."""
from collections import deque
import json
import math
import time
from .recipes import available_here
from . import categories
from .players import ENGINE

_room_cache={}

JOURNEYS = {
    'fish-supper': {'title':'From water to supper', 'icon':'🎣', 'steps':[
        ('fish','Catch a fish'), ('keep','Keep your catch'), ('meal','Prepare a meal')]},
    'welcome-companion': {'title':'Welcome a travelling friend', 'icon':'🐾', 'steps':[
        ('tame','Bond with a wild companion'), ('companion-follow','Walk together'), ('feed','Share some food')]},
    'build-robot': {'title':'A second life for scrap', 'icon':'🤖', 'steps':[
        ('gather','Gather robot materials'), ('robot','Craft a robot'), ('companion-activate','Activate your robot')]}}
ACTIONS = frozenset(('goal-pin','goal-clear'))

def rooms(game):
    files={}
    for base in (game.root/'world',game.store.world):
        if base.exists():
            for path in base.glob('*.json'):files[path.stem]=path
    signature=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in files.values())
    key=(str(game.root),str(game.store.world))
    cached=_room_cache.get(key)
    if cached and cached[0]==signature:return cached[1]
    result={}
    for path in files.values():
        try:
            row=json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(row,dict) and 'rows' in row:result[path.stem]=row
        except (ValueError,OSError):continue
    if len(_room_cache)>=8:_room_cache.clear()
    _room_cache[key]=(signature,result)
    return result

def route(game,start,destination,world=None):
    world=world or rooms(game)
    queue=deque([(start,[])]);seen={start}
    accessible=getattr(game,'can_enter',lambda room:True)
    while queue:
        here,path=queue.popleft()
        if here==destination:return path
        for door in world.get(here,{}).get('props',[]):
            nxt=door.get('to')
            if nxt not in world or nxt in seen or not accessible(nxt):continue
            seen.add(nxt)
            queue.append((nxt,path+[{'room':nxt,'name':world[nxt].get('name',nxt),
                'label':door.get('label','Take this path'),'arrival':door.get('arriveAt')}]))
    return None

def readiness(game,actor,activity,region=None):
    value=game.store.read(f'cooldown:{actor}:{activity}',0)
    until=value if isinstance(value,(int,float)) and math.isfinite(value) else 0
    remaining=max(0,math.ceil(until-time.time()))
    if activity in ('hunt','collect','gather','search'):
        region=region or getattr(game,'active_region','home')
        inner=game.quests.check_cooldown(actor,region,activity) or 0
        if inner>remaining:remaining=inner;until=time.time()+inner
    return {'available':not remaining,'ready_at':until,'seconds_remaining':remaining,
        'reason':f'Ready in {remaining} seconds.' if remaining else 'Ready now.'}

def belongings(state):
    result=[];condition_names=set()
    for item in state.get('inventory_condition_items',[]):
        condition_names.add(item['name'])
        result.append(dict(item,source='inventory',choices={'id':item['id']}))
    for name,quantity in state.get('inventory',{}).items():
        if name in condition_names:continue
        result.append({'name':name,'quantity':quantity,'source':'inventory',
            'actions':state.get('item_actions',{}).get(name,{}),'choices':{'target':name}})
    for item in state.get('made',[]):
        result.append(dict(item,source='made',actions=state.get('made_actions',{}).get(item['id'],{}),
            choices={'id':item['id'],'target':item['id']}))
    for item in result:
        a=item['actions'];item['servings_left']=a.get('servings_left',0)
        item['wearable_slot']=a.get('slot')
        item['available_actions']=[{'action':action,'choices':item['choices']} for action,flag in
            [('use','usable'),('feed','feedable'),('equip','equippable'),('repair','repairable'),
             ('companion-activate','activatable')] if a.get(flag)]
    return result

def quest_step(game,actor,room_id,quest,party=False,world=None,region=None):
    if not quest:return None
    world=world or rooms(game);progress=quest.get('progress',0);total=quest.get('target_amount',1)
    region=region or quest.get('world_id') or game.active_region
    done=progress>=total;required=quest.get('required_location','any')
    destinations=[]
    for rid,row in world.items():
        if region and ('home' if row.get('region','home')=='hub' else row.get('region','home'))!=region:continue
        if done:
            match=any(n.get('name','').casefold()==str(quest.get('given_by','')).casefold() for n in row.get('npcs',[]))
        else:
            location=row.get('game_location',rid)
            if quest.get('action_type') in ('hunt','tame'):location=row.get('wildlife_location',location)
            match=required in ('','any',location) and objective_choices(game,rid,row,quest) is not None
        if match:
            path=route(game,room_id,rid,world)
            if path is not None:destinations.append((len(path),rid,path))
    destination=min(destinations,key=lambda row:row[0]) if destinations else None
    action=('party-quest-complete' if party else 'quest-complete') if done else quest.get('action_type','search')
    choices=(objective_choices(game,destination[1],world[destination[1]],quest) or {}) if destination and not done else {}
    ready=readiness(game,actor,action,region)
    if done:ready={'available':True,'reason':'Return to the quest giver.','ready_at':0,'seconds_remaining':0}
    here=bool(destination and destination[1]==room_id)
    target_label=quest.get('target_item','quest steps')
    if action=='hack' and str(target_label).casefold().replace('_',' ') in ('any','system','hack success'):
        target_label='successful hacks'
    action_label={'hunt':'Hunt','collect':'Collect','gather':'Gather','tame':'Befriend','hack':'Hack','search':'Search'}.get(action,action)
    choice_label=quest.get('target_item','') if action=='tame' else choices.get('target',quest.get('target_item',''))
    return {'id':quest.get('quest_id'),'title':quest.get('title','Your quest'),'icon':'📜',
        'description':quest.get('objective',quest.get('description','')),
        'progress':progress,'total':total,'remaining':max(0,total-progress),
        'step':f"Return to {quest.get('given_by','the quest giver')}." if done else f"{max(0,total-progress)} more {target_label} needed.",
        'destination':destination[1] if destination else None,'route':destination[2] if destination else [],
        'action':action,'choices':choices,'available':here and ready['available'],
        'label':'Turn in quest' if done else (action_label+' '+str(choice_label)).strip(),
        'ready_blocked':not here,
        'reason':ready['reason'] if here else 'Follow the route to the quest location.' if destination else 'No accessible route is currently available.',
        'ready_at':ready.get('ready_at',0),'seconds_remaining':ready.get('seconds_remaining',0)}

def objective_choices(game,rid,row,quest):
    """[commons:quests] Exact playable catalog choices, including species IDs."""
    kind=quest.get('action_type');region=row.get('region','home')
    loc=row.get('game_location',rid);wildlife=row.get('wildlife_location',loc)
    normal=lambda value:' '.join(str(value or '').casefold().replace('_',' ').split())
    target=normal(quest.get('target_item'))
    if kind in ('collect','search'):return {}
    if kind=='hunt':
        options=game.quests.hunting_config.get('hunting_areas',{}).get(region,{}).get(wildlife,{}).get('animals',[])
    elif kind=='gather':
        areas=game.quests.gathering_config.get('gathering_areas',{}).get(region,{})
        options=areas.get(rid,areas.get(loc,{})).get('resources',[])
    elif kind=='tame':
        options=[dict(game.players.companions_config['companions'].get(ident,{}),id=ident)
            for ident,spec in game.players.companions_config.get('taming_info',{}).items()
            if ident!='fox' and spec.get('can_tame') and wildlife in spec.get('locations',[])]
    elif kind=='hack':
        if not hasattr(game,'_quest_hacking_catalog'):
            game._quest_hacking_catalog=json.loads((ENGINE/'hacking_config.json').read_text(encoding='utf-8-sig'))
        options=game._quest_hacking_catalog.get('hacking_areas',{}).get(region,{}).get(loc,{}).get('systems',[])
    else:return None
    for option in options:
        name=normal(option.get('name'));ident=normal(option.get('id'))
        matches=target in (name,ident)
        if kind=='hack':matches=target in ('any','system','hack success') or bool(target and name and (target in name or name in target))
        if matches:return {'target':option['id'] if kind=='tame' else option['name']}
    return None

def chain_steps(game,actor,room_id,region,world=None):
    """[commons:quests] One shared giver/route/start contract for chain cards."""
    world=world or rooms(game);player=game.players.get_player(actor)
    active=player['worlds'][region].get('current_quest')
    party=(game.players.get_party(actor) or {}).get('current_quest')
    held_party=bool(party and party.get('world_id')==region)
    result=[]
    for chain in game.players.get_available_quest_chains(actor,region):
        destinations=[]
        for rid,row in world.items():
            if row.get('region')!=region:continue
            if any(npc.get('name','').casefold()==chain.get('given_by','').casefold() for npc in row.get('npcs',[])):
                path=route(game,room_id,rid,world)
                if path is not None:destinations.append((len(path),rid,path))
        destination=min(destinations,key=lambda item:item[0]) if destinations else None
        here=bool(destination and destination[1]==room_id)
        completed=chain['status']=='completed'
        blocked=completed or bool(active) or held_party or not here
        reason=('Storyline completed.' if completed else 'Finish your current quest first.' if active
            else 'Finish your party quest in this region first.' if held_party
            else 'Follow the route to the quest giver.' if destination and not here
            else 'No accessible route to the quest giver.' if not destination else 'Ready now.')
        result.append(dict(chain,action='quest-chain',choices={'target':chain['chain_id']},
            label=('Finish storyline' if chain.get('pending_completion') else 'Continue storyline' if chain['status']=='in_progress' else 'Begin storyline'),
            available=not blocked,reason=reason,ready_blocked=blocked,ready_at=0,seconds_remaining=0,
            destination=destination[1] if destination else None,route=destination[2] if destination else [],
            region=region,active=bool(active and active.get('chain_id')==chain['chain_id'])))
    return result

def snapshot(game,actor,room_id,state):
    world=rooms(game);catalog=state['catalog'];next_actions=[]
    def choice(label,action,args=None,activity=None,reason=None):
        ready=readiness(game,actor,activity or action)
        if reason:ready.update(available=False,reason=reason)
        next_actions.append(dict(label=label,action=action,choices=args or {},**ready))
    if state.get('held_fish'):
        choice('Keep your waiting catch','keep');choice('Release your waiting catch','release')
    elif catalog.get('fishing'):choice('Cast a line','fish')
    for quarry in catalog.get('hunts',[])[:4]:choice('Hunt '+quarry['name'],'hunt',{'target':quarry['name']})
    for resource in catalog.get('gathering',[])[:5]:choice('Gather '+resource['name'],'gather',{'target':resource['name']},'gather')
    for animal in catalog.get('animals',[])[:3]:choice('Approach '+animal.get('name',animal['id']),'tame',{'target':animal['id']},'tame')
    party_quest=(state.get('party') or {}).get('current_quest')
    held_party=bool(party_quest and party_quest.get('world_id')==game.active_region)
    for quest in catalog.get('quests',[])[:3]:
        if not state.get('quest') and not held_party:choice('Accept '+quest['title'],'quest-accept',{'target':quest['quest_id']})
    quests=[q for q in [quest_step(game,actor,room_id,state.get('quest'),world=world,region=game.active_region),
        quest_step(game,actor,room_id,(state.get('party') or {}).get('current_quest'),True,world)] if q]
    for region,data in game.players.get_player(actor)['worlds'].items():
        quest=data.get('current_quest')
        if quest and region!=game.active_region and not any(q['id']==quest.get('quest_id') for q in quests):
            step=quest_step(game,actor,room_id,quest,world=world,region=region)
            if step:quests.append(step)
    pinned=game.store.read('goal:'+actor)
    journey=None
    if pinned and pinned.get('id') in JOURNEYS:
        spec=JOURNEYS[pinned['id']];index=min(pinned.get('step',0),len(spec['steps']))
        journey={'id':pinned['id'],**{k:spec[k] for k in ('title','icon')},'progress':index,'total':len(spec['steps']),
            'complete':index==len(spec['steps']),'step':'Journey complete.' if index==len(spec['steps']) else spec['steps'][index][1]}
        if index<len(spec['steps']):
            expected=spec['steps'][index][0];targets=[]
            if expected in ('fish','tame'):
                for rid,row in world.items():
                    region=row.get('region','home');loc=row.get('game_location',rid)
                    matches=bool(game.room_fish_pool(row,region,loc)) if expected=='fish' else bool(row.get('wilds') or any(loc in info.get('locations',[]) for info in game.players.companions_config.get('taming_info',{}).values() if info.get('can_tame')))
                    if matches:
                        path=route(game,room_id,rid,world)
                        if path is not None:targets.append((len(path),rid,path))
                if targets:
                    _,destination,path=min(targets,key=lambda t:t[0]);journey.update(destination=destination,route=path)
                if not journey.get('route'):
                    option=next((a for a in next_actions if a['action']==expected),None)
                    if option:journey.update(option)
            elif expected=='keep' and state.get('held_fish'):journey.update(action='keep',choices={},available=True)
            elif expected=='feed':
                item=next((i for i in state['belongings'] if i['actions'].get('feedable')),None)
                if item:journey.update(action='feed',choices=item['choices'],available=True)
            elif expected in ('meal','robot'):
                recipes=[(ident,r,False) for ident,r in game.players.crafting_config['recipes'].items()
                    if r.get('type')==('companion' if expected=='robot' else 'food')]
                if expected=='meal':recipes += [(ident,r,True) for ident,r in json.loads((game.root/'world/recipes.json').read_text(encoding='utf-8-sig')).get('recipes',{}).items() if r.get('type') in ('food','drink') or r.get('uses') or r.get('servings')]

                have=categories.by_category(state['inventory'],categories.load(game.root))
                for ident,recipe,home in recipes:
                    needs=recipe.get('materials',recipe.get('needs',{}))
                    count=(lambda name:have.get(name,0)) if home else (lambda name:sum(q for n,q in state['inventory'].items() if n.casefold()==name.casefold()))
                    missing={name:max(0,int(amount)-count(name)) for name,amount in needs.items()}
                    missing={name:q for name,q in missing.items() if q}
                    uses_fish=expected=='meal' and 'fish' in {str(n).casefold() for n in needs}
                    for rid,row in world.items():
                        if not available_here(recipe,row.get('region','home'),rid):continue
                        path=route(game,room_id,rid,world)
                        if path is not None:targets.append((bool(missing),not uses_fish,sum(missing.values()),len(path),ident,rid,path,missing,home))
                if targets:
                    *_,ident,destination,path,missing,home=min(targets,key=lambda t:t[:5])
                    journey.update(action='craft',choices={'target':ident},destination=destination,route=path,available=not path and not missing,
                        requirements=missing,reason='Need '+', '.join(f'{q} {name}' for name,q in missing.items()) if missing else 'Ready here.' if not path else 'Follow the path to a suitable workshop.')
            elif expected=='gather':
                path=route(game,room_id,'scavengers-reach',world)
                if path is not None:journey.update(destination='scavengers-reach',route=path)
                option=next((a for a in next_actions if a['action']=='gather'),None)
                if room_id=='scavengers-reach' and option:journey.update(option)
            elif expected=='companion-activate':
                robot=next((i for i in state['belongings'] if i['actions'].get('activatable')),None)
                if robot:journey.update(action='companion-activate',choices={'id':robot['id']},available=True)
    notices=[]
    if state['hp']<state['max_hp']:notices.append(f"Your health is {state['hp']}/{state['max_hp']} HP.")
    if state.get('held_fish'):notices.append('A catch is waiting for your Keep or Release decision.')
    damaged=[i['name'] for i in state['belongings'] if i.get('max_durability') and i.get('durability',0)<i['max_durability']]
    if damaged:notices.append('Gear needing care: '+', '.join(damaged[:4])+'.')
    injured=[pet.get('custom_name') or pet.get('name','Companion') for pet in state['companions'] if pet.get('care',{}).get('hp',1)<pet.get('care',{}).get('max_hp',1)]
    if injured:notices.append('Companions needing care: '+', '.join(injured[:4])+'.')
    jobs=[{'id':j['id'],'npc':j.get('npc'),'status':j['status']} for j in game.store.documents('npc-job:')
          if j.get('actor')==actor and j.get('status') in ('pending','running','complete','failed')][-6:]
    from .room_chat import read
    replies=[]
    book=read(game.store,room_id)['lines'];own={row['id'] for row in book if str(row.get('by','')).casefold() in (actor.casefold(),state['name'].casefold())}
    for row in book:
        if row.get('reply_to') in own and str(row.get('by','')).casefold()!=state['name'].casefold():
            replies.append({k:row[k] for k in ('id','by','text','reply_to') if k in row})
    chains=[]
    for region in game.players.get_player(actor)['worlds']:
        for chain in chain_steps(game,actor,room_id,region,world):
            if chain['status']=='in_progress' and not chain['active']:chains.append(chain)
    return {'heading':'Continue your adventure','notices':notices,'quests':quests,'chains':chains,'journey':journey,
        'journeys':[{'id':key,'title':v['title'],'icon':v['icon'],'steps':[s[1] for s in v['steps']]} for key,v in JOURNEYS.items()],
        'next_actions':next_actions,'jobs':jobs,'replies':replies[-5:],'invitations':state.get('party_invites',[])}

def execute(game,actor,room_id,action,args):
    old=game.store.read('goal:'+actor)
    if action=='goal-clear':game.store.write('goal:'+actor,None);return {'success':True,'changed':bool(old),'message':'Your journey bookmark is cleared.'}
    ident=args.get('target')
    if ident not in JOURNEYS:raise ValueError('Choose a journey from guidance.journeys.')
    if old and old.get('id')==ident:return {'success':True,'changed':False,'message':'This journey is already bookmarked.'}
    game.store.write('goal:'+actor,{'id':ident,'step':0})
    return {'success':True,'message':'Bookmarked '+JOURNEYS[ident]['title']+'.'}

def observe(game,actor,room_id,action,args,result):
    if not result.get('success'):return
    goal=game.store.read('goal:'+actor)
    if not goal or goal['id'] not in JOURNEYS:return
    spec=JOURNEYS[goal['id']];index=goal['step']
    if index>=len(spec['steps']):return
    expected=spec['steps'][index][0];match=expected==action
    if expected in ('meal','robot') and action in ('craft','home-craft'):
        recipe=game.players.crafting_config['recipes'].get(args.get('target'),{})
        if action=='home-craft':recipe=json.loads((game.root/'world/recipes.json').read_text(encoding='utf-8-sig')).get('recipes',{}).get(args.get('target'),{})
        match=recipe.get('type') in ('food','drink') if expected=='meal' else recipe.get('type')=='companion'
    if goal['id']=='build-robot' and expected=='gather':match=match and room_id=='scavengers-reach'
    if match:goal['step']+=1;game.store.write('goal:'+actor,goal)
    if goal['id']=='welcome-companion' and goal['step']==1 and game.players.get_active_companion(actor):
        goal['step']=2;game.store.write('goal:'+actor,goal)
