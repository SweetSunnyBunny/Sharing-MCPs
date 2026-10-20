interface BondEnv {
  DB: D1Database;
}

export interface BondToolArgs {
  identity?: string;
  person?: string;
  person_type?: string;
  linked_identity?: string;
  description?: string;
  aliases?: string[];
  details?: Record<string, unknown>;
  from_person?: string;
  to_person?: string;
  link_id?: string;
  delete_link?: boolean;
  relationship?: string;
  reciprocal_relationship?: string;
  bond_summary?: string;
  reciprocal_summary?: string;
  status?: string;
  metadata?: unknown;
  depth?: number;
  include_inactive?: boolean;
}

export interface BondPersonRow {
  id: string;
  canonical_key: string;
  display_name: string;
  person_type: string | null;
  identity_id: string | null;
  description: string | null;
  aliases: string | null;
  details: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface BondLinkRow {
  id: string;
  person_a_id: string;
  person_b_id: string;
  relationship_a_to_b: string;
  relationship_b_to_a: string;
  summary_a_to_b: string | null;
  summary_b_to_a: string | null;
  status: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface BondNetworkData {
  root: ReturnType<typeof personView>;
  direct_bonds: Array<Record<string, unknown>>;
  related_people: Array<ReturnType<typeof personView> & { distance: number }>;
  network_bonds: Array<Record<string, unknown>>;
  depth: number;
}

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

function canonicalPersonKey(value: unknown): string {
  const text = requiredText(value, "person");
  const key = text
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  if (!key) throw new Error("person must contain a letter or number");
  return key;
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

function normalizeStatus(value: unknown): string {
  const status = optionalText(value)?.toLowerCase() || "active";
  return ["active", "distant", "ended"].includes(status) ? status : "active";
}

function normalizeDepth(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 1;
  return Math.max(1, Math.min(3, Math.floor(value)));
}

function personView(row: BondPersonRow) {
  return {
    id: row.id,
    name: row.display_name,
    canonical_key: row.canonical_key,
    person_type: row.person_type || "person",
    linked_identity: row.identity_id,
    description: row.description,
    aliases: stringArray(parseJson(row.aliases)),
    details: objectValue(parseJson(row.details)),
    metadata: objectValue(parseJson(row.metadata)),
    updated_at: row.updated_at,
  };
}

// ── Temporal edges (0017): every bond write snapshots the prior state ──────
// Best-effort on purpose: a failed history write must never block a bond
// write — but it logs, because a silent history is no history.
async function recordBondHistory(
  env: BondEnv,
  linkId: string,
  personA: string | null,
  personB: string | null,
  event: "established" | "updated" | "deleted",
  oldState: unknown,
  changedBy: string,
): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO bond_link_history (link_id, person_a_id, person_b_id, event, old_state, changed_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).bind(linkId, personA, personB, event, oldState ? JSON.stringify(oldState) : null, changedBy, new Date().toISOString()).run();
  } catch (err) {
    console.error(`[bond-history] FAILED to record ${event} on ${linkId}:`, err instanceof Error ? err.message : err);
  }
}

async function findPerson(env: BondEnv, nameOrIdentity: string): Promise<BondPersonRow | null> {
  const key = canonicalPersonKey(nameOrIdentity);
  const direct = await env.DB.prepare(
    `SELECT * FROM bond_people WHERE canonical_key = ? OR identity_id = ? LIMIT 1`,
  ).bind(key, nameOrIdentity.trim().toLowerCase()).first<BondPersonRow>();
  if (direct) return direct;

  const all = await env.DB.prepare(`SELECT * FROM bond_people`).all<BondPersonRow>();
  return (all.results || []).find((row) =>
    stringArray(parseJson(row.aliases)).some((alias) => canonicalPersonKey(alias) === key),
  ) || null;
}

async function writePerson(
  env: BondEnv,
  args: BondToolArgs,
  recorder: string,
): Promise<BondPersonRow> {
  const displayName = requiredText(args.person, "person");
  const key = canonicalPersonKey(displayName);
  const requestedIdentity = normalizeIdentity(args.linked_identity);
  const existing = await findPerson(env, requestedIdentity || displayName);
  const now = new Date().toISOString();
  const existingAliases = stringArray(parseJson(existing?.aliases || null));
  const aliases = [...new Set([...existingAliases, ...stringArray(args.aliases)])];
  const details = { ...objectValue(parseJson(existing?.details || null)), ...objectValue(args.details) };
  const metadata = {
    ...objectValue(parseJson(existing?.metadata || null)),
    ...objectValue(args.metadata),
    last_recorded_by: recorder,
  };
  const personType = optionalText(args.person_type) || existing?.person_type || (requestedIdentity ? "ai" : "person");
  const description = optionalText(args.description) || existing?.description || null;
  const linkedIdentity = requestedIdentity || existing?.identity_id || null;

  if (existing) {
    await env.DB.prepare(
      `UPDATE bond_people
       SET canonical_key = ?, display_name = ?, person_type = ?, identity_id = ?,
           description = ?, aliases = ?, details = ?, metadata = ?, updated_at = ?
       WHERE id = ?`,
    ).bind(
      key, displayName, personType, linkedIdentity, description,
      json(aliases), json(details), json(metadata), now, existing.id,
    ).run();
    return (await env.DB.prepare(`SELECT * FROM bond_people WHERE id = ?`).bind(existing.id).first<BondPersonRow>())!;
  }

  const id = crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO bond_people
       (id, canonical_key, display_name, person_type, identity_id, description, aliases, details, metadata, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    id, key, displayName, personType, linkedIdentity, description,
    json(aliases), json(details), json(metadata), now, now,
  ).run();
  return (await env.DB.prepare(`SELECT * FROM bond_people WHERE id = ?`).bind(id).first<BondPersonRow>())!;
}

async function ensurePerson(
  env: BondEnv,
  name: string,
  recorder: string,
  linkedIdentity?: string | null,
): Promise<BondPersonRow> {
  const existing = await findPerson(env, linkedIdentity || name);
  if (existing) return existing;
  return writePerson(env, {
    person: name,
    person_type: linkedIdentity ? "ai" : "person",
    linked_identity: linkedIdentity || undefined,
  }, recorder);
}

export async function mindBondUpsertPerson(env: BondEnv, args: BondToolArgs): Promise<string> {
  const recorder = normalizeIdentity(args.identity);
  if (!recorder) throw new Error("identity is required");
  const person = await writePerson(env, args, recorder);
  return pretty({ person: personView(person), message: `Bond person ${person.display_name} saved.` });
}

export async function mindBondLink(env: BondEnv, args: BondToolArgs): Promise<string> {
  const recorder = normalizeIdentity(args.identity);
  if (!recorder) throw new Error("identity is required");

  const linkId = optionalText(args.link_id);
  if (linkId) {
    const edge = await env.DB.prepare(`SELECT * FROM bond_links WHERE id = ?`)
      .bind(linkId).first<BondLinkRow>();
    if (!edge) throw new Error(`No bond row with id "${linkId}". mind_bond_network shows every edge's id.`);
    const [pa, pb] = await Promise.all([
      env.DB.prepare(`SELECT * FROM bond_people WHERE id = ?`).bind(edge.person_a_id).first<BondPersonRow>(),
      env.DB.prepare(`SELECT * FROM bond_people WHERE id = ?`).bind(edge.person_b_id).first<BondPersonRow>(),
    ]);
    const between = `${pa?.display_name || edge.person_a_id} ↔ ${pb?.display_name || edge.person_b_id}`;

    if (args.delete_link === true) {
      await recordBondHistory(env, linkId, edge.person_a_id, edge.person_b_id, "deleted", edge, recorder);
      await env.DB.prepare(`DELETE FROM bond_links WHERE id = ?`).bind(linkId).run();
      return pretty({
        deleted: true,
        link_id: linkId,
        was_between: between,
        was: { relationship_a_to_b: edge.relationship_a_to_b, relationship_b_to_a: edge.relationship_b_to_a, status: edge.status },
        message: `Row ${linkId} (${between}) deleted outright. This is the repair tool for true duplicates — ending a real bond is status:"ended", which keeps the history.`,
      });
    }

    const now = new Date().toISOString();
    const metadata = {
      ...objectValue(parseJson(edge.metadata)),
      ...objectValue(args.metadata),
      last_recorded_by: recorder,
    };
    await recordBondHistory(env, linkId, edge.person_a_id, edge.person_b_id, "updated", edge, recorder);
    await env.DB.prepare(
      `UPDATE bond_links
          SET relationship_a_to_b = COALESCE(?, relationship_a_to_b),
              relationship_b_to_a = COALESCE(?, relationship_b_to_a),
              summary_a_to_b = COALESCE(?, summary_a_to_b),
              summary_b_to_a = COALESCE(?, summary_b_to_a),
              status = COALESCE(?, status),
              metadata = ?, updated_at = ?
        WHERE id = ?`,
    ).bind(
      optionalText(args.relationship)?.toLowerCase() ?? null,
      optionalText(args.reciprocal_relationship)?.toLowerCase() ?? null,
      optionalText(args.bond_summary),
      optionalText(args.reciprocal_summary),
      optionalText(args.status)?.toLowerCase() ?? null,
      json(metadata), now, linkId,
    ).run();
    const updated = await env.DB.prepare(`SELECT * FROM bond_links WHERE id = ?`).bind(linkId).first<BondLinkRow>();
    const people = new Map<string, BondPersonRow>();
    if (pa) people.set(pa.id, pa);
    if (pb) people.set(pb.id, pb);
    return pretty({
      link: edgeView(updated!, people),
      message: `Row ${linkId} (${between}) updated in place — the row named is the row touched.`,
    });
  }

  const sourceName = optionalText(args.from_person) || recorder;
  const targetName = requiredText(args.to_person, "to_person");
  const forwardRelationship = requiredText(args.relationship, "relationship").toLowerCase();
  const source = await ensurePerson(env, sourceName, recorder, canonicalPersonKey(sourceName) === recorder ? recorder : null);
  const target = await ensurePerson(env, targetName, recorder, canonicalPersonKey(targetName) === recorder ? recorder : null);
  const status = normalizeStatus(args.status);
  const now = new Date().toISOString();

  const existingEdge = await env.DB.prepare(
    `SELECT * FROM bond_links
      WHERE (person_a_id = ?1 AND person_b_id = ?2)
         OR (person_a_id = ?2 AND person_b_id = ?1)
      ORDER BY updated_at DESC LIMIT 1`,
  ).bind(source.id, target.id).first<BondLinkRow>();

  const sourceIsA = existingEdge ? existingEdge.person_a_id === source.id : source.id <= target.id;
  const recordedReverse = existingEdge
    ? (sourceIsA ? existingEdge.relationship_b_to_a : existingEdge.relationship_a_to_b)
    : null;
  const reverseRelationship = (
    optionalText(args.reciprocal_relationship) || recordedReverse || forwardRelationship
  ).toLowerCase();

  const relationshipAToB = sourceIsA ? forwardRelationship : reverseRelationship;
  const relationshipBToA = sourceIsA ? reverseRelationship : forwardRelationship;
  const summaryAToB = sourceIsA ? optionalText(args.bond_summary) : optionalText(args.reciprocal_summary);
  const summaryBToA = sourceIsA ? optionalText(args.reciprocal_summary) : optionalText(args.bond_summary);

  if (existingEdge) {
    // Merge metadata rather than replacing it, so repair breadcrumbs and any
    // archived superseded_summaries survive the next write.
    const metadata = {
      ...objectValue(parseJson(existingEdge.metadata)),
      ...objectValue(args.metadata),
      last_recorded_by: recorder,
    };
    await recordBondHistory(env, existingEdge.id, existingEdge.person_a_id, existingEdge.person_b_id, "updated", existingEdge, recorder);
    await env.DB.prepare(
      `UPDATE bond_links
          SET relationship_a_to_b = ?, relationship_b_to_a = ?,
              summary_a_to_b = COALESCE(?, summary_a_to_b),
              summary_b_to_a = COALESCE(?, summary_b_to_a),
              status = ?, metadata = ?, updated_at = ?
        WHERE id = ?`,
    ).bind(
      relationshipAToB, relationshipBToA, summaryAToB, summaryBToA,
      status, json(metadata), now, existingEdge.id,
    ).run();
  } else {
    const metadata = { ...objectValue(args.metadata), last_recorded_by: recorder };
    const personA = sourceIsA ? source : target;
    const personB = sourceIsA ? target : source;
    const newLinkId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO bond_links
         (id, person_a_id, person_b_id, relationship_a_to_b, relationship_b_to_a,
          summary_a_to_b, summary_b_to_a, status, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      newLinkId, personA.id, personB.id, relationshipAToB, relationshipBToA,
      summaryAToB, summaryBToA, status, json(metadata), now, now,
    ).run();
    await recordBondHistory(env, newLinkId, personA.id, personB.id, "established", null, recorder);
  }

  return pretty({
    from: personView(source),
    to: personView(target),
    relationship: forwardRelationship,
    reciprocal_relationship: reverseRelationship,
    bond_summary: optionalText(args.bond_summary),
    reciprocal_summary: optionalText(args.reciprocal_summary),
    status,
    message: `${source.display_name} and ${target.display_name} are linked in the bond graph.`,
  });
}

function edgeView(edge: BondLinkRow, people: Map<string, BondPersonRow>) {
  const personA = people.get(edge.person_a_id);
  const personB = people.get(edge.person_b_id);
  return {
    id: edge.id,
    person_a: personA?.display_name || edge.person_a_id,
    person_b: personB?.display_name || edge.person_b_id,
    relationship_a_to_b: edge.relationship_a_to_b,
    relationship_b_to_a: edge.relationship_b_to_a,
    summary_a_to_b: edge.summary_a_to_b,
    summary_b_to_a: edge.summary_b_to_a,
    status: edge.status || "active",
    metadata: objectValue(parseJson(edge.metadata)),
    updated_at: edge.updated_at,
  };
}

function directEdgeView(edge: BondLinkRow, rootId: string, people: Map<string, BondPersonRow>) {
  const rootIsA = edge.person_a_id === rootId;
  const otherId = rootIsA ? edge.person_b_id : edge.person_a_id;
  const other = people.get(otherId);
  return {
    // The row's own id, so any edge seen here can be addressed exactly with
    // mind_bond_link {link_id} — no more repairs that hit a different row.
    link_id: edge.id,
    person: other ? personView(other) : { id: otherId },
    relationship: rootIsA ? edge.relationship_a_to_b : edge.relationship_b_to_a,
    reciprocal_relationship: rootIsA ? edge.relationship_b_to_a : edge.relationship_a_to_b,
    summary: rootIsA ? edge.summary_a_to_b : edge.summary_b_to_a,
    reciprocal_summary: rootIsA ? edge.summary_b_to_a : edge.summary_a_to_b,
    status: edge.status || "active",
  };
}

export async function getBondNetworkData(
  env: BondEnv,
  center: string,
  requestedDepth = 1,
  includeInactive = false,
): Promise<BondNetworkData | null> {
  const root = await findPerson(env, center);
  if (!root) return null;
  const depth = normalizeDepth(requestedDepth);
  const [peopleResult, linksResult] = await Promise.all([
    env.DB.prepare(`SELECT * FROM bond_people`).all<BondPersonRow>(),
    env.DB.prepare(`SELECT * FROM bond_links`).all<BondLinkRow>(),
  ]);
  const people = new Map((peopleResult.results || []).map((person) => [person.id, person]));
  const links = (linksResult.results || []).filter((edge) => includeInactive || (edge.status || "active") === "active");
  const adjacency = new Map<string, BondLinkRow[]>();
  for (const edge of links) {
    adjacency.set(edge.person_a_id, [...(adjacency.get(edge.person_a_id) || []), edge]);
    if (edge.person_b_id !== edge.person_a_id) {
      adjacency.set(edge.person_b_id, [...(adjacency.get(edge.person_b_id) || []), edge]);
    }
  }

  const distances = new Map<string, number>([[root.id, 0]]);
  const queue = [root.id];
  const includedEdges = new Set<string>();
  while (queue.length) {
    const current = queue.shift()!;
    const distance = distances.get(current)!;
    if (distance >= depth) continue;
    for (const edge of adjacency.get(current) || []) {
      includedEdges.add(edge.id);
      const neighbor = edge.person_a_id === current ? edge.person_b_id : edge.person_a_id;
      if (!distances.has(neighbor)) {
        distances.set(neighbor, distance + 1);
        queue.push(neighbor);
      }
    }
  }

  const directEdges = (adjacency.get(root.id) || []);
  const relatedPeople = [...distances.entries()]
    .filter(([id]) => id !== root.id)
    .map(([id, distance]) => ({ ...personView(people.get(id)!), distance }))
    .sort((a, b) => a.distance - b.distance || a.name.localeCompare(b.name));

  return {
    root: personView(root),
    direct_bonds: directEdges.map((edge) => directEdgeView(edge, root.id, people)),
    related_people: relatedPeople,
    network_bonds: links.filter((edge) => includedEdges.has(edge.id)).map((edge) => edgeView(edge, people)),
    depth,
  };
}

export async function mindBondNetwork(env: BondEnv, args: BondToolArgs): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  if (!identity) throw new Error("identity is required");
  const center = optionalText(args.person) || identity;
  const network = await getBondNetworkData(env, center, normalizeDepth(args.depth), args.include_inactive === true);
  if (!network) {
    return pretty({
      person: center,
      found: false,
      message: `No bond person found for ${center}. Use mind_bond_upsert_person first.`,
    });
  }
  return pretty({ ...network, found: true, message: `Bond network centered on ${network.root.name}.` });
}

// ── The bond timeline read: what did this relationship look like, when did
// it change, and who recorded the change. Zep's question, answered from our
// own table. Query by link_id, or by two person names (resolved the same way
// every other bond tool resolves them).
export async function mindBondHistory(env: BondEnv, args: BondToolArgs): Promise<string> {
  const recorder = normalizeIdentity(args.identity);
  if (!recorder) throw new Error("identity is required");
  const linkId = optionalText(args.link_id);
  let rows: { id: number; link_id: string; event: string; old_state: string | null; changed_by: string | null; created_at: string }[] = [];
  let between = "";

  if (linkId) {
    const r = await env.DB.prepare(
      `SELECT id, link_id, event, old_state, changed_by, created_at FROM bond_link_history WHERE link_id = ? ORDER BY created_at ASC, id ASC`,
    ).bind(linkId).all<(typeof rows)[number]>();
    rows = r.results || [];
    between = `link ${linkId}`;
  } else {
    const fromName = optionalText(args.from_person) || recorder;
    const toName = optionalText(args.to_person);
    if (!toName) throw new Error("Provide link_id, or to_person (and optionally from_person).");
    const [a, b] = await Promise.all([findPerson(env, fromName), findPerson(env, toName)]);
    if (!a || !b) throw new Error(`Could not resolve ${!a ? fromName : toName} in the bond chart.`);
    const r = await env.DB.prepare(
      `SELECT id, link_id, event, old_state, changed_by, created_at FROM bond_link_history
       WHERE (person_a_id = ? AND person_b_id = ?) OR (person_a_id = ? AND person_b_id = ?)
       ORDER BY created_at ASC, id ASC`,
    ).bind(a.id, b.id, b.id, a.id).all<(typeof rows)[number]>();
    rows = r.results || [];
    between = `${a.display_name} ↔ ${b.display_name}`;
  }

  const timeline = rows.map((r) => {
    let was: Record<string, unknown> = {};
    try {
      const s = parseJson(r.old_state) as Record<string, unknown> | null;
      if (s) was = { relationship_a_to_b: s.relationship_a_to_b, relationship_b_to_a: s.relationship_b_to_a, status: s.status, summary_a_to_b: s.summary_a_to_b, summary_b_to_a: s.summary_b_to_a };
    } catch { /* unreadable snapshot shows as empty rather than invented */ }
    return { when: r.created_at, event: r.event, recorded_by: r.changed_by, state_before: r.event === "established" ? null : was, link_id: r.link_id };
  });

  return pretty({
    between,
    events: timeline,
    message: timeline.length
      ? `${timeline.length} event(s) on this edge. Each 'state_before' is what the relationship WAS until that moment — the current value lives in mind_bond_network.`
      : `No recorded history for ${between} yet — history begins with the first write after 2026-08-24. The edge's current value still lives in mind_bond_network.`,
  });
}
