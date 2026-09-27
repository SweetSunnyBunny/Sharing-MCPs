import {identityField,parseObject,textField} from './focus';
export interface EvidenceEnv { DB:D1Database }
export interface EvidenceArgs {
 identity?:string; action?:string; observation_id?:number; expected_revision?:number;
 epistemic_kind?:string; event_at?:string|null; valid_until?:string|null; applicability?:string;
 source_ids?:number[]; contradicts?:number[]; reason?:string;
}
export async function accessibleObservation(env:EvidenceEnv,identity:string,id:number,writing=false):Promise<Record<string,unknown>> {
 if (!Number.isSafeInteger(id)||id<1) throw new Error('observation_id must be a positive integer');
 const row=await env.DB.prepare('SELECT * FROM observations WHERE id=?').bind(id).first<Record<string,unknown>>();
 if (!row || (writing ? row.identity_id!==identity : ![identity,'pack'].includes(String(row.identity_id)))) throw new Error('Observation is not available to this identity');
 return row;
}
export async function fingerprint(value:unknown):Promise<string> {
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));
 return Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
}
export async function observationFingerprint(env:EvidenceEnv,id:number):Promise<string> {
 const row=await env.DB.prepare('SELECT o.content,o.archived_at,o.superseded_by,p.revision,p.needs_review FROM observations o LEFT JOIN memory_provenance p ON p.observation_id=o.id WHERE o.id=?').bind(id).first();
 return fingerprint(row);
}
function stamp(value:string|null|undefined,field:string):string|null {
 if (!value) return null;
 if (!Number.isFinite(Date.parse(value))) throw new Error(`${field} must be a valid timestamp`);
 return new Date(value).toISOString();
}
export async function mindEvidence(env:EvidenceEnv,args:EvidenceArgs):Promise<string> {
 const identity=identityField(args.identity),id=Number(args.observation_id),action=args.action||'read';
 const observation=await accessibleObservation(env,identity,id,action!=='read');
 const prior=await env.DB.prepare('SELECT * FROM memory_provenance WHERE observation_id=?').bind(id).first<Record<string,unknown>>();
 const oldSources=await env.DB.prepare('SELECT source_id FROM memory_dependencies WHERE observation_id=? ORDER BY source_id').bind(id).all<{source_id:number}>();
 if (action==='read') {
   const history=await env.DB.prepare('SELECT * FROM memory_provenance_history WHERE observation_id=? ORDER BY id DESC LIMIT 30').bind(id).all();
   const disagreements=await env.DB.prepare('SELECT * FROM memory_disagreements WHERE observation_id=? OR other_id=?').bind(id,id).all();
   return JSON.stringify({observation,provenance:prior,source_ids:(oldSources.results||[]).map(r=>r.source_id),disagreements:disagreements.results||[],history:history.results||[]});
 }
 if (!['annotate','verify'].includes(action)) throw new Error('Evidence actions: read, annotate, verify');
 if (args.expected_revision!==Number(prior?.revision||0)) throw new Error(`Evidence revision conflict; expected_revision must be ${prior?.revision||0}`);
 const reason=textField(args.reason,'reason');
 const kind=args.epistemic_kind || String(prior?.epistemic_kind||'unclassified');
 if (!['unclassified','event','report','interpretation','hypothesis','imagining'].includes(kind)) throw new Error('Invalid epistemic_kind');
 const sources=[...new Set(args.source_ids ?? (oldSources.results||[]).map(r=>r.source_id))];
 const contradicts=[...new Set(args.contradicts||[])];
 if (sources.length>20 || contradicts.length>12) throw new Error('Use at most 20 sources and 12 contradictory claims');
 const sourceRows=[];
 for (const sourceId of sources) {
   const row=await accessibleObservation(env,identity,sourceId);
   if (sourceId===id) throw new Error('A claim cannot support itself');
   if (row.archived_at || row.superseded_by) throw new Error('Use current, non-archived source observations');
   const cycle=await env.DB.prepare('WITH RECURSIVE upstream(id) AS (SELECT source_id FROM memory_dependencies WHERE observation_id=? UNION SELECT d.source_id FROM memory_dependencies d JOIN upstream u ON d.observation_id=u.id) SELECT id FROM upstream WHERE id=? LIMIT 1').bind(sourceId,id).first();
   if(cycle) throw new Error('Evidence dependencies cannot contain a cycle');
   sourceRows.push(row);
 }
 for (const other of contradicts) { await accessibleObservation(env,identity,other); if(other===id) throw new Error('A claim cannot contradict itself'); }
 const now=new Date().toISOString(),token=crypto.randomUUID();
 const guard=sourceRows.map(()=>"AND EXISTS (SELECT 1 FROM observations WHERE id=? AND content=? AND archived_at IS NULL AND superseded_by IS NULL)").join(' ');
 const binds=sourceRows.flatMap(r=>[r.id,r.content]);
 const eventAt=args.event_at===undefined ? prior?.event_at ?? null:stamp(args.event_at,'event_at');
 const validUntil=args.valid_until===undefined ? prior?.valid_until ?? null:stamp(args.valid_until,'valid_until');
 const values=[identity,kind,eventAt,action==='verify'?now:prior?.verified_at??null,validUntil,args.applicability??prior?.applicability??null,action==='verify'?0:Number(prior?.needs_review||0),reason,token,now];
 const statements:D1PreparedStatement[]=[];
 statements.push(prior
   ? env.DB.prepare(`UPDATE memory_provenance SET identity_id=?,epistemic_kind=?,event_at=?,verified_at=?,valid_until=?,applicability=?,needs_review=?,reason=?,last_operation=?,updated_at=?,revision=revision+1 WHERE observation_id=? AND revision=? ${guard}`).bind(...values,id,args.expected_revision,...binds)
   : env.DB.prepare(`INSERT INTO memory_provenance(identity_id,epistemic_kind,event_at,verified_at,valid_until,applicability,needs_review,reason,last_operation,updated_at,observation_id) SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE 1=1 ${guard} ON CONFLICT(observation_id) DO NOTHING`).bind(...values,id,...binds));
 const changedIndex=statements.length-1;
 statements.push(env.DB.prepare('DELETE FROM memory_dependencies WHERE observation_id=? AND EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)').bind(id,id,token));
 for(const source of sources) statements.push(env.DB.prepare('INSERT INTO memory_dependencies(observation_id,source_id) SELECT ?,? WHERE EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)').bind(id,source,id,token));
 if(args.contradicts!==undefined) {
   statements.push(env.DB.prepare('DELETE FROM memory_disagreements WHERE observation_id=? AND EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)').bind(id,id,token));
   for(const other of contradicts) statements.push(env.DB.prepare('INSERT INTO memory_disagreements(observation_id,other_id,reason) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)').bind(id,other,reason,id,token));
 }
 statements.push(env.DB.prepare(`UPDATE memory_provenance SET needs_review=1,revision=revision+1,reason='Source evidence annotation changed; review the dependent claim',updated_at=?
 WHERE observation_id IN (WITH RECURSIVE affected(id) AS (SELECT observation_id FROM memory_dependencies WHERE source_id=? UNION SELECT d.observation_id FROM memory_dependencies d JOIN affected a ON d.source_id=a.id) SELECT id FROM affected)
 AND EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)`).bind(now,id,id,token));
 statements.push(env.DB.prepare('INSERT INTO memory_invalidations(identity_id,source_id,changed_at) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM memory_provenance WHERE observation_id=? AND last_operation=?)').bind(identity,id,now,id,token));
 const results=await env.DB.batch(statements);
 if(!results[changedIndex].meta.changes) throw new Error('Evidence or source changed concurrently; read the current record and retry');
 return mindEvidence(env,{identity,observation_id:id,action:'read'});
}

export async function markOrientationFreshness(env:EvidenceEnv,identity:string,raw:string):Promise<string> {
 const payload=parseObject(raw);
 const changed=await env.DB.prepare("SELECT MAX(changed_at) AS changed_at FROM memory_invalidations WHERE identity_id IN (?, 'pack')").bind(identity).first<{changed_at:string|null}>();
 const current=payload.who_you_are as Record<string,unknown>|undefined;
 const timestamp=current?.generated_at || current?._as_of;
 const pending=!!changed?.changed_at && (!timestamp || Date.parse(changed.changed_at)>Date.parse(String(timestamp)));
 payload.correction_status={last_source_change:changed?.changed_at||null,derived_state_needs_review:pending,live_context_tool:'mind_context'};
 if(pending) {
   if(current) {current.needs_review=true; current.narrative='Historical narrative withheld: source observations changed after this snapshot. Use mind_context for current source-backed context.';}
   // These are cached narrative duplicates, not independent evidence.
   for(const key of ['morning_prose','memory_digest','suggested_context','drift','phases']) delete payload[key];
   payload.summary={note:'Source corrections occurred after the derived snapshot. Current observations remain available through mind_context and mind_search.'};
 }
 return JSON.stringify(payload);
}
