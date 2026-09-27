"""Authenticated Commons NPC generation; game history is owned by Commons."""
import asyncio
import json
from pathlib import Path
import re
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services.game_npc import generate

router=APIRouter(prefix='/api/game',tags=['Commons NPCs'])
ROOT=Path(__file__).resolve().parents[2]/'commons/server/adventure/engine/npcs'
_slots=asyncio.Semaphore(2)


class Dialogue(BaseModel):
    npc: str=Field(max_length=80)
    message: str=Field(min_length=1,max_length=6000)
    history: list[dict]=Field(default_factory=list,max_length=20)
    scene: str=Field(default='',max_length=6000)


@router.post('/npc')
async def npc(request:Dialogue):
    if request.npc=='narrator':
        persona={'name':'The Commons storyteller','role':'Narrate the supplied adventure scene, leaving choices to the players.'}
    else:
        if not re.fullmatch(r'[a-z0-9_-]+',request.npc):raise HTTPException(400,'Unknown NPC')
        path=ROOT/request.npc/'persona.json'
        if not path.is_file():raise HTTPException(404,'Unknown NPC')
        persona=json.loads(path.read_text(encoding='utf-8-sig'))
        persona={k:v for k,v in persona.items() if k not in ('trigger','channels','special_triggers','avatar_url','gives_quests')}
    try:
        async with _slots:
            text=await generate(persona,request.history,'SCENE: '+request.scene+'\nPLAYER: '+request.message)
        if not text:raise RuntimeError('The NPC returned an empty response.')
        return {'ok':True,'text':text}
    except Exception as exc:
        logging.exception('Commons NPC generation failed')
        raise HTTPException(503,'NPC dialogue is unavailable; game actions still work.') from exc
