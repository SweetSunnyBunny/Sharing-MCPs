"""Authored story chapters and relationship actions, independent of transport."""
import json
import time
import uuid
from .players import ENGINE

def story_action(game,actor,room_id,action,args):
    p,room,world,loc=game.context(actor,room_id)
    target=str(args.get('target',''))
    npc=next((n for n in room.get('npcs',[]) if n['id']==target),None)
    if not npc:raise ValueError('That person is not here.')
    m=game.players
    if action in ('tell-story','hear-story'):
        if target!='takoda':raise ValueError('Join Takoda at the campfire for storytelling.')
        from .storyteller import Storyteller
        teller=Storyteller()
        game.cooldown(actor,'campfire-story',300)
        if action=='tell-story':
            words=str(args.get('message','')).strip()
            if not 30<=len(words)<=4000:raise ValueError('Tell your story in 30–4000 characters.')
            evaluation=teller.evaluate_player_story(words,p['name'])
            m.add_currency(actor,world,evaluation['reward_dollars']*2,'Campfire storytelling')
            m.record_npc_interaction(actor,npc['name'],p['name'])
            m.add_npc_memory(actor,npc['name'],'conversation','Told this story: '+words[:500],3)
            return {'success':True,'message':teller.get_takoda_response_to_story(evaluation)+'\n'+(teller.get_reward_message(evaluation) or '')}
        job_id=uuid.uuid4().hex
        game.store.write('npc-job:'+job_id,{'id':job_id,'actor':actor,'npc':target,'room':room_id,'status':'pending',
            'message':'Tell a three-part tale about '+__import__('random').choice(teller.EPIC_THEMES)+'. Give each part a short title.',
            'scene':'By the campfire, with time to listen. '+room.get('description',''),
            'history_key':f'npc-history:{actor}:{target}'})
        return {'success':True,'message':'Takoda settles beside the fire…','job_id':job_id}
    if action=='flirt':
        game.cooldown(actor,'flirt:'+target,300)
        return m.record_flirt(actor,npc['name'],p['name'])
    if action=='backstory':
        data=json.loads((ENGINE/'backstory_config.json').read_text(encoding='utf-8'))
        cfg=data.get('npcs',{}).get(target)
        if not cfg:raise ValueError('This character shares their history in ordinary conversation.')
        progress=game.store.read(f'story:{actor}:{target}',{})
        ready=m.can_unlock_backstory(actor,npc['name'],world,**{
            'rep_threshold':data['unlock_requirements']['reputation_threshold'],
            'flirt_threshold':data['unlock_requirements']['flirt_count_threshold']})
        if not progress.get('complete') and not ready.get('can_unlock'):
            return {'success':False,'message':cfg['locked_message']}
        known=m.get_discovered_backstories(actor,npc['name'])
        
        if isinstance(known,dict):known=known.get(npc['name'],known.get(target,[]))
        fragment=next((f for f in cfg['fragments'] if f['id'] not in known),None)
        if fragment:
            m.discover_backstory_fragment(actor,npc['name'],fragment['id'])
            return {'success':True,'message':cfg['unlock_message']+'\n'+fragment['text']}
        return {'success':True,'message':'\n\n'.join(f['text'] for f in cfg['fragments'])}
    story=json.loads((ENGINE/'storylines.json').read_text(encoding='utf-8')).get(target)
    if not story:raise ValueError('This character has no chapter storyline. You can still talk with them.')
    key=f'story:{actor}:{target}'
    progress=game.store.read(key,{'chapter':0,'complete':False,'ready':0})
    if progress['complete']:return {'success':True,'message':'You have completed '+story['title']+'. Their history is in your journal.'}
    if progress['ready']>time.time():raise ValueError('The next chapter will be ready in '+str(max(1,round((progress['ready']-time.time())/3600,1)))+' hours.')
    chapter=story['chapters'][progress['chapter']]
    if action=='story-start':
        progress['active']=True;game.store.write(key,progress)
        return {'success':True,'message':story['trigger_message']+'\n\n'+chapter['title']+'\n'+chapter['task'],'chapter':chapter['chapter']}
    if not progress.get('active'):raise ValueError('Begin this chapter first.')
    words=str(args.get('message','')).strip()
    if not words or len(words)>4000:raise ValueError('Describe your part in the scene in 1–4000 characters.')
    keywords=chapter.get('completion_keywords',[])
    if keywords and not any(k in words.lower() for k in keywords):
        return {'success':False,'message':chapter['task']+' — describe how you take part.'}
    if story.get('mechanic')=='storytelling' and len(words.split())<30:
        return {'success':False,'message':'Give your story a little room to breathe: at least thirty words.'}
    progress['chapter']+=1
    progress.update(active=False,complete=progress['chapter']>=len(story['chapters']),ready=time.time()+story.get('cooldown_hours',5)*3600)
    game.store.write(key,progress)
    m.record_npc_interaction(actor,npc['name'],p['name'])
    if progress['complete']:m.modify_reputation(actor,world,npc['name'],50,'Completed storyline')
    return {'success':True,'message':chapter['backstory_fragment']+'\n\n'+chapter['npc_response']+
        ('\nStory completed. +50 reputation.' if progress['complete'] else '\nChapter complete. Return later to continue.')}

def dungeon_ability(game,actor,world,args):
    m=game.players;d=m.get_party_dungeon(actor)
    if not d:raise ValueError('Your party is not in a dungeon.')
    order=d.get('turn_order',[])
    if not order or order[d.get('turn_index',0)]!=actor:raise ValueError('Wait for your turn.')
    if actor in d.get('downed',{}):raise ValueError('You are downed. Use the recovery roll.')
    ability=str(args.get('ability',''))
    role=m.dungeon_rules['roles'].get(d.get('roles',{}).get(actor),{})
    if ability not in role.get('abilities',[]):raise ValueError('That ability is not available to your chosen role.')
    target=str(args.get('target',actor)).lower()
    party=m.get_party(actor)
    if ability in ('guard','inspire','aid') and target not in [v['player_id'] for v in party['members']]:raise ValueError('Choose a party member.')
    if ability=='guard':result=m.use_guard(actor,world,target)
    elif ability=='inspire':result=m.use_inspire(actor,world,target,role.get('buff_attack',2))
    elif ability=='sing':result=m.use_sing(actor,world,role.get('buff_attack',2))
    elif ability=='focus':result=m.use_focus(actor,world,2)
    elif ability=='taunt':result=m.use_taunt(actor,world,3)
    elif ability=='aid':result=m.use_aid(actor,world,target,10+role.get('aid_heal_bonus',0))
    elif ability=='fireball':

        return m.attack_dungeon_target(actor,world,str(args.get('target','')),**{
            'use_spell':True,'spell_damage_die':role.get('fireball_damage','3d6'),
            'spell_attack_bonus':role.get('spell_attack_bonus',1)})
    if result.get('success'):result['turn']=m.advance_dungeon_turn(actor,world)
    return result

def settle_event(game,key,event,resolution):
    if event.get('resolved'):return event
    factor={'success':1.5,'timeout':.75}.get(resolution,1)
    rewards=[]
    for actor,data in event.get('participants',{}).items():
        xp=int((event.get('base_xp',25)+min(data.get('contributions',1)*2,20))*factor)
        game.players.add_xp(actor,xp,'World event: '+event['title'],False)
        rewards.append({'name':data.get('name',actor),'xp':xp})
        journal=game.store.read('journal:'+actor,[])
        journal.append({'action':'Event concluded','result':{'message':event['title']+f' — earned {xp} XP.'}})
        game.store.write('journal:'+actor,journal)
    event.update(resolved=True,resolution=resolution,rewards=rewards)
    if resolution=='success':
        from .community import record_event
        record_event(game,event,event.get('room') or key.split(':',1)[-1])
    game.store.write(key,event)
    return event
