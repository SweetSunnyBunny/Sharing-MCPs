"""One-time, additive legacy profile transfer. Never opens an old game itself."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
import hashlib

from .players import Players

SCALES={'heros_journey':Decimal('1'),'dusty_trails':Decimal('2'),
        'black_serenity':Decimal('.5'),'hearthlight':Decimal('1')}

def union(left,right):
    result=deepcopy(left)
    for item in right:
        if item not in result:result.append(deepcopy(item))
    return result

def merge_progress(current,old,key=''):
    """Accumulate independent counters, retain distinct events and best milestones."""
    if isinstance(current,dict) and isinstance(old,dict):
        return {k:merge_progress(current[k],old[k],k) if k in current and k in old
                else deepcopy(current[k] if k in current else old[k]) for k in current.keys()|old.keys()}
    if isinstance(current,list) and isinstance(old,list):return union(current,old)
    if isinstance(current,bool) and isinstance(old,bool):return current or old
    if isinstance(current,(int,float)) and isinstance(old,(int,float)):
        return max(current,old) if key in ('current_step','level','current_chapter','chapter','step','bond_level') else current+old
    if key.startswith('first_') and current and old:return min(current,old)
    if key.startswith('last_') and current and old:return max(current,old)
    if current in (None,'','stranger','newcomer'):return deepcopy(old)
    return deepcopy(current)

def transfer(store, actor, source_id, source, source_hash, archive):
    """One atomic import with an account-bound receipt; safe even while the house runs."""
    marker='import:aisling-profile:'+source_id
    with store.transaction():
        receipt=store.read(marker)
        if receipt:
            if receipt['actor']!=actor or receipt['source_sha256']!=source_hash:
                raise ValueError('This source account was already transferred; review changes before another import.')
            return {**receipt['summary'],'already_imported':True}
        if not store.read('player:'+actor):raise ValueError('Choose an existing Commons character.')
        manager=Players(store);p=manager.get_player(actor);shared=p['shared']
        before={'xp':p.get('xp',0),'level':p.get('level',1),'gold_cents':shared['gold_cents']}
        added=Counter();made_count=0;gold=Decimal('0');conflicts=[]
        worlds=source.get('worlds',{})
        for region,world in worlds.items():
            if region not in SCALES:raise ValueError('Unknown source currency region: '+region)
            amount=Decimal(str(world.get('currency',0)))
            if not amount.is_finite() or amount<0:raise ValueError('Invalid source balance.')
            gold+=amount*SCALES[region]*100
            for item in world.get('inventory',[]):
                name=item.get('name') if isinstance(item,dict) else item
                qty=item.get('quantity',1) if isinstance(item,dict) else 1
                if not isinstance(name,str) or not name or isinstance(qty,bool) or not isinstance(qty,int) or qty<0:
                    raise ValueError('Invalid source inventory entry.')
                added[name]+=qty
            for i,item in enumerate(world.get('crafted_items',[])):
                if item.get('type') in ('food','potion','companion_treat'):
                    added[item['name']]+=max(0,int(item.get('durability',item.get('uses_left',1))))
                    continue
                made=deepcopy(item)
                made['id']='ais-'+hashlib.sha256(f'{source_id}:{region}:{i}'.encode()).hexdigest()[:16]
                made['what']=made['name'];made['made_by']=p['name']
                made['import_origin']={'source':source_id,'world':region,'index':i}
                shared['crafted_items'].append(made);made_count+=1
            shared['perishable_inventory']=union(shared['perishable_inventory'],world.get('perishable_inventory',[]))
            if region in p['worlds']:
                dest=p['worlds'][region]
                for key in ('quest_history','stats','reputation','titles','storyline_progress'):
                    if key in world:dest[key]=merge_progress(dest.get(key,[] if isinstance(world[key],list) else {}),world[key])
                if world.get('current_quest'):
                    if not dest.get('current_quest'):dest['current_quest']=deepcopy(world['current_quest'])
                    elif dest['current_quest']!=world['current_quest']:conflicts.append(region+': current Commons quest kept; old quest preserved in the source archive')
        for name,qty in added.items():shared['inventory'].extend([name]*qty)
        shared['gold_cents']+=int(gold.quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        p['xp']=int(p.get('xp',0))+int(source.get('xp',0))
        old_level=max(p.get('level',1),source.get('level',1))
        p['level']=max(old_level,manager._calculate_level(p['xp']))
        hp_bonus=sum(manager.achievements_config.get('level_rewards',{}).get(str(level),{}).get('max_hp_bonus',0)
                     for level in range(old_level+1,p['level']+1))
        injury=max(0,shared['max_hp']-shared['hp'])
        shared['max_hp']=max([shared['max_hp']]+[w.get('max_hp',100) for w in worlds.values()])+hp_bonus
        shared['hp']=max(1,shared['max_hp']-injury)
        for key in ('achievements','dungeon_titles'):
            p[key]=union(p.get(key,[]),source.get(key,[]))
        for key in ('global_stats','relationships','quest_chains','dungeon_stat_mods','dungeon_renown'):
            p[key]=merge_progress(p.get(key,{}),source.get(key,{}))
        if not p.get('title'):p['title']=source.get('title')
        if p.get('character_type','all')=='all':p['character_type']=source.get('character_type','all')
        
        for world in worlds.values():
            for slot,name in world.get('equipped',{}).items():
                if not name:continue
                if name not in shared['inventory'] and not any(i['name']==name for i in shared['crafted_items']):

                    shared['inventory'].append(name);added[name]+=1
                    conflicts.append('Recovered worn item from its equipped slot: '+name)
                if shared.get('equipped',{}).get(slot):continue
                shared['equipped'][slot]=name
                if slot=='weapon':
                    weapon=next((k for k,v in manager.weapons_config['weapons'].items() if v['name']==name),None)
                    if weapon:shared['equipped_weapon']=weapon
        companions=deepcopy(source.get('companions',[]))
        if not companions and source.get('companion'):companions=[deepcopy(source['companion'])]
        imported_pets=[]
        for i,pet in enumerate(companions):
            name=pet.get('custom_name') or pet.get('name')
            family_cloud=(actor=='admin' and manager._companion_id(pet.get('id'))=='wolf'
                           and str(name).casefold()=='cloud')
            if any(((c.get('custom_name') or c.get('name'))==name and c.get('id')==pet.get('id'))
                   or (family_cloud and c.get('_house',{}).get('family_body')=='cloud')
                   for c in p['companions']+p.get('family_pets',[])):
                conflicts.append('Existing companion kept: '+str(name));continue
            pet.setdefault('id',str(pet.get('name','companion')).lower().replace(' ','_'))
            pet['instance_id']='ais-'+hashlib.sha256(f'{source_id}:pet:{i}'.encode()).hexdigest()[:16]
            pet['_house']={'import_origin':source_id}
            if family_cloud:pet['_house']['family_body']='cloud'
            p['family_pets' if family_cloud else 'companions'].append(pet);imported_pets.append(name)
            if (not family_cloud and p.get('active_companion_index') is None and 'companion_follow_choice' not in p
                    and i==source.get('active_companion_index')):
                p['active_companion_index']=len(p['companions'])-1
        p['companion_slots']=max(p.get('companion_slots',10),len(p['companions'])+1)
        manager.flush()
        summary={'actor':actor,'source_name':source.get('name'),'xp_before':before['xp'],'xp_added':source.get('xp',0),'xp_after':p['xp'],
                 'level':p['level'],'gold_added':float(gold/100),'gold_after':shared['gold_cents']/100,
                 'items_added':sum(added.values()),'distinct_items':len(added),'crafted_items_added':made_count,
                 'companions_added':imported_pets,'notes':conflicts}
        store.write(marker,{'actor':actor,'source_sha256':source_hash,'source_archive':str(archive),'summary':summary})
        journal=store.read('journal:'+actor,[])
        journal.append({'at':datetime.now(timezone.utc).isoformat(),'action':'Your Aisling belongings came home','result':{'message':
            f"Recovered {summary['items_added']} carried items, {made_count} crafted things, {summary['xp_added']} XP and {summary['gold_added']:g} gold. "+
            'Companions: '+(', '.join(imported_pets) or 'none to add')+'. '+'. '.join(conflicts)}})
        store.write('journal:'+actor,journal)
        return summary
