import {mindArtStudy,type SketchbookToolArgs} from './sketchbook';
import {accessibleObservation,observationFingerprint} from './evidence';
import {identityField,textField} from './focus';
export interface ProcedureArgs extends SketchbookToolArgs {
 domain?:string; title?:string; evidence_observation_id?:number; applicability?:string;
 prerequisites?:string[]; steps?:string[]; failure_modes?:string[]; tool_version?:string;
}
/** One evidence/revision engine for art, writing, code and tool operation. */
export async function mindProcedure(env:{DB:D1Database},args:ProcedureArgs):Promise<string>{
 const identity=identityField(args.identity),action=args.action||'recall';
 if(action==='study'||action==='compare'){
  const domain=textField(args.domain,'domain',100);
  const metadata={...args.metadata,procedure:{domain,applicability:args.applicability||null,prerequisites:args.prerequisites||[],steps:args.steps||[],failure_modes:args.failure_modes||[],tool_version:args.tool_version||null}};
  let source=args.source_path;
  if(args.evidence_observation_id){
   const evidence=await accessibleObservation(env,identity,args.evidence_observation_id);
   if(evidence.archived_at||evidence.superseded_by)throw new Error('Use current evidence');
   source=`qualia:observation:${args.evidence_observation_id}:${await observationFingerprint(env,args.evidence_observation_id)}`;
  }
  return mindArtStudy(env,{...args,identity,action,artwork_title:args.title||args.artwork_title,medium:domain,source_path:source,observation_id:args.evidence_observation_id||args.observation_id,metadata,tags:[...args.tags||[],'procedure',domain]});
 }
 return mindArtStudy(env,{...args,identity,action,medium:args.domain||args.medium});
}
