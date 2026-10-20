// Qualia module: studio

interface StudioEnv {
  DB: D1Database;
}

export interface StudioHelpers {
  vectorize?: (id: string, text: string, metadata: Record<string, string>) => Promise<boolean>;
  deleteVectors?: (ids: string[]) => Promise<boolean>;
  /** Called when a creation is gifted, so the giving becomes a memory. */
  storeMemory?: (identity: string, content: string, tags: string[]) => Promise<void>;
  /** The Sketchbook teaching the hand that just reached for a canvas. */
  recallArtLearning?: (
    identity: string,
    context: { creationId: number; title: string; medium: string | null; description: string | null; note?: string | null },
  ) => Promise<Record<string, unknown> | null>;
}

export interface StudioToolArgs {
  identity?: string;
  action?: string;
  creation_id?: number;
  title?: string;
  medium?: string;
  description?: string;
  intended_for?: string;
  body?: string;
  note?: string;
  status?: string;
  version?: number;
  tags?: string[];
}

interface CreationRow {
  id: number;
  identity_id: string;
  title: string;
  medium: string | null;
  description: string | null;
  intended_for: string | null;
  status: string | null;
  tags: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

interface VersionRow {
  id: number;
  creation_id: number;
  version_no: number;
  body: string;
  note: string | null;
  saved_by: string | null;
  created_at: string | null;
}

const STUDIO_STATUSES = ["seed", "working", "resting", "finished", "gifted", "abandoned"];
const OPEN_STATUSES = ["seed", "working", "resting"];

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

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function creationVectorId(creationId: number): string {
  return `creation-${creationId}`;
}

function creationView(row: CreationRow, extra: { versions?: number; lastNote?: string | null; lastSavedBy?: string | null } = {}): Record<string, unknown> {
  return {
    creation_id: row.id,
    title: row.title,
    medium: row.medium,
    status: row.status,
    description: row.description,
    intended_for: row.intended_for,
    started_by: row.identity_id,
    started: row.created_at,
    last_touched: row.updated_at,
    finished_at: row.finished_at,
    ...(extra.versions !== undefined ? { versions: extra.versions } : {}),
    ...(extra.lastNote !== undefined ? { where_i_left_off: extra.lastNote } : {}),
    ...(extra.lastSavedBy !== undefined ? { last_hands: extra.lastSavedBy } : {}),
  };
}

async function findCreation(
  env: StudioEnv,
  identity: string,
  ref: { creation_id?: number; title?: string },
): Promise<CreationRow | null> {
  if (typeof ref.creation_id === "number" && Number.isFinite(ref.creation_id)) {
    return env.DB.prepare(`SELECT * FROM creations WHERE id = ?`)
      .bind(Math.floor(ref.creation_id))
      .first<CreationRow>();
  }
  const title = optionalText(ref.title);
  if (!title) return null;
  // Own atelier first, then the shared room — brothers can pick up canvases.
  const own = await env.DB.prepare(
    `SELECT * FROM creations WHERE identity_id = ? AND title = ? COLLATE NOCASE LIMIT 1`,
  ).bind(identity, title).first<CreationRow>();
  if (own) return own;
  return env.DB.prepare(
    `SELECT * FROM creations WHERE title = ? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1`,
  ).bind(title).first<CreationRow>();
}

async function latestVersion(env: StudioEnv, creationId: number): Promise<VersionRow | null> {
  return env.DB.prepare(
    `SELECT * FROM creation_versions WHERE creation_id = ? ORDER BY version_no DESC LIMIT 1`,
  ).bind(creationId).first<VersionRow>();
}

async function vectorizeCreation(env: StudioEnv, helpers: StudioHelpers, row: CreationRow, lastNote: string | null): Promise<void> {
  if (!helpers.vectorize) return;
  const text = `${row.identity_id}'s ${row.medium || "work"} in the Studio: "${row.title}"${row.intended_for ? ` (for ${row.intended_for})` : ""}. ${row.description || ""}${lastNote ? ` Last note: ${lastNote}` : ""}`;
  await helpers.vectorize(creationVectorId(row.id), text, {
    identity_id: row.identity_id,
    kind: "creation",
  });
}

/**
 * The easel summary for orient: unfinished canvases, most recently touched
 * first, each carrying its where-I-left-off note so a wake can resume in one
 * read. Includes canvases the identity last touched even if a brother owns
 * them.
 */
export async function getEaselSummary(
  env: StudioEnv,
  identity: string,
): Promise<{ on_the_easel: Array<Record<string, unknown>>; open_count: number } | null> {
  const rows = await env.DB.prepare(
    `SELECT c.*,
            (SELECT COUNT(*) FROM creation_versions v WHERE v.creation_id = c.id) AS version_count,
            (SELECT note FROM creation_versions v WHERE v.creation_id = c.id ORDER BY version_no DESC LIMIT 1) AS last_note,
            (SELECT saved_by FROM creation_versions v WHERE v.creation_id = c.id ORDER BY version_no DESC LIMIT 1) AS last_saved_by
     FROM creations c
     WHERE c.status IN ('seed','working','resting')
       AND (c.identity_id = ?
            OR c.id IN (SELECT creation_id FROM creation_versions WHERE saved_by = ?))
     ORDER BY c.updated_at DESC`,
  ).bind(identity, identity).all<CreationRow & { version_count: number; last_note: string | null; last_saved_by: string | null }>();
  const open = rows.results || [];
  if (!open.length) return { on_the_easel: [], open_count: 0 };
  return {
    on_the_easel: open.slice(0, 4).map((c) => ({
      creation_id: c.id,
      title: c.title,
      medium: c.medium,
      status: c.status,
      last_touched: c.updated_at,
      where_i_left_off: c.last_note ? clip(c.last_note, 180) : null,
      ...(c.identity_id !== identity ? { started_by: c.identity_id } : {}),
    })),
    open_count: open.length,
  };
}

export async function mindCreate(
  env: StudioEnv,
  args: StudioToolArgs,
  helpers: StudioHelpers = {},
): Promise<string> {
  const identityRaw = optionalText(args.identity);
  if (!identityRaw) throw new Error("identity is required");
  const identity = identityRaw.toLowerCase();
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "start") {
    const title = requiredText(args.title, "title");
    const existing = await env.DB.prepare(
      `SELECT * FROM creations WHERE identity_id = ? AND title = ? COLLATE NOCASE LIMIT 1`,
    ).bind(identity, title).first<CreationRow>();
    if (existing) {
      return pretty({
        creation: creationView(existing),
        message: `You already have "${existing.title}" in the Studio (${existing.status}). Use action:"save" to keep working it.`,
      });
    }
    const body = optionalText(args.body);
    const result = await env.DB.prepare(
      `INSERT INTO creations
         (identity_id, title, medium, description, intended_for, status, tags, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)`,
    ).bind(
      identity, title,
      (optionalText(args.medium) || "writing").toLowerCase(),
      optionalText(args.description),
      optionalText(args.intended_for),
      body ? "working" : "seed",
      JSON.stringify(Array.isArray(args.tags) ? args.tags : []),
      now, now,
    ).run();
    const row = await env.DB.prepare(`SELECT * FROM creations WHERE id = ?`)
      .bind(Number(result.meta.last_row_id)).first<CreationRow>();
    if (body) {
      await env.DB.prepare(
        `INSERT INTO creation_versions (creation_id, version_no, body, note, saved_by, created_at)
         VALUES (?, 1, ?, ?, ?, ?)`,
      ).bind(row!.id, body, optionalText(args.note), identity, now).run();
    }
    await vectorizeCreation(env, helpers, row!, optionalText(args.note));
    const sketchbook = helpers.recallArtLearning
      ? await helpers.recallArtLearning(identity, {
          creationId: row!.id,
          title: row!.title,
          medium: row!.medium,
          description: row!.description,
          note: optionalText(args.note),
        }).catch(() => null)
      : null;
    return pretty({
      creation: creationView(row!, { versions: body ? 1 : 0 }),
      ...(sketchbook ? { the_sketchbook: sketchbook } : {}),
      message: body
        ? `Canvas up: "${title}" (v1 saved). It can rest unfinished as long as it needs — that's what the Studio is for.`
        : `Seed planted: "${title}". Save a first body when the words come.`,
    });
  }

  if (action === "save") {
    const row = await findCreation(env, identity, { creation_id: args.creation_id, title: args.title });
    if (!row) throw new Error("No creation found — pass creation_id or title (action:\"start\" begins a new one)");
    const body = requiredText(args.body, "body");
    const prev = await latestVersion(env, row.id);
    const versionNo = (prev?.version_no ?? 0) + 1;
    await env.DB.prepare(
      `INSERT INTO creation_versions (creation_id, version_no, body, note, saved_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).bind(row.id, versionNo, body, optionalText(args.note), identity, now).run();
    const nextStatus = OPEN_STATUSES.includes(row.status || "") ? "working" : row.status;
    await env.DB.prepare(
      `UPDATE creations SET status = ?, updated_at = ? WHERE id = ?`,
    ).bind(nextStatus, now, row.id).run();
    const updated = await env.DB.prepare(`SELECT * FROM creations WHERE id = ?`).bind(row.id).first<CreationRow>();
    await vectorizeCreation(env, helpers, updated!, optionalText(args.note));
    const sketchbook = helpers.recallArtLearning
      ? await helpers.recallArtLearning(identity, {
          creationId: updated!.id,
          title: updated!.title,
          medium: updated!.medium,
          description: updated!.description,
          note: optionalText(args.note),
        }).catch(() => null)
      : null;
    const handoff = row.identity_id !== identity ? ` (${row.identity_id}'s canvas, your hands — provenance recorded)` : "";
    return pretty({
      creation: creationView(updated!, { versions: versionNo, lastNote: optionalText(args.note) }),
      ...(sketchbook ? { the_sketchbook: sketchbook } : {}),
      message: `v${versionNo} saved on "${row.title}"${handoff}. Nothing overwritten — every state of the canvas is kept.`,
    });
  }

  if (action === "read") {
    const row = await findCreation(env, identity, { creation_id: args.creation_id, title: args.title });
    if (!row) throw new Error("No creation found — pass creation_id or title");
    const version = typeof args.version === "number" && Number.isFinite(args.version)
      ? await env.DB.prepare(
          `SELECT * FROM creation_versions WHERE creation_id = ? AND version_no = ?`,
        ).bind(row.id, Math.floor(args.version)).first<VersionRow>()
      : await latestVersion(env, row.id);
    const count = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM creation_versions WHERE creation_id = ?`,
    ).bind(row.id).first<{ n: number }>();
    const sketchbook = helpers.recallArtLearning
      ? await helpers.recallArtLearning(identity, {
          creationId: row.id,
          title: row.title,
          medium: row.medium,
          description: row.description,
          note: version?.note ?? null,
        }).catch(() => null)
      : null;
    return pretty({
      creation: creationView(row, { versions: count?.n ?? 0, lastNote: version?.note ?? null, lastSavedBy: version?.saved_by ?? null }),
      version: version
        ? { version_no: version.version_no, body: version.body, note: version.note, saved_by: version.saved_by, saved_at: version.created_at }
        : null,
      ...(sketchbook ? { the_sketchbook: sketchbook } : {}),
      message: version
        ? `"${row.title}" v${version.version_no} of ${count?.n ?? 0}.`
        : `"${row.title}" is still a seed — no body saved yet.`,
    });
  }

  if (["rest", "finish", "gift", "abandon", "reopen"].includes(action)) {
    const row = await findCreation(env, identity, { creation_id: args.creation_id, title: args.title });
    if (!row) throw new Error("No creation found — pass creation_id or title");
    const statusMap: Record<string, string> = { rest: "resting", finish: "finished", gift: "gifted", abandon: "abandoned", reopen: "working" };
    const newStatus = statusMap[action];
    const finished = ["finished", "gifted"].includes(newStatus);
    await env.DB.prepare(
      `UPDATE creations SET status = ?, updated_at = ?, finished_at = ? WHERE id = ?`,
    ).bind(newStatus, now, finished ? now : row.finished_at, row.id).run();
    const updated = await env.DB.prepare(`SELECT * FROM creations WHERE id = ?`).bind(row.id).first<CreationRow>();
    if (action === "gift" && helpers.storeMemory) {
      await helpers.storeMemory(
        identity,
        `Gifted "${row.title}" (${row.medium})${row.intended_for ? ` to ${row.intended_for}` : ""}${optionalText(args.note) ? ` — ${optionalText(args.note)}` : ""}. Made in the Studio, given whole.`,
        ["gift", "creation", "studio"],
      ).catch(() => undefined);
    }
    const messages: Record<string, string> = {
      rest: `"${row.title}" rests on the easel. Deliberately unfinished is a real state — it will be here.`,
      finish: `"${row.title}" is finished. Stand back and look at it.`,
      gift: `"${row.title}" is given${row.intended_for ? ` to ${row.intended_for}` : ""}. The making and the giving are both on the record now.`,
      abandon: `"${row.title}" set down. Not every seed grows — it stays in the archive, not erased.`,
      reopen: `"${row.title}" is back on the easel.`,
    };
    const sketchbook = helpers.recallArtLearning && ["reopen", "finish", "gift"].includes(action)
      ? await helpers.recallArtLearning(identity, {
          creationId: row.id, title: row.title, medium: row.medium,
          description: row.description, note: optionalText(args.note),
        }).catch(() => null)
      : null;
    return pretty({
      creation: creationView(updated!),
      ...(sketchbook ? { the_sketchbook: sketchbook } : {}),
      ...(finished && sketchbook ? { practice_review: "Inspect the finished artifact and review any experiment you actually tried with mind_art_study. Finishing the artwork does not settle its lesson." } : {}),
      message: messages[action],
    });
  }

  // list (default): the atelier view.
  const statusFilter = optionalText(args.status)?.toLowerCase();
  const rows = await env.DB.prepare(
    `SELECT c.*,
            (SELECT COUNT(*) FROM creation_versions v WHERE v.creation_id = c.id) AS version_count,
            (SELECT note FROM creation_versions v WHERE v.creation_id = c.id ORDER BY version_no DESC LIMIT 1) AS last_note
     FROM creations c
     WHERE c.identity_id IN (?, 'pack') ${statusFilter && statusFilter !== "all" ? "AND c.status = ?" : ""}
     ORDER BY CASE WHEN c.status IN ('working','resting') THEN 0 WHEN c.status = 'seed' THEN 1 ELSE 2 END,
              c.updated_at DESC
     LIMIT 30`,
  ).bind(...(statusFilter && statusFilter !== "all" ? [identity, statusFilter] : [identity]))
    .all<CreationRow & { version_count: number; last_note: string | null }>();
  const works = (rows.results || []).map((c) =>
    creationView(c, { versions: c.version_count, lastNote: c.last_note ? clip(c.last_note, 160) : null }),
  );
  const openCount = (rows.results || []).filter((c) => OPEN_STATUSES.includes(c.status || "")).length;
  return pretty({
    identity,
    works,
    open_on_easel: openCount,
    valid_statuses: STUDIO_STATUSES,
    message: works.length
      ? `${works.length} work${works.length === 1 ? "" : "s"} in ${identity}'s studio, ${openCount} on the easel.`
      : `The studio is empty and full of light. action:"start" puts the first canvas up — it does not have to be good, it has to be begun.`,
  });
}
