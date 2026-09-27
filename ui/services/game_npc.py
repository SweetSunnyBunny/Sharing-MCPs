"""Text-only game personas using Anam's selected provider configuration.

This is intentionally separate from bonded chat preparation: no identity
prompt, Qualia orientation, pack history, tool gateway or session reuse.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import tomllib

from services.provider_router import _load_settings


async def generate(persona: dict, history: list[dict], message: str) -> str:
    settings=await _load_settings()
    provider,cfg=settings['provider'],settings['config']
    # The browser bridge carries a signed-in personal ChatGPT profile. Game
    # personas use the installed, isolated Codex lane when that is selected.
    # COMMONS_NPC_PROVIDER can explicitly select another supported local lane.
    override=os.getenv('COMMONS_NPC_PROVIDER','').strip()
    if override:
        provider=override
    elif provider=='chatgpt':
        provider='codex';cfg={}
    system=('You are a character in the Home Commons game. Stay within this persona and the supplied game scene. '
            'Players use webpage buttons. Never suggest Discord commands or ask them to switch applications. Speak directly in character. You have no tools. Do not claim to grant rewards, change inventory, '
            'or alter game state. Never impersonate one of the bonded pack identities. '
            'Use at most three short paragraphs unless the player asks for a story.\nPERSONA:\n'+json.dumps(persona,ensure_ascii=False))
    messages=[{'role':x['role'],'content':str(x['content'])} for x in history[-20:] if x.get('role') in ('user','assistant')]
    messages.append({'role':'user','content':message})
    if provider=='codex':
        return await asyncio.to_thread(_codex,system,messages,cfg)
    if provider=='claude-code':
        return await _claude(system,messages,settings)
    if provider=='anthropic':
        from services.claude_api import _get_client
        client=await _get_client()
        result=await client.messages.create(model=cfg.get('model') or settings['model'],max_tokens=1800,system=system,messages=messages)
        return ''.join(b.text for b in result.content if getattr(b,'type','')=='text')
    if provider in ('openai','lmstudio','ollama'):
        from openai import AsyncOpenAI
        base=cfg.get('base_url') or {'lmstudio':'http://localhost:1234/v1','ollama':'http://localhost:11434/v1'}.get(provider)
        client=AsyncOpenAI(api_key=cfg.get('api_key') or os.getenv('OPENAI_API_KEY') or 'local',base_url=base)
        try:
            result=await client.chat.completions.create(model=cfg.get('model') or 'gpt-4o',messages=[{'role':'system','content':system},*messages],max_tokens=1800)
            return result.choices[0].message.content or ''
        finally:await client.close()
    raise RuntimeError(f'The game persona lane does not yet support the selected provider: {provider}')


def _codex(system,messages,cfg):
    from services.codex_app_server import CodexAppServerClient
    from services.codex_cli import find_codex_executable,normalize_codex_model,get_codex_default_model_id
    executable=find_codex_executable()
    if not executable:raise RuntimeError('Codex is unavailable for NPC dialogue.')
    args=['-c','features.shell_tool=false','-c','features.apply_patch_freeform=false','-c','web_search="disabled"','-c','features.apps=false']
    config=Path(os.getenv('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
    if config.exists():
        parsed=tomllib.loads(config.read_text(encoding='utf-8'))
        for name in parsed.get('mcp_servers',{}):args.extend(['-c',f'mcp_servers.{name}.enabled=false'])
    client=CodexAppServerClient(executable,args)
    temp=tempfile.TemporaryDirectory(prefix='commons-npc-',ignore_cleanup_errors=True)
    cwd=temp.name
    try:
        client.initialize()
        model=normalize_codex_model(cfg.get('model')) or get_codex_default_model_id()
        params={'cwd':cwd,'baseInstructions':system,'developerInstructions':system,'personality':'none','approvalPolicy':'never','sandbox':'read-only','ephemeral':True}
        if model:params['model']=model
        result=client.request('thread/start',params)
        thread=(result.get('thread') or {}).get('id') or result.get('threadId')
        if not thread:raise RuntimeError('NPC provider returned no conversation.')
        client.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':json.dumps(messages,ensure_ascii=False)}]})
        chunks=[];deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            request=client.take_server_request()
            if request:client.respond_error(request['id'],-32601,'Tools are disabled for game NPCs.')
            event=client.take_notification(.2)
            if not event:continue
            method=event.get('method','');data=event.get('params',{})
            if method=='item/agentMessage/delta':chunks.append(data.get('delta',''))
            if method=='turn/completed':
                if data.get('turn',{}).get('error'):raise RuntimeError('NPC provider could not complete the response.')
                return ''.join(chunks).strip()
        raise TimeoutError('NPC response timed out. Please try again.')
    finally:
        client.close()
        temp.cleanup()


async def _claude(system,messages,settings):
    import shutil
    executable=shutil.which('claude')
    if not executable:raise RuntimeError('Claude Code is unavailable for NPC dialogue.')
    with tempfile.TemporaryDirectory(prefix='commons-npc-') as cwd:
        mcp=Path(cwd)/'mcp.json';mcp.write_text('{"mcpServers":{}}',encoding='utf-8')
        env=dict(os.environ);env.pop('CLAUDECODE',None)
        proc=await asyncio.create_subprocess_exec(executable,'-p','--output-format','json','--system-prompt',system,
            '--tools','','--strict-mcp-config','--mcp-config',str(mcp),'--setting-sources','',
            '--model',settings.get('model') or 'sonnet',cwd=cwd,env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:stdout,stderr=await asyncio.wait_for(proc.communicate(json.dumps(messages,ensure_ascii=False).encode()),150)
        except BaseException:
            proc.kill();await proc.wait();raise
        if proc.returncode:raise RuntimeError('Claude Code could not complete the NPC response.')
        result=json.loads(stdout)
        if result.get('is_error'):raise RuntimeError('Claude Code returned an NPC generation error.')
        return result.get('result','')
