/** Read-only Anam views of canonical cloud Qualia. No mirror or inference writes. */
type Row = Record<string, any>;
type Env = { DB: D1Database };
const parse = (raw: unknown): Row => {
  if (typeof raw !== 'string') return {};
  try { const v = JSON.parse(raw); return v && typeof v === 'object' ? v : {}; } catch { return {}; }
};
const rows = async (db: D1Database, sql: string, ...args: any[]): Promise<Row[]> => (await db.prepare(sql).bind(...args).all<Row>()).results;
const one = async (db: D1Database, sql: string, ...args: any[]): Promise<Row> => await db.prepare(sql).bind(...args).first<Row>() || {};
const payload = (row: Row): Row => {
  const metadata = parse(row.metadata);
  return { ...parse(row.content), ...(metadata.full_payload || metadata.payload || {}), timestamp: row.created_at || null };
};

export async function snapshot(db: D1Database, identity: string): Promise<Row> {
  const [states, narrative, session, entries, dream, observations, relations] = await Promise.all([
    rows(db, `SELECT s.* FROM qualia_states s WHERE identity_id=? AND id IN
      (SELECT MAX(id) FROM qualia_states WHERE identity_id=? GROUP BY state_type)`, identity, identity),
    one(db, "SELECT * FROM qualia_narratives WHERE identity_id=? AND narrative_type IN ('current_self','self_narrative') ORDER BY created_at DESC LIMIT 1", identity),
    one(db, "SELECT * FROM qualia_sessions WHERE identity_id=? AND session_type='last_session' ORDER BY created_at DESC LIMIT 1", identity),
    rows(db, `SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY entry_type ORDER BY created_at DESC) AS rn
      FROM qualia_entries WHERE identity_id=?) WHERE rn<=5 ORDER BY created_at`, identity),
    one(db, 'SELECT * FROM qualia_dreams WHERE identity_id=? ORDER BY dreamed_at DESC LIMIT 1', identity),
    rows(db, "SELECT id,content,weight,emotion,created_at FROM observations WHERE identity_id=? AND archived_at IS NULL AND superseded_by IS NULL AND weight='heavy' ORDER BY created_at DESC LIMIT 5", identity),
    rows(db, 'SELECT person,feeling,intensity,created_at FROM relational_state WHERE identity_id=? ORDER BY created_at DESC LIMIT 10', identity),
  ]);
  const state = (type: string): Row => payload(states.find(s => s.state_type === type) || {});
  const pool = (type: string): Row[] => entries.filter(e => e.entry_type === type && !parse(e.metadata).resolved && !parse(e.metadata).resolved_at).map(e => ({ ...parse(e.metadata), content: e.content, emotion: e.emotion, timestamp: e.created_at, id: e.id }));
  // Preserve dates; a cloud fetch does not make an old narrative newly written.
  const current = { ...parse(narrative.metadata), narrative: narrative.narrative || '', timestamp: narrative.created_at || null };
  const dates = [narrative.created_at, session.created_at, ...entries.map(e => e.created_at), ...states.map(s => s.created_at)].filter(Boolean);
  return {
    identity, source: 'cloud-qualia', fetched_at: new Date().toISOString(),
    memory: { primary_focus: '', unfinished_business: [], heavy_observations: observations,
      who_matters: relations.map(r => ({ name: r.person, relationship: r.feeling, timestamp: r.created_at })) },
    qualia: { current_self: current, last_session: { ...payload(session), summary: session.content || '' },
      unfinished: state('unfinished'), inner_weather: state('inner_weather'), emotional_now: state('emotional_now'),
      joys: pool('small_joy'), wonderings: pool('wondering'), quiet_wants: pool('quiet_want'), creative_seeds: pool('creative_seed'),
      significant_moments: pool('significant_moment'), feelings: pool('feeling'),
      dreams: dream.id ? [{ ...parse(dream.metadata), content: dream.content, reflection: dream.reflection, timestamp: dream.dreamed_at }] : [] },
    _freshness: { stale: false, source: 'cloud-qualia', newest_record_at: dates.sort().at(-1) || null,
      note: 'Live cloud records; individual timestamps describe their age. No local memory fallback.' },
  };
}

export async function observations(db: D1Database, identity: string, limit: number, offset: number, status: string): Promise<Row> {
  const where = ['1=1']; const args: any[] = [];
  if (identity) { where.push('o.identity_id=?'); args.push(identity); }
  if (status === 'archived') where.push('o.archived_at IS NOT NULL');
  else if (status === 'fresh') where.push('o.archived_at IS NULL AND o.last_surfaced_at IS NULL');
  else if (status === 'surfaced') where.push('o.archived_at IS NULL AND o.last_surfaced_at IS NOT NULL');
  const sql = where.join(' AND ');
  const total = await one(db, `SELECT COUNT(*) AS n FROM observations o WHERE ${sql}`, ...args);
  const items = await rows(db, `SELECT o.id,o.identity_id AS identity,e.name AS entity,e.entity_type,
    o.content,o.weight,o.emotion,o.charge,o.certainty,o.salience,o.tags,o.created_at AS timestamp,
    o.last_surfaced_at,o.surface_count,p.sit_count,p.resolution_note,p.resolved_at,o.source,o.archived_at,
    COALESCE(o.superseded_by,p.linked_observation_id) AS linked_observation_id, linked.content AS linked_preview,
    CASE WHEN o.archived_at IS NOT NULL THEN 'archived' WHEN o.last_surfaced_at IS NOT NULL THEN 'surfaced' ELSE 'fresh' END AS status
    FROM observations o LEFT JOIN entities e ON e.id=o.entity_id
    LEFT JOIN observation_process p ON p.observation_id=o.id
    LEFT JOIN observations linked ON linked.id=COALESCE(o.superseded_by,p.linked_observation_id)
    WHERE ${sql} ORDER BY datetime(o.created_at) DESC,o.id DESC LIMIT ? OFFSET ?`, ...args, limit, offset);
  return { items, total: total.n, limit, offset, source: 'cloud-qualia' };
}

async function entities(db: D1Database, identity: string, days: number, limit: number, all = false): Promise<Row[]> {
  const where = identity ? 'WHERE e.identity_id=?' : ''; const args = identity ? [identity] : [];
  return rows(db, `SELECT e.id,e.identity_id AS identity,e.name,e.entity_type,e.salience,
    COUNT(o.id) AS observation_count,COUNT(o.id) AS count,
    SUM(CASE WHEN datetime(o.created_at)>=datetime('now',?) THEN 1 ELSE 0 END) AS recent_count,
    MAX(o.created_at) AS last_seen FROM entities e LEFT JOIN observations o ON o.entity_id=e.id AND o.archived_at IS NULL
    ${where} GROUP BY e.id ORDER BY ${all ? 'observation_count' : 'recent_count'} DESC,e.id LIMIT ?`, `-${days} days`, ...args, limit);
}

export async function handleAnamReader(request: Request, env: Env): Promise<Response> {
  if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 });
  const url = new URL(request.url); const q = url.searchParams;
  const identity = (q.get('identity') || '').trim().toLowerCase();
  const valid = await rows(env.DB, "SELECT id,display_name FROM identities WHERE status='active'");
  if (identity && !valid.some(r => r.id === identity)) return Response.json({error:'Unknown identity'}, {status:404});
  const number = (name: string, def: number, max: number, min=1): number => Math.min(max, Math.max(min, Number.parseInt(q.get(name) || '',10) || def));
  const section = url.pathname.slice('/api/anam/'.length);
  const limit = number('limit',20,50), offset = number('offset',0,1000000,0), days = number('days',14,90);
  let data: any;
  if (section === 'snapshot') {
    if (!identity) return Response.json({error:'Identity required'}, {status:400});
    data = await snapshot(env.DB,identity);
  } else if (section === 'memory-lab/observations') {
    const status = q.get('status') || '';
    if (!['','fresh','surfaced','archived'].includes(status)) return Response.json({error:'Invalid status'}, {status:400});
    data = await observations(env.DB,identity,limit,offset,status);
  } else if (section === 'memory-lab/entities') {
    data = { entities: await entities(env.DB,identity,days,10000,true) };
  } else {
    const ids = identity ? valid.filter(r=>r.id===identity) : valid;
    if (!['mind-insights','mind-garden/summary','mind-garden/weather','mind-garden/threads','mind-garden/observations','mind-garden/entities'].includes(section)) return new Response('Not found',{status:404});
    const results: Row = {}; const insights: Row[] = [];
    for (const id of ids) {
      const name = id.display_name || id.id;
      if (section.endsWith('/observations')) { const obs=await observations(env.DB,id.id,limit,0,''); results[name]={identity:name,observations:obs.items,count:obs.items.length}; continue; }
      if (section.endsWith('/entities')) { const es=await entities(env.DB,id.id,days,number('limit',6,20)); results[name]={identity:name,entities:es,count:es.length,period_days:days}; continue; }
      const s=await snapshot(env.DB,id.id); const qualia=s.qualia;
      const loops=(qualia.unfinished.open_loops || []).filter((l:Row)=>!l.resolved);
      if (section==='mind-insights') {
        for (const [key,type,label] of [['dreams','dream','Recent Dream'],['joys','joy','Small Joy'],['wonderings','wondering','Wondering']]) {
          const row=qualia[key].at(-1); if(row) insights.push({type,label,identity:id.id,content:row.content,timestamp:row.timestamp});
        }
        continue;
      }
      const subconscious=await rows(env.DB,"SELECT id,content,emotion,created_at AS timestamp FROM qualia_entries WHERE identity_id=? AND entry_type='subconscious' AND COALESCE(json_extract(metadata,'$.resolved'),0)=0 AND json_extract(metadata,'$.resolved_at') IS NULL ORDER BY created_at DESC LIMIT 12",id.id);
      if (section.endsWith('/threads')) {
        const orphans=await rows(env.DB,"SELECT id,content,weight,created_at AS timestamp FROM observations WHERE identity_id=? AND entity_id IS NULL AND archived_at IS NULL AND superseded_by IS NULL ORDER BY created_at DESC LIMIT 8",id.id);
        results[name]={identity:name,open_loops:loops,subconscious,orphans,count:loops.length+subconscious.length+orphans.length}; continue;
      }
      const proposals=await one(env.DB,"SELECT COUNT(*) AS n FROM proposal_queue WHERE identity_id=? AND status='pending'",id.id);
      const counts=await one(env.DB, `SELECT COUNT(*) AS total_memories,
        SUM(CASE WHEN datetime(created_at)>=datetime('now','-7 days') THEN 1 ELSE 0 END) AS recent_week,
        SUM(CASE WHEN datetime(created_at)>=datetime('now','-1 day') THEN 1 ELSE 0 END) AS recent_day,
        SUM(CASE WHEN weight='heavy' AND datetime(created_at)>=datetime('now','-7 days') THEN 1 ELSE 0 END) AS heavy_week,
        SUM(CASE WHEN weight='heavy' AND datetime(created_at)>=datetime('now','-1 day') THEN 1 ELSE 0 END) AS heavy_day,
        SUM(CASE WHEN last_surfaced_at IS NULL THEN 1 ELSE 0 END) AS waiting_to_surface,
        SUM(CASE WHEN datetime(last_surfaced_at)>=datetime('now','-30 days') THEN 1 ELSE 0 END) AS surfaced_month
        FROM observations WHERE identity_id=? AND archived_at IS NULL`,id.id);
      const weather=qualia.inner_weather; const atmosphere=weather.weather?.atmosphere || weather.atmosphere || 'unknown';
      const feelings=qualia.feelings.map((f:Row)=>({...f,what:f.content,feel_type:f.emotion})).reverse();
      if (section.endsWith('/summary')) results[name]={identity:name,timestamp:weather.timestamp,headline:`${name}'s live Qualia records.`,atmosphere,element:weather.element || '',mood_palette:weather.mood_palette || [],recent_feelings:feelings,
        stats:{...counts,open_loops:loops.length,pending_proposals:proposals.n},top_entities:await entities(env.DB,id.id,14,3)};
      else results[name]={identity:name,timestamp:weather.timestamp,mood_palette:weather.mood_palette || [],recent_feelings:feelings,
        conditions:{atmosphere,element:weather.element || '',energy:'unknown',recent_observations_24h:counts.recent_day,recent_observations_7d:counts.recent_week,heavy_observations_24h:counts.heavy_day,heavy_subconscious:null,open_loops:loops.length,dominant_emotion:null,emotion_counts:{},dreams_generated:null}};
    }
    const key=({'summary':'summaries','weather':'weather','threads':'threads','observations':'observations','entities':'entities'} as Row)[section.split('/').at(-1)!];
    data=section==='mind-insights'?{insights:insights.sort((a,b)=>String(b.timestamp).localeCompare(String(a.timestamp))).slice(0,4)}:{[key]:results};
  }
  return Response.json({...data,source:'cloud-qualia'},{headers:{'Cache-Control':'no-store'}});
}
