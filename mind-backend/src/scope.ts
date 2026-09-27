import {mindEvidence, type EvidenceEnv} from './evidence';
import {identityField,parseObject,textField} from './focus';

export interface ScopeArgs {
 identity?:string;
 action?:string;
 observation_id?:number;
 expected_revision?:number;
 applies_when?:string[];
 does_not_apply_when?:string[];
 source_ids?:number[];
 reason?:string;
}

interface ScopedState {
 kind:'scoped-state';
 version:1;
 applies_when:string[];
 does_not_apply_when:string[];
}

function readScope(value:unknown):ScopedState|null {
 if(typeof value!=='string'||!value.trim()) return null;
 try {
  const parsed=JSON.parse(value) as Partial<ScopedState>;
  if(parsed?.kind!=='scoped-state'||parsed.version!==1||!Array.isArray(parsed.applies_when)||!Array.isArray(parsed.does_not_apply_when)) return null;
  if(!parsed.applies_when.every(item=>typeof item==='string')||!parsed.does_not_apply_when.every(item=>typeof item==='string')) return null;
  return {kind:'scoped-state',version:1,applies_when:parsed.applies_when,does_not_apply_when:parsed.does_not_apply_when};
 } catch { return null; }
}

function conditions(value:unknown,field:string,required=false):string[] {
 if(value===undefined) {
  if(required) throw new Error(`${field} is required`);
  return [];
 }
 if(!Array.isArray(value)) throw new Error(`${field} must be an array of strings`);
 if(value.length>12) throw new Error(`${field} accepts at most 12 conditions`);
 const result=[...new Set(value.map(item=>textField(item,field,500)))];
 if(required&&!result.length) throw new Error(`${field} requires at least one condition`);
 return result;
}

function scopeView(payload:Record<string,unknown>):Record<string,unknown> {
 const provenance=payload.provenance as Record<string,unknown>|null;
 const stored=provenance?.applicability;
 const scope=readScope(stored);
 const history=Array.isArray(payload.history) ? payload.history as Record<string,unknown>[] : [];
 return {
  ...payload,
  scope,
  legacy_applicability:scope ? null : stored ?? null,
  scope_history:history.map(entry=>{
   const snapshot=parseObject(typeof entry.snapshot==='string' ? entry.snapshot : null);
   const historicalScope=readScope(snapshot.applicability);
   return {
    revision:entry.revision,
    recorded_at:entry.recorded_at,
    scope:historicalScope,
    legacy_applicability:historicalScope ? null : snapshot.applicability ?? null,
    reason:snapshot.reason,
   };
  }),
 };
}

export async function mindScope(env:EvidenceEnv,args:ScopeArgs):Promise<string> {
 const identity=identityField(args.identity),id=Number(args.observation_id),action=args.action||'read';
 const current=JSON.parse(await mindEvidence(env,{identity,observation_id:id,action:'read'})) as Record<string,unknown>;
 if(action==='read') return JSON.stringify(scopeView(current));
 if(!['define','revise'].includes(action)) throw new Error('Scope actions: read, define, revise');
 const provenance=current.provenance as Record<string,unknown>|null;
 const priorScope=readScope(provenance?.applicability);
 if(action==='define'&&priorScope) throw new Error('This memory already has structured scope; use revise with the current revision');
 if(action==='revise'&&!priorScope) throw new Error('This memory has no structured scope; use define first');
 const appliesWhen=conditions(args.applies_when,'applies_when',true);
 const doesNotApplyWhen=conditions(args.does_not_apply_when,'does_not_apply_when');
 const excluded=new Set(doesNotApplyWhen.map(item=>item.toLocaleLowerCase()));
 if(appliesWhen.some(item=>excluded.has(item.toLocaleLowerCase()))) throw new Error('A condition cannot both apply and not apply');
 const scope:ScopedState={kind:'scoped-state',version:1,applies_when:appliesWhen,does_not_apply_when:doesNotApplyWhen};
 const result=JSON.parse(await mindEvidence(env,{
  identity,
  action:'annotate',
  observation_id:id,
  expected_revision:args.expected_revision,
  applicability:JSON.stringify(scope),
  source_ids:args.source_ids,
  reason:textField(args.reason,'reason'),
 })) as Record<string,unknown>;
 return JSON.stringify(scopeView(result));
}
