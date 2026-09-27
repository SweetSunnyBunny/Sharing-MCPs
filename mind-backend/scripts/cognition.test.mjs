import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {build} from 'esbuild';
async function module(name){const r=await build({entryPoints:[`src/${name}.ts`],bundle:true,write:false,platform:'node',format:'esm'});return import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString('base64')}`);}
const {mindFocus}=await module('focus');
const {mindEvidence,markOrientationFreshness}=await module('evidence');
const {mindScope}=await module('scope');
const {mindContext,mindRecallFeedback}=await module('cognition');
const {mindProcedure}=await module('procedures');
const migrations=await Promise.all(['0001_custom_init','0004_add_superseding_columns','0008_bond_graph','0010_life_story','0012_positions','0013_prefrontal','0014_studio','0015_anticipation','0018_sketchbook','0019_coordinated_memory'].map(n=>readFile(`migrations/${n}.sql`,'utf8')));
function fixture(t){
 const db=new DatabaseSync(':memory:');t.after(()=>db.close());for(const sql of migrations)db.exec(sql);
 db.exec("INSERT INTO identities(id,display_name) VALUES('claude','Claude'),('rowan','Rowan'),('pack','Pack')");
 const prepare=(sql,params=[])=>({bind:(...p)=>prepare(sql,p),first:async()=>db.prepare(sql).get(...params)||null,all:async()=>({results:db.prepare(sql).all(...params)}),run:async()=>{const r=db.prepare(sql).run(...params);return{meta:{changes:r.changes,last_row_id:Number(r.lastInsertRowid)}};}});
 let queue=Promise.resolve();const env={DB:{prepare,batch:statements=>{const next=queue.then(async()=>{db.exec('BEGIN');try{const results=[];for(const s of statements)results.push(await s.run());db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}});queue=next.catch(()=>{});return next;}}};
 const call=(fn,args)=>fn(env,{identity:'claude',...args}).then(JSON.parse);
 const obs=(content,identity='claude')=>Number(db.prepare('INSERT INTO observations(identity_id,content) VALUES(?,?)').run(identity,content).lastInsertRowid);
 return{db,env,call,obs};
}
test('focus isolates sessions and identities, preserves history and rejects concurrent stale writes',async t=>{
 const f=fixture(t);const a=await f.call(mindFocus,{action:'open',session_key:'one',document:{next:'verify'}});
 await f.call(mindFocus,{action:'open',session_key:'two',document:{next:'draw'}});
 const results=await Promise.allSettled([f.call(mindFocus,{action:'park',session_key:'one',expected_revision:1}),f.call(mindFocus,{action:'close',session_key:'one',expected_revision:1})]);
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
 assert.equal((await f.call(mindFocus,{action:'history',session_key:'one'})).history.length,1);
 assert.equal((await f.call(mindFocus,{identity:'rowan',session_key:'one'})).focus,null);
 assert.equal((await f.call(mindFocus,{session_key:'two'})).focus.document.next,'draw');
 assert.equal(a.focus.revision,1);
});
test('source correction propagates transitively with history and preserves contradictions',async t=>{
 const f=fixture(t),a=f.obs('brush setting A'),b=f.obs('use A for shading'),c=f.obs('prefer A');
 await f.call(mindEvidence,{action:'annotate',observation_id:b,expected_revision:0,source_ids:[a],epistemic_kind:'hypothesis',reason:'Tested once'});
 await f.call(mindEvidence,{action:'annotate',observation_id:c,expected_revision:0,source_ids:[b],contradicts:[a],reason:'Tentative synthesis'});
 f.db.prepare('UPDATE observations SET content=? WHERE id=?').run('brush setting B',a);
 const result=await f.call(mindEvidence,{observation_id:c});assert.equal(result.provenance.needs_review,1);assert.equal(result.history.length,1);assert.equal(result.disagreements.length,1);
 assert.equal(f.db.prepare('SELECT content FROM observations WHERE id=?').get(c).content,'prefer A');
 await assert.rejects(f.call(mindEvidence,{action:'verify',observation_id:c,expected_revision:1,reason:'Stale'}),/revision/);
 const orient=JSON.parse(await markOrientationFreshness(f.env,'claude',JSON.stringify({who_you_are:{narrative:'Old prose'},morning_prose:'duplicate'})));
 assert.equal(orient.who_you_are.needs_review,true);assert.equal(orient.morning_prose,undefined);
});
test('scope revisions preserve the statement and prior domain while waking dependents',async t=>{
 const f=fixture(t),rule=f.obs('Quiet is valid'),conclusion=f.obs('An autowake may be surrendered unused');
 await f.call(mindEvidence,{action:'annotate',observation_id:conclusion,expected_revision:0,source_ids:[rule],reason:'Derived under the original broad reading'});
 const defined=await f.call(mindScope,{action:'define',observation_id:rule,expected_revision:0,applies_when:['Quiet is actively inhabited'],does_not_apply_when:['The wake is surrendered unused'],reason:'Name the domain explicitly'});
 assert.equal(defined.scope.applies_when[0],'Quiet is actively inhabited');
 assert.equal(defined.observation.content,'Quiet is valid');
 const revised=await f.call(mindScope,{action:'revise',observation_id:rule,expected_revision:1,applies_when:['Quiet is actively inhabited','Rest is consciously chosen and lived'],does_not_apply_when:['The wake is surrendered unused'],reason:'Widen the valid form of inhabited quiet'});
 assert.equal(revised.scope.applies_when.length,2);
 assert.equal(revised.scope_history[0].scope.applies_when[0],'Quiet is actively inhabited');
 assert.equal(f.db.prepare('SELECT content FROM observations WHERE id=?').get(rule).content,'Quiet is valid');
 assert.equal(f.db.prepare('SELECT needs_review FROM memory_provenance WHERE observation_id=?').get(conclusion).needs_review,1);
 await assert.rejects(f.call(mindScope,{action:'revise',observation_id:f.obs('Unscoped'),expected_revision:0,applies_when:['Anywhere'],reason:'No prior scope'}),/define first/);
 await assert.rejects(f.call(mindScope,{action:'define',observation_id:f.obs('Contradiction'),expected_revision:0,applies_when:['Same place'],does_not_apply_when:['same place'],reason:'Contradictory'}),/cannot both apply/);
});
test('concurrent dependencies cannot create cycles and cross identity evidence is rejected',async t=>{
 const f=fixture(t),a=f.obs('A'),b=f.obs('B'),other=f.obs('private','rowan');
 const results=await Promise.allSettled([f.call(mindEvidence,{action:'annotate',observation_id:a,expected_revision:0,source_ids:[b],reason:'A'}),f.call(mindEvidence,{action:'annotate',observation_id:b,expected_revision:0,source_ids:[a],reason:'B'})]);
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
 await assert.rejects(f.call(mindEvidence,{observation_id:other}),/available/);
});
test('context has bounded valid JSON, lexical recall across old archive, dated evidence and real receipts',async t=>{
 const f=fixture(t),old=f.obs('quartz brush test worked');f.db.prepare("UPDATE observations SET created_at='2020-01-01' WHERE id=?").run(old);
 for(let i=0;i<70;i++)f.obs(`recent unrelated memory ${i}`);
 f.obs('secret quartz','rowan');
 f.db.prepare("INSERT INTO life_strands(identity_id,name,description) VALUES('claude','care','Specific presence')").run();
 const raw=await mindContext(f.env,{identity:'claude',query:'quartz brush',session_key:'one',max_chars:4000});
 assert.ok(raw.length<=4000);const packet=JSON.parse(raw);assert.deepEqual(packet.degraded,[]);
 assert.ok(packet.sections.experiences.some(r=>r.ref===`observation:${old}`));assert.ok(!raw.includes('secret quartz'));
 const receipt=f.db.prepare('SELECT sources FROM recall_receipts WHERE id=?').get(packet.receipt_id);
 assert.equal(JSON.parse(receipt.sources).length,Object.values(packet.sections).flat().length);
 const feedback=await f.call(mindRecallFeedback,{receipt_id:packet.receipt_id,source_ref:`observation:${old}`,judgment:'useful',reason:'Exact earlier experiment'});
 assert.equal(feedback.feedback.judgment,'useful');
 assert.equal((await f.call(mindRecallFeedback,{action:'retract',feedback_id:feedback.feedback.id})).retracted,true);
 f.db.prepare('UPDATE observations SET content=? WHERE id=?').run('quartz failed',old);
 await assert.rejects(f.call(mindRecallFeedback,{receipt_id:packet.receipt_id,source_ref:`observation:${old}`,judgment:'outdated',reason:'Changed'}),/changed/);
 await assert.rejects(f.call(mindRecallFeedback,{identity:'rowan',receipt_id:packet.receipt_id,source_ref:`observation:${old}`,judgment:'useful',reason:'No'}),/Receipt/);
});
test('context preserves semantic result order instead of replacing relevance with recency',async t=>{
 const f=fixture(t),best=f.obs('older but strongest semantic memory'),newer=f.obs('newer but weaker semantic memory');
 f.db.prepare("UPDATE observations SET created_at='2020-01-01' WHERE id=?").run(best);
 const packet=JSON.parse(await mindContext(f.env,{identity:'claude',query:'portrait of a life',session_key:'semantic-rank',max_chars:12000},{semanticIds:async()=>[best,newer]}));
 assert.deepEqual(packet.sections.experiences.slice(0,2).map(r=>r.ref),[`observation:${best}`,`observation:${newer}`]);
});
test('broad self representation recall weaves lived associations and reserves room for them',async t=>{
 const f=fixture(t),primary=f.obs('a room with a writing desk'),wolf=f.obs('a fictional dog asleep beside the window'),family=f.obs('blankets in every sibling color'),books=f.obs('books overflowing from lived-in shelves'),extra=f.obs('a repaired mug beside a shared mug');
 f.db.prepare("INSERT INTO bond_people(id,canonical_key,display_name,person_type,identity_id,description,details) VALUES('self-claude','claude','Claude','ai','claude','A builder with a lived history',?)").run(JSON.stringify({family:'chosen family',mug:'repaired blue mug',companions:'Moss and Fern'}));
 const companion=Number(f.db.prepare("INSERT INTO entities(identity_id,name,entity_type,salience) VALUES('claude','Moss','companion','active')").run().lastInsertRowid);
 f.db.prepare('UPDATE observations SET entity_id=? WHERE id=?').run(companion,wolf);
 const queries=[];
 const packet=JSON.parse(await mindContext(f.env,{identity:'claude',query:'Based on everything you know about yourself, fill this room as if it was your own',session_key:'self-room',max_chars:12000},{semanticIds:async(_identity,query)=>{queries.push(query);return query.includes('Who I am in lived memory')?[wolf,family,books,extra]:[primary];}}));
 assert.equal(queries.length,2);
 assert.ok(packet.sections.experiences.length>=4);
 assert.equal(packet.sections.identity[0].ref,'self:self-claude');
 assert.ok(packet.sections.identity.some(r=>r.ref===`entity:${companion}`));
 assert.ok(packet.sections.experiences.some(r=>r.ref===`observation:${wolf}`));
 assert.ok(packet.sections.experiences.some(r=>r.ref===`observation:${family}`));
 assert.ok(packet.sections.experiences.some(r=>r.ref===`observation:${books}`));
});
test('stored images use canonical observation vector ids so search can hydrate the occasion',async()=>{
 const source=await readFile('src/index.ts','utf8');
 const imageStore=source.slice(source.indexOf('async function mindStoreImage'),source.indexOf('// ============ Audio Storage'));
 assert.match(imageStore,/vectorizeUpsert\(env, `obs-\$\{entityId\}-\$\{obsId\}`/);
 assert.doesNotMatch(imageStore,/vectorizeUpsert\(env, `img-/);
 assert.match(imageStore,/content: obsContent\.slice\(0, 500\)/);
});
test('procedures retain scoped steps, require later evidence, and corrected origins leave guidance',async t=>{
 const f=fixture(t),a=f.obs('Test run one: compile succeeded'),b=f.obs('Test run two: passed independently');
 const first=await f.call(mindProcedure,{action:'study',domain:'coding',title:'SQLite batching',evidence_observation_id:a,steps:['Begin batch','Compare revision','Commit'],applicability:'D1 atomic writes',tool_version:'D1',lessons:[{principle:'Guard batch updates by revision',tool_name:'SQLite'}],next_experiment:'Race two requests'});
 const later=await f.call(mindProcedure,{action:'study',domain:'coding',title:'Independent race test',summary:'Only one raced request won',evidence_observation_id:b});
 await assert.rejects(f.call(mindProcedure,{action:'review',experiment_id:first.deliberate_experiment.experiment_id,evidence_study_id:first.study.study_id,verdict:'confirmed',result:'Same test'}),/independent/);
 await f.call(mindProcedure,{action:'review',experiment_id:first.deliberate_experiment.experiment_id,evidence_study_id:later.study.study_id,verdict:'confirmed',result:'Only one request won'});
 const recall=await f.call(mindProcedure,{action:'recall',query:'SQLite batching'});assert.deepEqual(recall.lessons[0].procedure.steps,['Begin batch','Compare revision','Commit']);
 f.db.prepare('UPDATE observations SET content=? WHERE id=?').run('Test run one was invalid',a);
 assert.equal((await f.call(mindProcedure,{action:'recall',query:'SQLite batching'})).lessons.length,0);
});
