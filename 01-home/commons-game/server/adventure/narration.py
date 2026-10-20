"""Durable, bounded NPC jobs; model waits never hold the game transaction."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
import logging
import threading
import subprocess
import sys
import urllib.error

POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='commons-npc')
RUNNING=set()
LOCK=threading.Lock()

def launch(store,root,job_id):
    with LOCK:
        if job_id in RUNNING or len(RUNNING)>=8:return
        RUNNING.add(job_id)
    POOL.submit(_run,store,Path(root),job_id)

def _run(store,root,job_id):
    try:
        job=store.read('npc-job:'+job_id)
        if not job or job['status']!='pending':return
        
        import os
        from settings import ANAM_URL
        key=os.environ.get('ANAM_API_KEY', '')
        if os.environ.get('COMMONS_ANAM_ENABLED') != 'true' or not key:
            raise RuntimeError('Optional Anam NPC narration is disabled.')
        history=store.read(job['history_key'],[])
        payload={'npc':job['npc'],'message':job['message'],'scene':job['scene'],'history':history[-20:]}
        req=urllib.request.Request(ANAM_URL+'/api/game/npc',data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        with urllib.request.urlopen(req,timeout=170) as response:data=json.load(response)
        text=data.get('text','').strip()
        if not text:raise RuntimeError('The NPC returned no dialogue.')
        _settle_success(store,root,job_id,text)
    except Exception:
        logging.exception('Commons NPC response failed')
        _settle_failure(store,root,job_id)
    finally:
        with LOCK:RUNNING.discard(job_id)

def _settle_success(store,root,job_id,text):
    """Commit one late answer and its public outcome in the job's original room."""
    from .activity import publish_job
    with store.transaction():

        job=store.read('npc-job:'+job_id)
        if not job or job.get('status')!='pending':return False
        if job.get('event_key'):
            from .game import Game
            from .stories import settle_event
            event=store.read(job['event_key'])
            if event and event['id']==job['event_id'] and not event.get('resolved'):
                if '[EVENT_COMPLETE]' in text:
                    game=Game(store,root);settle_event(game,job['event_key'],event,'success');game.players.flush()
                elif '[STAGE_ADVANCE]' in text:
                    stages=['discovery','confrontation','resolution'];event['stage']=stages[min(2,stages.index(event['stage'])+1)]
                event['narrating']=None
                store.write(job['event_key'],event)
            text=text.replace('[STAGE_ADVANCE]','').replace('[EVENT_COMPLETE]','').strip()
        history=store.read(job['history_key'],[])
        history.extend([{'role':'user','content':job['message']},{'role':'assistant','content':text}])
        store.write(job['history_key'],history)
        job.update(status='complete',text=text)
        store.write('npc-job:'+job_id,job)
        journal=store.read('journal:'+job['actor'],[])
        journal.append({'at':datetime.now(timezone.utc).isoformat(),'room':job['room'],'action':job['npc'], 'result':{'message':text}})
        store.write('journal:'+job['actor'],journal)
        publish_job(store,root,job,'complete')
        return True

def _settle_failure(store,root,job_id):
    from .activity import publish_job
    with store.transaction():
        job=store.read('npc-job:'+job_id)
        if not job or job.get('status')!='pending':return False
        job.update(status='failed',error='The NPC could not answer through Anam. You can retry; game actions still work.')
        store.write('npc-job:'+job_id,job)
        if job.get('event_key'):
            event=store.read(job['event_key'])
            if event and event.get('narrating')==job_id:
                event['narrating']=None
                store.write(job['event_key'],event)
        publish_job(store,root,job,'failed')
        return True
