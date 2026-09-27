"""Read-only smoke test of local MCP, FileSystem+ machine upstream, and bridge action adapters."""
import asyncio
import json
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastmcp import Client
from scripts import anam_gateway_mcp as adapter
from services import anam_tool_gateway as gateway, chatgpt_actions as actions

async def main():
    token='parity-'+uuid.uuid4().hex
    args={'identity':'Claude','limit':3}
    report={'request_id':token}
    adapter.BOUND_IDENTITY='Claude'
    adapter.BOUND_CONVERSATION_ID=token
    async with Client(adapter.mcp) as local:
        tools={t.name:t for t in await local.list_tools()}
        assert set(tools)=={'anam'}
        result=await local.call_tool('anam',{'operation':'invoke','server':'anam-context','tool':'anam_list_canvases',
            'arguments_json':json.dumps(args),'request_id':token})
        assert not result.is_error and ('Anam result operation' in result.content[0].text
                                        or 'anam_result' in result.content[0].text)
        # Exercise FileSystem+'s real authenticated machine-agent upstream.
        import httpx
        key=(ROOT/'data/runtime/machine-agent.key').read_text(encoding='utf-8').strip()
        async with httpx.AsyncClient(trust_env=False,timeout=30) as machine:
            response=await machine.post('http://127.0.0.1:8811/tools/invoke',
                headers={'Authorization':'Bearer '+key},
                json={'tool':'anam_result','arguments':{'job_id':token,'offset':16000,'limit':200}})
        response.raise_for_status()
        assert response.json()['ok']
        parsed=response.json()['result']
        assert parsed['offset']==16000 and len(parsed['text'])==200
        report['machine_page_chars']=len(parsed['text'])
        found=await local.call_tool('anam',{'operation':'result','job_id':token,'query':'total','limit':300})
        assert not found.is_error and json.loads(found.content[0].text)['found']
    block='<anam_action>'+json.dumps({'id':token,'operation':'result','job_id':token,'offset':16000,'limit':200})+'</anam_action>'
    action=actions.parse_action(block)
    # Synthetic source proves adapter parity, not a generated ChatGPT turn.
    receipt=await actions.dispatch(action,identity='Claude',conversation_id=token,source_message_id='diagnostic-'+token)
    assert receipt['status']=='succeeded' and receipt['result']['text']==parsed['text']
    report.update({'local_mcp':True,'machine_connector_upstream':True,'bridge_adapter':True,'passed':True})
    path=ROOT/'data/runtime/gateway-parity-check.json'
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)

if __name__=='__main__': asyncio.run(main())
