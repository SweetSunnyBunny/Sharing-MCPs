
"""Public, bounded gameplay outcomes, never copies of private action payloads.

The caller supplies a verified character and commits this alongside the action.
Messages use an explicit vocabulary and selected catalog fields; journals, NPC
dialogue, approaches, chapters, terminal dossiers and wallet balances stay private.
"""
from copy import deepcopy
import math

from .room_chat import append_activity

def _label(value, fallback='something', limit=100):
    text=' '.join(str(value or '').split())
    return text[:limit] or fallback

def _number(value, fallback=0):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        return fallback
    return value

def capture(game, actor, room_id, action, args):
    """Keep only names and small before-values needed for genuine outcome/no-op checks."""
    p,room,world,_=game.context(actor,room_id)
    target=str(args.get('target',''))
    shared=p['shared']
    before={'by':_label(p['name'],'Adventurer',40), 'target':None,
            'hp':shared['hp'], 'max_hp':shared['max_hp'],
            'equipment':deepcopy(shared.get('equipment',{})),
            'equipped':deepcopy(shared.get('equipped',{})),
            'active_companion':p.get('active_companion_index'),
            'form':p.get('current_form','human'),
            'competitive':bool(p.get('competitive'))}
    if action in ('fish','keep','release'):before['fish']=deepcopy(game.held_fish(actor))
    groups={'hunt':'hunts','gather':'gathering','tame':'animals',
            'quest-accept':'quests','party-quest-start':'quests',
            'quest-chain':'chains','dungeon-start':'dungeons','hack':'hacking'}
    if action in groups:
        catalog=game.catalogs(actor,room_id)
        for row in catalog[groups[action]]:
            choices=(row.get('id'),row.get('name'),row.get('quest_id'),row.get('chain_id'),row.get('dungeon_id'))
            if any(str(choice).casefold()==target.casefold() for choice in choices if choice is not None):
                before['target']=row.get('name') or row.get('title') or row.get('id')
                break
        if action=='hunt' and before['target'] is None:
            matches=[row for row in catalog['hunts'] if target.casefold() in str(row.get('name','')).casefold()]
            if len(matches)==1:before['target']=matches[0]['name']
        if action=='hack':
            before['character']=next((row['name'] for row in catalog.get('terminal_characters',[])
                if row['id']==args.get('character')),None)
    for npc in room.get('npcs',[]):
        if npc['id']==target:before['npc']=npc['name']
    if action=='craft':
        recipe=game.players.crafting_config.get('recipes',{}).get(target)
        if recipe:before['target']=recipe['name']
    names=[]
    for item in shared.get('inventory',[])+shared.get('crafted_items',[])+shared.get('perishable_inventory',[]):
        row=item if isinstance(item,dict) else {'name':item}
        name=row.get('name',row.get('what',row.get('item','')))
        if name:names.append((row.get('id'),name,row))
    for ident,name,item in names:
        if str(name).casefold()==target.casefold() or ident and ident==args.get('id',args.get('item_id',target)):
            before['item']=name
            before['placed']=bool(item.get('placed'))
            before['durability']=item.get('durability')
            before['max_durability']=item.get('max_durability')
            break
    if action in ('repair','equip') and before.get('item'):
        _,owned=game.players._owned_item(actor,before['item'],args.get('id',args.get('item_id')))
        if owned:
            before['durability']=owned.get('durability')
            before['max_durability']=owned.get('max_durability')
    if action.startswith('gift'):
        item_arg=args.get('item')
        before['item']=next((name for _,name,_ in names if str(name).casefold()==str(item_arg).casefold()),None)
    other=game.store.read('player:'+target.lower()) if action in (
        'gift','gift-made','gift-perishable','gift-gold','party-invite','party-kick','steal','duel-challenge') else None
    if other:before['other']=_label(other.get('name'),target)
    ordinary=p.get('companions',[])
    companions=ordinary+p.get('family_pets',[])
    pet=None
    identifier=args.get('companion') if action=='feed' else target
    if str(identifier).isdigit():
        index=int(identifier)-1
        if 0<=index<len(ordinary):pet=ordinary[index]
    if pet is None:pet=next((pet for pet in companions if identifier in (
        pet.get('id'),pet.get('instance_id'),pet.get('custom_name'),pet.get('name'))),None)
    if pet is None and action in ('feed','companion-rest'):
        index=p.get('active_companion_index')
        if isinstance(index,int) and 0<=index<len(ordinary):pet=ordinary[index]
    if pet:
        before['pet']=_label(pet.get('custom_name') or pet.get('name') or pet.get('id'),'a companion')
        before['pet_hp']=pet.get('hp')
        before['pet_max_hp']=pet.get('max_hp')
    quest=p['worlds'][world].get('current_quest')
    party=game.players.get_party(actor)
    if action.startswith('party-quest'):quest=(party or {}).get('current_quest')
    if quest:
        before['quest']=quest.get('title') or 'a quest'
        before['quest_item']=quest.get('target_item')
    if action in ('story-start','story-respond'):
        before['story']=game.store.read(f'story:{actor}:{target}',{})
    if action=='backstory' and before.get('npc'):
        before['backstory']=deepcopy(game.players.get_discovered_backstories(actor,before['npc']))
    if action=='event-start':before['event']=game.store.read('world-event:'+world)
    if action in ('dungeon-attack','dungeon-ability'):
        dungeon=(party or {}).get('current_dungeon') or {}
        active=dungeon.get('active_room',{})
        enemy=next((enemy for enemy in active.get('enemies',[]) if str(enemy.get('name','')).casefold()==target.casefold()),None)
        if enemy:before['enemy']={'name':enemy['name'],'hp':enemy['hp'],'room':active.get('room_id')}
    if action=='duel-attack':
        duel=game.store.read('duel:'+str(args.get('duel','')),{})
        opponent=next((ident for ident in duel.get('players',[]) if ident!=actor),None)
        if opponent:
            before['duel_hp']=duel.get('hp',{}).get(opponent)
            before['duel_opponent']=opponent
            player=game.store.read('player:'+opponent,{})
            before['other']=_label(player.get('name'),'their opponent')
    return before

def _summary(game, actor, room_id, action, args, result, before):
    success=result.get('success') is True
    failed=result.get('success') is False
    by=before['by']
    target=_label(before.get('target'))
    item=_label(before.get('item'),'an item')
    npc=_label(before.get('npc'),'an NPC')
    identity=result.get('companion') if isinstance(result.get('companion'),dict) else {}
    pet=_label(result.get('name') or identity.get('custom_name') or identity.get('name') or before.get('pet'),'a companion')
    receiver=game.players.players.get(str(args.get('target','')).lower(),{})
    other=_label(before.get('other') or receiver.get('name'),'another character')
    outcome='success' if success else 'failure' if failed else 'completed'
    body=None
    if action in ('goal-pin','goal-clear','field-guide-title','project-contribute','project-withdraw','project-service',
                  'dungeon-pause','dungeon-resume','dungeon-away','dungeon-return',
                  'companion-track','companion-scout','companion-forage','companion-befriend','companion-diagnose'):
        if result.get('changed') is False:return None
        body=_label(result.get('message'),'completed an activity',300)
    elif action=='fish':
        if before.get('fish'):return None
        catch=result.get('catch',{})
        if success:
            body=f"fished and caught {_label(catch.get('species'),'a fish')} ({_number(catch.get('lb')):g} lb); the catch is waiting to be kept or released."
        elif failed:body='fished, but the fish slipped away.'
    elif action in ('keep','release'):
        fish=before.get('fish')
        if success and fish:
            verb='kept' if action=='keep' else 'released'
            body=f"{verb} {_label(fish.get('species'),'a fish')} ({_number(fish.get('lb')):g} lb)."
    elif action=='hunt' and before.get('target'):
        if not success and not (result.get('combat') or result.get('rolls') or 'roll' in result):return None
        body=f"hunted {target} and {'succeeded' if success else 'did not catch the quarry'}."
    elif action=='tame' and before.get('target'):
        if not success and 'roll' not in result:return None
        body=f"{'befriended' if success else 'tried to tame'} {target}"+('.' if success else ', but the attempt did not succeed.')
    elif action in ('gather','collect'):
        if not success and not (result.get('rolls') or 'roll' in result):return None
        resource=before.get('target') or before.get('quest_item')
        if resource:
            resource=_label(result.get('item') or resource)
            qty=result.get('quantity',result.get('amount_found',result.get('amount',1)))
            body=f"gathered {_number(qty,1):g} × {resource}." if success else f"searched for {resource}, but found none."
    elif action=='search':
        if not success and 'roll' not in result:return None
        body='searched for quest clues and '+('found something useful.' if success else 'found nothing useful.')
    elif action=='craft' and before.get('target'):
        made=result.get('item',{})
        name=_label(made.get('name') if isinstance(made,dict) else before['target'],target)
        body=f"crafted {name}." if success else f"tried to craft {target}, but could not finish it."
    elif action=='equip':
        p=game.players.get_player(actor)['shared']
        if success and (p.get('equipment',{})!=before['equipment'] or p.get('equipped',{})!=before['equipped']):
            body=f"equipped {_label(result.get('equipped'),item)}."
        elif failed and before.get('item'):body=f"could not equip {item}."
    elif action=='unequip' and success:body=f"put away {_label(result.get('unequipped'),'an equipped item')}."
    elif action=='repair' and before.get('item'):
        if before.get('durability') is not None and before.get('durability')==before.get('max_durability'):return None
        body=f"{'repaired' if success else 'could not repair'} {item}."
    elif action=='made-stow' and success and before.get('placed'):body=f"put {item} back in their bag."
    elif action=='process':body=f"{'processed' if success else 'could not process'} {item}."
    elif action=='use' and success:body=f"used a serving of {item}"+(f" and recovered {_number(result.get('healed')):g} HP." if result.get('healed') else '.')
    elif action=='feed' and success:body=f"fed {pet} a serving of {item}; {pet} recovered {_number(result.get('healed')):g} HP."
    elif action=='companion-pet' and success:body=f"gave {pet} a gentle pat."
    elif action=='companion-release' and success:body=f"released {pet} back into the wild."
    elif action=='companion-repair':
        if success:body=f"repaired {pet}, restoring {_number(result.get('healed')):g} HP."
        elif failed and before.get('pet') and _number(before.get('pet_hp'))<_number(before.get('pet_max_hp')):body=f"could not repair {pet}."
    elif action=='companion-name' and success:body='gave their companion a new name.'
    elif action=='companion-activate' and success and game.players.get_player(actor).get('active_companion_index')!=before['active_companion']:
        body=f"activated {item} as a robot companion."
    elif action=='companion-follow' and success and game.players.get_player(actor).get('active_companion_index')!=before['active_companion']:
        body=f"began walking with {pet}."
    elif action=='companion-rest' and success and before['active_companion'] is not None:body=f"let {pet} rest."
    elif action=='rest' and success and before['hp']<before['max_hp']:
        gained=game.players.get_player(actor)['shared']['hp']-before['hp']
        body=f"rested and recovered {_number(gained):g} HP."
    elif action=='transform' and success and game.players.get_player(actor).get('current_form','human')!=before['form']:
        form=game.players.get_current_form(actor)
        body=f"changed into {_label(form.get('name'),'a different form')}."
    elif action=='character-type' and success:body='chose their character type.'
    elif action=='competitive' and success and bool(game.players.get_player(actor).get('competitive'))!=before['competitive']:
        body='enabled competitive play.' if args.get('enabled') else 'disabled competitive play.'
    elif action=='hack':
        mode=result.get('mode',args.get('mode','recover'))
        if failed:body=f"tried to hack {target}, but access was refused."
        elif success and mode=='intel':body=f"opened {_label(result.get('intel',{}).get('name'),'a character')}’s dossier at {target}."
        elif success and mode in ('steal','give'):
            credit=_number(result.get('transfer',{}).get('credits'))
            account=_label(before.get('character'),'another character')
            body=f"{'stole' if mode=='steal' else 'gave'} {credit:g} credits {'from' if mode=='steal' else 'to'} {account} through {target}."
        elif success:body=f"hacked {target} and recovered {_number(result.get('recovered_credits')):g} credits."
    elif action in ('npc-talk','hear-story','event-respond') and result.get('job_id'):
        outcome='pending'
        body={'npc-talk':f"started a conversation with {npc}.", 'hear-story':f"settled in to hear a tale from {npc}.",
              'event-respond':'contributed to the regional story; the storyteller is responding.'}[action]
    elif action in ('story-start','story-respond','backstory','flirt','tell-story') and before.get('npc'):
        progress=before.get('story',{})
        if progress.get('complete') or action=='story-start' and progress.get('active'):return None
        if action=='backstory' and success and before.get('backstory')==game.players.get_discovered_backstories(actor,before['npc']):return None
        verbs={'story-start':'began a story chapter with', 'story-respond':'completed a story chapter with',
               'backstory':'learned a piece of history from','flirt':'shared a playful moment with','tell-story':'told a tale to'}
        body=f"{verbs[action]} {npc}." if success else f"tried to continue their story with {npc}, but could not progress yet."
    elif action=='event-start':
        if before.get('event') and not before['event'].get('resolved'):return None
        event=result.get('event')
        if success and event and not event.get('resolved'):body='started a regional story event.'
    elif action in ('buy','sell'):
        if success:body=f"{'bought' if action=='buy' else 'sold'} {_number(args.get('quantity',1),1):g} × {_label(args.get('target'))}."
    elif action=='shop-service' and success:body='had a shopkeeper make or improve an item.'
    elif action=='gamble':
        won=result.get('result') in ('win','jackpot')
        if success:
            body='played a table game and '+('won.' if won else 'lost the wager.')
            outcome='success' if won else 'failure'
    elif action=='daily' and success:body='collected the daily welcome reward.'
    elif action.startswith('gift'):
        if success:
            if action=='gift-gold':body=f"gave {_number(args.get('quantity',1),1):g} gold to {other}."
            else:body=f"gave {_number(args.get('quantity',1),1):g} × {item} to {other}."
        elif failed and before.get('item'):body=f"could not give {item} to {other}."
    elif action in ('quest-accept','party-quest-start','quest-chain') and success:
        body=f"accepted {_label(before.get('target'),'a quest')}"+(' for their party.' if action=='party-quest-start' else '.')
    elif action in ('quest-complete','party-quest-complete','quest-abandon') and before.get('quest'):
        quest=_label(before['quest'])
        body=f"{'turned in' if action!='quest-abandon' else 'set aside'} {quest}." if success else f"could not turn in {quest} yet."
    elif action.startswith('party-') and success:
        verbs={'party-create':'formed an adventuring party.','party-invite':f"invited {other} to their party.",
               'party-join':'joined an adventuring party.','party-decline':'declined a party invitation.',
               'party-kick':f"removed {other} from their party.",'party-leave':'left their adventuring party.'}
        body=verbs.get(action)
    elif action.startswith('dungeon-'):
        if failed and 'roll' not in result:return None
        verbs={'dungeon-start':'entered a party dungeon','dungeon-attack':'attacked a dungeon enemy',
               'dungeon-ability':'used a dungeon ability','dungeon-recover':'tried to recover in the dungeon',
               'dungeon-pass':'passed their dungeon turn','dungeon-flee':'tried to leave the dungeon',
               'dungeon-role':'chose their dungeon role'}
        if action in verbs:body=verbs[action]+(' successfully.' if success else ', but the attempt did not succeed.')
        if action=='dungeon-recover' and 'roll' in result:
            body='recovered and rose in the dungeon.' if result.get('revived') else 'tried to recover in the dungeon, but remained downed.'
            outcome='success' if result.get('revived') else 'failure'
        if success and before.get('enemy') and (action=='dungeon-attack' or action=='dungeon-ability' and args.get('ability')=='fireball'):
            enemy=before['enemy']
            dungeon=game.players.get_party_dungeon(actor) or {}
            active=dungeon.get('active_room',{})
            remaining=next((row for row in active.get('enemies',[]) if row.get('name')==enemy['name']),None)
            if remaining and active.get('room_id')==enemy['room']:
                damage=max(0,enemy['hp']-remaining['hp'])
                body=f"hit {_label(enemy['name'])} for {damage:g} damage in the dungeon." if damage else f"attacked {_label(enemy['name'])} in the dungeon, but missed."
                outcome='success' if damage else 'failure'
            elif not result.get('dungeon_failed'):body=f"defeated {_label(enemy['name'])} in the dungeon."
            else:
                body=f"attacked {_label(enemy['name'])} in the dungeon."
                outcome='failure'
        if result.get('dungeon_complete'):body+=' The party completed the dungeon.'
        elif result.get('dungeon_failed'):body+=' The party fell and the dungeon run ended.'
    elif action.startswith('duel-'):
        verbs={'duel-challenge':f"offered {other} a duel",'duel-accept':'accepted a duel',
               'duel-attack':'attacked in a duel','duel-flee':'left a duel'}
        if action in verbs:body=verbs[action]+(' successfully.' if success else ', but the attempt did not succeed.')
        if success and action=='duel-attack' and before.get('duel_hp') is not None:
            after=result.get('duel',{}).get('hp',{}).get(before['duel_opponent'],before['duel_hp'])
            damage=max(0,before['duel_hp']-after)
            body=f"hit {other} for {damage:g} damage in a duel." if damage else f"attacked {other} in a duel, but missed."
            outcome='success' if damage else 'failure'
    elif action=='steal':body=f"{'pickpocketed' if success else 'tried to pickpocket'} {other}"+('.' if success else ', but was noticed.')
    if not body:return None
    
    injuries=[]
    for wear in result.get('item_wear',[]):
        if wear.get('broken'):injuries.append(_label(wear.get('name'),'gear')+' broke and can be repaired')
    care=list(result.get('companion_injuries',[]))
    if isinstance(result.get('companion_injury'),dict):care.append(result['companion_injury'])
    for injury in care:
        if injury.get('damage'):
            injuries.append(_label(injury.get('name'),'a companion')+f" took {_number(injury['damage']):g} damage")
    recovery=result.get('companion_recovery')
    if isinstance(recovery,dict) and recovery.get('healed'):
        injuries.append(_label(recovery.get('name'),'a companion')+f" restored {_number(recovery['healed']):g} HP")
    if action in ('hunt','tame','dungeon-attack','dungeon-ability','dungeon-recover'):
        lost=before['hp']-game.players.get_player(actor)['shared']['hp']
        if lost>0:injuries.append(f"{by} took {_number(lost):g} damage")
    if injuries:body+=' '+'; '.join(injuries[:3])+'.'
    return by+' '+body,outcome

def publish(game, actor, room_id, action, args, result, before, request_id):
    summary=_summary(game,actor,room_id,action,args,result,before)
    if result.get('job_id'):
        key='npc-job:'+result['job_id']
        job=game.store.read(key)
        if job:
            job.update(activity_action=action,activity_by=before['by'],
                       activity_npc=_label(before.get('npc'),'the storyteller' if action=='event-respond' else 'an NPC'))
            game.store.write(key,job)
    if summary:
        text,outcome=summary
        return append_activity(game.store,room_id,before['by'],text,action,outcome,'game:'+actor+':'+request_id)

def publish_job(store, root, job, status):
    """Only an action's completion is public; NPC answers and event details are private."""
    from pathlib import Path
    import json
    actor=job['actor']
    player=store.read('player:'+actor,{})
    by=_label(job.get('activity_by') or player.get('name'),actor.title(),40)
    npc=job.get('activity_npc')
    if not npc:
        path=Path(root)/'world'/str(job['room']+'.json')
        room=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        npc=next((n['name'] for n in room.get('npcs',[]) if n.get('id')==job.get('npc')),'the storyteller' if job.get('npc')=='narrator' else 'an NPC')
    npc=_label(npc)
    action=job.get('activity_action','event-respond' if job.get('event_key') else 'npc-talk')
    if status=='failed':text=f"{by}’s conversation with {npc} could not finish; they can try again."
    elif action=='event-respond':text=f"The storyteller finished responding to {by}’s contribution to the regional story."
    elif action=='hear-story':text=f"{by} heard a tale from {npc}."
    else:text=f"{by} finished a conversation with {npc}."
    return append_activity(store,job['room'],by,text,action,'failure' if status=='failed' else 'completed','npc-job:'+job['id'])
