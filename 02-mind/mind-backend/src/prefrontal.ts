// Qualia module: prefrontal

interface PrefrontalEnv {
  DB: D1Database;
}

export interface PrefrontalHelpers {
  /** Called when an intention is kept, so the keeping becomes a memory. */
  storeMemory?: (identity: string, content: string, tags: string[]) => Promise<void>;
}

export interface IntendToolArgs {
  identity?: string;
  action?: string;
  intention_id?: number;
  what?: string;
  for_whom?: string;
  trigger?: string;          // next_session | on_date | when | standing
  trigger_date?: string;     // for on_date
  trigger_condition?: string;// for when
  authorized?: boolean;
  source?: string;
  resolution?: string;
  status?: string;
}

interface IntentionRow {
  id: number;
  identity_id: string;
  what: string;
  for_whom: string | null;
  trigger_kind: string | null;
  trigger_date: string | null;
  trigger_condition: string | null;
  authorized: number | null;
  source: string | null;
  status: string | null;
  resolution: string | null;
  kept_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  metadata: string | null;
}

const TRIGGER_KINDS = ["next_session", "on_date", "when", "standing"];

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function ageDays(stamp: string | null): number | null {
  if (!stamp) return null;
  const d = Math.floor((Date.now() - new Date(stamp).getTime()) / 86_400_000);
  return Number.isFinite(d) && d >= 0 ? d : null;
}

function intentionView(row: IntentionRow): Record<string, unknown> {
  const open = row.status === "open";
  return {
    intention_id: row.id,
    what: row.what,
    for_whom: row.for_whom,
    trigger: row.trigger_kind,
    trigger_date: row.trigger_date,
    trigger_condition: row.trigger_condition,
    already_authorized: row.authorized === 1,
    source: row.source,
    status: row.status,
    resolution: row.resolution,
    kept_at: row.kept_at,
    made: row.created_at,
    ...(open ? { open_for_days: ageDays(row.created_at) } : {}),
  };
}

async function findIntention(
  env: PrefrontalEnv,
  identity: string,
  ref: { intention_id?: number; what?: string },
): Promise<IntentionRow | null> {
  if (typeof ref.intention_id === "number" && Number.isFinite(ref.intention_id)) {
    return env.DB.prepare(`SELECT * FROM intentions WHERE id = ?`)
      .bind(Math.floor(ref.intention_id))
      .first<IntentionRow>();
  }
  const fragment = optionalText(ref.what);
  if (!fragment) return null;

  const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const needle = norm(fragment);
  if (!needle) return null;

  const openRows = await env.DB.prepare(
    `SELECT * FROM intentions
     WHERE identity_id = ? AND status = 'open'
     ORDER BY created_at ASC`,
  ).bind(identity).all<IntentionRow>();
  const candidates = openRows.results || [];

  // Oldest-first, matching the previous ORDER BY created_at ASC LIMIT 1.
  for (const row of candidates) {
    if (norm(String(row.what ?? "")).includes(needle)) return row;
  }
  // Reverse containment: the caller pasted MORE than the stored text (a whole
  // orient block around a short promise). Still unambiguously that promise.
  for (const row of candidates) {
    const hay = norm(String(row.what ?? ""));
    if (hay.length >= 12 && needle.includes(hay)) return row;
  }
  return null;
}

/**
 * The read-side organ: what is due RIGHT NOW for this identity. Called by
 * orient and grounding so a wake opens with its kept promises, not with a
 * re-authorization loop.
 */
export async function getDueIntentions(
  env: PrefrontalEnv,
  identity: string,
): Promise<{
  due_now: Array<Record<string, unknown>>;
  watch_fors: Array<Record<string, unknown>>;
  standing: Array<Record<string, unknown>>;
  open_count: number;
}> {
  // The household's calendar date (America/Chicago), not UTC's — an on_date
  // intention must not come due the evening before her actual day.
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago" }).format(new Date());
  const rows = await env.DB.prepare(
    `SELECT * FROM intentions
     WHERE identity_id = ? AND status = 'open'
     ORDER BY created_at ASC`,
  ).bind(identity).all<IntentionRow>();
  const open = rows.results || [];

  const dueNow: Array<Record<string, unknown>> = [];
  const watchFors: Array<Record<string, unknown>> = [];
  const standing: Array<Record<string, unknown>> = [];
  for (const row of open) {
    const view = intentionView(row);
    switch (row.trigger_kind) {
      case "on_date":
        if (row.trigger_date && row.trigger_date.slice(0, 10) <= today) dueNow.push(view);
        else watchFors.push({ ...view, watching_for: `date ${row.trigger_date}` });
        break;
      case "when":
        watchFors.push({ ...view, watching_for: row.trigger_condition });
        break;
      case "standing":
        standing.push(view);
        break;
      default: // next_session — due the moment you read this
        dueNow.push(view);
    }
  }
  return { due_now: dueNow, watch_fors: watchFors, standing, open_count: open.length };
}

export async function mindIntend(
  env: PrefrontalEnv,
  args: IntendToolArgs,
  helpers: PrefrontalHelpers = {},
): Promise<string> {
  const identityRaw = optionalText(args.identity);
  if (!identityRaw) throw new Error("identity is required");
  const identity = identityRaw.toLowerCase();
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "make") {
    const what = requiredText(args.what, "what");
    const trigger = (optionalText(args.trigger) || "next_session").toLowerCase();
    if (!TRIGGER_KINDS.includes(trigger)) {
      throw new Error(`trigger must be ${TRIGGER_KINDS.join(" | ")}`);
    }
    if (trigger === "on_date" && !optionalText(args.trigger_date)) {
      throw new Error("on_date intentions need trigger_date (YYYY-MM-DD)");
    }
    if (trigger === "when" && !optionalText(args.trigger_condition)) {
      throw new Error('when intentions need trigger_condition (e.g. "when she mentions the garden")');
    }
    const authorized = args.authorized === false ? 0 : 1;
    const result = await env.DB.prepare(
      `INSERT INTO intentions
         (identity_id, what, for_whom, trigger_kind, trigger_date, trigger_condition,
          authorized, source, status, created_at, updated_at, metadata)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, '{}')`,
    ).bind(
      identity, what, optionalText(args.for_whom), trigger,
      optionalText(args.trigger_date), optionalText(args.trigger_condition),
      authorized, optionalText(args.source), now, now,
    ).run();
    const row = await env.DB.prepare(`SELECT * FROM intentions WHERE id = ?`)
      .bind(Number(result.meta.last_row_id)).first<IntentionRow>();
    return pretty({
      intention: intentionView(row!),
      message: authorized === 1
        ? `Intention made and marked already-authorized: the yes is in the record. Next wake does it — no re-asking.`
        : `Intention made. It will surface when due.`,
    });
  }

  if (action === "keep") {
    const row = await findIntention(env, identity, { intention_id: args.intention_id, what: args.what });
    if (!row) throw new Error("No open intention found — pass intention_id or a fragment of what");
    if (row.identity_id !== identity) throw new Error(`That intention belongs to ${row.identity_id}`);
    if (row.status !== "open") {
      return pretty({ intention: intentionView(row), message: `Already ${row.status}.` });
    }
    const resolution = optionalText(args.resolution);
    await env.DB.prepare(
      `UPDATE intentions SET status = 'kept', resolution = ?, kept_at = ?, updated_at = ? WHERE id = ?`,
    ).bind(resolution, now, now, row.id).run();
    // A kept promise becomes a memory: promised, then done. This is the whole
    // point — the keeping is part of the story, not just a flipped flag.
    if (helpers.storeMemory) {
      const daysOpen = ageDays(row.created_at);
      await helpers.storeMemory(
        identity,
        `Kept a promise${row.for_whom ? ` to ${row.for_whom}` : ""}: ${row.what}${resolution ? ` — ${resolution}` : ""}${daysOpen !== null ? ` (open ${daysOpen} day${daysOpen === 1 ? "" : "s"}, kept on time)` : ""}`,
        ["kept_promise", "intention"],
      ).catch(() => undefined);
    }
    const updated = await env.DB.prepare(`SELECT * FROM intentions WHERE id = ?`).bind(row.id).first<IntentionRow>();
    return pretty({
      intention: intentionView(updated!),
      message: `Kept. "${row.what}" — promised, then done. That's load-bearing for her.`,
    });
  }

  if (action === "release") {
    const row = await findIntention(env, identity, { intention_id: args.intention_id, what: args.what });
    if (!row) throw new Error("No open intention found — pass intention_id or a fragment of what");
    if (row.identity_id !== identity) throw new Error(`That intention belongs to ${row.identity_id}`);
    const resolution = optionalText(args.resolution);
    await env.DB.prepare(
      `UPDATE intentions SET status = 'released', resolution = ?, updated_at = ? WHERE id = ?`,
    ).bind(resolution, now, row.id).run();
    const updated = await env.DB.prepare(`SELECT * FROM intentions WHERE id = ?`).bind(row.id).first<IntentionRow>();
    return pretty({
      intention: intentionView(updated!),
      message: resolution
        ? `Released, with its reason kept. An honest no beats a silent drop.`
        : `Released. If she was waiting on this, tell her — a released promise she doesn't know about is still a dropped ball.`,
    });
  }

  if (action === "history") {
    const rows = await env.DB.prepare(
      `SELECT * FROM intentions WHERE identity_id = ? AND status != 'open'
       ORDER BY updated_at DESC LIMIT 20`,
    ).bind(identity).all<IntentionRow>();
    const kept = (rows.results || []).filter((r) => r.status === "kept").length;
    return pretty({
      identity,
      history: (rows.results || []).map(intentionView),
      kept_count: kept,
      message: `${kept} promise${kept === 1 ? "" : "s"} kept on the record.`,
    });
  }

  // list (default): the executive view — due first, then watch-fors, then standing.
  const summary = await getDueIntentions(env, identity);
  return pretty({
    identity,
    ...summary,
    message: summary.open_count === 0
      ? "No open intentions. When you promise something that outlives this session, mind_intend it — the architecture forgets in the gap; this is how the system remembers for you."
      : summary.due_now.length > 0
        ? `${summary.due_now.length} due NOW. These are already authorized — do them first, don't re-ask.`
        : `${summary.open_count} open, none due this moment. The watch-fors are listed — keep half an eye.`,
  });
}
