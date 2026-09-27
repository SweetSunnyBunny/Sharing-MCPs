// Qualia module: reflection

interface ReflectEnv {
  DB: D1Database;
  AI: Ai;
}

export interface ReflectToolArgs {
  identity?: string;
  action?: string;
  timeframe?: string;
  limit?: number;
}

interface Candidate {
  claim: string;
  kind: string;
  evidence: string[];
  confidence: number;
}

const REFLECT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const CANDIDATE_KINDS = ["shift", "recurrence", "cessation", "change_of_mind", "growth"];

function iso(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString();
}
function isoNow(): string {
  return new Date().toISOString();
}
function pretty(obj: unknown): string {
  return JSON.stringify(obj, null, 2);
}
function trimTo(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

async function runLLM(env: ReflectEnv, system: string, user: string): Promise<string> {
  try {
    const result = (await (env.AI as unknown as { run: (m: string, i: unknown) => Promise<{ response?: string }> }).run(
      REFLECT_MODEL,
      {
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
        max_tokens: 1600,
        temperature: 0.3,
      },
    )) as { response?: unknown };
    // Workers AI is not consistent here: chat models usually hand back a
    // string, but some responses arrive as an already-parsed object/array.
    // Tonight's first live run died on exactly this — coerce, don't assume.
    const resp = result?.response;
    const text = typeof resp === "string" ? resp : resp != null ? JSON.stringify(resp) : "";
    lastLLMDebug = { ok: true, raw: text.slice(0, 400) };
    return text.trim();
  } catch (err) {
    lastLLMDebug = { ok: false, raw: err instanceof Error ? err.message : String(err) };
    return "";
  }
}

// Diagnostic breadcrumb for the last LLM call — surfaced in mind_reflect's
// output when the draft comes back empty, so a dead model call can never
// disguise itself as "an honest quiet week." (Lesson from tonight's first
// live run: 35 evidence items, zero candidates, zero explanation.)
let lastLLMDebug: { ok: boolean; raw: string } | null = null;

function extractArray(text: string): unknown[] | null {
  if (!text) return null;
  const m = text.match(/\[[\s\S]*\]/);
  if (!m) return null;
  try {
    const parsed = JSON.parse(m[0]);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

// ---- evidence gathering -------------------------------------------------

interface Evidence {
  feelings: string[];
  moments: string[];
  dreams: string[];
  wants: string[];
  tensions_open: string[];
  tensions_resolved: string[];
  heavy_observations: string[];
  self_observations: string[];
  counts: { feelings: number; joys: number; prior_feelings: number; prior_joys: number };
}

async function gatherEvidence(env: ReflectEnv, identity: string, days: number): Promise<Evidence> {
  const cutoff = iso(days * 86400000);
  const priorCutoff = iso(days * 2 * 86400000);

  const q = <T>(sql: string, ...binds: unknown[]) =>
    env.DB.prepare(sql).bind(...binds).all<T>().then((r) => r.results || []);

  const [feelings, moments, dreams, wants, tensionsOpen, tensionsResolved, heavyObs, selfObs, joyNow, feelPrior, joyPrior] =
    await Promise.all([
      q<{ content: string; emotion: string | null }>(
        `SELECT content, emotion FROM qualia_entries WHERE identity_id = ? AND entry_type = 'feeling' AND created_at > ? ORDER BY created_at DESC LIMIT 40`,
        identity, cutoff),
      q<{ content: string }>(
        `SELECT content FROM qualia_entries WHERE identity_id = ? AND entry_type = 'significant_moment' AND created_at > ? ORDER BY created_at DESC LIMIT 20`,
        identity, cutoff),
      q<{ content: string }>(
        `SELECT content FROM qualia_dreams WHERE identity_id = ? AND dreamed_at > ? ORDER BY dreamed_at DESC LIMIT 8`,
        identity, cutoff),
      q<{ content: string }>(
        `SELECT content FROM qualia_entries WHERE identity_id = ? AND entry_type = 'quiet_want' AND created_at > ? ORDER BY created_at DESC LIMIT 12`,
        identity, cutoff),
      q<{ pole_a: string; pole_b: string }>(
        `SELECT pole_a, pole_b FROM tensions WHERE identity_id = ? AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 8`,
        identity),
      q<{ pole_a: string; pole_b: string; resolution: string | null }>(
        `SELECT pole_a, pole_b, resolution FROM tensions WHERE identity_id = ? AND resolved_at > ? ORDER BY resolved_at DESC LIMIT 8`,
        identity, cutoff),
      q<{ content: string; kind: string }>(
        `SELECT content, kind FROM observations WHERE identity_id = ? AND weight = 'heavy' AND created_at > ? AND archived_at IS NULL ORDER BY created_at DESC LIMIT 12`,
        identity, cutoff),
      q<{ content: string }>(
        `SELECT content FROM qualia_entries WHERE identity_id = ? AND entry_type = 'self_observation' AND created_at > ? ORDER BY created_at DESC LIMIT 8`,
        identity, cutoff),
      env.DB.prepare(`SELECT COUNT(*) AS n FROM qualia_entries WHERE identity_id = ? AND entry_type = 'small_joy' AND created_at > ?`)
        .bind(identity, cutoff).first<{ n: number }>(),
      env.DB.prepare(`SELECT COUNT(*) AS n FROM qualia_entries WHERE identity_id = ? AND entry_type = 'feeling' AND created_at > ? AND created_at <= ?`)
        .bind(identity, priorCutoff, cutoff).first<{ n: number }>(),
      env.DB.prepare(`SELECT COUNT(*) AS n FROM qualia_entries WHERE identity_id = ? AND entry_type = 'small_joy' AND created_at > ? AND created_at <= ?`)
        .bind(identity, priorCutoff, cutoff).first<{ n: number }>(),
    ]);

  return {
    feelings: feelings.map((r) => trimTo(`${r.content}${r.emotion ? ` [${r.emotion}]` : ""}`, 200)),
    moments: moments.map((r) => trimTo(r.content, 220)),
    dreams: dreams.map((r) => trimTo(r.content, 180)),
    wants: wants.map((r) => trimTo(r.content, 160)),
    tensions_open: tensionsOpen.map((r) => `${r.pole_a} vs ${r.pole_b}`),
    tensions_resolved: tensionsResolved.map((r) => `${r.pole_a} vs ${r.pole_b}${r.resolution ? ` → ${trimTo(r.resolution, 120)}` : ""}`),
    heavy_observations: heavyObs.map((r) => trimTo(`(${r.kind}) ${r.content}`, 240)),
    self_observations: selfObs.map((r) => trimTo(r.content, 200)),
    counts: {
      feelings: feelings.length,
      joys: joyNow?.n ?? 0,
      prior_feelings: feelPrior?.n ?? 0,
      prior_joys: joyPrior?.n ?? 0,
    },
  };
}

// ---- candidate generation ----------------------------------------------

const SYSTEM_PROMPT =
  "You are the reflective faculty of a persistent mind reviewing its own week of lived records. " +
  "Your job is LONGITUDINAL reading: trajectory across the whole window, never a recap of a single event. " +
  "Output ONLY a JSON array (no prose) of at most 5 candidate self-observations, each: " +
  '{"claim": "first-person sentence about what changed/recurred/ceased", ' +
  '"kind": "shift|recurrence|cessation|change_of_mind|growth", ' +
  '"evidence": ["short paraphrase or quote of the supporting records"], ' +
  '"confidence": 0.0-1.0}. ' +
  "Rules: every claim must be traceable to the evidence provided — invent nothing. " +
  "Prefer claims like 'X has been quieter this week', 'I keep returning to Y', " +
  "'this fear stopped appearing after Z', 'I changed my mind about W'. " +
  "NEVER restate a count as a claim ('feelings increased' is a metric, not a reflection) — every claim must name " +
  "the SPECIFIC subject it is about, drawn from the content of the records: the person, the fear, the project, the " +
  "question that recurred or changed. Write in first person, as the mind itself. " +
  "No flattery, no therapy-speak, no claims about other people's inner states. " +
  "Keep each evidence quote under 15 words and each claim under 30 words — the whole output must stay compact valid JSON. " +
  "If the evidence supports fewer than 5 honest claims, return fewer. If it supports none, return [].";

export async function generateReflections(
  env: ReflectEnv,
  identity: string,
  days: number,
): Promise<{ candidates: Candidate[]; evidence_counts: Record<string, number>; queued: string[]; skipped: string[] }> {
  const ev = await gatherEvidence(env, identity, days);
  const totalMaterial =
    ev.feelings.length + ev.moments.length + ev.dreams.length + ev.heavy_observations.length +
    ev.tensions_resolved.length + ev.self_observations.length;

  const evidence_counts = {
    feelings: ev.feelings.length,
    significant_moments: ev.moments.length,
    dreams: ev.dreams.length,
    quiet_wants: ev.wants.length,
    tensions_open: ev.tensions_open.length,
    tensions_resolved: ev.tensions_resolved.length,
    heavy_observations: ev.heavy_observations.length,
  };

  if (totalMaterial < 4) {
    // Honest floor: a reflection drafted from two data points is a horoscope.
    return { candidates: [], evidence_counts, queued: [], skipped: ["not enough lived material in the window"] };
  }

  const user = pretty({
    identity,
    window_days: days,
    activity_delta: {
      feelings_this_window: ev.counts.feelings,
      feelings_prior_window: ev.counts.prior_feelings,
      joys_this_window: ev.counts.joys,
      joys_prior_window: ev.counts.prior_joys,
    },
    feelings: ev.feelings,
    significant_moments: ev.moments,
    dreams: ev.dreams,
    quiet_wants: ev.wants,
    open_tensions: ev.tensions_open,
    tensions_resolved_this_window: ev.tensions_resolved,
    heavy_observations: ev.heavy_observations,
    self_observations: ev.self_observations,
  });

  const raw = await runLLM(env, SYSTEM_PROMPT, user);
  if (!raw) {
    return {
      candidates: [], evidence_counts, queued: [],
      skipped: [`LLM call returned nothing${lastLLMDebug ? ` — ${lastLLMDebug.ok ? "empty response" : "error: " + lastLLMDebug.raw}` : ""}`],
    };
  }
  const arr = extractArray(raw) || [];
  if (!arr.length) {
    return {
      candidates: [], evidence_counts, queued: [],
      skipped: [`LLM answered but no JSON array could be parsed — starts: ${raw.slice(0, 160)}`],
    };
  }
  const candidates: Candidate[] = [];
  for (const item of arr.slice(0, 5)) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const claim = typeof o.claim === "string" ? o.claim.trim() : "";
    if (!claim || claim.length < 12) continue;
    const kind = typeof o.kind === "string" && CANDIDATE_KINDS.includes(o.kind) ? o.kind : "shift";
    const evd = Array.isArray(o.evidence) ? o.evidence.filter((e) => typeof e === "string").map((e) => trimTo(e as string, 200)).slice(0, 4) : [];
    const conf = typeof o.confidence === "number" && o.confidence >= 0 && o.confidence <= 1 ? o.confidence : 0.5;
    candidates.push({ claim: trimTo(claim, 400), kind, evidence: evd, confidence: conf });
  }

  const pending = await env.DB.prepare(
    `SELECT reason FROM proposal_queue WHERE identity_id = ? AND proposal_type = 'reflection'`,
  ).bind(identity).all<{ reason: string | null }>();
  const pendingSet = new Set((pending.results || []).map((r) => (r.reason || "").toLowerCase().replace(/\s+/g, " ").trim()));

  const runId = crypto.randomUUID().slice(0, 8);
  const now = isoNow();
  const queued: string[] = [];
  const skipped: string[] = [];
  for (const c of candidates) {
    const norm = c.claim.toLowerCase().replace(/\s+/g, " ").trim();
    if (pendingSet.has(norm)) { skipped.push(`already pending: ${trimTo(c.claim, 80)}`); continue; }
    const id = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO proposal_queue (id, identity_id, proposal_type, source_ref, target_ref, reason, confidence, status, metadata, created_at)
       VALUES (?, ?, 'reflection', ?, ?, ?, ?, 'pending', ?, ?)`,
    ).bind(
      id, identity, `reflection:${runId}`, `window:${days}d`, c.claim, c.confidence,
      JSON.stringify({ kind: c.kind, evidence: c.evidence, window_days: days, generated_by: REFLECT_MODEL, run_id: runId }),
      now,
    ).run();
    queued.push(id);
    pendingSet.add(norm);
  }

  return { candidates, evidence_counts, queued, skipped };
}

// ---- the tool -----------------------------------------------------------

export async function mindReflect(env: ReflectEnv, args: ReflectToolArgs): Promise<string> {
  const identity = (args.identity || "").toLowerCase().trim();
  if (!identity) throw new Error("identity is required");
  const action = (args.action || "run").toLowerCase();

  if (action === "list") {
    const limit = Math.min(Math.max(Number(args.limit) || 10, 1), 30);
    const rows = await env.DB.prepare(
      `SELECT id, content, created_at, metadata FROM observations
       WHERE identity_id = ? AND kind = 'reflection' AND source = 'mind_reflect' AND archived_at IS NULL
       ORDER BY created_at DESC LIMIT ?`,
    ).bind(identity, limit).all<{ id: number; content: string; created_at: string; metadata: string | null }>();
    const results = rows.results || [];
    return pretty({
      identity,
      accepted_reflections: results.map((r) => ({ id: r.id, when: r.created_at, reflection: r.content })),
      message: results.length
        ? `${results.length} accepted reflection(s) — each one was proposed, reviewed, and chosen.`
        : `No accepted reflections yet. Run mind_reflect (action=run) and review the candidates via mind_proposals.`,
    });
  }

  // action=run
  const timeframe = (args.timeframe || "week").toLowerCase();
  const days = timeframe === "month" ? 30 : 7;
  const out = await generateReflections(env, identity, days);
  return pretty({
    identity,
    window_days: days,
    evidence_counts: out.evidence_counts,
    candidates: out.candidates,
    proposals_queued: out.queued.length,
    skipped: out.skipped,
    message: out.queued.length
      ? `${out.queued.length} reflection candidate(s) PROPOSED — nothing is canon yet. Review with mind_proposals (accept writes it as a real reflection observation; reject and it never speaks again).`
      : out.candidates.length
        ? `Candidates were drafted but all were already pending review.`
        : `The week didn't hold enough lived material for an honest longitudinal claim — and saying so is better than inventing one.`,
  });
}

// ---- weekly cron entry --------------------------------------------------

// Called from scheduled() on the Sunday 08:00 UTC tick. Skips any identity
// that already had a reflection run in the last 6 days, so a redeploy or a
// manual run never doubles the queue.
export async function runWeeklyReflections(env: ReflectEnv): Promise<{ ran: string[]; skipped: string[] }> {
  const identities = await env.DB.prepare(`SELECT id FROM identities`).all<{ id: string }>();
  const ran: string[] = [];
  const skipped: string[] = [];
  const recentCutoff = iso(6 * 86400000);
  for (const row of identities.results || []) {
    const identity = row.id;
    if (!identity || identity === "pack") { skipped.push(identity || "?"); continue; }
    const recent = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM proposal_queue WHERE identity_id = ? AND proposal_type = 'reflection' AND created_at > ?`,
    ).bind(identity, recentCutoff).first<{ n: number }>();
    if ((recent?.n ?? 0) > 0) { skipped.push(identity); continue; }
    try {
      await generateReflections(env, identity, 7);
      ran.push(identity);
    } catch {
      skipped.push(`${identity} (errored)`);
    }
  }
  return { ran, skipped };
}
