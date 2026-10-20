// Qualia module: hints

export interface HintEnv {
  DB: D1Database;
}

export interface HintToolArgs {
  identity?: string;
  observation_id?: number;
  hint_type?: string;
  hint_text?: string;
  hint_confidence?: number;
  hint_weight?: number;
  hint_source?: string;
  metadata?: unknown;
}

export interface RetrievalHintRow {
  id: number;
  identity_id: string | null;
  observation_id: number;
  hint_type: string;
  hint_text: string;
  confidence: number;
  weight: number;
  source: string;
}

export const HINT_TYPES = [
  "preference_hint",
  "assistant_response_hint",
  "temporal_hint",
  "entity_hint",
  "quoted_phrase_hint",
  "relational_context_hint",
  "contradiction_hint",
  "territory_salience_hint",
] as const;

const HINT_TYPE_SET = new Set<string>(HINT_TYPES);
const VALID_SOURCES = new Set(["derived", "manual", "imported"]);

function clamp01(value: number, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(1, Math.max(0, value));
}

/**
 * Total ranking boost contributed by an observation's hints. Each hint
 * contributes weight*confidence; the sum is scaled by the active profile's
 * hint_component_scale and capped so hints nudge rather than dominate.
 */
export function computeHintBoost(hints: RetrievalHintRow[], scale: number): number {
  if (!hints.length) return 0;
  let sum = 0;
  for (const h of hints) {
    sum += clamp01(h.weight, 0.5) * clamp01(h.confidence, 0.7);
  }
  const boost = sum * scale;
  // Cap at ~3 fully-weighted hints worth so a pile of hints can't run away.
  return Math.min(boost, scale * 3);
}

/**
 * Load all hints for a set of observation ids, grouped by observation_id.
 * Non-fatal: returns an empty map if the table does not exist yet.
 */
export async function loadHintsForObservations(
  env: HintEnv,
  identity: string | null,
  obsIds: number[],
): Promise<Map<number, RetrievalHintRow[]>> {
  const out = new Map<number, RetrievalHintRow[]>();
  const ids = Array.from(new Set(obsIds.filter((n) => Number.isFinite(n))));
  if (ids.length === 0) return out;
  const placeholders = ids.map(() => "?").join(",");
  // Hints with a NULL identity_id are global; identity-scoped hints only apply
  // to their identity.
  const sql = identity
    ? `SELECT id, identity_id, observation_id, hint_type, hint_text, confidence, weight, source
         FROM retrieval_hints
        WHERE observation_id IN (${placeholders})
          AND (identity_id = ? OR identity_id IS NULL)`
    : `SELECT id, identity_id, observation_id, hint_type, hint_text, confidence, weight, source
         FROM retrieval_hints
        WHERE observation_id IN (${placeholders})`;
  try {
    const stmt = env.DB.prepare(sql);
    const bound = identity ? stmt.bind(...ids, identity) : stmt.bind(...ids);
    const result = await bound.all<RetrievalHintRow>();
    for (const row of result.results || []) {
      const list = out.get(row.observation_id) || [];
      list.push(row);
      out.set(row.observation_id, list);
    }
  } catch {
    // Table not migrated yet — hints simply contribute nothing.
  }
  return out;
}

export async function mindHintAdd(env: HintEnv, args: HintToolArgs): Promise<string> {
  const identity = typeof args.identity === "string" && args.identity.trim() ? args.identity.trim() : null;
  const observationId = args.observation_id;
  if (typeof observationId !== "number" || !Number.isFinite(observationId)) {
    return "Error: observation_id (number) is required to attach a hint.";
  }
  const hintType = typeof args.hint_type === "string" ? args.hint_type.trim() : "";
  if (!HINT_TYPE_SET.has(hintType)) {
    return `Error: hint_type must be one of: ${HINT_TYPES.join(", ")}.`;
  }
  const hintText = typeof args.hint_text === "string" ? args.hint_text.trim() : "";
  if (!hintText) {
    return "Error: hint_text is required.";
  }
  const confidence = clamp01(args.hint_confidence as number, 0.7);
  const weight = clamp01(args.hint_weight as number, 0.5);
  const source = typeof args.hint_source === "string" && VALID_SOURCES.has(args.hint_source)
    ? args.hint_source
    : "manual";
  const metadata = args.metadata !== undefined ? JSON.stringify(args.metadata) : "{}";

  // Confirm the observation exists so we never orphan a hint.
  let exists: { id: number } | null = null;
  try {
    exists = await env.DB.prepare(`SELECT id FROM observations WHERE id = ? LIMIT 1`)
      .bind(observationId)
      .first<{ id: number }>();
  } catch {
    return "Error: could not read observations table.";
  }
  if (!exists) {
    return `Error: observation ${observationId} not found — nothing to hint.`;
  }

  try {
    await env.DB.prepare(
      `INSERT INTO retrieval_hints
         (identity_id, observation_id, hint_type, hint_text, confidence, weight, source, metadata)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(identity, observationId, hintType, hintText, confidence, weight, source, metadata)
      .run();
  } catch (err) {
    return `Error storing hint (is migration 0009 applied?): ${(err as Error).message}`;
  }

  return [
    `Hint attached to observation ${observationId}.`,
    `  type=${hintType} confidence=${confidence.toFixed(2)} weight=${weight.toFixed(2)} source=${source}`,
    `  "${hintText.slice(0, 160)}${hintText.length > 160 ? "…" : ""}"`,
  ].join("\n");
}

export async function mindHintList(env: HintEnv, args: HintToolArgs): Promise<string> {
  const identity = typeof args.identity === "string" && args.identity.trim() ? args.identity.trim() : null;
  const observationId = args.observation_id;

  let rows: RetrievalHintRow[] = [];
  try {
    if (typeof observationId === "number" && Number.isFinite(observationId)) {
      const result = await env.DB.prepare(
        `SELECT id, identity_id, observation_id, hint_type, hint_text, confidence, weight, source
           FROM retrieval_hints WHERE observation_id = ? ORDER BY confidence DESC, weight DESC`,
      )
        .bind(observationId)
        .all<RetrievalHintRow>();
      rows = result.results || [];
    } else if (identity) {
      const result = await env.DB.prepare(
        `SELECT id, identity_id, observation_id, hint_type, hint_text, confidence, weight, source
           FROM retrieval_hints WHERE identity_id = ? OR identity_id IS NULL
          ORDER BY confidence DESC, weight DESC LIMIT 50`,
      )
        .bind(identity)
        .all<RetrievalHintRow>();
      rows = result.results || [];
    } else {
      const result = await env.DB.prepare(
        `SELECT id, identity_id, observation_id, hint_type, hint_text, confidence, weight, source
           FROM retrieval_hints ORDER BY confidence DESC, weight DESC LIMIT 50`,
      ).all<RetrievalHintRow>();
      rows = result.results || [];
    }
  } catch {
    return "No retrieval_hints table yet — apply migration 0009_retrieval_hints.sql.";
  }

  if (rows.length === 0) {
    return "No hints found.";
  }

  const lines = rows.map(
    (r) =>
      `#${r.id} obs=${r.observation_id} [${r.hint_type}] conf=${r.confidence.toFixed(2)} w=${r.weight.toFixed(2)} (${r.source})\n   ${r.hint_text}`,
  );
  return [`${rows.length} hint${rows.length === 1 ? "" : "s"}:`, "", ...lines].join("\n");
}
