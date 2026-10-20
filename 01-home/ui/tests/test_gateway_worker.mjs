// Self-contained public worker fixture; no private runtime staging is required.
import assert from 'node:assert/strict';
import worker from './fixtures/gateway-worker/mcp-worker.js';

const env = {SERVER_NAME:'filesystem', MCP_AUTH_TOKEN:'test-public', MACHINE_AGENT_KEY:'test-upstream', MACHINE_AGENT_URL:'https://machine.invalid'};
const rpc = async (method, params={}, overrides={}) => {
  const response = await worker.fetch(new Request('https://worker.invalid/mcp', {
    method:'POST', headers:{Authorization:'Bearer test-public','Content-Type':'application/json'},
    body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})
  }), {...env,...overrides});
  return response.json();
};
const catalog = (await rpc('tools/list')).result.tools;
assert.equal(catalog.filter(t=>t.name.startsWith('anam_')).length,4);
assert.equal(catalog.find(t=>t.name==='anam_invoke').annotations.readOnlyHint,false);
assert.equal(catalog.find(t=>t.name==='anam_discover').annotations.readOnlyHint,true);
assert.equal(catalog.find(t=>t.name==='anam_result').annotations.readOnlyHint,true);
assert.equal(catalog.find(t=>t.name==='anam_discover').inputSchema.properties.include_schema.default,true);
let calls=0;
globalThis.fetch=async (url, opts)=>{
  calls++;
  assert.equal(url,'https://machine.invalid/tools/invoke');
  assert.equal(opts.headers.Authorization,'Bearer test-upstream');
  return Response.json({ok:true,result:{status:'completed',isError:true,
    content:[{type:'image',data:'YWJj',mimeType:'image/png'},{type:'text',text:'partial image'}],
    structuredContent:{ok:false}}});
};
const output=(await rpc('tools/call',{name:'anam_invoke',arguments:{}})).result;
assert.equal(output.content[0].type,'image');
assert.equal(output.content.length,2);
assert.equal(output.isError,true);
assert.deepEqual(output.structuredContent,{ok:false});
const blocked=(await rpc('tools/call',{name:'anam_invoke',arguments:{}},{MACHINE_AGENT_KEY:''})).result;
assert.equal(blocked.isError,true);
assert.equal(calls,1);
console.log('Gateway Worker: catalog, annotations, native content, error flags, and fail-closed upstream auth passed');
