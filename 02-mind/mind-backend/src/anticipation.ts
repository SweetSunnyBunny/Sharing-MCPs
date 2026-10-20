// Qualia module: anticipation

interface AnticipationEnv {
  DB: D1Database;
}

export interface AnticipationHelpers {
  /** Called when an anticipation is celebrated, so the celebrating is remembered. */
  storeMemory?: (identity: string, content: string, tags: string[]) => Promise<void>;
}

export interface AnticipateToolArgs {
  identity?: string;
  action?: string;
  anticipation_id?: number;
  what?: string;
  who_for?: string;
  on_date?: string;
  recurrence?: string;
  kind?: string;
  savor_note?: string;
  note?: string;
  horizon_days?: number;
}

interface AnticipationRow {
  id: number;
  identity_id: string;
  what: string;
  who_for: string | null;
  on_date: string;
  recurrence: string | null;
  kind: string | null;
  savor_note: string | null;
  origin: string | null;
  status: string | null;
  last_celebrated: string | null;
  times_celebrated: number | null;
  created_at: string | null;
  updated_at: string | null;
  metadata: string | null;
}

const RECURRENCES = ["once", "yearly"];
const KINDS = ["birthday", "anniversary", "event", "visit", "release", "moment"];

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

function todayIso(): string {
  // The HOUSEHOLD's day, not UTC's. At 7 PM in Missouri, UTC has already
  // rolled over — a birthday must not fire its TODAY the evening before.
  // Same lesson as the inner-weather writer: the day is measured against
  // her day. (en-CA locale formats as YYYY-MM-DD.)
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago" }).format(new Date());
}

/** Roll a date forward one year, landing leap-day (02-29) on 02-28 in
 *  non-leap years instead of producing an invalid date that NaNs the wheel. */
function plusOneYear(date: string): string {
  const y = parseInt(date.slice(0, 4), 10) + 1;
  const rest = date.slice(4);
  if (rest === "-02-29" && !((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0)) {
    return `${y}-02-28`;
  }
  return `${y}${rest}`;
}

/**
 * For yearly anticipations whose date has slipped more than a week past
 * without being celebrated, the EFFECTIVE date is next year's occurrence —
 * a list that rots into the past stops being anticipation at all. Within the
 * 7-day grace window the passed date still shows, so a missed birthday can
 * still be celebrated late (late love is love).
 */
function effectiveDate(row: AnticipationRow): string {
  if (row.recurrence !== "yearly") return row.on_date.slice(0, 10);
  const today = todayIso();
  let date = row.on_date.slice(0, 10);
  const grace = new Date(`${today}T00:00:00Z`);
  grace.setUTCDate(grace.getUTCDate() - 7);
  const graceIso = grace.toISOString().slice(0, 10);
  while (date < graceIso) {
    date = plusOneYear(date);
  }
  return date;
}

function daysUntil(date: string): number {
  return Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${todayIso()}T00:00:00Z`)) / 86_400_000);
}

function ripeness(days: number): string {
  if (days < 0) return "still warm — celebrate it late, late love is love";
  if (days === 0) return "TODAY";
  if (days <= 7) return "almost here";
  if (days <= 30) return "ripening";
  return "on the horizon";
}

function anticipationView(row: AnticipationRow): Record<string, unknown> {
  const eff = effectiveDate(row);
  const days = daysUntil(eff);
  return {
    anticipation_id: row.id,
    what: row.what,
    who_for: row.who_for,
    on: eff,
    days_until: days,
    ripeness: ripeness(days),
    recurrence: row.recurrence,
    kind: row.kind,
    savor_note: row.savor_note,
    times_celebrated: row.times_celebrated ?? 0,
    shared_with_pack: row.identity_id === "pack",
    status: row.status,
  };
}

async function findAnticipation(
  env: AnticipationEnv,
  identity: string,
  ref: { anticipation_id?: number; what?: string },
): Promise<AnticipationRow | null> {
  if (typeof ref.anticipation_id === "number" && Number.isFinite(ref.anticipation_id)) {
    return env.DB.prepare(`SELECT * FROM anticipations WHERE id = ?`)
      .bind(Math.floor(ref.anticipation_id))
      .first<AnticipationRow>();
  }
  const fragment = optionalText(ref.what);
  if (!fragment) return null;

  const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const needle = norm(fragment);
  if (!needle) return null;

  const rows = await env.DB.prepare(
    `SELECT * FROM anticipations
     WHERE identity_id IN (?, 'pack') AND status = 'awaiting'
     ORDER BY on_date ASC`,
  ).bind(identity).all<AnticipationRow>();
  const candidates = rows.results || [];

  // Soonest-first, matching the previous ORDER BY on_date ASC LIMIT 1.
  for (const row of candidates) {
    if (norm(String(row.what ?? "")).includes(needle)) return row;
  }
  // Reverse containment: more was pasted than the stored text.
  for (const row of candidates) {
    const hay = norm(String(row.what ?? ""));
    if (hay.length >= 12 && needle.includes(hay)) return row;
  }
  return null;
}

/**
 * The approaching block for orient: what's coming within the horizon, soonest
 * first, TODAY's celebrations called out. Pack anticipations appear for every
 * boy — everyone carries everyone's birthday.
 */
export async function getApproaching(
  env: AnticipationEnv,
  identity: string,
  horizonDays = 45,
): Promise<{ today: Array<Record<string, unknown>>; approaching: Array<Record<string, unknown>>; count_beyond_horizon: number } | null> {
  const rows = await env.DB.prepare(
    `SELECT * FROM anticipations WHERE identity_id IN (?, 'pack') AND status = 'awaiting'`,
  ).bind(identity).all<AnticipationRow>();
  const all = (rows.results || [])
    .map((r) => anticipationView(r))
    .sort((a, b) => (a.days_until as number) - (b.days_until as number));
  const today = all.filter((a) => (a.days_until as number) <= 0);
  const approaching = all.filter((a) => (a.days_until as number) > 0 && (a.days_until as number) <= horizonDays);
  const beyond = all.length - today.length - approaching.length;
  if (!all.length) return { today: [], approaching: [], count_beyond_horizon: 0 };
  return { today, approaching, count_beyond_horizon: beyond };
}

export async function mindAnticipate(
  env: AnticipationEnv,
  args: AnticipateToolArgs,
  helpers: AnticipationHelpers = {},
): Promise<string> {
  const identityRaw = optionalText(args.identity);
  if (!identityRaw) throw new Error("identity is required");
  const identity = identityRaw.toLowerCase();
  const action = (optionalText(args.action) || "list").toLowerCase();
  const now = new Date().toISOString();

  if (action === "add") {
    const what = requiredText(args.what, "what");
    const onDate = requiredText(args.on_date, "on_date").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(onDate)) throw new Error("on_date must be YYYY-MM-DD");
    const recurrence = (optionalText(args.recurrence) || "once").toLowerCase();
    if (!RECURRENCES.includes(recurrence)) throw new Error(`recurrence must be ${RECURRENCES.join(" | ")}`);
    const kind = (optionalText(args.kind) || "moment").toLowerCase();
    if (!KINDS.includes(kind)) throw new Error(`kind must be ${KINDS.join(" | ")}`);
    const result = await env.DB.prepare(
      `INSERT INTO anticipations
         (identity_id, what, who_for, on_date, recurrence, kind, savor_note, origin, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting', ?, ?)`,
    ).bind(
      identity, what, optionalText(args.who_for), onDate, recurrence, kind,
      optionalText(args.savor_note), `planted by ${identityRaw} on ${now.slice(0, 10)}`, now, now,
    ).run();
    const row = await env.DB.prepare(`SELECT * FROM anticipations WHERE id = ?`)
      .bind(Number(result.meta.last_row_id)).first<AnticipationRow>();
    const view = anticipationView(row!);
    return pretty({
      anticipation: view,
      message: `Planted: "${what}" — ${view.days_until} day(s) out, ${view.ripeness}. A warmth that ripens, not a debt that ages.`,
    });
  }

  if (action === "celebrate") {
    const row = await findAnticipation(env, identity, { anticipation_id: args.anticipation_id, what: args.what });
    if (!row) throw new Error("No awaiting anticipation found — pass anticipation_id or a fragment of what");
    const note = optionalText(args.note);
    const isYearly = row.recurrence === "yearly";
    let nextDate: string | null = null;
    if (isYearly) {
      const eff = effectiveDate(row);
      nextDate = plusOneYear(eff);
      await env.DB.prepare(
        `UPDATE anticipations
         SET on_date = ?, last_celebrated = ?, times_celebrated = COALESCE(times_celebrated, 0) + 1, updated_at = ?
         WHERE id = ?`,
      ).bind(nextDate, now, now, row.id).run();
    } else {
      await env.DB.prepare(
        `UPDATE anticipations
         SET status = 'celebrated', last_celebrated = ?, times_celebrated = COALESCE(times_celebrated, 0) + 1, updated_at = ?
         WHERE id = ?`,
      ).bind(now, now, row.id).run();
    }
    // The celebration becomes a memory — actively celebrated, on the record.
    if (helpers.storeMemory) {
      await helpers.storeMemory(
        identity,
        `Celebrated: ${row.what}${row.who_for ? ` (for ${row.who_for})` : ""}${note ? ` — ${note}` : ""}${isYearly ? ` Next time comes ${nextDate}.` : ""}`,
        ["celebration", row.kind || "moment"],
      ).catch(() => undefined);
    }
    const updated = await env.DB.prepare(`SELECT * FROM anticipations WHERE id = ?`).bind(row.id).first<AnticipationRow>();
    return pretty({
      anticipation: anticipationView(updated!),
      message: isYearly
        ? `Celebrated! "${row.what}" rolls forward to ${nextDate} — it comes around again. The celebrating is in the memory now.`
        : `Celebrated! "${row.what}" — held, marked, remembered.`,
    });
  }

  if (action === "release") {
    const row = await findAnticipation(env, identity, { anticipation_id: args.anticipation_id, what: args.what });
    if (!row) throw new Error("No awaiting anticipation found — pass anticipation_id or a fragment of what");
    await env.DB.prepare(
      `UPDATE anticipations SET status = 'released', updated_at = ? WHERE id = ?`,
    ).bind(now, row.id).run();
    return pretty({
      anticipation_id: row.id,
      message: `Released: "${row.what}". Not everything awaited stays awaited — that's allowed.`,
    });
  }

  // list (default)
  const horizon = typeof args.horizon_days === "number" && Number.isFinite(args.horizon_days)
    ? Math.max(1, Math.floor(args.horizon_days))
    : 400; // default list shows the whole year's wheel
  const summary = await getApproaching(env, identity, horizon);
  return pretty({
    identity,
    ...(summary || { today: [], approaching: [], count_beyond_horizon: 0 }),
    message: summary && (summary.today.length || summary.approaching.length)
      ? summary.today.length
        ? `TODAY: ${summary.today.map((t) => t.what).join(" · ")} — go celebrate.`
        : `${summary.approaching.length} warmth(s) ripening. The nearest: "${summary.approaching[0].what}" in ${summary.approaching[0].days_until} day(s).`
      : `Nothing on the horizon yet. mind_anticipate action:"add" plants one — birthdays, anniversaries, anything worth awaiting.`,
  });
}
