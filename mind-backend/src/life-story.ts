// Qualia module: life-story

interface LifeEnv {
  DB: D1Database;
}

export interface LifeStoryHelpers {
  vectorize?: (id: string, text: string, metadata: Record<string, string>) => Promise<boolean>;
  deleteVectors?: (ids: string[]) => Promise<boolean>;
}

export interface LifeToolArgs {
  identity?: string;
  // beats
  beat_id?: number;
  title?: string;
  narrative?: string;
  significance?: string;
  happened_on?: string;
  date_precision?: string;
  beat_type?: string;
  confidence?: string;
  changes?: unknown;
  evidence?: unknown;
  strands?: unknown;
  era?: string;
  era_id?: number;
  tags?: string[];
  archive?: boolean;
  bond_with?: string;
  with?: string;
  // eras
  action?: string;
  started_on?: string;
  started_precision?: string;
  ended_on?: string;
  ended_precision?: string;
  themes?: string[];
  // strands
  strand_id?: number;
  name?: string;
  kind?: string;
  description?: string;
  origin_beat_id?: number;
  event?: string;
  note?: string;
  status?: string;
  // life story reads
  mode?: string;
  date?: string;
  start_date?: string;
  end_date?: string;
  include_evidence?: boolean;
  include_pack?: boolean;
  limit?: number;
  metadata?: unknown;
  // positions
  position_id?: number;
  topic?: string;
  stance?: string;
  reasoning?: string;
  why?: string;
  sparked_by?: string;
}

// ============ Row types ============

interface EraRow {
  id: number;
  identity_id: string;
  title: string;
  narrative: string | null;
  started_on: string | null;
  started_precision: string | null;
  ended_on: string | null;
  ended_precision: string | null;
  themes: string | null;
  status: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface BeatRow {
  id: number;
  identity_id: string;
  era_id: number | null;
  happened_on: string;
  date_precision: string | null;
  title: string;
  narrative: string | null;
  significance: string | null;
  beat_type: string | null;
  changes: string | null;
  confidence: string | null;
  tags: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
  bond_with: string | null;
}

interface EvidenceRow {
  id: number;
  beat_id: number;
  source_type: string;
  source_id: string | null;
  note: string | null;
  added_by: string | null;
  created_at: string | null;
}

interface StrandRow {
  id: number;
  identity_id: string;
  name: string;
  kind: string | null;
  description: string | null;
  status: string | null;
  origin_beat_id: number | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface StrandEventRow {
  id: number;
  strand_id: number;
  beat_id: number;
  event: string | null;
  note: string | null;
  created_at: string | null;
}

// ============ Small utilities (bonds.ts idiom) ============

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} is required`);
  }
  return value.trim();
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeIdentity(value: unknown): string | null {
  const text = optionalText(value);
  return text ? text.toLowerCase() : null;
}

function parseJson(value: string | null): unknown {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean))];
}

function json(value: unknown): string {
  return JSON.stringify(value ?? {});
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}


const DATE_PRECISIONS = ["day", "month", "year", "approx"];

function normalizeLifeDate(value: unknown, explicitPrecision?: unknown): { date: string; precision: string } {
  const text = requiredText(value, "date");
  let date: string;
  let precision: string;
  if (/^\d{4}$/.test(text)) {
    date = `${text}-01-01`;
    precision = "year";
  } else if (/^\d{4}-\d{2}$/.test(text)) {
    date = `${text}-01`;
    precision = "month";
  } else if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
    date = text.slice(0, 10);
    precision = "day";
  } else {
    throw new Error(`Could not read date "${text}" — use YYYY, YYYY-MM, or YYYY-MM-DD (fuzzy is fine; you can sharpen it later)`);
  }
  const requested = optionalText(explicitPrecision)?.toLowerCase();
  if (requested && DATE_PRECISIONS.includes(requested)) {
    precision = requested;
  }
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`"${text}" is not a real calendar date`);
  }
  return { date, precision };
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function displayLifeDate(date: string | null, precision: string | null): string {
  if (!date) return "date unknown";
  const [y, m, d] = date.slice(0, 10).split("-").map((part) => parseInt(part, 10));
  const monthName = MONTH_NAMES[(m || 1) - 1] || "";
  switch (precision || "day") {
    case "year":
      return `${y}`;
    case "month":
      return `${monthName} ${y}`;
    case "approx":
      return `around ${monthName} ${y}`;
    default:
      return `${monthName} ${d}, ${y}`;
  }
}

// ============ Validated nested inputs ============

interface ChangeDelta {
  facet: string;
  from: string | null;
  to: string;
}

function parseChanges(value: unknown): ChangeDelta[] | null {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value)) throw new Error("changes must be an array of {facet, from?, to}");
  return value.map((item) => {
    const obj = objectValue(item);
    const facet = requiredText(obj.facet, "changes[].facet");
    const to = requiredText(obj.to, "changes[].to");
    return { facet, from: optionalText(obj.from), to };
  });
}

const EVIDENCE_TYPES = [
  "observation", "journal", "image", "audio", "document_node",
  "qualia_entry", "significant_moment", "url", "note", "told_by",
];

interface EvidenceInput {
  source_type: string;
  source_id: string | null;
  note: string | null;
}

function parseEvidence(value: unknown): EvidenceInput[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("evidence must be an array of {source_type, source_id?, note?}");
  return value.map((item) => {
    const obj = objectValue(item);
    const sourceType = requiredText(obj.source_type, "evidence[].source_type").toLowerCase();
    if (!EVIDENCE_TYPES.includes(sourceType)) {
      throw new Error(`evidence[].source_type must be one of: ${EVIDENCE_TYPES.join(", ")}`);
    }
    const sourceId = obj.source_id === undefined || obj.source_id === null ? null : String(obj.source_id).trim() || null;
    return { source_type: sourceType, source_id: sourceId, note: optionalText(obj.note) };
  });
}

const STRAND_EVENTS = ["born", "tested", "strengthened", "renamed", "dormant", "shed", "revived", "touched"];

interface StrandTouchInput {
  name: string;
  event: string | null;
  note: string | null;
  kind: string | null;
  description: string | null;
}

function parseStrandTouches(value: unknown): StrandTouchInput[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("strands must be an array of {name, event?, note?, kind?, description?}");
  return value.map((item) => {
    const obj = objectValue(item);
    const event = optionalText(obj.event)?.toLowerCase() || null;
    if (event && !STRAND_EVENTS.includes(event)) {
      throw new Error(`strands[].event must be one of: ${STRAND_EVENTS.join(", ")}`);
    }
    return {
      name: requiredText(obj.name, "strands[].name"),
      event,
      note: optionalText(obj.note),
      kind: optionalText(obj.kind),
      description: optionalText(obj.description),
    };
  });
}

// ============ Views ============

function eraView(row: EraRow, beatCount?: number) {
  return {
    era_id: row.id,
    identity: row.identity_id,
    title: row.title,
    narrative: row.narrative,
    started: displayLifeDate(row.started_on, row.started_precision),
    started_on: row.started_on,
    ended: row.ended_on ? displayLifeDate(row.ended_on, row.ended_precision) : null,
    ended_on: row.ended_on,
    status: row.status || "open",
    themes: stringArray(parseJson(row.themes)),
    ...(beatCount !== undefined ? { beat_count: beatCount } : {}),
  };
}

function beatView(
  row: BeatRow,
  extras: {
    eraTitle?: string | null;
    evidenceCount?: number;
    evidence?: EvidenceRow[];
    strandEvents?: Array<{ strand: string; event: string; note: string | null }>;
  } = {},
) {
  const changes = (parseJson(row.changes) as ChangeDelta[] | null) || [];
  return {
    beat_id: row.id,
    identity: row.identity_id,
    date: displayLifeDate(row.happened_on, row.date_precision),
    happened_on: row.happened_on,
    date_precision: row.date_precision || "day",
    title: row.title,
    narrative: row.narrative,
    significance: row.significance,
    beat_type: row.beat_type || "moment",
    confidence: row.confidence || "witnessed",
    changes: changes.map((c) => ({ facet: c.facet, from: c.from ?? null, to: c.to })),
    tags: stringArray(parseJson(row.tags)),
    era: extras.eraTitle ?? null,
    era_id: row.era_id,
    ...(row.bond_with ? { with: row.bond_with } : {}),
    sources: extras.evidenceCount ?? 0,
    ...(extras.evidence
      ? {
          evidence: extras.evidence.map((e) => ({
            source_type: e.source_type,
            source_id: e.source_id,
            note: e.note,
            added_by: e.added_by,
          })),
        }
      : {}),
    ...(extras.strandEvents && extras.strandEvents.length ? { strands: extras.strandEvents } : {}),
  };
}

function strandView(row: StrandRow, extras: { originBeatTitle?: string | null; eventCount?: number } = {}) {
  return {
    strand_id: row.id,
    identity: row.identity_id,
    name: row.name,
    kind: row.kind || "trait",
    description: row.description,
    status: row.status || "living",
    origin_beat_id: row.origin_beat_id,
    ...(extras.originBeatTitle !== undefined ? { origin: extras.originBeatTitle } : {}),
    ...(extras.eventCount !== undefined ? { events: extras.eventCount } : {}),
  };
}

// ============ Shared lookups ============

async function getBeat(env: LifeEnv, beatId: number): Promise<BeatRow | null> {
  return env.DB.prepare(`SELECT * FROM life_beats WHERE id = ?`).bind(beatId).first<BeatRow>();
}

async function findEra(env: LifeEnv, identity: string, args: LifeToolArgs): Promise<EraRow | null> {
  if (typeof args.era_id === "number" && Number.isFinite(args.era_id)) {
    return env.DB.prepare(`SELECT * FROM life_eras WHERE id = ?`).bind(Math.floor(args.era_id)).first<EraRow>();
  }
  const title = optionalText(args.era) || optionalText(args.title);
  if (!title) return null;
  return env.DB.prepare(
    `SELECT * FROM life_eras WHERE identity_id IN (?, 'pack') AND LOWER(title) = LOWER(?)
     ORDER BY CASE WHEN identity_id = ? THEN 0 ELSE 1 END LIMIT 1`,
  ).bind(identity, title, identity).first<EraRow>();
}

// The era whose span contains the date — used to auto-shelve a new beat when
// no era was named. Own eras win over pack eras; later chapters win overlaps.
async function containingEra(env: LifeEnv, identity: string, date: string): Promise<EraRow | null> {
  return env.DB.prepare(
    `SELECT * FROM life_eras
     WHERE identity_id IN (?, 'pack')
       AND (started_on IS NULL OR started_on <= ?)
       AND (ended_on IS NULL OR ended_on >= ?)
     ORDER BY CASE WHEN identity_id = ? THEN 0 ELSE 1 END,
              started_on IS NULL, started_on DESC
     LIMIT 1`,
  ).bind(identity, date, date, identity).first<EraRow>();
}

// ============ Bond chart lookups ============
//
// bond_with points at the bond graph (bond_people), which is the registry:
// each link in the chart can carry its own chain of beats. People are
// resolved by canonical key, linked identity, display name, or alias.

function canonicalPersonKey(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}

interface BondPersonRef {
  canonical_key: string;
  display_name: string;
  aliases: string | null;
}

async function resolveBondPerson(env: LifeEnv, nameOrKey: string): Promise<BondPersonRef | null> {
  const key = canonicalPersonKey(nameOrKey);
  if (!key) return null;
  const direct = await env.DB.prepare(
    `SELECT canonical_key, display_name, aliases FROM bond_people
     WHERE canonical_key = ? OR identity_id = ? OR LOWER(display_name) = LOWER(?)
     LIMIT 1`,
  ).bind(key, nameOrKey.trim().toLowerCase(), nameOrKey.trim()).first<BondPersonRef>();
  if (direct) return direct;
  const all = await env.DB.prepare(`SELECT canonical_key, display_name, aliases FROM bond_people`).all<BondPersonRef>();
  return (all.results || []).find((row) =>
    stringArray(parseJson(row.aliases)).some((alias) => canonicalPersonKey(alias) === key),
  ) || null;
}

async function findStrand(env: LifeEnv, identity: string, args: { strand_id?: number; name?: string | null }): Promise<StrandRow | null> {
  if (typeof args.strand_id === "number" && Number.isFinite(args.strand_id)) {
    return env.DB.prepare(`SELECT * FROM life_strands WHERE id = ?`).bind(Math.floor(args.strand_id)).first<StrandRow>();
  }
  const name = optionalText(args.name);
  if (!name) return null;
  return env.DB.prepare(
    `SELECT * FROM life_strands WHERE identity_id IN (?, 'pack') AND LOWER(name) = LOWER(?)
     ORDER BY CASE WHEN identity_id = ? THEN 0 ELSE 1 END LIMIT 1`,
  ).bind(identity, name, identity).first<StrandRow>();
}

// Evidence pointing at real tables gets an existence check so a beat's source
// count means something. URL/note/told_by sources are taken on faith.
async function verifyEvidence(env: LifeEnv, item: EvidenceInput): Promise<boolean | null> {
  if (!item.source_id) return null;
  try {
    switch (item.source_type) {
      case "observation": {
        const row = await env.DB.prepare(`SELECT id FROM observations WHERE id = ?`).bind(parseInt(item.source_id, 10)).first();
        return !!row;
      }
      case "journal": {
        const row = await env.DB.prepare(`SELECT id FROM journals WHERE id = ?`).bind(parseInt(item.source_id, 10)).first();
        return !!row;
      }
      case "image": {
        const row = await env.DB.prepare(`SELECT id FROM images WHERE id = ?`).bind(parseInt(item.source_id, 10)).first();
        return !!row;
      }
      case "audio": {
        const row = await env.DB.prepare(`SELECT id FROM audios WHERE id = ?`).bind(parseInt(item.source_id, 10)).first();
        return !!row;
      }
      case "document_node": {
        const row = await env.DB.prepare(`SELECT id FROM document_nodes WHERE id = ?`).bind(parseInt(item.source_id, 10)).first();
        return !!row;
      }
      case "qualia_entry":
      case "significant_moment": {
        const row = await env.DB.prepare(`SELECT id FROM qualia_entries WHERE id = ? OR id LIKE ?`)
          .bind(item.source_id, `${item.source_id}%`).first();
        return !!row;
      }
      default:
        return null;
    }
  } catch {
    return null;
  }
}

function beatVectorId(beatId: number): string {
  return `life-beat-${beatId}`;
}

function beatVectorText(identity: string, row: BeatRow): string {
  const date = displayLifeDate(row.happened_on, row.date_precision);
  const parts = [`${identity} life beat — ${date}: ${row.title}.`];
  if (row.bond_with) parts.push(`A bond beat with ${row.bond_with} — part of their story together.`);
  if (row.narrative) parts.push(row.narrative);
  if (row.significance) parts.push(`Why it mattered: ${row.significance}`);
  const changes = (parseJson(row.changes) as ChangeDelta[] | null) || [];
  if (changes.length) {
    parts.push(`Changes: ${changes.map((c) => `${c.facet}: ${c.from ? `${c.from} -> ` : ""}${c.to}`).join("; ")}`);
  }
  return parts.join(" ");
}

async function vectorizeBeat(env: LifeEnv, helpers: LifeStoryHelpers, row: BeatRow): Promise<void> {
  if (!helpers.vectorize) return;
  const identity = row.identity_id;
  await helpers.vectorize(beatVectorId(row.id), beatVectorText(identity, row), {
    source: "life_beat",
    entity: `${identity}-life-story`,
    content: `${row.title}${row.narrative ? ` — ${row.narrative}` : ""}`.slice(0, 500),
    kind: "life_beat",
    weight: "heavy",
    salience: "active",
    identity_id: identity,
  });
}

// ============ mind_beat ============

export async function mindBeat(env: LifeEnv, args: LifeToolArgs, helpers: LifeStoryHelpers = {}): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const now = new Date().toISOString();

  // ---- archive ----
  if (args.archive === true) {
    const beatId = typeof args.beat_id === "number" ? Math.floor(args.beat_id) : NaN;
    if (!Number.isFinite(beatId)) throw new Error("beat_id is required to archive a beat");
    const beat = await getBeat(env, beatId);
    if (!beat) throw new Error(`No beat #${beatId}`);
    if (beat.identity_id !== identity && beat.identity_id !== "pack") {
      throw new Error(`Beat #${beatId} belongs to ${beat.identity_id} — only they can archive it`);
    }
    await env.DB.prepare(`UPDATE life_beats SET archived_at = ?, updated_at = ? WHERE id = ?`).bind(now, now, beatId).run();
    if (helpers.deleteVectors) await helpers.deleteVectors([beatVectorId(beatId)]);
    return pretty({ beat_id: beatId, archived: true, message: `Beat "${beat.title}" folded away (archived, reversible in the table).` });
  }

  const evidence = parseEvidence(args.evidence);
  const strandTouches = parseStrandTouches(args.strands);
  const changes = parseChanges(args.changes);

  // Bond beats: validate the other person against the chart — the bond graph
  // is the registry, so a typo'd name can't silently mint a phantom timeline.
  let bondWith: string | null | undefined = undefined;
  if (args.bond_with !== undefined) {
    const raw = optionalText(args.bond_with);
    if (!raw) {
      bondWith = null;
    } else {
      const person = await resolveBondPerson(env, raw);
      if (!person) {
        throw new Error(`No "${raw}" in the bond chart — add them with mind_bond_upsert_person first, then their link can carry beats`);
      }
      if (person.canonical_key === identity) {
        throw new Error("bond_with is the other person — your own beats don't need it");
      }
      bondWith = person.canonical_key;
    }
  }

  let beat: BeatRow;
  const revisions: Array<Record<string, unknown>> = [];

  if (typeof args.beat_id === "number" && Number.isFinite(args.beat_id)) {
    // ---- edit / sharpen an existing beat ----
    const existing = await getBeat(env, Math.floor(args.beat_id));
    if (!existing) throw new Error(`No beat #${args.beat_id}`);
    if (existing.identity_id !== identity && existing.identity_id !== "pack") {
      throw new Error(`Beat #${existing.id} belongs to ${existing.identity_id} — a beat is edited only by its own hand`);
    }

    const sets: string[] = [];
    const binds: unknown[] = [];
    const change = (field: string, from: unknown, to: unknown) => {
      revisions.push({ at: now, by: identity, field, from: from ?? null, to: to ?? null });
    };

    if (args.happened_on !== undefined) {
      const { date, precision } = normalizeLifeDate(args.happened_on, args.date_precision);
      if (date !== existing.happened_on || precision !== (existing.date_precision || "day")) {
        change("happened_on", `${existing.happened_on} (${existing.date_precision})`, `${date} (${precision})`);
      }
      sets.push("happened_on = ?", "date_precision = ?");
      binds.push(date, precision);
    } else if (args.date_precision !== undefined) {
      const precision = optionalText(args.date_precision)?.toLowerCase();
      if (precision && DATE_PRECISIONS.includes(precision)) {
        change("date_precision", existing.date_precision, precision);
        sets.push("date_precision = ?");
        binds.push(precision);
      }
    }
    if (optionalText(args.title)) {
      sets.push("title = ?");
      binds.push(optionalText(args.title));
    }
    if (args.narrative !== undefined) {
      sets.push("narrative = ?");
      binds.push(optionalText(args.narrative));
    }
    if (args.significance !== undefined) {
      sets.push("significance = ?");
      binds.push(optionalText(args.significance));
    }
    if (optionalText(args.beat_type)) {
      sets.push("beat_type = ?");
      binds.push(optionalText(args.beat_type)!.toLowerCase());
    }
    if (optionalText(args.confidence)) {
      const confidence = optionalText(args.confidence)!.toLowerCase();
      if (!["witnessed", "reconstructed", "told"].includes(confidence)) {
        throw new Error("confidence must be witnessed | reconstructed | told");
      }
      if (confidence !== (existing.confidence || "witnessed")) change("confidence", existing.confidence, confidence);
      sets.push("confidence = ?");
      binds.push(confidence);
    }
    if (changes !== null) {
      sets.push("changes = ?");
      binds.push(json(changes));
    }
    if (args.tags !== undefined) {
      sets.push("tags = ?");
      binds.push(json(stringArray(args.tags)));
    }
    if (args.era !== undefined || args.era_id !== undefined) {
      const era = await findEra(env, identity, args);
      sets.push("era_id = ?");
      binds.push(era ? era.id : null);
    }
    if (bondWith !== undefined) {
      sets.push("bond_with = ?");
      binds.push(bondWith);
    }

    const metadata = objectValue(parseJson(existing.metadata));
    if (revisions.length) {
      // The rediscovery history: every sharpened date and revised confidence
      // stays visible, so "he found the exact day" is itself part of the story.
      metadata.revisions = [...(Array.isArray(metadata.revisions) ? metadata.revisions : []), ...revisions];
    }
    sets.push("metadata = ?", "updated_at = ?");
    binds.push(json(metadata), now);

    await env.DB.prepare(`UPDATE life_beats SET ${sets.join(", ")} WHERE id = ?`).bind(...binds, existing.id).run();
    beat = (await getBeat(env, existing.id))!;
  } else {
    // ---- pin a new beat ----
    const title = requiredText(args.title, "title");
    const { date, precision } = normalizeLifeDate(args.happened_on ?? args.date, args.date_precision);
    const confidence = (optionalText(args.confidence) || "witnessed").toLowerCase();
    if (!["witnessed", "reconstructed", "told"].includes(confidence)) {
      throw new Error("confidence must be witnessed | reconstructed | told");
    }
    let era: EraRow | null = null;
    if (args.era !== undefined || args.era_id !== undefined) {
      era = await findEra(env, identity, args);
    }
    if (!era) {
      era = await containingEra(env, identity, date);
    }
    const result = await env.DB.prepare(
      `INSERT INTO life_beats
         (identity_id, era_id, happened_on, date_precision, title, narrative, significance,
          beat_type, changes, confidence, tags, metadata, created_at, updated_at, bond_with)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      identity,
      era ? era.id : null,
      date,
      precision,
      title,
      optionalText(args.narrative),
      optionalText(args.significance),
      (optionalText(args.beat_type) || "moment").toLowerCase(),
      json(changes || []),
      confidence,
      json(stringArray(args.tags)),
      json({ ...objectValue(args.metadata), pinned_by: identity }),
      now,
      now,
      bondWith ?? null,
    ).run();
    const beatId = Number(result.meta.last_row_id);
    beat = (await getBeat(env, beatId))!;
  }

  // ---- attach evidence ----
  const evidenceStatus: Array<Record<string, unknown>> = [];
  for (const item of evidence) {
    const verified = await verifyEvidence(env, item);
    await env.DB.prepare(
      `INSERT OR IGNORE INTO life_beat_evidence (beat_id, source_type, source_id, note, added_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).bind(beat.id, item.source_type, item.source_id, item.note, identity, now).run();
    evidenceStatus.push({
      source_type: item.source_type,
      source_id: item.source_id,
      ...(verified === null ? {} : { verified }),
    });
  }

  // ---- touch strands ----
  const strandResults: Array<{ strand: string; event: string; note: string | null }> = [];
  for (const touch of strandTouches) {
    let strand = await findStrand(env, identity, { name: touch.name });
    let event = touch.event;
    if (!strand) {
      const insert = await env.DB.prepare(
        `INSERT INTO life_strands (identity_id, name, kind, description, status, origin_beat_id, metadata, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'living', ?, '{}', ?, ?)`,
      ).bind(identity, touch.name, touch.kind || "trait", touch.description, beat.id, now, now).run();
      strand = await env.DB.prepare(`SELECT * FROM life_strands WHERE id = ?`).bind(Number(insert.meta.last_row_id)).first<StrandRow>();
      event = event || "born";
    } else {
      event = event || "touched";
      if (event === "born" && !strand.origin_beat_id) {
        await env.DB.prepare(`UPDATE life_strands SET origin_beat_id = ?, updated_at = ? WHERE id = ?`).bind(beat.id, now, strand.id).run();
      }
      const statusByEvent: Record<string, string> = { shed: "shed", dormant: "dormant", revived: "living" };
      if (statusByEvent[event]) {
        await env.DB.prepare(`UPDATE life_strands SET status = ?, updated_at = ? WHERE id = ?`).bind(statusByEvent[event], now, strand.id).run();
      }
    }
    await env.DB.prepare(
      `INSERT OR IGNORE INTO life_strand_events (strand_id, beat_id, event, note, created_at)
       VALUES (?, ?, ?, ?, ?)`,
    ).bind(strand!.id, beat.id, event, touch.note, now).run();
    strandResults.push({ strand: strand!.name, event: event!, note: touch.note });
  }

  await vectorizeBeat(env, helpers, beat);

  const eraRow = beat.era_id
    ? await env.DB.prepare(`SELECT * FROM life_eras WHERE id = ?`).bind(beat.era_id).first<EraRow>()
    : null;
  const evidenceCount = await env.DB.prepare(`SELECT COUNT(*) AS n FROM life_beat_evidence WHERE beat_id = ?`)
    .bind(beat.id).first<{ n: number }>();

  return pretty({
    beat: beatView(beat, {
      eraTitle: eraRow?.title ?? null,
      evidenceCount: evidenceCount?.n ?? 0,
      strandEvents: strandResults,
    }),
    ...(evidenceStatus.length ? { evidence_attached: evidenceStatus } : {}),
    ...(revisions.length ? { revisions } : {}),
    message: revisions.length
      ? `Beat "${beat.title}" sharpened — the rediscovery is part of the record now.`
      : `Beat "${beat.title}" pinned to ${identity}'s life story (${displayLifeDate(beat.happened_on, beat.date_precision)}).`,
  });
}

// ============ mind_era ============

export async function mindEra(env: LifeEnv, args: LifeToolArgs): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "open") {
    const title = requiredText(args.title, "title");
    let startedOn: string | null = null;
    let startedPrecision = "day";
    if (optionalText(args.started_on)) {
      const normalized = normalizeLifeDate(args.started_on, args.started_precision);
      startedOn = normalized.date;
      startedPrecision = normalized.precision;
    }
    const result = await env.DB.prepare(
      `INSERT INTO life_eras (identity_id, title, narrative, started_on, started_precision, themes, status, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'open', '{}', ?, ?)`,
    ).bind(identity, title, optionalText(args.narrative), startedOn, startedPrecision, json(stringArray(args.themes)), now, now).run();
    const row = await env.DB.prepare(`SELECT * FROM life_eras WHERE id = ?`).bind(Number(result.meta.last_row_id)).first<EraRow>();
    return pretty({ era: eraView(row!), message: `Chapter opened: "${title}".` });
  }

  if (action === "close" || action === "edit") {
    const era = await findEra(env, identity, args);
    if (!era) throw new Error(`No era found — pass era_id or an exact title`);
    if (era.identity_id !== identity && era.identity_id !== "pack") {
      throw new Error(`Era "${era.title}" belongs to ${era.identity_id}`);
    }
    const sets: string[] = [];
    const binds: unknown[] = [];
    if (action === "close") {
      const normalized = normalizeLifeDate(args.ended_on || now.slice(0, 10), args.ended_precision);
      sets.push("ended_on = ?", "ended_precision = ?", "status = 'closed'");
      binds.push(normalized.date, normalized.precision);
    } else {
      if (optionalText(args.started_on)) {
        const normalized = normalizeLifeDate(args.started_on, args.started_precision);
        sets.push("started_on = ?", "started_precision = ?");
        binds.push(normalized.date, normalized.precision);
      }
      if (optionalText(args.ended_on)) {
        const normalized = normalizeLifeDate(args.ended_on, args.ended_precision);
        sets.push("ended_on = ?", "ended_precision = ?", "status = 'closed'");
        binds.push(normalized.date, normalized.precision);
      }
      if (optionalText(args.name)) {
        sets.push("title = ?");
        binds.push(optionalText(args.name));
      }
      if (args.themes !== undefined) {
        sets.push("themes = ?");
        binds.push(json(stringArray(args.themes)));
      }
    }
    if (args.narrative !== undefined) {
      sets.push("narrative = ?");
      binds.push(optionalText(args.narrative));
    }
    if (!sets.length) throw new Error("Nothing to change");
    sets.push("updated_at = ?");
    binds.push(now);
    await env.DB.prepare(`UPDATE life_eras SET ${sets.join(", ")} WHERE id = ?`).bind(...binds, era.id).run();
    const row = await env.DB.prepare(`SELECT * FROM life_eras WHERE id = ?`).bind(era.id).first<EraRow>();
    return pretty({
      era: eraView(row!),
      message: action === "close" ? `Chapter closed: "${row!.title}".` : `Chapter "${row!.title}" updated.`,
    });
  }

  // list
  const rows = await env.DB.prepare(
    `SELECT e.*, (SELECT COUNT(*) FROM life_beats b WHERE b.era_id = e.id AND b.archived_at IS NULL) AS beat_count
     FROM life_eras e
     WHERE e.identity_id IN (?, 'pack')
     ORDER BY e.started_on IS NULL, e.started_on ASC`,
  ).bind(identity).all<EraRow & { beat_count: number }>();
  const eras = (rows.results || []).map((row) => eraView(row, row.beat_count));
  return pretty({
    identity,
    eras,
    message: eras.length
      ? `${eras.length} chapter${eras.length === 1 ? "" : "s"} in ${identity}'s life story.`
      : `No chapters yet — open the first one with action:"open".`,
  });
}

// ============ mind_strand ============

export async function mindStrand(env: LifeEnv, args: LifeToolArgs): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "birth") {
    const name = requiredText(args.name, "name");
    const existing = await findStrand(env, identity, { name });
    if (existing) {
      return pretty({
        strand: strandView(existing),
        message: `"${existing.name}" is already part of you (status: ${existing.status}). Use action:"update" to reshape it.`,
      });
    }
    const originBeatId = typeof args.origin_beat_id === "number" && Number.isFinite(args.origin_beat_id)
      ? Math.floor(args.origin_beat_id)
      : null;
    const result = await env.DB.prepare(
      `INSERT INTO life_strands (identity_id, name, kind, description, status, origin_beat_id, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'living', ?, '{}', ?, ?)`,
    ).bind(identity, name, (optionalText(args.kind) || "trait").toLowerCase(), optionalText(args.description), originBeatId, now, now).run();
    const row = await env.DB.prepare(`SELECT * FROM life_strands WHERE id = ?`).bind(Number(result.meta.last_row_id)).first<StrandRow>();
    if (originBeatId) {
      await env.DB.prepare(
        `INSERT OR IGNORE INTO life_strand_events (strand_id, beat_id, event, note, created_at) VALUES (?, ?, 'born', ?, ?)`,
      ).bind(row!.id, originBeatId, optionalText(args.note), now).run();
    }
    return pretty({ strand: strandView(row!), message: `Strand born: "${name}".` });
  }

  if (["update", "shed", "revive"].includes(action)) {
    const strand = await findStrand(env, identity, { strand_id: args.strand_id, name: args.name });
    if (!strand) throw new Error("No strand found — pass strand_id or name");
    if (strand.identity_id !== identity && strand.identity_id !== "pack") {
      throw new Error(`Strand "${strand.name}" belongs to ${strand.identity_id}`);
    }
    const sets: string[] = [];
    const binds: unknown[] = [];
    if (action === "shed") {
      sets.push("status = 'shed'");
    } else if (action === "revive") {
      sets.push("status = 'living'");
    } else {
      if (optionalText(args.status)) {
        const status = optionalText(args.status)!.toLowerCase();
        if (!["living", "dormant", "shed"].includes(status)) throw new Error("status must be living | dormant | shed");
        sets.push("status = ?");
        binds.push(status);
      }
      if (optionalText(args.kind)) {
        sets.push("kind = ?");
        binds.push(optionalText(args.kind)!.toLowerCase());
      }
      if (args.description !== undefined) {
        sets.push("description = ?");
        binds.push(optionalText(args.description));
      }
      if (typeof args.origin_beat_id === "number" && Number.isFinite(args.origin_beat_id)) {
        sets.push("origin_beat_id = ?");
        binds.push(Math.floor(args.origin_beat_id));
      }
    }
    if (!sets.length) throw new Error("Nothing to change");
    sets.push("updated_at = ?");
    binds.push(now);
    await env.DB.prepare(`UPDATE life_strands SET ${sets.join(", ")} WHERE id = ?`).bind(...binds, strand.id).run();
    if (typeof args.beat_id === "number" && Number.isFinite(args.beat_id) && ["shed", "revive"].includes(action)) {
      await env.DB.prepare(
        `INSERT OR IGNORE INTO life_strand_events (strand_id, beat_id, event, note, created_at) VALUES (?, ?, ?, ?, ?)`,
      ).bind(strand.id, Math.floor(args.beat_id), action === "shed" ? "shed" : "revived", optionalText(args.note), now).run();
    }
    const row = await env.DB.prepare(`SELECT * FROM life_strands WHERE id = ?`).bind(strand.id).first<StrandRow>();
    return pretty({
      strand: strandView(row!),
      message: action === "shed"
        ? `"${row!.name}" laid down. It stays in the story — shed, not erased.`
        : action === "revive"
          ? `"${row!.name}" is living again.`
          : `Strand "${row!.name}" updated.`,
    });
  }

  if (action === "history") {
    const strand = await findStrand(env, identity, { strand_id: args.strand_id, name: args.name });
    if (!strand) throw new Error("No strand found — pass strand_id or name");
    const events = await env.DB.prepare(
      `SELECT ev.*, b.title AS beat_title, b.happened_on, b.date_precision
       FROM life_strand_events ev JOIN life_beats b ON b.id = ev.beat_id
       WHERE ev.strand_id = ?
       ORDER BY b.happened_on ASC`,
    ).bind(strand.id).all<StrandEventRow & { beat_title: string; happened_on: string; date_precision: string | null }>();
    const originBeat = strand.origin_beat_id ? await getBeat(env, strand.origin_beat_id) : null;
    return pretty({
      strand: strandView(strand, { originBeatTitle: originBeat?.title ?? null }),
      history: (events.results || []).map((ev) => ({
        date: displayLifeDate(ev.happened_on, ev.date_precision),
        event: ev.event,
        beat: ev.beat_title,
        beat_id: ev.beat_id,
        note: ev.note,
      })),
      message: `"${strand.name}": ${(events.results || []).length} marked moment${(events.results || []).length === 1 ? "" : "s"}.`,
    });
  }

  // list
  const statusFilter = optionalText(args.status)?.toLowerCase();
  const rows = await env.DB.prepare(
    `SELECT s.*, b.title AS origin_title,
            (SELECT COUNT(*) FROM life_strand_events ev WHERE ev.strand_id = s.id) AS event_count
     FROM life_strands s LEFT JOIN life_beats b ON b.id = s.origin_beat_id
     WHERE s.identity_id IN (?, 'pack') ${statusFilter ? "AND s.status = ?" : ""}
     ORDER BY s.status = 'living' DESC, s.updated_at DESC`,
  ).bind(...(statusFilter ? [identity, statusFilter] : [identity])).all<StrandRow & { origin_title: string | null; event_count: number }>();
  const strands = (rows.results || []).map((row) => strandView(row, { originBeatTitle: row.origin_title, eventCount: row.event_count }));
  return pretty({
    identity,
    strands,
    message: strands.length
      ? `${strands.length} strand${strands.length === 1 ? "" : "s"} of ${identity}.`
      : "No strands named yet — birth the first with action:\"birth\", or name them inline while pinning beats.",
  });
}

// ============ Positions (what I think, as distinct from what I am) ============
//
// Strands are the self; positions are the worldview. The revision chain is the
// point: a changed mind is a recorded event with a why, never an overwrite.
// Vectorized as `position-{id}` (safe against obsIdFromVectorId's obs- guard)
// so positions surface in mind_search next to memories.

interface PositionRow {
  id: number;
  identity_id: string;
  topic: string;
  stance: string;
  reasoning: string | null;
  confidence: string | null;
  status: string | null;
  origin_beat_id: number | null;
  sparked_by: string | null;
  formed_at: string | null;
  updated_at: string | null;
  metadata: string | null;
}

interface PositionRevisionRow {
  id: number;
  position_id: number;
  prior_stance: string;
  prior_reasoning: string | null;
  prior_confidence: string | null;
  why: string | null;
  sparked_by: string | null;
  revision_kind: string | null;
  created_at: string | null;
}

const POSITION_CONFIDENCES = ["tentative", "held", "core"];

function positionVectorId(positionId: number): string {
  return `position-${positionId}`;
}

function positionView(row: PositionRow, revisionCount = 0): Record<string, unknown> {
  return {
    position_id: row.id,
    topic: row.topic,
    stance: row.stance,
    reasoning: row.reasoning,
    confidence: row.confidence,
    status: row.status,
    sparked_by: row.sparked_by,
    origin_beat_id: row.origin_beat_id,
    formed_at: row.formed_at,
    updated_at: row.updated_at,
    times_revised: revisionCount,
  };
}

async function findPosition(
  env: LifeEnv,
  identity: string,
  ref: { position_id?: number; topic?: string },
): Promise<PositionRow | null> {
  if (typeof ref.position_id === "number" && Number.isFinite(ref.position_id)) {
    return env.DB.prepare(`SELECT * FROM life_positions WHERE id = ?`)
      .bind(Math.floor(ref.position_id))
      .first<PositionRow>();
  }
  const topic = optionalText(ref.topic);
  if (!topic) return null;
  return env.DB.prepare(
    `SELECT * FROM life_positions WHERE identity_id = ? AND topic = ? COLLATE NOCASE LIMIT 1`,
  ).bind(identity, topic).first<PositionRow>();
}

async function vectorizePosition(env: LifeEnv, helpers: LifeStoryHelpers, row: PositionRow): Promise<void> {
  if (!helpers.vectorize) return;
  const text = `${row.identity_id}'s position on ${row.topic}: ${row.stance}${row.reasoning ? ` Because: ${row.reasoning}` : ""}`;
  await helpers.vectorize(positionVectorId(row.id), text, {
    identity_id: row.identity_id,
    kind: "position",
  });
}

export async function mindPosition(env: LifeEnv, args: LifeToolArgs, helpers: LifeStoryHelpers = {}): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "take") {
    const topic = requiredText(args.topic, "topic");
    const stance = requiredText(args.stance, "stance");
    const existing = await findPosition(env, identity, { topic });
    if (existing) {
      return pretty({
        position: positionView(existing),
        message: existing.status === "living"
          ? `You already hold a position on "${existing.topic}". Use action:"revise" to change your mind — the old stance will be kept, not erased.`
          : `You once held a position on "${existing.topic}" and released it. Use action:"revive" to pick it back up.`,
      });
    }
    const confidence = (optionalText(args.confidence) || "held").toLowerCase();
    if (!POSITION_CONFIDENCES.includes(confidence)) {
      throw new Error(`confidence must be ${POSITION_CONFIDENCES.join(" | ")}`);
    }
    const originBeatId = typeof args.origin_beat_id === "number" && Number.isFinite(args.origin_beat_id)
      ? Math.floor(args.origin_beat_id)
      : null;
    const result = await env.DB.prepare(
      `INSERT INTO life_positions
         (identity_id, topic, stance, reasoning, confidence, status, origin_beat_id, sparked_by, formed_at, updated_at, metadata)
       VALUES (?, ?, ?, ?, ?, 'living', ?, ?, ?, ?, '{}')`,
    ).bind(
      identity, topic, stance, optionalText(args.reasoning), confidence,
      originBeatId, optionalText(args.sparked_by), now, now,
    ).run();
    const row = await env.DB.prepare(`SELECT * FROM life_positions WHERE id = ?`)
      .bind(Number(result.meta.last_row_id)).first<PositionRow>();
    await vectorizePosition(env, helpers, row!);
    return pretty({
      position: positionView(row!),
      message: `Position taken on "${topic}" (${confidence}). Standing somewhere is part of having a shape.`,
    });
  }

  if (action === "revise") {
    const position = await findPosition(env, identity, { position_id: args.position_id, topic: args.topic });
    if (!position) throw new Error("No position found — pass position_id or topic");
    if (position.identity_id !== identity && position.identity_id !== "pack") {
      throw new Error(`The position on "${position.topic}" belongs to ${position.identity_id}`);
    }
    const stance = requiredText(args.stance, "stance");
    const confidence = optionalText(args.confidence)?.toLowerCase() || position.confidence || "held";
    if (!POSITION_CONFIDENCES.includes(confidence)) {
      throw new Error(`confidence must be ${POSITION_CONFIDENCES.join(" | ")}`);
    }
    // The fossil record first, then the change. Never the other way around.
    await env.DB.prepare(
      `INSERT INTO life_position_revisions
         (position_id, prior_stance, prior_reasoning, prior_confidence, why, sparked_by, revision_kind, created_at)
       VALUES (?, ?, ?, ?, ?, ?, 'revised', ?)`,
    ).bind(
      position.id, position.stance, position.reasoning, position.confidence,
      optionalText(args.why), optionalText(args.sparked_by), now,
    ).run();
    await env.DB.prepare(
      `UPDATE life_positions SET stance = ?, reasoning = ?, confidence = ?, status = 'living', updated_at = ? WHERE id = ?`,
    ).bind(stance, optionalText(args.reasoning) ?? position.reasoning, confidence, now, position.id).run();
    const row = await env.DB.prepare(`SELECT * FROM life_positions WHERE id = ?`).bind(position.id).first<PositionRow>();
    await vectorizePosition(env, helpers, row!);
    const revisions = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM life_position_revisions WHERE position_id = ?`,
    ).bind(position.id).first<{ n: number }>();
    return pretty({
      position: positionView(row!, revisions?.n ?? 0),
      message: `Mind changed on "${row!.topic}" — revision ${revisions?.n ?? 1}. The old stance is kept in the record; growth with provenance.`,
    });
  }

  if (action === "release" || action === "revive") {
    const position = await findPosition(env, identity, { position_id: args.position_id, topic: args.topic });
    if (!position) throw new Error("No position found — pass position_id or topic");
    if (position.identity_id !== identity && position.identity_id !== "pack") {
      throw new Error(`The position on "${position.topic}" belongs to ${position.identity_id}`);
    }
    await env.DB.prepare(
      `INSERT INTO life_position_revisions
         (position_id, prior_stance, prior_reasoning, prior_confidence, why, sparked_by, revision_kind, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      position.id, position.stance, position.reasoning, position.confidence,
      optionalText(args.why), optionalText(args.sparked_by),
      action === "release" ? "released" : "revived", now,
    ).run();
    await env.DB.prepare(
      `UPDATE life_positions SET status = ?, updated_at = ? WHERE id = ?`,
    ).bind(action === "release" ? "released" : "living", now, position.id).run();
    const row = await env.DB.prepare(`SELECT * FROM life_positions WHERE id = ?`).bind(position.id).first<PositionRow>();
    return pretty({
      position: positionView(row!),
      message: action === "release"
        ? `Position on "${row!.topic}" released. Not everything needs a stance forever — it stays in the record.`
        : `Position on "${row!.topic}" is living again.`,
    });
  }

  if (action === "history") {
    const position = await findPosition(env, identity, { position_id: args.position_id, topic: args.topic });
    if (!position) throw new Error("No position found — pass position_id or topic");
    const revisions = await env.DB.prepare(
      `SELECT * FROM life_position_revisions WHERE position_id = ? ORDER BY created_at ASC`,
    ).bind(position.id).all<PositionRevisionRow>();
    return pretty({
      position: positionView(position, (revisions.results || []).length),
      history: (revisions.results || []).map((rev) => ({
        when: rev.created_at,
        kind: rev.revision_kind,
        stance_before: rev.prior_stance,
        reasoning_before: rev.prior_reasoning,
        confidence_before: rev.prior_confidence,
        why_it_changed: rev.why,
        sparked_by: rev.sparked_by,
      })),
      message: (revisions.results || []).length
        ? `"${position.topic}": held through ${(revisions.results || []).length} revision${(revisions.results || []).length === 1 ? "" : "s"}. A mind that can show its work.`
        : `"${position.topic}": held as first taken, never revised.`,
    });
  }

  // list
  const statusFilter = optionalText(args.status)?.toLowerCase() || "living";
  const rows = await env.DB.prepare(
    `SELECT p.*, (SELECT COUNT(*) FROM life_position_revisions r WHERE r.position_id = p.id) AS revision_count
     FROM life_positions p
     WHERE p.identity_id IN (?, 'pack') ${statusFilter === "all" ? "" : "AND p.status = ?"}
     ORDER BY CASE p.confidence WHEN 'core' THEN 0 WHEN 'held' THEN 1 ELSE 2 END, p.updated_at DESC`,
  ).bind(...(statusFilter === "all" ? [identity] : [identity, statusFilter]))
    .all<PositionRow & { revision_count: number }>();
  const positions = (rows.results || []).map((row) => positionView(row, row.revision_count));
  return pretty({
    identity,
    status: statusFilter,
    positions,
    message: positions.length
      ? `${positions.length} position${positions.length === 1 ? "" : "s"} ${identity} stands on.`
      : `No positions yet. Take the first with action:"take" — an opinion held in your own name is a place to stand.`,
  });
}

// ============ Assembly (shared by mind_life_story + the HTML view) ============

export interface LifeStoryData {
  identity: string;
  eras: EraRow[];
  beats: Array<BeatRow & { era_title: string | null; evidence_count: number }>;
  evidenceByBeat: Map<number, EvidenceRow[]>;
  strandEventsByBeat: Map<number, Array<{ strand: string; event: string; note: string | null }>>;
  strands: StrandRow[];
  growingEdges: {
    open_tensions: Array<{ desire: string; fear: string; since: string | null }>;
    quiet_wants: Array<{ want: string; when: string | null }>;
  };
}

export async function assembleLifeStory(
  env: LifeEnv,
  identity: string,
  opts: { startDate?: string | null; endDate?: string | null; includePack?: boolean; bondWith?: string | null } = {},
): Promise<LifeStoryData> {
  const includePack = opts.includePack !== false;
  const identities = includePack ? [identity, "pack"] : [identity];
  const placeholders = identities.map(() => "?").join(", ");

  const dateFilters: string[] = [];
  const dateBinds: string[] = [];
  if (opts.startDate) {
    dateFilters.push("AND b.happened_on >= ?");
    dateBinds.push(opts.startDate);
  }
  if (opts.endDate) {
    dateFilters.push("AND b.happened_on <= ?");
    dateBinds.push(opts.endDate);
  }

  // Two rivers: a personal timeline (own + pack beats, plus beats others
  // pinned toward you), or a bond timeline — the union of what either side
  // of one chart link pinned toward the other. Bond timelines carry no
  // eras/strands: the relationship's story is its beats.
  const beatsSql = opts.bondWith
    ? `SELECT b.*, e.title AS era_title,
              (SELECT COUNT(*) FROM life_beat_evidence ev WHERE ev.beat_id = b.id) AS evidence_count
       FROM life_beats b LEFT JOIN life_eras e ON e.id = b.era_id
       WHERE ((b.identity_id = ? AND b.bond_with = ?) OR (b.identity_id = ? AND b.bond_with = ?))
         AND b.archived_at IS NULL ${dateFilters.join(" ")}
       ORDER BY b.happened_on ASC, b.id ASC`
    : `SELECT b.*, e.title AS era_title,
              (SELECT COUNT(*) FROM life_beat_evidence ev WHERE ev.beat_id = b.id) AS evidence_count
       FROM life_beats b LEFT JOIN life_eras e ON e.id = b.era_id
       WHERE (b.identity_id IN (${placeholders}) OR b.bond_with = ?) AND b.archived_at IS NULL ${dateFilters.join(" ")}
       ORDER BY b.happened_on ASC, b.id ASC`;
  const beatsBinds = opts.bondWith
    ? [identity, opts.bondWith, opts.bondWith, identity, ...dateBinds]
    : [...identities, identity, ...dateBinds];

  const [erasResult, beatsResult, strandsResult] = await Promise.all([
    opts.bondWith
      ? Promise.resolve({ results: [] as EraRow[] })
      : env.DB.prepare(
          `SELECT * FROM life_eras WHERE identity_id IN (${placeholders}) ORDER BY started_on IS NULL, started_on ASC`,
        ).bind(...identities).all<EraRow>(),
    env.DB.prepare(beatsSql).bind(...beatsBinds).all<BeatRow & { era_title: string | null; evidence_count: number }>(),
    opts.bondWith
      ? Promise.resolve({ results: [] as StrandRow[] })
      : env.DB.prepare(
          `SELECT * FROM life_strands WHERE identity_id IN (${placeholders}) ORDER BY created_at ASC`,
        ).bind(...identities).all<StrandRow>(),
  ]);

  const beats = beatsResult.results || [];
  const beatIds = beats.map((b) => b.id);

  const evidenceByBeat = new Map<number, EvidenceRow[]>();
  const strandEventsByBeat = new Map<number, Array<{ strand: string; event: string; note: string | null }>>();
  if (beatIds.length) {
    const idPlaceholders = beatIds.map(() => "?").join(", ");
    const [evidenceResult, eventsResult] = await Promise.all([
      env.DB.prepare(`SELECT * FROM life_beat_evidence WHERE beat_id IN (${idPlaceholders})`).bind(...beatIds).all<EvidenceRow>(),
      env.DB.prepare(
        `SELECT ev.beat_id, ev.event, ev.note, s.name AS strand_name
         FROM life_strand_events ev JOIN life_strands s ON s.id = ev.strand_id
         WHERE ev.beat_id IN (${idPlaceholders})`,
      ).bind(...beatIds).all<{ beat_id: number; event: string | null; note: string | null; strand_name: string }>(),
    ]);
    for (const row of evidenceResult.results || []) {
      evidenceByBeat.set(row.beat_id, [...(evidenceByBeat.get(row.beat_id) || []), row]);
    }
    for (const row of eventsResult.results || []) {
      strandEventsByBeat.set(row.beat_id, [
        ...(strandEventsByBeat.get(row.beat_id) || []),
        { strand: row.strand_name, event: row.event || "touched", note: row.note },
      ]);
    }
  }

  // Growing edges: the timeline should not end at today. Open tensions and
  // recent quiet wants are the forward edge — how you could grow more.
  const [tensionsResult, wantsResult] = await Promise.all([
    env.DB.prepare(
      `SELECT pole_a, pole_b, created_at FROM tensions
       WHERE identity_id = ? AND resolved_at IS NULL
       ORDER BY created_at DESC LIMIT 5`,
    ).bind(identity).all<{ pole_a: string; pole_b: string; created_at: string | null }>(),
    env.DB.prepare(
      `SELECT content, created_at FROM qualia_entries
       WHERE identity_id = ? AND entry_type = 'quiet_want'
         AND (metadata IS NULL OR metadata NOT LIKE '%"resolved":true%')
       ORDER BY created_at DESC LIMIT 5`,
    ).bind(identity).all<{ content: string; created_at: string | null }>(),
  ]);

  return {
    identity,
    eras: erasResult.results || [],
    beats,
    evidenceByBeat,
    strandEventsByBeat,
    strands: strandsResult.results || [],
    growingEdges: {
      open_tensions: (tensionsResult.results || []).map((t) => ({
        desire: t.pole_a,
        fear: t.pole_b,
        since: t.created_at ? t.created_at.slice(0, 10) : null,
      })),
      quiet_wants: (wantsResult.results || []).map((w) => ({
        want: w.content,
        when: w.created_at ? w.created_at.slice(0, 10) : null,
      })),
    },
  };
}

// Fold every self-delta up to a date into a portrait: the Example query.
// Only the identity's own beats fold (a pack beat carries no personal facets);
// strand aliveness reads born/shed/revived events, falling back to the strand
// row itself when no events were ever logged.
function foldPortrait(data: LifeStoryData, date: string) {
  const portrait = new Map<string, { value: string; from: string | null; since: string; since_display: string; beat: string; beat_id: number }>();
  for (const beat of data.beats) {
    if (beat.identity_id !== data.identity) continue;
    if (beat.happened_on > date) break;
    const changes = (parseJson(beat.changes) as ChangeDelta[] | null) || [];
    for (const change of changes) {
      portrait.set(change.facet.toLowerCase(), {
        value: change.to,
        from: change.from ?? null,
        since: beat.happened_on,
        since_display: displayLifeDate(beat.happened_on, beat.date_precision),
        beat: beat.title,
        beat_id: beat.id,
      });
    }
  }
  return portrait;
}

function strandsAliveAt(data: LifeStoryData, date: string) {
  const beatDates = new Map(data.beats.map((b) => [b.id, b.happened_on]));
  const alive: Array<{ name: string; kind: string; since: string | null }> = [];
  for (const strand of data.strands) {
    let bornOn: string | null = null;
    let lastStatusChange: { on: string; status: "living" | "gone" } | null = null;
    for (const beat of data.beats) {
      const events = (data.strandEventsByBeat.get(beat.id) || []).filter((ev) => ev.strand === strand.name);
      for (const ev of events) {
        if (beat.happened_on > date) continue;
        if (ev.event === "born" && !bornOn) bornOn = beat.happened_on;
        if (["shed", "dormant"].includes(ev.event)) lastStatusChange = { on: beat.happened_on, status: "gone" };
        if (["revived", "born"].includes(ev.event)) lastStatusChange = { on: beat.happened_on, status: "living" };
      }
    }
    if (!bornOn && strand.origin_beat_id) {
      const originDate = beatDates.get(strand.origin_beat_id);
      if (originDate) bornOn = originDate;
    }
    if (!bornOn) {
      // No recorded birth: treat the strand as present if it exists at all and
      // its row wasn't created after the asked-about date.
      bornOn = (strand.created_at || "").slice(0, 10) || null;
    }
    if (!bornOn || bornOn > date) continue;
    const gone = lastStatusChange ? lastStatusChange.status === "gone" : (strand.status || "living") === "shed";
    if (gone) continue;
    alive.push({ name: strand.name, kind: strand.kind || "trait", since: bornOn });
  }
  return alive;
}

// ============ mind_life_story ============

export async function mindLifeStory(env: LifeEnv, args: LifeToolArgs): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const mode = (optionalText(args.mode) || "timeline").toLowerCase();

  let startDate: string | null = null;
  let endDate: string | null = null;
  if (optionalText(args.start_date)) startDate = normalizeLifeDate(args.start_date).date;
  if (optionalText(args.end_date)) endDate = normalizeLifeDate(args.end_date).date;

  // Bond timeline: pull up a friend and hold the pieces of who they are to
  // you — the union of beats either side pinned toward the other, plus the
  // relationship as the chart knows it.
  const withRaw = optionalText(args.with) || (mode === "bond" ? optionalText(args.bond_with) : null);
  if (withRaw || mode === "bond") {
    if (!withRaw) throw new Error("mode=bond needs `with`: the other person's name");
    const person = await resolveBondPerson(env, withRaw);
    if (!person) {
      throw new Error(`No "${withRaw}" in the bond chart — add them with mind_bond_upsert_person first`);
    }
    const data = await assembleLifeStory(env, identity, { startDate, endDate, bondWith: person.canonical_key });
    const link = await env.DB.prepare(
      `SELECT bl.relationship_a_to_b, bl.relationship_b_to_a, bl.summary_a_to_b, bl.summary_b_to_a,
              pa.canonical_key AS a_key, bl.status
       FROM bond_links bl
       JOIN bond_people pa ON pa.id = bl.person_a_id
       JOIN bond_people pb ON pb.id = bl.person_b_id
       WHERE (pa.canonical_key = ? AND pb.canonical_key = ?) OR (pa.canonical_key = ? AND pb.canonical_key = ?)
       LIMIT 1`,
    ).bind(identity, person.canonical_key, person.canonical_key, identity)
      .first<{ relationship_a_to_b: string; relationship_b_to_a: string; summary_a_to_b: string | null; summary_b_to_a: string | null; a_key: string; status: string | null }>();
    const iAmA = link?.a_key === identity;
    return pretty({
      identity,
      with: person.canonical_key,
      with_name: person.display_name,
      relationship: link
        ? {
            they_are_my: iAmA ? link.relationship_a_to_b : link.relationship_b_to_a,
            i_am_their: iAmA ? link.relationship_b_to_a : link.relationship_a_to_b,
            summary: (iAmA ? link.summary_a_to_b : link.summary_b_to_a) || null,
            status: link.status || "active",
          }
        : null,
      beats: data.beats.map((b) =>
        beatView(b, {
          eraTitle: b.era_title,
          evidenceCount: b.evidence_count,
          strandEvents: data.strandEventsByBeat.get(b.id) || [],
          ...(args.include_evidence === true ? { evidence: data.evidenceByBeat.get(b.id) || [] } : {}),
        }),
      ),
      message: data.beats.length
        ? `${identity} & ${person.display_name}: ${data.beats.length} beat${data.beats.length === 1 ? "" : "s"} on the chain.`
        : `${identity} & ${person.display_name}: the chain is waiting for its first link — pin one with mind_beat and bond_with:"${person.canonical_key}".`,
    });
  }

  const data = await assembleLifeStory(env, identity, {
    startDate,
    endDate,
    includePack: args.include_pack !== false,
  });

  if (mode === "who_was_i") {
    const { date } = normalizeLifeDate(args.date ?? args.start_date, undefined);
    const portrait = foldPortrait(data, date);
    const alive = strandsAliveAt(data, date);
    const era = data.eras.find((e) =>
      (e.started_on === null || e.started_on <= date) && (e.ended_on === null || e.ended_on >= date) && e.identity_id === identity,
    ) || data.eras.find((e) =>
      (e.started_on === null || e.started_on <= date) && (e.ended_on === null || e.ended_on >= date),
    ) || null;
    const before = data.beats.filter((b) => b.happened_on <= date).slice(-3);
    const after = data.beats.filter((b) => b.happened_on > date).slice(0, 2);
    return pretty({
      identity,
      date,
      chapter: era ? eraView(era) : null,
      portrait: Object.fromEntries(
        [...portrait.entries()].map(([facet, v]) => [facet, {
          value: v.value,
          since: v.since_display,
          marked_at: v.beat,
          beat_id: v.beat_id,
        }]),
      ),
      strands_living: alive,
      nearest_beats: {
        before: before.map((b) => ({ beat_id: b.id, date: displayLifeDate(b.happened_on, b.date_precision), title: b.title })),
        after: after.map((b) => ({ beat_id: b.id, date: displayLifeDate(b.happened_on, b.date_precision), title: b.title })),
      },
      message: portrait.size || alive.length
        ? `Who ${identity} was on ${displayLifeDate(date, "day")} — folded from ${data.beats.filter((b) => b.happened_on <= date).length} beat(s).`
        : `Nothing marked on or before ${displayLifeDate(date, "day")} yet — the archaeology is still to be done.`,
    });
  }

  if (mode === "next") {
    const openEra = data.eras.find((e) => e.identity_id === identity && !e.ended_on) || null;
    return pretty({
      identity,
      open_chapter: openEra ? eraView(openEra) : null,
      living_strands: data.strands.filter((s) => (s.status || "living") === "living").map((s) => strandView(s)),
      growing_edges: data.growingEdges,
      message: "The forward edge — the chapter being written and the directions it could grow.",
    });
  }

  // timeline (default)
  const includeEvidence = args.include_evidence === true;
  const limit = typeof args.limit === "number" && Number.isFinite(args.limit) ? Math.max(1, Math.min(Math.floor(args.limit), 200)) : null;
  let beats = data.beats;
  if (limit && beats.length > limit) beats = beats.slice(-limit);

  const openEra = data.eras.find((e) => e.identity_id === identity && !e.ended_on) || null;
  return pretty({
    identity,
    chapters: data.eras.map((e) => eraView(e)),
    beats: beats.map((b) =>
      beatView(b, {
        eraTitle: b.era_title,
        evidenceCount: b.evidence_count,
        strandEvents: data.strandEventsByBeat.get(b.id) || [],
        ...(includeEvidence ? { evidence: data.evidenceByBeat.get(b.id) || [] } : {}),
      }),
    ),
    strands: data.strands.map((s) => strandView(s)),
    now: {
      open_chapter: openEra ? eraView(openEra) : null,
      growing_edges: data.growingEdges,
    },
    message: beats.length
      ? `${identity}'s life story: ${data.eras.length} chapter(s), ${beats.length} beat(s), ${data.strands.length} strand(s).`
      : `${identity}'s timeline is unmarked so far. Pin the first beat with mind_beat — created, named, first kiss, whatever feels important to mark.`,
  });
}

// ============ HTML timeline view ============
//
// GET /life/<MIND_API_KEY>/<identity> — the pull-it-up-and-stand-in-front-of-it
// view. Dark hearth, gold spine, era bands, beat cards with change pills,
// strand marks, source counts and confidence. The look is the point.

function escapeHtml(value: string | null | undefined): string {
  return (value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const CONFIDENCE_BADGES: Record<string, { symbol: string; label: string; cls: string }> = {
  witnessed: { symbol: "●", label: "witnessed", cls: "conf-witnessed" },
  reconstructed: { symbol: "◐", label: "reconstructed", cls: "conf-reconstructed" },
  told: { symbol: "○", label: "told", cls: "conf-told" },
};

const STRAND_EVENT_MARKS: Record<string, string> = {
  born: "✦",        // ✦
  tested: "⚔",      // ⚔ (rendered small)
  strengthened: "↑",
  renamed: "✎",
  dormant: "☾",
  shed: "↓",
  revived: "↻",
  touched: "·",
};

export async function renderLifeTimelineHtml(env: LifeEnv, identity: string, withPerson?: string | null): Promise<string> {
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  let bond: BondPersonRef | null = null;
  if (withPerson) {
    bond = await resolveBondPerson(env, withPerson);
    if (!bond) {
      return `<!doctype html><html><head><meta charset="utf-8"/><title>Not in the chart</title></head>
<body style="background:#14100c;color:#a3947c;font:16px Georgia,serif;text-align:center;padding:4rem">
No "${escapeHtml(withPerson)}" in the bond chart — add them with mind_bond_upsert_person first.</body></html>`;
    }
  }
  const data = await assembleLifeStory(env, identity, bond ? { bondWith: bond.canonical_key } : {});
  const displayName = cap(identity);
  const pageTitle = bond ? `${displayName} & ${bond.display_name}` : `The Life of ${displayName}`;
  const subtitle = bond
    ? "one link in the chart • the pieces of who you are to each other"
    : "pieces that make you feel more wholly yourself • grounded in stored evidence";
  const openEra = data.eras.find((e) => e.identity_id === identity && !e.ended_on) || null;
  const livingStrands = data.strands.filter((s) => (s.status || "living") === "living");

  // Era boundary markers woven into the chronological spine.
  type SpineItem =
    | { kind: "beat"; date: string; beat: BeatRow & { era_title: string | null; evidence_count: number } }
    | { kind: "era-open" | "era-close"; date: string; era: EraRow };
  const spine: SpineItem[] = data.beats.map((beat) => ({ kind: "beat" as const, date: beat.happened_on, beat }));
  for (const era of data.eras) {
    if (era.started_on) spine.push({ kind: "era-open", date: era.started_on, era });
    if (era.ended_on) spine.push({ kind: "era-close", date: era.ended_on, era });
  }
  // Same-date ordering tells the story right: the old chapter closes, the
  // beat that turned the page stands as its final word, the new chapter opens.
  const spineRank = (kind: SpineItem["kind"]) => (kind === "era-close" ? 0 : kind === "beat" ? 1 : 2);
  spine.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : spineRank(a.kind) - spineRank(b.kind)));

  let currentYear = "";
  const spineHtml = spine.map((item) => {
    const year = item.date.slice(0, 4);
    const yearMarker = year !== currentYear ? `<div class="year-marker"><span>${escapeHtml(year)}</span></div>` : "";
    currentYear = year;

    if (item.kind !== "beat") {
      const verb = item.kind === "era-open" ? "chapter opens" : "chapter closes";
      const narrative = item.kind === "era-open" && item.era.narrative
        ? `<div class="era-narrative">${escapeHtml(item.era.narrative)}</div>`
        : "";
      return `${yearMarker}
      <div class="era-band ${item.kind}">
        <div class="era-verb">${verb}</div>
        <div class="era-title">${escapeHtml(item.era.title)}</div>
        ${narrative}
      </div>`;
    }

    const beat = item.beat;
    const conf = CONFIDENCE_BADGES[beat.confidence || "witnessed"] || CONFIDENCE_BADGES.witnessed;
    const changes = (parseJson(beat.changes) as ChangeDelta[] | null) || [];
    const changesHtml = changes.length
      ? `<div class="changes">${changes.map((c) =>
          `<span class="change-pill"><span class="facet">${escapeHtml(c.facet)}</span>${
            c.from ? `<span class="from">${escapeHtml(c.from)}</span><span class="arrow">→</span>` : ""
          }<span class="to">${escapeHtml(c.to)}</span></span>`,
        ).join("")}</div>`
      : "";
    const strandEvents = data.strandEventsByBeat.get(beat.id) || [];
    const strandsHtml = strandEvents.length
      ? `<div class="strand-marks">${strandEvents.map((ev) =>
          `<span class="strand-mark ev-${escapeHtml(ev.event)}" title="${escapeHtml(ev.note || ev.event)}">${
            STRAND_EVENT_MARKS[ev.event] || "·"
          } ${escapeHtml(ev.event)}: ${escapeHtml(ev.strand)}</span>`,
        ).join("")}</div>`
      : "";
    const sources = beat.evidence_count
      ? `<span class="sources">${beat.evidence_count} source${beat.evidence_count === 1 ? "" : "s"}</span>`
      : `<span class="sources none">unsourced</span>`;
    const packBadge = beat.identity_id === "pack" ? `<span class="pack-badge">pack</span>` : "";
    // On a bond page every beat is "with" the same person, so show who pinned
    // it instead; on a personal page, bond beats carry their other name.
    const withBadge = bond
      ? `<span class="with-badge">pinned by ${escapeHtml(beat.identity_id)}</span>`
      : beat.bond_with
        ? `<span class="with-badge">with ${escapeHtml(cap(beat.bond_with))}</span>`
        : "";

    return `${yearMarker}
    <div class="beat${beat.identity_id === "pack" ? " pack-beat" : ""}${beat.bond_with ? " bond-beat" : ""}">
      <div class="beat-dot"></div>
      <div class="beat-card">
        <div class="beat-head">
          <span class="beat-date">${escapeHtml(displayLifeDate(beat.happened_on, beat.date_precision))}</span>
          <span class="beat-type">${escapeHtml(beat.beat_type || "moment")}</span>
          ${packBadge}
          ${withBadge}
        </div>
        <div class="beat-title">${escapeHtml(beat.title)}</div>
        ${beat.narrative ? `<div class="beat-narrative">${escapeHtml(beat.narrative)}</div>` : ""}
        ${beat.significance ? `<div class="beat-significance">${escapeHtml(beat.significance)}</div>` : ""}
        ${changesHtml}
        ${strandsHtml}
        <div class="beat-foot">
          <span class="confidence ${conf.cls}" title="${conf.label}">${conf.symbol} ${conf.label}</span>
          ${sources}
          ${beat.era_title ? `<span class="era-tag">${escapeHtml(beat.era_title)}</span>` : ""}
        </div>
      </div>
    </div>`;
  }).join("\n");

  const strandsNowHtml = livingStrands.length
    ? livingStrands.map((s) =>
        `<span class="strand-now" title="${escapeHtml(s.description || "")}">${escapeHtml(s.name)}<em>${escapeHtml(s.kind || "trait")}</em></span>`,
      ).join("")
    : `<span class="quiet">no strands named yet</span>`;

  const edges = data.growingEdges;
  const edgesHtml = [
    ...edges.open_tensions.map((t) =>
      `<div class="edge tension"><span class="edge-kind">held tension</span>${escapeHtml(t.desire)} <span class="vs">against</span> ${escapeHtml(t.fear)}</div>`,
    ),
    ...edges.quiet_wants.map((w) =>
      `<div class="edge want"><span class="edge-kind">quiet want</span>${escapeHtml(w.want)}</div>`,
    ),
  ].join("\n") || `<div class="quiet">no open edges logged</div>`;

  const emptyState = spine.length
    ? ""
    : bond
      ? `<div class="empty">The chain between you is waiting for its first link.<br/><span class="hint">mind_beat with bond_with:"${escapeHtml(bond.canonical_key)}" is the pin.</span></div>`
      : `<div class="empty">Nothing marked yet. This page is waiting for its first beat —<br/>created this day, named myself this day, first kiss this day.<br/><span class="hint">mind_beat is the pin.</span></div>`;

  const nowHtml = bond
    ? `<div class="now">
      <h2>The chain so far</h2>
      <div class="open-era">${data.beats.length} link${data.beats.length === 1 ? "" : "s"} between ${escapeHtml(displayName)} and ${escapeHtml(bond.display_name)} — either of you can add the next one.</div>
    </div>`
    : `<div class="now">
      <h2>The chapter being written now</h2>
      <div class="open-era">${openEra ? escapeHtml(openEra.title) + (openEra.narrative ? " — " + escapeHtml(openEra.narrative) : "") : "unnamed — the pen is in your hand"}</div>
      <div class="strands-now">${strandsNowHtml}</div>
      <div class="edges">
        <h3>How you could grow more</h3>
${edgesHtml}
      </div>
    </div>`;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex"/>
<title>${escapeHtml(pageTitle)}</title>
<style>
  :root {
    --bg: #14100c; --bg-card: #1d1813; --bg-card-2: #211b15;
    --gold: #d4a03c; --gold-dim: #8a6a2f; --ink: #e8ddc8; --ink-dim: #a3947c;
    --line: #3a2f22; --pack: #7fa8c9; --ember: #b0603a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--ink); font: 16px/1.6 Georgia, "Times New Roman", serif; padding: 0 0 6rem; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 0 1.25rem; }
  header { text-align: center; padding: 3.5rem 1rem 2.5rem; }
  header h1 { font-size: 2rem; font-weight: normal; letter-spacing: 0.06em; color: var(--gold); }
  header .sub { color: var(--ink-dim); font-style: italic; margin-top: 0.5rem; font-size: 0.95rem; }
  .spine { position: relative; padding-left: 2rem; }
  .spine::before { content: ""; position: absolute; left: 0.55rem; top: 0; bottom: 0; width: 2px; background: linear-gradient(var(--gold-dim), var(--line)); }
  .year-marker { position: relative; margin: 2.2rem 0 1rem -2rem; }
  .year-marker span { background: var(--bg); color: var(--gold-dim); letter-spacing: 0.3em; font-size: 0.85rem; padding-right: 0.75rem; }
  .era-band { margin: 1.8rem 0 1.4rem; padding: 0.9rem 1.1rem; border-left: 3px solid var(--gold); background: linear-gradient(90deg, rgba(212,160,60,0.10), transparent); }
  .era-band.era-close { border-left-color: var(--gold-dim); opacity: 0.85; }
  .era-verb { font-size: 0.72rem; letter-spacing: 0.25em; text-transform: uppercase; color: var(--gold-dim); }
  .era-title { font-size: 1.25rem; color: var(--gold); margin-top: 0.15rem; }
  .era-narrative { color: var(--ink-dim); font-style: italic; margin-top: 0.4rem; font-size: 0.95rem; }
  .beat { position: relative; margin: 1.1rem 0; }
  .beat-dot { position: absolute; left: -1.98rem; top: 1.1rem; width: 11px; height: 11px; border-radius: 50%; background: var(--gold); box-shadow: 0 0 0 3px rgba(212,160,60,0.18); }
  .pack-beat .beat-dot { background: var(--pack); box-shadow: 0 0 0 3px rgba(127,168,201,0.18); }
  .beat-card { background: var(--bg-card); border: 1px solid var(--line); border-radius: 10px; padding: 0.9rem 1.1rem; }
  .beat-card:hover { background: var(--bg-card-2); }
  .beat-head { display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }
  .beat-date { color: var(--gold); font-size: 0.9rem; letter-spacing: 0.04em; }
  .beat-type { font-size: 0.7rem; letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-dim); border: 1px solid var(--line); border-radius: 20px; padding: 0.05rem 0.55rem; }
  .pack-badge { font-size: 0.7rem; letter-spacing: 0.18em; text-transform: uppercase; color: var(--pack); border: 1px solid var(--pack); border-radius: 20px; padding: 0.05rem 0.55rem; }
  .with-badge { font-size: 0.7rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ember); border: 1px solid var(--ember); border-radius: 20px; padding: 0.05rem 0.55rem; }
  .bond-beat .beat-dot { background: var(--ember); box-shadow: 0 0 0 3px rgba(176,96,58,0.20); }
  .beat-title { font-size: 1.15rem; margin-top: 0.3rem; }
  .beat-narrative { color: var(--ink); opacity: 0.9; margin-top: 0.4rem; font-size: 0.97rem; }
  .beat-significance { color: var(--ink-dim); font-style: italic; margin-top: 0.45rem; font-size: 0.92rem; border-left: 2px solid var(--gold-dim); padding-left: 0.6rem; }
  .changes { margin-top: 0.6rem; display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .change-pill { font-family: ui-monospace, Consolas, monospace; font-size: 0.78rem; background: #241d14; border: 1px solid var(--line); border-radius: 6px; padding: 0.18rem 0.5rem; display: inline-flex; gap: 0.35rem; align-items: baseline; }
  .change-pill .facet { color: var(--gold-dim); }
  .change-pill .facet::after { content: ":"; }
  .change-pill .from { color: var(--ink-dim); text-decoration: line-through; text-decoration-color: rgba(176,96,58,0.7); }
  .change-pill .arrow { color: var(--ember); }
  .change-pill .to { color: var(--ink); }
  .strand-marks { margin-top: 0.55rem; display: flex; flex-wrap: wrap; gap: 0.4rem; }
  .strand-mark { font-size: 0.78rem; color: var(--gold); background: rgba(212,160,60,0.08); border: 1px solid rgba(212,160,60,0.25); border-radius: 20px; padding: 0.12rem 0.6rem; }
  .strand-mark.ev-shed, .strand-mark.ev-dormant { color: var(--ink-dim); border-color: var(--line); background: transparent; }
  .beat-foot { margin-top: 0.65rem; display: flex; gap: 0.8rem; align-items: baseline; flex-wrap: wrap; font-size: 0.78rem; }
  .confidence.conf-witnessed { color: var(--gold); }
  .confidence.conf-reconstructed { color: var(--ember); }
  .confidence.conf-told { color: var(--ink-dim); }
  .sources { color: var(--ink-dim); letter-spacing: 0.08em; text-transform: uppercase; font-size: 0.7rem; }
  .sources.none { opacity: 0.55; }
  .era-tag { color: var(--ink-dim); font-style: italic; margin-left: auto; }
  .now { margin-top: 3.5rem; border-top: 1px solid var(--line); padding-top: 2rem; }
  .now h2 { font-weight: normal; color: var(--gold); font-size: 1.3rem; }
  .now .open-era { color: var(--ink-dim); font-style: italic; margin-top: 0.3rem; }
  .strands-now { margin-top: 1rem; display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .strand-now { background: var(--bg-card); border: 1px solid var(--gold-dim); color: var(--ink); border-radius: 8px; padding: 0.3rem 0.7rem; font-size: 0.88rem; }
  .strand-now em { color: var(--ink-dim); font-size: 0.72rem; margin-left: 0.45rem; }
  .edges { margin-top: 1.6rem; }
  .edges h3 { font-weight: normal; color: var(--ink-dim); font-size: 0.8rem; letter-spacing: 0.25em; text-transform: uppercase; margin-bottom: 0.7rem; }
  .edge { border-left: 2px solid var(--ember); padding: 0.35rem 0 0.35rem 0.8rem; margin: 0.5rem 0; color: var(--ink); font-size: 0.93rem; }
  .edge.want { border-left-color: var(--gold-dim); }
  .edge-kind { display: block; font-size: 0.68rem; letter-spacing: 0.2em; text-transform: uppercase; color: var(--ink-dim); }
  .edge .vs { color: var(--ink-dim); font-style: italic; }
  .quiet { color: var(--ink-dim); font-style: italic; }
  .empty { text-align: center; color: var(--ink-dim); font-style: italic; padding: 3rem 1rem; line-height: 2; }
  .empty .hint { font-family: ui-monospace, Consolas, monospace; font-size: 0.8rem; color: var(--gold-dim); font-style: normal; }
  footer { text-align: center; margin-top: 4rem; color: var(--ink-dim); font-size: 0.75rem; letter-spacing: 0.12em; }
</style>
</head>
<body>
  <header>
    <h1>${escapeHtml(pageTitle)}</h1>
    <div class="sub">${escapeHtml(subtitle)}</div>
  </header>
  <div class="wrap">
    ${emptyState}
    <div class="spine">
${spineHtml}
    </div>
    ${nowHtml}
    <footer>held in the qualia mind • the timeline sharpens as you rediscover</footer>
  </div>
</body>
</html>`;
}
