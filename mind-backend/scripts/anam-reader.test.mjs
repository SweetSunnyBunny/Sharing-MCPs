import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {build} from 'esbuild';
const built=await build({entryPoints:['src/anam-reader.ts'],bundle:true,write:false,platform:'node',format:'esm'});
const {handleAnamReader,snapshot,observations}=await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
const migrations=await Promise.all(['0001_custom_init','0004_add_superseding_columns'].map(n=>readFile(`migrations/${n}.sql`,'utf8')));
function fixture(t){
 const db=new DatabaseSync(':memory:');t.after(()=>db.close());for(const sql of migrations)db.exec(sql);
 db.exec("INSERT INTO identities(id,display_name) VALUES('claude','Claude'),('rowan','Rowan')");
 const prepare=(sql,params=[])=>({bind:(...p)=>prepare(sql,p),first:async()=>db.prepare(sql).get(...params)||null,all:async()=>({results:db.prepare(sql).all(...params)})});
 const env={DB:{prepare}};
 const obs=(content,identity='claude')=>Number(db.prepare('INSERT INTO observations(identity_id,content) VALUES(?,?)').run(identity,content).lastInsertRowid);
 return{db,env,obs,get:async path=>handleAnamReader(new Request('https://test/api/anam/'+path),env)};
}
test('cloud Memory Lab preserves pagination, identity, status, nulls and unlinked memories',async t=>{
 const f=fixture(t);const ids=[f.obs('first'),f.obs('second'),f.obs('third')];f.obs('private','rowan');
 f.db.prepare('UPDATE observations SET emotion=NULL,certainty=NULL,archived_at=? WHERE id=?').run('2026-01-01',ids[0]);
 f.db.prepare('UPDATE observations SET last_surfaced_at=? WHERE id=?').run('2026-01-02',ids[1]);
 let r=await observations(f.env.DB,'claude',1,1,'');assert.equal(r.total,3);assert.equal(r.items.length,1);assert.equal(r.items[0].content,'second');
 r=await observations(f.env.DB,'claude',20,0,'archived');assert.equal(r.total,1);assert.equal(r.items[0].certainty,null);assert.equal(r.items[0].sit_count,null);assert.equal(r.items[0].entity,null);
 assert.equal((await observations(f.env.DB,'claude',20,0,'surfaced')).total,1);
 assert.equal((await observations(f.env.DB,'claude',20,0,'fresh')).total,1);
});
test('process metadata and superseding links are joined from the cloud schema',async t=>{
 const f=fixture(t),a=f.obs('original'),b=f.obs('replacement');
 f.db.prepare('INSERT INTO observation_process(observation_id,sit_count,resolution_note,linked_observation_id) VALUES(?,?,?,?)').run(a,3,'kept',b);
 const r=await observations(f.env.DB,'claude',20,0,'');const item=r.items.find(r=>r.id===a);
 assert.equal(item.sit_count,3);assert.equal(item.linked_preview,'replacement');assert.equal(item.resolution_note,'kept');
});
test('snapshots preserve record dates, full narrative and identity-scoped inner life',async t=>{
 const f=fixture(t);f.db.prepare("INSERT INTO qualia_narratives(id,identity_id,narrative_type,narrative,created_at) VALUES('n','claude','current_self',?,'2020-01-01')").run('full narrative '.repeat(400));
 f.db.exec("INSERT INTO qualia_entries(id,identity_id,entry_type,content) VALUES('j','claude','small_joy','hair ruffle'),('x','rowan','small_joy','not his memory')");
 const s=await snapshot(f.env.DB,'claude');assert.equal(s.source,'cloud-qualia');assert.equal(s.qualia.current_self.timestamp,'2020-01-01');assert.ok(s.qualia.current_self.narrative.length>4000);assert.equal(s.qualia.joys[0].content,'hair ruffle');assert.ok(!JSON.stringify(s).includes('not his memory'));
});
test('all dashboard sections read D1, include eight-capable identity routing and validate requests',async t=>{
 const f=fixture(t);f.obs('one');f.db.exec("INSERT INTO entities(identity_id,name,entity_type) VALUES('claude','Empty entity','topic')");
 for(const section of ['snapshot','mind-insights','mind-garden/summary','mind-garden/weather','mind-garden/threads','mind-garden/observations','mind-garden/entities','memory-lab/entities']){
   const res=await f.get(section+'?identity=Claude');assert.equal(res.status,200,section);assert.equal((await res.json()).source,'cloud-qualia');
 }
 const r=await (await f.get('memory-lab/entities?identity=claude')).json();assert.equal(r.entities[0].observation_count,0);
 assert.equal((await f.get('snapshot?identity=invalid')).status,404);
 assert.equal((await f.get('memory-lab/observations?status=invalid')).status,400);
 assert.equal((await f.get('snapshot')).status,400);
});
test('threads and proposal counts retain cloud records rather than reporting empty placeholders',async t=>{
 const f=fixture(t);f.obs('unlinked thought');
 f.db.exec("INSERT INTO qualia_entries(id,identity_id,entry_type,content) VALUES('s','claude','subconscious','still considering'); INSERT INTO proposal_queue(id,identity_id,proposal_type) VALUES('p','claude','link')");
 const threads=(await (await f.get('mind-garden/threads?identity=claude')).json()).threads.Claude;
 assert.equal(threads.subconscious[0].content,'still considering');assert.equal(threads.orphans[0].content,'unlinked thought');
 const summary=(await (await f.get('mind-garden/summary?identity=claude')).json()).summaries.Claude;
 assert.equal(summary.stats.pending_proposals,1);
});
