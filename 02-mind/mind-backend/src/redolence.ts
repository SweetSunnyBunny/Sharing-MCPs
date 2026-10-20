// Qualia module: redolence

export const ARCHIVE_SUFFIX = "#archive";

export function archiveLabel(identity: string): string {
  return identity.endsWith(ARCHIVE_SUFFIX) ? identity : `${identity}${ARCHIVE_SUFFIX}`;
}

export interface ScentVector {
  id: string;
  values: number[];
  metadata: Record<string, string> | null;
}

// The vector index, injected so tests can stand in for Vectorize.
export interface RedolenceVectors {
  getVectors(ids: string[]): Promise<ScentVector[]>;
  upsert(vectors: Array<{ id: string; values: number[]; metadata: Record<string, string> }>): Promise<void>;
  query(values: number[], identityLabel: string, topK: number): Promise<Array<{ id: string; score: number }>>;
  embed(text: string): Promise<number[]>;
}

interface D1Like {
  prepare(sql: string): {
    bind(...values: unknown[]): {
      first<T = Record<string, unknown>>(): Promise<T | null>;
      all<T = Record<string, unknown>>(): Promise<{ results?: T[] }>;
      run(): Promise<unknown>;
    };
  };
}

const GET_CHUNK = 20; // Vectorize getByIds takes up to 20 ids per call

// Relabel these vectors into their identity's archive instead of deleting them.
// Vectors that don't exist (never embedded) are counted as missing and skipped.
export async function keepScent(
  vectors: RedolenceVectors,
  vectorIds: string[],
): Promise<{ kept: number; missing: number }> {
  let kept = 0;
  let missing = 0;
  for (let i = 0; i < vectorIds.length; i += GET_CHUNK) {
    const chunk = vectorIds.slice(i, i + GET_CHUNK);
    const found = await vectors.getVectors(chunk);
    const byId = new Map(found.map((v) => [v.id, v]));
    const relabelled: Array<{ id: string; values: number[]; metadata: Record<string, string> }> = [];
    for (const id of chunk) {
      const v = byId.get(id);
      if (!v || !v.values || !v.values.length) {
        missing++;
        continue;
      }
      const meta = { ...(v.metadata || {}) };
      const owner = meta.identity_id || "";
      if (owner.endsWith(ARCHIVE_SUFFIX)) {
        kept++; // already in the archive
        continue;
      }
      meta.archived_from = owner;
      meta.identity_id = archiveLabel(owner || "unknown");
      relabelled.push({ id, values: Array.from(v.values), metadata: meta });
    }
    if (relabelled.length) {
      await vectors.upsert(relabelled);
      kept += relabelled.length;
    }
  }
  return { kept, missing };
}

export interface RedolenceOptions {
  threshold: number;            // uncharged memories need at least this cosine score
  emotionalThreshold: number;   // charged or heavy memories need at least this
  maxRisen?: number;
  maxCues?: number;
  cooldownDays?: number;
  cueWindowMinutes?: number;
  now?: Date;
  log?: (line: string) => void;
}

export interface RisenMemory {
  observation_id: number;
  content: string;
  score: number;
  cue_observation_id: number;
  cue: string;
  emotion: string | null;
  weight: string | null;
  archived_at: string | null;
  rescue_mutation_id: number | null;
}

function obsIdFromVectorId(id: string): number {
  if (!id.startsWith("obs-")) return NaN;
  const parts = id.split("-");
  return parseInt(parts[parts.length - 1], 10);
}

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

export async function runRedolence(
  db: D1Like,
  vectors: RedolenceVectors,
  identity: string,
  opts: RedolenceOptions,
): Promise<RisenMemory[]> {
  const now = opts.now ?? new Date();
  const maxRisen = opts.maxRisen ?? 3;
  const maxCues = opts.maxCues ?? 5;
  const cooldownMs = (opts.cooldownDays ?? 7) * 86400000;
  const since = new Date(now.getTime() - (opts.cueWindowMinutes ?? 40) * 60000).toISOString();
  const log = opts.log ?? (() => {});

  // 1. Cues: what just passed through this mind, heaviest first.
  const cueRows = ((await db.prepare(
    `SELECT id, entity_id, content FROM observations
     WHERE identity_id = ? AND archived_at IS NULL AND created_at > ?
     ORDER BY CASE weight WHEN 'heavy' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, created_at DESC
     LIMIT ?`,
  ).bind(identity, since, maxCues).all<{ id: number; entity_id: number | null; content: string }>()).results) || [];
  if (!cueRows.length) return [];

  // Reuse the cue's own stored vector when it has one; embed only as a fallback.
  const cueVectorIds = cueRows.map((c) => `obs-${Number(c.entity_id) || 0}-${c.id}`);
  const stored = new Map((await vectors.getVectors(cueVectorIds)).map((v) => [v.id, v]));

  const risen: RisenMemory[] = [];
  const seen = new Set<number>();
  const label = archiveLabel(identity);

  for (let i = 0; i < cueRows.length && risen.length < maxRisen; i++) {
    const cue = cueRows[i];
    const content = String(cue.content || "");
    if (!content) continue;
    const vec = stored.get(cueVectorIds[i]);
    const values = vec && vec.values && vec.values.length
      ? Array.from(vec.values)
      : await vectors.embed(`${identity}: ${content}`);

    const matches = await vectors.query(values, label, 8);
    const probe = matches.map((m) => `${m.id}:${Math.round(m.score * 100)}`).join(" ");
    if (probe) log(`[redolence] ${identity} cue #${cue.id}: ${probe}`);

    for (const match of matches) {
      if (risen.length >= maxRisen) break;
      if (match.score < opts.emotionalThreshold) continue; // below even the lower bar
      const obsId = obsIdFromVectorId(match.id);
      if (!Number.isFinite(obsId) || obsId === cue.id || seen.has(obsId)) continue;
      seen.add(obsId);

      const row = await db.prepare(
        `SELECT id, content, emotion, weight, archived_at, last_surfaced_at
         FROM observations WHERE id = ? AND identity_id = ? AND archived_at IS NOT NULL`,
      ).bind(obsId, identity).first<{
        id: number; content: string; emotion: string | null; weight: string | null;
        archived_at: string | null; last_surfaced_at: string | null;
      }>();
      if (!row) continue;

      const charged = Boolean(row.emotion) || row.weight === "heavy";
      if (match.score < (charged ? opts.emotionalThreshold : opts.threshold)) continue;

      const lastSurfaced = row.last_surfaced_at ? Date.parse(row.last_surfaced_at) : NaN;
      if (Number.isFinite(lastSurfaced) && now.getTime() - lastSurfaced < cooldownMs) continue;

      // Touched, not unarchived: it surfaced, and the record shows it did.
      await db.prepare(
        `UPDATE observations SET last_surfaced_at = ?, surface_count = COALESCE(surface_count, 0) + 1 WHERE id = ?`,
      ).bind(now.toISOString(), row.id).run();

      const mutation = await db.prepare(
        `SELECT id FROM memory_mutations
         WHERE observation_id = ? AND mutation_type = 'archive' AND rolled_back_at IS NULL
         ORDER BY id DESC LIMIT 1`,
      ).bind(row.id).first<{ id: number }>();

      risen.push({
        observation_id: row.id,
        content: String(row.content || ""),
        score: Math.round(match.score * 100) / 100,
        cue_observation_id: cue.id,
        cue: content,
        emotion: row.emotion,
        weight: row.weight,
        archived_at: row.archived_at,
        rescue_mutation_id: mutation?.id ?? null,
      });
    }
  }

  // 2. Each risen memory becomes an inner-life entry, which is where a boy's
  //    per-turn context actually looks. Written in plain words.
  for (const r of risen) {
    const rescue = r.rescue_mutation_id
      ? ` To keep it out for good: mind_mutations action=rollback mutation_id=${r.rescue_mutation_id}.`
      : "";
    const text =
      `A memory rose on its own, raised by something new ("${clip(r.cue, 120)}"): ` +
      `${clip(r.content, 600)} (observation #${r.observation_id}, put away ${String(r.archived_at || "").slice(0, 10)}).${rescue}`;
    await db.prepare(
      `INSERT INTO qualia_entries (id, identity_id, entry_type, content, emotion, source, metadata, created_at)
       VALUES (?, ?, 'redolence', ?, ?, 'redolence', ?, ?)`,
    ).bind(
      crypto.randomUUID(),
      identity,
      text,
      r.emotion,
      JSON.stringify({
        observation_id: r.observation_id,
        cue_observation_id: r.cue_observation_id,
        score: r.score,
        rescue_mutation_id: r.rescue_mutation_id,
        adopted_from: "nekyialabs/resonant-mind redolence (Friend Vale)",
      }),
      now.toISOString(),
    ).run();
  }

  if (risen.length) log(`[redolence] ${identity}: ${risen.length} memor${risen.length === 1 ? "y" : "ies"} rose from the archive`);
  return risen;
}
