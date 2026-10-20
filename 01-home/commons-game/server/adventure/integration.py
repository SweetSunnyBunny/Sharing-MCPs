
"""Attach the game to the existing Commons host; no extra server process."""
from contextlib import nullcontext
import io
import json
import re
import sys
import uuid
import logging
import posixpath
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

from .store import Store, MIGRATED
from .game import Game
from .narration import launch
from . import native_activity

ECONOMY = frozenset(('/api/plant','/api/tend','/api/harvest','/api/cast','/api/creel',
    '/api/forage','/api/hunt','/api/approach','/api/name-companion','/api/follow-companion',
    '/api/craft','/api/equip','/api/unequip','/api/share','/api/place-made','/api/place',
    '/api/rook','/api/editor/save','/api/verb'))

def install(ns):
    stores={}
    live_world=Path(ns['WORLD_DIR']).resolve()
    live_private=Path(ns['PRIVATE_ROOT']).resolve()
    original_read,original_write=ns['_read_json'],ns['_write_json']
    def store():
        if Path(ns['KEYS_FILE']).parent.resolve()!=Path(ns['PRIVATE_ROOT']).resolve():return None
        if Path(ns['WORLD_DIR']).resolve()==live_world and Path(ns['PRIVATE_ROOT']).resolve()!=live_private:return None
        if not (Path(ns['WORLD_DIR'])/'game-config.json').exists():return None
        key=(str(ns['PRIVATE_ROOT']),str(ns['WORLD_DIR']))
        if key not in stores:
            stores[key]=Store(Path(key[0]),Path(ns.get('RUNTIME_WORLD_DIR', key[1])))
            from .backups import start_if_live
            start_if_live(stores[key],Path(ns['COMMONS_ROOT']))
        return stores[key]
    def managed(path):
        p=Path(path)
        return p.name in MIGRATED and p.parent.resolve()==Path(ns.get('RUNTIME_WORLD_DIR', ns['WORLD_DIR'])).resolve()
    def house_read(path,default):
        s=store()
        if s and s.chat_room(path):return s.chat_read(path,default)
        return s.house_read(path,default) if s and managed(path) else original_read(path,default)
    def house_write(path,value):
        s=store()
        if s and s.chat_room(path):s.chat_write(path,value)
        elif s and managed(path):s.house_write(path,value)
        else:original_write(path,value)
    ns['_read_json'],ns['_write_json']=house_read,house_write
    ns['_game_store']=store
    old_public_presence=ns['_public_presence']
    def public_presence(presence):
        result=old_public_presence(presence)
        s=store()
        if s:
            for name,row in result.items():
                book=s.read('field-guide:'+ns['_norm'](name),{})
                title=book.get('selected_title')
                if title:row['title']=str(title)[:100]
        return result
    ns['_public_presence']=public_presence
    old_mentions=ns['_discord_mention_map']
    ns['_discord_mention_map']=lambda: {} if store() else old_mentions()
    cls=ns['HearthHandler']
    old_get,old_post,old_json=cls.do_GET,cls.do_POST,cls._json
    def respond(self,obj,status=200):
        if getattr(self,'_game_capture',False):
            self._game_reply=(obj,status)
            return
        return old_json(self,obj,status)
    cls._json=respond

    def actor(self,body):
        name=str(body.get('by',body.get('as',''))).strip()
        key=str(body.get('key',''))
        n=ns['_norm'](name)
        
        if n not in ns['_addressable']() or not ns['_verify'](name,key):
            raise PermissionError('Sign in with your household or invitation key.')
        return n

    def game_for(s,who,key,password=''):
        game=Game(s,ns['COMMONS_ROOT'])
        game.can_enter=lambda room: ns['_room_open_for'](room,who,key,password)
        return game

    def current_room(who):
        presence=original_read(ns['HERE_FILE'],{})
        entry=next((r for n,r in presence.items() if ns['_norm'](n)==who),{})
        return entry.get('room','hearth')

    def get(self):
        u=urlparse(self.path)
        decoded=unquote(u.path).replace('\\', '/')
        if '..' in decoded.split('/'):
            return self._json({'error':'Not found'},404)
        route=posixpath.normpath('/'+decoded.lstrip('/'))
        qs={k:v[0] for k,v in parse_qs(u.query).items()}

        if any(part.lower() in ('server','tools','docs','.git','__pycache__') for part in route.replace('\\','/').split('/')):
            return self._json({'error':'Not found'},404)
        s=store()
        if s and route.lower().startswith('/world/') and route.lower().rsplit('/',1)[-1] in ('baskets.json','made-archive.json'):
            return self._json({'error':'Use your signed-in inventory.'},403)
        if route.startswith('/assets/music/'):
            name=route.rsplit('/',1)[-1]
            if not re.fullmatch(r'[A-Za-z0-9_-]+\.(mp3|ogg|wav|m4a)',name):return self._json({'error':'Not found'},404)
            path=Path(ns['COMMONS_ROOT'])/'uploads/music'/name
            if not path.is_file():path=Path(ns['COMMONS_ROOT'])/'assets/music'/name
            if not path.is_file():return self._json({'error':'Not found'},404)
            size=path.stat().st_size;start,end=0,size-1
            offered=self.headers.get('Range','')
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',offered) if offered else None
            if offered:
                if not match:return self._json({'error':'Unsupported audio range'},416)
                start=int(match[1]);end=min(size-1,int(match[2])) if match[2] else size-1
                if start>end or start>=size:return self._json({'error':'Audio range outside file'},416)
            self.send_response(206 if offered else 200)
            self.send_header('Content-Type',{'mp3':'audio/mpeg','ogg':'audio/ogg','wav':'audio/wav','m4a':'audio/mp4'}[path.suffix[1:]])
            self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            if offered:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with path.open('rb') as audio:
                audio.seek(start);remaining=end-start+1
                while remaining:
                    chunk=audio.read(min(65536,remaining));self.wfile.write(chunk);remaining-=len(chunk)
            return
        if route.startswith('/api/game/'):
            if not s:return self._json({'error':'Adventure is not enabled'},503)
            try:
                who=actor(self,qs)
                with s.transaction():
                    game=game_for(s,who,qs.get('key',''),qs.get('password',''))
                    if route=='/api/game/state':
                        result=game.snapshot(who,current_room(who))
                        game.players.flush()
                    elif route=='/api/game/visit':
                        from . import visits
                        rid=current_room(who)
                        if qs.get('room') and qs['room']!=rid:
                            return self._json({'available':False,'message':'Enter this room to record a visit; a remote look does not acknowledge changes.'})
                        if not ns['_room_open_for'](rid,who,qs.get('key',''),qs.get('password','')):
                            raise PermissionError('That room is locked.')
                        visible={}
                        for path in Path(ns['WORLD_DIR']).glob('*.json'):
                            data=original_read(path,{})
                            if isinstance(data,dict) and data.get('rows') and ns['_room_open_for'](path.stem,who,qs.get('key',''),qs.get('password','')):
                                visible[path.stem]=data.get('name',path.stem)
                        room=game.room(rid)
                        things=room.get('props',[])+[row for row in s.house_read(Path(ns['PLACE_FILE']),[])
                                                  if row.get('room')==rid]
                        result=visits.look(s,who,rid,visible,things)
                    elif route=='/api/game/music':
                        result={'ok':True,'library':s.read('music-library',{'tracks':{},'assignments':{}})}
                    elif route=='/api/game/maintenance':
                        if who!='admin':raise PermissionError('Maintenance status belongs to Admin.')
                        from .backups import status
                        result={'ok':True,'backups':status(s,Path(ns['COMMONS_ROOT']))}
                    elif route=='/api/game/job':
                        job=s.read('npc-job:'+str(qs.get('id','')))
                        if not job or job['actor']!=who:raise PermissionError('That response belongs to someone else.')
                        result={'ok':True,'job':{k:job[k] for k in ('id','status','text','error') if k in job}}
                        if job['status']=='pending':launch(s,ns['COMMONS_ROOT'],job['id'])
                    else:return self._json({'error':'Unknown game route'},404)
                return self._json(result)
            except PermissionError as e:return self._json({'error':str(e)},403)
            except ValueError as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons adventure request failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        
        if s and route.startswith('/world/') and route.rsplit('/',1)[-1] in MIGRATED:
            name=route.rsplit('/',1)[-1]
            if name in ('rook.json','archery.json','howls.json'):
                return self._json({'error':'Use the activity endpoints for this ledger.'},403)
            return self._json(s.house_read(Path(ns['WORLD_DIR'])/name,[] if name=='placeables.json' else {}))
        if s and route.startswith('/api/'):
            with s.transaction():return old_get(self)
        return old_get(self)

    def post(self):
        route=urlparse(self.path).path
        s=store()
        if s and route=='/api/game/music/upload':
            try:
                query={k:v[0] for k,v in parse_qs(urlparse(self.path).query).items()}
                who=actor(self,query)
                if who!='admin':raise PermissionError('This music library belongs to Admin.')
                length=int(self.headers.get('Content-Length','0'))
                if not 128<=length<=50*1024*1024:return self._refuse_size(length,'Choose an audio file smaller than 50 MB.',413)
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('The upload was interrupted.')
                if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':ext='wav'
                elif raw[:4]==b'OggS':ext='ogg'
                elif raw[:3]==b'ID3' or (raw[0]==255 and raw[1]&224==224):ext='mp3'
                elif raw[4:8]==b'ftyp':ext='m4a'
                else:raise ValueError('Choose an MP3, OGG, WAV, or M4A audio file.')
                track_id=uuid.uuid4().hex
                dest=Path(ns['COMMONS_ROOT'])/'uploads/music'/f'{track_id}.{ext}'
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(raw)
                with s.transaction():
                    lib=s.read('music-library',{'tracks':{},'assignments':{}})
                    lib['tracks'][track_id]={'id':track_id,'title':str(query.get('title','Untitled'))[:100],
                        'url':'/assets/music/'+dest.name,'description':'Uploaded soundtrack'}
                    s.write('music-library',lib)
                return self._json({'ok':True,'track':lib['tracks'][track_id]})
            except PermissionError as e:return self._json({'error':str(e)},403)
            except (ValueError,TypeError) as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons adventure request failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        if s and route=='/api/follow-companion':

            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:return self._refuse_size(length)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Expected companion choices.')
                who=actor(self,body)
                ident=str(body.get('id') or '').strip()
                if not ident:raise ValueError('Choose a companion.')
                follow=body.get('follow') is not False
                with s.transaction():
                    game=game_for(s,who,body.get('key',''),body.get('password',''))
                    pet=game.players.care_companion(who,ident)

                    if str(pet.get('instance_id'))!=ident:
                        raise ValueError('Choose one of your own companions by its id.')
                    if pet.get('_house',{}).get('family_body'):

                        game.players.flush()
                        response={'state':game.snapshot(who,current_room(who))}
                        result={'success':True,'message':'Cloud follows as your family pet, alongside your chosen companion.'}
                    else:
                        response=game.execute(who,current_room(who),
                            'companion-follow' if follow else 'companion-rest',
                            {'target':ident},body.get('request_id') or uuid.uuid4().hex)
                        result=response['result']
                    if not result.get('success'):
                        return self._json({'error':result.get('message','That companion could not follow.')},409)
                    rows=s.house_read(Path(ns['WORLD_DIR'])/'companions.json',{}).get('companions',[])
                    row=next((r for r in rows if str(r.get('id'))==ident and str(r.get('owner','')).lower()==who),{})
                    reply=dict(result,ok=True,companion=row,following=bool(row.get('following')),state=response['state'])
                return self._json(reply)
            except PermissionError as e:return self._json({'error':str(e)},403)
            except (ValueError,TypeError,KeyError) as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons companion selection failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        if s and route in ('/api/hunt','/api/cast','/api/creel','/api/approach'):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:return self._refuse_size(length)
                body=json.loads(self.rfile.read(length))
                who=actor(self,body)
                room=current_room(who)
                if body.get('room') and body['room']!=room:raise ValueError('Enter that room before acting there.')
                action={'/api/hunt':'hunt','/api/cast':'fish','/api/approach':'tame','/api/creel':'keep' if body.get('keep') else 'release'}[route]
                args={'target':body.get('quarry',body.get('animal','')),'words':body.get('words','')}
                if route=='/api/creel':args['catch_id']=str(body.get('id',''))
                with s.transaction():
                    game=game_for(s,who,body.get('key',''),body.get('password',''))
                    response=game.execute(who,room,action,args,body.get('request_id') or uuid.uuid4().hex)
                    result=response['result']
                    reply=dict(result,ok=True,game=True,message=result.get('message',''),state=response['state'])
                    reply['basket']=self._basket_view(who,room)
                    if action=='fish':
                        reply['water']=game.room(room).get('water',room)
                        if not result.get('catch'):reply.update(ok=False,error=result.get('message','The fish slipped away.'))
                    if action in ('keep','release'):
                        held=result['catch'];reply.update(kept=action=='keep',catch=held,now_runs_to=held.get('lb',0),grown_by=.02)
                return self._json(reply)
            except PermissionError as e:return self._json({'error':str(e)},403)
            except (ValueError,TypeError,KeyError) as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons adventure request failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        if s and route in ('/api/equip','/api/unequip','/api/share'):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:return self._refuse_size(length)
                body=json.loads(self.rfile.read(length))
                who=actor(self,body)
                room=current_room(who)
                action={'/api/equip':'equip','/api/unequip':'unequip','/api/share':'use'}[route]
                args=({'id':body.get('id'),'target':body.get('target',''),'slot':body.get('slot')}
                      if action=='equip' else {'target':body.get('slot','')} if action=='unequip'
                      else {'id':body.get('id'),'share':True})
                with s.transaction():
                    game=game_for(s,who,body.get('key',''),body.get('password',''))
                    response=game.execute(who,room,action,args,body.get('request_id') or uuid.uuid4().hex)
                    result=response['result']
                    if not result.get('success'):
                        return self._json({'error':result.get('message','That item could not be used.')},409)
                    reply=dict(result,ok=True,state=response['state'])
                    if action in ('equip','unequip'):
                        reply['equipment']=ns['_equipment_summary'](s.house_read(Path(ns['WORLD_DIR'])/'baskets.json',{}).get(who,{}))
                    if action=='use' and result.get('made'):reply['made']=ns['_made_view'](result['made'])
                return self._json(reply)
            except PermissionError as e:return self._json({'error':str(e)},403)
            except (ValueError,TypeError,KeyError) as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons equipment/use request failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        if route.startswith('/api/game/'):
            if not s:return self._json({'error':'Adventure is not enabled'},503)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:return self._refuse_size(length,'Game request is too large.',413)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Expected a game request.')
                who=actor(self,body)
                if route=='/api/game/action':
                    from .agent import normalize_action, action_feedback_before, compact_action_response, unknown_action_message
                    args=body.get('args',{})
                    if not isinstance(args,dict):raise ValueError('Expected action choices.')
                    action,args=normalize_action(body.get('action',''),args)
                    if action in ('gift','gift-gold','gift-made','gift-perishable','party-invite'):
                        target=str(args.get('target','')).lower()
                        if target not in ns['_addressable']():raise ValueError('That player has not been invited.')
                    with s.transaction():
                        game=game_for(s,who,body.get('key',''),body.get('password',''))
                        rid=current_room(who)
                        request_id=body.get('request_id')
                        feedback_key='action-feedback:'+who+':'+str(request_id)
                        feedback=s.read(feedback_key)
                        historical=bool(isinstance(request_id,str) and s.local.db.execute(
                            'SELECT 1 FROM receipts WHERE actor=? AND request_id=?',(who,request_id)).fetchone())
                        before=action_feedback_before(game,who,rid) if feedback is None and not historical else None
                        try:result=game.execute(who,rid,action,args,request_id)
                        except ValueError as error:
                            if str(error)=='Unknown game action.':raise ValueError(unknown_action_message(action)) from error
                            raise
                        if feedback is None:

                            if historical:

                                feedback=compact_action_response({k:v for k,v in result.items() if k!='state'})
                                feedback.update(changes={},changes_available=False)
                            else:feedback=compact_action_response(result,before)
                            s.write(feedback_key,feedback)
                    if result.get('result',{}).get('job_id'):launch(s,ns['COMMONS_ROOT'],result['result']['job_id'])
                    if body.get('compact') is True:
                        return self._json(dict(feedback,state=result['state']) if body.get('full_state') is True else feedback)
                    return self._json(result)
                if route=='/api/game/music/assign':
                    if who!='admin':raise PermissionError('This music library belongs to Admin.')
                    scope=str(body.get('scope',''))
                    if not re.fullmatch(r'(room|region):[a-z0-9_-]+',scope):raise ValueError('Choose a room or region.')
                    with s.transaction():
                        lib=s.read('music-library',{'tracks':{},'assignments':{}})
                        tracks=body.get('tracks',[])
                        if not isinstance(tracks,list) or any(t not in lib['tracks'] for t in tracks):raise ValueError('Choose tracks from the library.')
                        lib['assignments'][scope]={'tracks':tracks,'silent':bool(body.get('silent',False))}
                        s.write('music-library',lib)
                    return self._json({'ok':True,'library':lib})
                return self._json({'error':'Unknown game route'},404)
            except PermissionError as e:return self._json({'error':str(e)},403)
            except (ValueError,TypeError,KeyError) as e:return self._json({'error':str(e)},400)
            except Exception:
                logging.exception('Commons adventure request failed')
                return self._json({'error':'The action could not finish. Your game transaction was rolled back.'},500)
        if s and route=='/api/verb':

            try:length=int(self.headers.get('Content-Length','0'))
            except (TypeError,ValueError):return old_post(self)
            body={}
            if 0<length<=4096:
                raw=self.rfile.read(length);self.rfile=io.BytesIO(raw)
                try:body=json.loads(raw)
                except (ValueError,UnicodeDecodeError):body={}
                if not isinstance(body,dict):body={}
            request_id=body.get('request_id')
            if request_id is not None and (not isinstance(request_id,str) or not 8<=len(request_id)<=100):
                return self._json({'error':'Supply a request_id between 8 and 100 characters when retrying an action.'},400)
            if request_id is None:return old_post(self)
            call=self._verb_call;instance_call=self.__dict__.get('_verb_call')
            def receipt_call(path,method,data,extra=None):
                return call(path,method,dict(data,request_id=request_id) if method=='POST' else data,extra)
            self._verb_call=receipt_call
            try:return old_post(self)
            finally:
                if instance_call is None:self.__dict__.pop('_verb_call',None)
                else:self._verb_call=instance_call
        if s:

            send=self._json
            instance_json=self.__dict__.get('_json')
            captured=[]
            self._json=lambda obj,status=200:captured.append((obj,status))
            try:
                with s.transaction():
                    body={};native=route in native_activity.ROUTES or route=='/api/say'
                    if native:
                        length=int(self.headers.get('Content-Length','0'))
                        if 0<length<=65536:
                            raw=self.rfile.read(length)
                            self.rfile=io.BytesIO(raw)
                            try:body=json.loads(raw)
                            except (ValueError,UnicodeDecodeError):body={}
                            if not isinstance(body,dict):body={}
                    who=ns['_norm'](str(body.get('by',body.get('as',''))))
                    request_id=body.get('request_id')
                    if native and request_id is not None and (not isinstance(request_id,str) or not 8<=len(request_id)<=100):
                        raise ValueError('Supply a request_id between 8 and 100 characters when retrying an action.')
                    request=json.dumps({'native':route,'body':{k:v for k,v in body.items()
                        if k not in ('key','request_id')}},sort_keys=True,ensure_ascii=False)
                    verified=bool(native and who and ns['_verify'](str(body.get('by',body.get('as',''))),body.get('key')))
                    saved=s.replay(who,request_id,request) if verified and isinstance(request_id,str) and request_id else None
                    if saved is not None:

                        if not ns['_verify'](str(body.get('by',body.get('as',''))),body.get('key')):
                            captured.append(({'error':'That name is signed on another device.'},403))
                        else:captured.append((saved['body'],saved['status']))
                    else:
                        previous=native_activity.before(ns,route,body) if native else {}
                        if verified and route in ('/api/forage','/api/loose','/api/howl','/api/rook') and body.get('room') and body['room']!=current_room(who):
                            captured.append(({'error':'Enter that room before acting there.'},409))
                        else:old_post(self)
                        if captured and native:
                            reply,status=captured[-1]
                            if isinstance(reply,dict):
                                if verified and route=='/api/craft' and status<400 and reply.get('ok'):
                                    from . import field_guide, guidance
                                    game=game_for(s,who,body.get('key',''),body.get('password',''))
                                    resolved=dict(reply,success=True)
                                    choices={'target':body.get('recipe','')}
                                    field_guide.observe(game,who,current_room(who),'home-craft',choices,resolved)
                                    guidance.observe(game,who,current_room(who),'home-craft',choices,resolved)
                                    game.players.flush()
                                native_activity.publish(s,ns,route,body,reply,status,previous,
                                    'native:'+who+':'+request_id if isinstance(request_id,str) and request_id else uuid.uuid4().hex)
                                if verified and isinstance(request_id,str) and request_id and status not in (401,403):
                                    s.receipt(who,request_id,request,{'body':reply,'status':status})
                reply=captured[-1] if captured else None
            except ValueError as e:
                reply=({'error':str(e)},400)
            except Exception:
                logging.exception('Commons Home action failed')
                reply=({'error':'The action could not finish.'},500)
            finally:
                if instance_json is None:self.__dict__.pop('_json',None)
                else:self._json=instance_json
            if reply:return send(*reply)
            return
        return old_post(self)
    cls.do_GET,cls.do_POST=get,post
