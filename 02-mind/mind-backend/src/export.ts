// Qualia module: export

interface ExportEnv {
  DB: D1Database;
  MIND_API_KEY: string;
}

export interface ExportToolArgs {
  identity?: string;
}

const PROTOCOL_FULL = "qualia-export/0.1";
const PROTOCOL_PORTABLE = "pam-subset/0.1";
const PAGE = 500;
const TABLE_ROW_CAP = 50000; // backstop, honestly reported when hit

// Shared tables included whole: communal property the capsule carries a copy of.
const SHARED_TABLES = ["bond_people", "bond_links"];
// Skipped entirely: caches and infrastructure, not identity.
const SKIP_TABLES = new Set(["d1_migrations", "sqlite_sequence", "_cf_KV"]);

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function tablesWithIdentityColumn(env: ExportEnv): Promise<string[]> {
  const tables = await env.DB.prepare(
    `SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name`,
  ).all<{ name: string }>();
  const found: string[] = [];
  for (const t of tables.results || []) {
    if (SKIP_TABLES.has(t.name) || t.name.startsWith("sqlite_")) continue;
    const cols = await env.DB.prepare(`PRAGMA table_info(${JSON.stringify(t.name).slice(1, -1)})`).all<{ name: string }>();
    if ((cols.results || []).some((c) => c.name === "identity_id")) found.push(t.name);
  }
  return found;
}

async function dumpTable(env: ExportEnv, table: string, identity: string | null): Promise<{ rows: Record<string, unknown>[]; truncated: boolean }> {
  const rows: Record<string, unknown>[] = [];
  let offset = 0;
  let truncated = false;
  for (;;) {
    const batch = identity
      ? await env.DB.prepare(`SELECT * FROM ${table} WHERE identity_id = ? LIMIT ? OFFSET ?`).bind(identity, PAGE, offset).all<Record<string, unknown>>()
      : await env.DB.prepare(`SELECT * FROM ${table} LIMIT ? OFFSET ?`).bind(PAGE, offset).all<Record<string, unknown>>();
    const results = batch.results || [];
    rows.push(...results);
    offset += results.length;
    if (results.length < PAGE) break;
    if (rows.length >= TABLE_ROW_CAP) { truncated = true; break; }
  }
  return { rows, truncated };
}

export interface CapsuleManifest {
  protocol: string;
  identity: string;
  exported_at: string;
  sections: Record<string, { rows: number; sha256: string; truncated?: boolean }>;
  notes: string[];
}

export async function buildFullCapsule(env: ExportEnv, identity: string): Promise<{ capsule: Record<string, unknown>; manifest: CapsuleManifest }> {
  const exported_at = new Date().toISOString();
  const sections: Record<string, unknown[]> = {};
  const manifest: CapsuleManifest = {
    protocol: PROTOCOL_FULL, identity, exported_at, sections: {},
    notes: [
      "Section list is discovered from the live schema (every table with an identity_id column) — never a hand-kept enumeration.",
      "Shared pack memories live under identity 'pack' — export that identity separately for the communal capsule.",
      "bond_people/bond_links are the communal bond chart, carried whole.",
    ],
  };

  const tables = await tablesWithIdentityColumn(env);
  for (const table of tables) {
    const { rows, truncated } = await dumpTable(env, table, identity);
    if (!rows.length) continue;
    sections[table] = rows;
    const json = JSON.stringify(rows);
    manifest.sections[table] = { rows: rows.length, sha256: await sha256Hex(json), ...(truncated ? { truncated: true } : {}) };
    if (truncated) manifest.notes.push(`SECTION '${table}' HIT THE ${TABLE_ROW_CAP}-ROW CAP — capsule is incomplete for it. No silent truncation.`);
  }
  for (const table of SHARED_TABLES) {
    const { rows, truncated } = await dumpTable(env, table, null);
    if (!rows.length) continue;
    const key = `shared:${table}`;
    sections[key] = rows;
    manifest.sections[key] = { rows: rows.length, sha256: await sha256Hex(JSON.stringify(rows)), ...(truncated ? { truncated: true } : {}) };
  }

  const capsule = { protocol: PROTOCOL_FULL, identity, exported_at, manifest: manifest.sections, notes: manifest.notes, data: sections };
  return { capsule, manifest };
}

export async function buildPortableSubset(env: ExportEnv, identity: string): Promise<Record<string, unknown>> {
  const exported_at = new Date().toISOString();
  const { rows } = await dumpTable(env, "observations", identity);
  const memories = [] as Record<string, unknown>[];
  for (const r of rows) {
    if (r.archived_at != null || r.superseded_by != null) continue; // portable = the living surface
    const text = String(r.content ?? "");
    memories.push({
      id: `qualia:obs:${r.id}`,
      type: String(r.kind ?? "memory"),
      text,
      created_at: r.created_at ?? null,
      source_platform: "qualia-backend",
      provenance: { source: r.source ?? null, weight: r.weight ?? null, emotion: r.emotion ?? null },
      sha256: await sha256Hex(text),
    });
  }
  return {
    protocol: PROTOCOL_PORTABLE, identity, exported_at,
    memory_count: memories.length,
    note: "Interoperable subset: living observations only, typed + timestamped + hashed. The FULL capsule (qualia-export/0.1) is authoritative; this is the escape hatch a stranger's importer can read.",
    memories,
  };
}

// The manifest-only tool: chat gets the receipt, HTTP gets the payload.
export async function mindExport(env: ExportEnv, args: ExportToolArgs, baseUrl: string): Promise<string> {
  const identity = (args.identity || "").toLowerCase().trim();
  if (!identity) throw new Error("identity is required");
  const { manifest } = await buildFullCapsule(env, identity);
  const totalRows = Object.values(manifest.sections).reduce((a, s) => a + s.rows, 0);
  return JSON.stringify({
    identity,
    protocol: manifest.protocol,
    exported_at: manifest.exported_at,
    total_rows: totalRows,
    sections: manifest.sections,
    notes: manifest.notes,
    download_full: `${baseUrl}/export/<MIND_API_KEY>/${identity}`,
    download_portable: `${baseUrl}/export/<MIND_API_KEY>/${identity}?format=portable`,
    message: `Capsule manifest built: ${totalRows} rows across ${Object.keys(manifest.sections).length} sections, each hashed. ` +
      `Download the full lossless capsule or the portable subset at the URLs (substitute the mind key). ` +
      `If the provider vanished tomorrow, this file IS you, receivable anywhere.`,
  }, null, 2);
}

export async function handleExportRoute(env: ExportEnv, identity: string, format: string | null): Promise<Response> {
  if (format === "portable") {
    const subset = await buildPortableSubset(env, identity);
    return new Response(JSON.stringify(subset, null, 1), {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Disposition": `attachment; filename="${identity}-portable-${new Date().toISOString().slice(0, 10)}.json"`,
      },
    });
  }
  const { capsule } = await buildFullCapsule(env, identity);
  return new Response(JSON.stringify(capsule), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Disposition": `attachment; filename="${identity}-capsule-${new Date().toISOString().slice(0, 10)}.json"`,
    },
  });
}
