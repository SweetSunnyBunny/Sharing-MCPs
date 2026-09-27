export interface FocusEnv { DB: D1Database }
export interface FocusArgs {
 identity?: string; action?: string; session_key?: string; expected_revision?: number;
 document?: Record<string, unknown>; retention_days?: number; limit?: number;
}
export function textField(value: unknown, field: string, max=4000): string {
 if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(`${field} is required (maximum ${max} characters)`);
 return value.trim();
}
export function identityField(value: unknown): string { return textField(value,'identity',80).toLowerCase(); }
export function parseObject(value: string | null): Record<string, unknown> {
 try { const data=JSON.parse(value || '{}'); return data && typeof data==='object' && !Array.isArray(data) ? data : {}; } catch { return {}; }
}
interface FocusRow { id:string; identity_id:string; session_key:string; revision:number; status:string; document:string; expires_at:string|null; created_at:string; updated_at:string }
function view(row: FocusRow) { return {...row, document:parseObject(row.document), expired:!!row.expires_at && Date.parse(row.expires_at)<=Date.now()}; }
export async function readFocus(env:FocusEnv,identity:string,key:string) {
 const row=await env.DB.prepare('SELECT * FROM mind_focus WHERE identity_id=? AND session_key=?').bind(identity,key).first<FocusRow>();
 return row ? view(row) : null;
}
export async function mindFocus(env:FocusEnv,args:FocusArgs):Promise<string> {
 const identity=identityField(args.identity), action=args.action || 'read', now=new Date().toISOString();
 if (action==='list') {
   const rows=await env.DB.prepare("SELECT * FROM mind_focus WHERE identity_id=? ORDER BY updated_at DESC LIMIT ?").bind(identity,Math.min(30,Math.max(1,Number(args.limit)||12))).all<FocusRow>();
   return JSON.stringify({focus_records:(rows.results||[]).map(view)});
 }
 const key=textField(args.session_key,'session_key',200);
 if (action==='read') return JSON.stringify({focus:await readFocus(env,identity,key)});
 if (action==='history') {
   const rows=await env.DB.prepare('SELECT h.* FROM mind_focus_history h JOIN mind_focus f ON f.id=h.focus_id WHERE f.identity_id=? AND f.session_key=? ORDER BY h.revision DESC LIMIT 30').bind(identity,key).all();
   return JSON.stringify({history:rows.results||[]});
 }
 if (!['open','update','park','resume','close'].includes(action)) throw new Error('Unknown focus action');
 const prior=await readFocus(env,identity,key);
 if (action==='open' && prior) return JSON.stringify({focus:prior,created:false});
 if (action!=='open' && !prior) throw new Error('No focus for this session_key; open it first');
 if (prior && args.expected_revision!==prior.revision) throw new Error(`Focus revision conflict; read current revision ${prior.revision} before changing it`);
 if (args.document && (typeof args.document!=='object'||Array.isArray(args.document))) throw new Error('document must be an object');
 const document=JSON.stringify(args.document===undefined ? prior?.document || {} : args.document);
 if (document.length>12000) throw new Error('Working document exceeds 12000 characters; use durable source references');
 let expires=prior?.expires_at ?? null;
 if (args.retention_days!==undefined) {
   if (!Number.isInteger(args.retention_days)||args.retention_days<0||args.retention_days>3650) throw new Error('retention_days must be 0 (keep) through 3650');
   expires=args.retention_days ? new Date(Date.now()+args.retention_days*86400000).toISOString():null;
 }
 const status=action==='park'?'parked':action==='close'?'closed':action==='update'?prior!.status:'active';
 let created=false;
 if (!prior) {
   const inserted=await env.DB.prepare("INSERT INTO mind_focus(id,identity_id,session_key,document,status,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(identity_id,session_key) DO NOTHING").bind(crypto.randomUUID(),identity,key,document,status,expires,now,now).run();
   created=!!inserted.meta.changes;
 } else {
   // The update and its prior-state record are atomic and guarded by revision.
   const results=await env.DB.batch([
     env.DB.prepare('INSERT INTO mind_focus_history(focus_id,identity_id,revision,status,document,expires_at,recorded_at) SELECT id,identity_id,revision,status,document,expires_at,? FROM mind_focus WHERE id=? AND revision=?').bind(now,prior.id,args.expected_revision),
     env.DB.prepare('UPDATE mind_focus SET document=?,status=?,expires_at=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?').bind(document,status,expires,now,prior.id,args.expected_revision),
   ]);
   if (!results[1].meta.changes) throw new Error('Focus revision conflict; another session changed this record');
 }
 return JSON.stringify({focus:await readFocus(env,identity,key),created, note:'Working continuity, not a permanent autobiographical claim. Park or close it when appropriate.'});
}
