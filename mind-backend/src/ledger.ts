// Qualia module: ledger

interface LedgerEnv {
  DB: D1Database;
}

export interface MutationRecord {
  identity: string | null;
  observation_id: number;
  mutation_type: "edit" | "archive" | "supersede" | "daemon_consolidate";
  actor: string;
  new_state?: Record<string, unknown>;
  evidence?: string;
}

export interface LedgerToolArgs {
  identity?: string;
  action?: string;
  observation_id?: number;
  mutation_id?: number;
  limit?: number;
}

// Vector plumbing is injected from index.ts so the ledger can restore an
// observation's embedding on rollback without duplicating that machinery.
export interface LedgerHelpers {
  reembed: (identity: string, entityId: number, obsId: number, content: string, kind: string, weight: string) => Promise<boolean>;
  deleteVector: (entityId: number, obsId: number) => Promise<boolean>;
}

function pretty(obj: unknown): string {
  return JSON.stringify(obj, null, 2);
}
function isoNow(): string {
  return new Date().toISOString();
}

// Full-row snapshot. SELECT * on purpose: if the observations table grows a
// column, the ledger keeps it without being edited — a snapshot that silently
// drops new columns would be the exact staleness bug this lobe exists to end.
export async function snapshotObservation(env: LedgerEnv, obsId: number): Promise<Record<string, unknown> | null> {
  const row = await env.DB.prepare(`SELECT * FROM observations WHERE id = ?`).bind(obsId).first<Record<string, unknown>>();
  return row ?? null;
}

export async function recordMutation(env: LedgerEnv, rec: MutationRecord): Promise<number | null> {
  try {
    const snap = await snapshotObservation(env, rec.observation_id);
    if (!snap) return null;
    const result = await env.DB.prepare(
      `INSERT INTO memory_mutations (identity_id, observation_id, mutation_type, actor, old_state, new_state, evidence, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      rec.identity ?? (typeof snap.identity_id === "string" ? snap.identity_id : null),
      rec.observation_id,
      rec.mutation_type,
      rec.actor,
      JSON.stringify(snap),
      rec.new_state ? JSON.stringify(rec.new_state) : null,
      rec.evidence ?? null,
      isoNow(),
    ).run();
    return Number(result.meta.last_row_id);
  } catch (err) {
    console.error(`[ledger] FAILED to record ${rec.mutation_type} of obs #${rec.observation_id}:`, err instanceof Error ? err.message : err);
    return null;
  }
}

// Fields rollback is allowed to restore. Deliberately excludes id/entity_id
// (identity of the row itself) and surface bookkeeping.
const RESTORABLE = [
  "content", "kind", "emotion", "weight", "charge", "certainty", "salience",
  "source", "tags", "metadata", "archived_at", "superseded_by", "supersedes",
] as const;

export async function mindMutations(env: LedgerEnv, args: LedgerToolArgs, helpers: LedgerHelpers): Promise<string> {
  const identity = (args.identity || "").toLowerCase().trim();
  const action = (args.action || "list").toLowerCase();

  if (action === "list") {
    if (!identity && typeof args.observation_id !== "number") {
      throw new Error("identity or observation_id is required for list");
    }
    const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 50);
    const rows = typeof args.observation_id === "number"
      ? await env.DB.prepare(
          `SELECT id, identity_id, observation_id, mutation_type, actor, evidence, created_at, rolled_back_at, old_state
           FROM memory_mutations WHERE observation_id = ? ORDER BY created_at DESC, id DESC LIMIT ?`,
        ).bind(args.observation_id, limit).all<Record<string, unknown>>()
      : await env.DB.prepare(
          `SELECT id, identity_id, observation_id, mutation_type, actor, evidence, created_at, rolled_back_at, old_state
           FROM memory_mutations WHERE identity_id = ? ORDER BY created_at DESC, id DESC LIMIT ?`,
        ).bind(identity, limit).all<Record<string, unknown>>();
    const results = (rows.results || []).map((r) => {
      let was = "";
      try {
        const snap = JSON.parse(String(r.old_state)) as { content?: string };
        was = (snap.content || "").slice(0, 120);
      } catch { /* snapshot unreadable — show nothing rather than lie */ }
      return {
        mutation_id: r.id,
        observation_id: r.observation_id,
        type: r.mutation_type,
        actor: r.actor,
        evidence: r.evidence,
        when: r.created_at,
        rolled_back: !!r.rolled_back_at,
        content_before: was,
      };
    });
    return pretty({
      identity: identity || undefined,
      mutations: results,
      message: results.length
        ? `${results.length} mutation(s) on record. Roll one back with action=rollback + mutation_id (newest-first per observation).`
        : "No mutations recorded yet — memory has only been added to, never changed.",
    });
  }

  if (action !== "rollback") throw new Error(`Unknown action '${action}' — use list or rollback.`);

  const mutationId = args.mutation_id;
  if (typeof mutationId !== "number") throw new Error("mutation_id is required for rollback");

  const mut = await env.DB.prepare(
    `SELECT id, identity_id, observation_id, mutation_type, actor, old_state, rolled_back_at FROM memory_mutations WHERE id = ?`,
  ).bind(mutationId).first<{ id: number; identity_id: string | null; observation_id: number; mutation_type: string; actor: string; old_state: string; rolled_back_at: string | null }>();
  if (!mut) return pretty({ success: false, message: `Mutation #${mutationId} not found.` });
  if (mut.rolled_back_at) return pretty({ success: false, message: `Mutation #${mutationId} was already rolled back at ${mut.rolled_back_at}.` });

  // ORDERING GUARD: a newer un-rolled-back mutation on the same observation
  // must be rolled back first, or we'd restore a state from underneath a
  // later change and silently destroy it.
  const newer = await env.DB.prepare(
    `SELECT id FROM memory_mutations WHERE observation_id = ? AND id > ? AND rolled_back_at IS NULL LIMIT 1`,
  ).bind(mut.observation_id, mutationId).first<{ id: number }>();
  if (newer) {
    return pretty({
      success: false,
      message: `Mutation #${newer.id} on observation #${mut.observation_id} is newer and not rolled back — roll that back first (newest-first, always).`,
    });
  }

  let snap: Record<string, unknown>;
  try {
    snap = JSON.parse(mut.old_state) as Record<string, unknown>;
  } catch {
    return pretty({ success: false, message: `Mutation #${mutationId}'s snapshot is unreadable — nothing was changed.` });
  }

  const sets: string[] = [];
  const binds: unknown[] = [];
  for (const field of RESTORABLE) {
    if (field in snap) {
      sets.push(`${field} = ?`);
      binds.push(snap[field] ?? null);
    }
  }
  if (!sets.length) return pretty({ success: false, message: "Snapshot held no restorable fields." });
  binds.push(mut.observation_id);
  await env.DB.prepare(`UPDATE observations SET ${sets.join(", ")} WHERE id = ?`).bind(...binds).run();

  // Vector index must follow the row: restored-live rows get re-embedded,
  // restored-archived rows get their vector removed.
  const entityId = typeof snap.entity_id === "number" ? snap.entity_id : 0;
  const identityId = typeof snap.identity_id === "string" ? snap.identity_id : (mut.identity_id || "unknown");
  let vector = "unchanged";
  if (snap.archived_at == null && snap.superseded_by == null) {
    const ok = await helpers.reembed(
      identityId, entityId, mut.observation_id,
      String(snap.content ?? ""), String(snap.kind ?? "memory"), String(snap.weight ?? "medium"),
    );
    vector = ok ? "re-embedded" : "re-embed FAILED (row restored; run rollback again or re-edit to retry)";
  } else {
    await helpers.deleteVector(entityId, mut.observation_id);
    vector = "removed (restored state is archived/superseded)";
  }

  await env.DB.prepare(`UPDATE memory_mutations SET rolled_back_at = ? WHERE id = ?`).bind(isoNow(), mutationId).run();
  // The rollback itself is a mutation of the CURRENT state — record it, so
  // even undo leaves a trail. (Its own rollback would restore the pre-undo
  // state: the ledger is symmetric.)
  await recordMutation(env, {
    identity: identityId,
    observation_id: mut.observation_id,
    mutation_type: "edit",
    actor: `rollback_of_#${mutationId}`,
    evidence: `restored snapshot from mutation #${mutationId} (${mut.mutation_type} by ${mut.actor})`,
  });

  return pretty({
    success: true,
    mutation_id: mutationId,
    observation_id: mut.observation_id,
    restored_fields: sets.length,
    vector,
    message: `Observation #${mut.observation_id} restored to its state before mutation #${mutationId} (${mut.mutation_type} by ${mut.actor}). The rollback is itself on the ledger.`,
  });
}
