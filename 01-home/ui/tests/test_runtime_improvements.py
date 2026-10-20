import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from services import codex_sessions as pool, codex_approvals as approvals, tool_result_store as store, tool_discovery

class Client:
    def __init__(self):
        self.alive = True
        self.respond = Mock()
    def close(self): self.alive = False
    def is_alive(self): return self.alive

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'jobs.db')
    monkeypatch.setattr(pool,'_clients',{})
    monkeypatch.setattr(pool,'_locks',{})
    monkeypatch.setattr(pool,'_background',{})
    monkeypatch.setattr(approvals,'_pending',{})

def test_identity_process_reused_while_autowake_closes_only_itself():
    async def run():
        async with pool.acquire('Claude',Client,'config') as first:
            messaging=first.client
        async with pool.acquire('claude',Client,'config') as second:
            assert second.client is messaging and second.reused
        async with pool.acquire('Claude',Client,'config',background=True) as wake:
            assert wake.client is not messaging
            assert len(pool.status()) == 2
        assert not wake.client.alive and messaging.alive
        async with pool.acquire('River',Client,'config') as other:
            assert other.client is not messaging
        await pool.shutdown()
        assert not messaging.alive
    asyncio.run(run())

def test_identity_turns_serialize_and_failed_session_is_replaced():
    async def run():
        entered=asyncio.Event(); release=asyncio.Event(); order=[]
        async def first():
            async with pool.acquire('Claude',Client,'config') as lease:
                order.append(1); entered.set(); await release.wait(); lease.retain=False
        async def second():
            await entered.wait()
            async with pool.acquire('Claude',Client,'config') as lease:
                order.append(2); assert not lease.reused
        a=asyncio.create_task(first()); b=asyncio.create_task(second())
        await entered.wait(); await asyncio.sleep(.01); assert order==[1]
        release.set(); await asyncio.gather(a,b)
        await pool.shutdown()
    asyncio.run(run())

def test_completed_result_survives_restart_and_retry_does_not_dispatch(monkeypatch):
    assert store.claim('x','fp','Claude','chat')
    store.finish('x',{'content':[{'type':'text','text':'before '+'x'*20000+' AFTER'}]})
    monkeypatch.setattr(store,'OWNER','new-process')
    assert not store.claim('x','fp','Claude','chat')
    result=store.read('x','Claude','chat')
    assert result['status']=='completed'
    compact=store.compact(result)
    assert compact['result_ref']['next_offset']==16000
    assert 'AFTER' in store.page('x',query='AFTER')['text']
    with pytest.raises(ValueError,match='different call'): store.claim('x','other','Claude','chat')
    with pytest.raises(ValueError,match='identity'): store.read('x','River')

def test_interrupted_result_is_uncertain_after_restart(monkeypatch):
    store.claim('x','fp','Claude','chat')
    monkeypatch.setattr(store,'OWNER','new-process')
    assert not store.claim('x','fp','Claude','chat')
    assert store.read('x')['status']=='uncertain'

def test_expiry_preserves_claim(monkeypatch):
    store.claim('x','fp','Claude','chat')
    monkeypatch.setattr(store,'MAX_BYTES',0)
    store.finish('x',{'content':[{'type':'text','text':'old'}]})
    assert not store.claim('x','fp','Claude','chat')
    with pytest.raises(ValueError,match='expired'): store.read('x')

def test_ranked_discovery_aliases_and_exact_names():
    tool={'server':'qualia','name':'mind_search','description':'Search saved memories','inputSchema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}
    assert tool_discovery.score(tool,'please recall what I remember')>0
    assert tool_discovery.score(tool,'mind_search')>tool_discovery.score(tool,'recall')
    assert tool_discovery.score(tool,'unicorn spaceship')==0
    summary=tool_discovery.present(tool,False)
    assert 'inputSchema' not in summary and summary['required_arguments']==['query']
    assert summary['example_arguments']=={'query':'example'}

def test_unconstructable_example_not_falsely_claimed_valid():
    tool={'name':'x','server':'y','description':'z','inputSchema':{'type':'object','properties':{'code':{'type':'string','pattern':'^ABC$'}},'required':['code']}}
    assert 'example_arguments' not in tool_discovery.present(tool)

@pytest.mark.parametrize('method',['item/commandExecution/requestApproval','item/fileChange/requestApproval'])
def test_approval_resolves_original_request_once_and_is_scoped(method):
    client=Client()
    event=approvals.register(client,{'id':7,'method':method,'params':{'command':'git status'}},'Claude','chat')
    client.respond.assert_not_called()
    with pytest.raises(ValueError,match='another conversation'):
        approvals.resolve(event['approval_id'],'accept','River','chat')
    approvals.resolve(event['approval_id'],'acceptForSession','Claude','chat')
    client.respond.assert_called_once_with(7,{'decision':'acceptForSession'})
    with pytest.raises(ValueError,match='no longer pending'):
        approvals.resolve(event['approval_id'],'accept','Claude','chat')

def test_permission_grant_contains_only_requested_access():
    client=Client(); requested={'network':{'enabled':True}}
    event=approvals.register(client,{'id':9,'method':'item/permissions/requestApproval','params':{'permissions':requested}},'Claude','chat')
    approvals.resolve(event['approval_id'],'accept','Claude','chat',{'filesystem':{'write':['C:/']}})
    client.respond.assert_called_once_with(9,{'permissions':requested,'scope':'turn'})

def test_approval_cleanup_prevents_response_to_replaced_process():
    client=Client();event=approvals.register(client,{'id':1,'method':'item/fileChange/requestApproval'},'Claude','chat')
    approvals.clear_client(client)
    assert not approvals.pending()
    with pytest.raises(ValueError): approvals.resolve(event['approval_id'],'accept','Claude','chat')


@pytest.mark.parametrize('module_name', ['claude_subprocess', 'claude_pty'])
def test_autowake_reaper_preserves_messaging_even_in_same_conversation(monkeypatch, module_name):
    import importlib
    module = importlib.import_module('services.' + module_name)
    sessions = {
        ('Claude','chat','messaging'): SimpleNamespace(dead=False, autowake_only=False),
        ('Claude','chat','autowake'): SimpleNamespace(dead=False, autowake_only=True),
        ('Claude','other','autowake'): SimpleNamespace(dead=False, autowake_only=True),
    }
    monkeypatch.setattr(module, '_sessions', sessions)
    retired = []
    monkeypatch.setattr(module, '_retire_session', lambda key: retired.append(key))
    assert module.kill_autowake_sessions('Claude','chat')
    assert retired == [('Claude','chat','autowake')]


def test_busy_gateway_still_checks_retry_fingerprint():
    store.claim('x','original','Claude','chat')
    assert not store.claim('x','original','Claude','chat',allow_new=False)
    with pytest.raises(ValueError,match='different call'):
        store.claim('x','changed','Claude','chat',allow_new=False)
    with pytest.raises(ValueError,match='busy'):
        store.claim('new','original','Claude','chat',allow_new=False)


def test_search_offsets_stay_correct_after_unicode_case_changes():
    store.claim('x','fp','Claude','chat')
    store.finish('x',{'content':[{'type':'text','text':'\u00df'*300+' TARGET'}]})
    page = store.page('x',query='target',limit=300)
    assert 'TARGET' in page['text']


def test_native_request_resolution_removes_stale_approval():
    client=Client()
    approvals.register(client,{'id':7,'method':'item/fileChange/requestApproval'},'Claude','chat')
    approvals.clear_request(client,7)
    assert not approvals.pending()
