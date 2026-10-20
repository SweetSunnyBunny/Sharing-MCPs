"""Read-only live check of Codex process retention and autowake isolation.

Uses isolated diagnostic conversations, Anam's real MCP configuration, and no
Anam chat records. Closes only this script's owned diagnostic processes.
"""
import asyncio
import json
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import codex_app_server as provider, codex_sessions as pool

async def main():
    saved = {}
    report = {'turns': []}
    conversation = 'runtime-check-' + uuid.uuid4().hex
    async def lookup(key): return saved.get(key)
    async def turn(source, text):
        key = conversation + ('-wake' if source == 'autowake' else '')
        entry = {'source': source, 'tools': [], 'errors': [], 'tool_results': []}
        pending = None
        latest_input = None
        started = time.monotonic()
        async for event in provider.stream_codex_app_server(text, 'Claude', key, turn_source=source):
            if event['type'] == 'meta' and 'process_reused' in event:
                entry.update({k:event[k] for k in ('process_reused','startup_ms')})
            elif event['type'] == 'tool_use_start':
                entry['tools'].append(event.get('tool_name'))
            elif event['type'] == 'tool_input':
                latest_input = event
            elif event['type'] == 'tool_result':
                entry['tool_results'].append({'tool':event.get('tool_name'),'status':event.get('status'),'content':event.get('content','')[:500]})
            elif event['type'] == 'approval_required':
                pending = event
                print('Diagnostic request: '+event['message'],flush=True)
            elif event['type'] == 'error':
                entry['errors'].append(event['message'])
            elif event['type'] == 'stream_end':
                entry['reply'] = event.get('full_content','')
                saved[key] = event.get('session_id')
            if pending and latest_input and latest_input.get('tool_name') == 'mcp.anam_anam-gateway.anam':
                from services import codex_approvals
                args = latest_input.get('input') or {}
                operation = args.get('operation')
                allowed_invoke = (operation == 'invoke'
                    and args.get('server') == 'anam-context'
                    and args.get('tool') == 'anam_list_canvases'
                    and json.loads(args.get('arguments_json','{}')) == {'identity':'Claude','limit':1})
                allowed = (pending['method'] == 'mcpServer/elicitation/request'
                    and pending['message'] == 'Allow the anam_anam-gateway MCP server to run tool "anam"?'
                    and latest_input.get('tool_name') == 'mcp.anam_anam-gateway.anam'
                    and (operation == 'discover' or allowed_invoke or operation in {'job','result'}))
                # This exact read was explicitly requested by the diagnostic above.
                codex_approvals.resolve(pending['approval_id'], 'accept' if allowed else 'decline', 'Claude', key, {})
                entry['approved_read'] = allowed
                pending = None
        entry['elapsed_seconds'] = round(time.monotonic()-started,2)
        entry['processes'] = pool.status()
        report['turns'].append(entry)
        print(json.dumps(entry, ensure_ascii=True), flush=True)
        assert not entry['errors'] and entry.get('reply'), 'Diagnostic turn failed'
        return entry
    instructions = ('This is a bounded read-only Anam infrastructure diagnostic. '
        'Do not edit files, save memories, send messages, use shell, or control devices. '
        'Use only the single anam tool on the Anam gateway when requested. '
        'The only allowed invoked tool is anam-context/anam_list_canvases, identity Claude, limit 1. '
        'Answer briefly.')
    try:
        with patch.object(provider,'_saved_thread_id',lookup), patch.object(provider,'_build_developer_instructions',return_value=instructions), patch.object(provider,'_TURN_TIMEOUT',180):
            first = await turn('web', 'Discover anam-context anam_list_canvases and invoke it for Claude with limit 1. Report the actual total or the returned error. Remember marker AMBER-73.')
            assert any(r['tool'].endswith('.anam') and 'items' in r['content'] and 'rejected' not in r['content'] for r in first['tool_results']), 'The live read did not return canvas data'
            pid = first['processes'][0]['pid']
            second = await turn('web', 'Without calling any tools, repeat the diagnostic marker from our previous message.')
            assert second['process_reused'] and second['processes'][0]['pid'] == pid
            assert 'AMBER-73' in second['reply']
            wake = await turn('autowake', 'Without calling tools, reply AUTOWAKE-CHECK.')
            assert not wake['process_reused']
            assert len(wake['processes']) == 1 and wake['processes'][0]['pid'] == pid
            assert wake['processes'][0]['alive']
            report['passed'] = True
    finally:
        await pool.shutdown()
        path = ROOT / 'data/runtime/codex-runtime-check.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('PASS: real MCP read, same messaging PID across turns, isolated autowake cleanup',flush=True)

if __name__ == '__main__':
    asyncio.run(main())
