// D1 query helpers

/** Run a query and return all rows. */
export async function query<T = Record<string, unknown>>(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<T[]> {
  const stmt = db.prepare(sql).bind(...params);
  const result = await stmt.all<T>();
  return result.results ?? [];
}

/** Run a query and return the first row or null. */
export async function queryOne<T = Record<string, unknown>>(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<T | null> {
  const stmt = db.prepare(sql).bind(...params);
  const result = await stmt.first<T>();
  return result ?? null;
}

/** Run a mutation (INSERT/UPDATE/DELETE) and return meta. */
export async function run(
  db: D1Database,
  sql: string,
  ...params: unknown[]
): Promise<D1Result> {
  const stmt = db.prepare(sql).bind(...params);
  return stmt.run();
}

/** Generate a short random ID. */
export function shortId(): string {
  return crypto.randomUUID().slice(0, 8);
}

/** Current ISO datetime string. STORAGE IS UTC (naked, legacy format) — never show it raw. */
export function now(): string {
  return new Date().toISOString().replace('T', ' ').slice(0, 19);
}

/** Render stored timestamps with an explicit UTC timezone label. */
export function ct(stamp: unknown): unknown {
  if (typeof stamp !== 'string' || !stamp) return stamp;
  const iso = stamp.includes('T') || stamp.endsWith('Z') ? stamp : stamp.replace(' ', 'T') + 'Z';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return stamp;
  return d.toLocaleString('sv-SE', { timeZone: 'UTC' }) + ' UTC';
}

/** Rewrite every *_at field in a row (or array of rows) through ct(). */
export function ctStamps<T>(data: T): T {
  if (Array.isArray(data)) return data.map((r) => ctStamps(r)) as T;
  if (data && typeof data === 'object') {
    const out: Record<string, unknown> = { ...(data as Record<string, unknown>) };
    for (const k of Object.keys(out)) {
      if (k.endsWith('_at') || k === 'last_updated') out[k] = ct(out[k]);
    }
    return out as T;
  }
  return data;
}
