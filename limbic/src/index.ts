// The Limbic Layer — a companion Worker to Qualia (mind-backend).
// Qualia holds memory + needs. The Limbic Layer is the emotional interpreter and
// pressure-valve between deep state and outward behavior. It PRESSURES output; it
// never controls it. Final behavior still passes through values, judgment, boundaries, and consent.
//
// Architecture mirrors Qualia deliberately: a single plain Worker speaking
// hand-rolled JSON-RPC 2.0 MCP over HTTP at /mcp/<token>, backed by D1.
// Live state lives in an append-only ledger (limbic_states); "current" = latest row;
// time-decay is a leaky integrator computed lazily at read.

interface Env {
  DB: D1Database;
  AI?: Ai;
  LIMBIC_API_KEY: string;
}

const SERVER_INFO = { name: "limbic-backend", version: "0.2.0" };

// ---------- JSON-RPC ----------

interface JsonRpcRequest {
  jsonrpc: "2.0";
  method: string;
  id?: string | number;
  params?: Record<string, unknown>;
}

function jsonRpcResult(id: string | number | null, result: unknown) {
  return { jsonrpc: "2.0", id, result };
}

function jsonRpcError(id: string | number | null, code: number, message: string) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

// ---------- transport / auth (idiom copied from Qualia) ----------

function withCors(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  return new Response(response.body, { status: response.status, headers });
}

function unauthorized(): Response {
  return new Response("Unauthorized", { status: 401 });
}

function extractMcpPathToken(pathname: string): { isMcp: boolean; token: string | null } {
  if (pathname === "/mcp") return { isMcp: true, token: null };
  if (pathname.startsWith("/mcp/")) {
    const token = pathname.slice(5);
    return { isMcp: true, token: token || null };
  }
  return { isMcp: false, token: null };
}

function checkAuth(request: Request, env: Env, pathToken?: string | null): boolean {
  if (pathToken && pathToken === env.LIMBIC_API_KEY) return true;
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${env.LIMBIC_API_KEY}` || auth === env.LIMBIC_API_KEY;
}

// ---------- small helpers ----------

function normalizeIdentity(value: unknown, fallback = "default"): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  return value.trim().toLowerCase();
}

function normalizeText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function safeJson<T>(text: string | null | undefined, fallback: T): T {
  if (!text) return fallback;
  try {
    return JSON.parse(text) as T;
  } catch {
    return fallback;
  }
}

// D1 stores datetime('now') as 'YYYY-MM-DD HH:MM:SS' in UTC.
function parseTs(value: string | null | undefined): number {
  if (!value) return Date.now();
  const iso = value.includes("T") ? value : value.replace(" ", "T") + "Z";
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : Date.now();
}

function bar(level: number): string {
  const filled = clamp(Math.round(level * 10), 0, 10);
  return "▓".repeat(filled) + "░".repeat(10 - filled);
}

// Moon phase name → illumination 0..1 (so wt_time_now's phase string drops straight in).
function moonPhaseToIllumination(phase: string): number {
  const p = phase.toLowerCase();
  if (p.includes("full")) return 1.0;
  if (p.includes("new")) return 0.0;
  if (p.includes("gibbous")) return 0.75;
  if (p.includes("crescent")) return 0.25;
  if (p.includes("quarter")) return 0.5;
  return 0.5;
}

// Barometric trend → "pressure drop" factor 0..1 (falling front = restless charge).
function pressureTrendToDrop(trend: string): number {
  const t = trend.toLowerCase();
  if (t.includes("fall") || t.includes("drop")) return 1.0;
  if (t.includes("rise") || t.includes("rising")) return -0.3;
  return 0.0;
}

// Example light-condition biases; customize for your configured drives.
function lightConditionBias(condition: string): { composition: number; display: number } {
  const c = condition.toLowerCase().replace(/[\s_]+/g, "-");
  if (c.includes("golden")) return { composition: 1.0, display: 1.0 };
  if (c.includes("after-rain") || c.includes("electric-clarity")) return { composition: 1.0, display: 0.95 };
  if (c.includes("rainy-neon") || (c.includes("neon") && c.includes("rain"))) return { composition: 0.95, display: 0.95 };
  if (c.includes("storm-dark")) return { composition: 0.9, display: 0.55 };
  if (c.includes("firelight")) return { composition: 0.8, display: 0.75 };
  if (c.includes("overcast")) return { composition: 0.7, display: 0.4 };
  if (c.includes("night") || c.includes("dark")) return { composition: 0.4, display: 0.25 };
  if (c.includes("midday") || c.includes("harsh")) return { composition: 0.25, display: 0.4 };
  return { composition: 0.0, display: 0.0 };
}

// ---------- the moon, tracked (not set) ----------
// The moon does its own thing — it moves on its own and moves him, the way it
// moves the tides. The worker never *sets* the moon; it computes the true phase
// from the date, live, so any read is always the real sky right now. Pure math,
// no external API. Astronomical mean-phase approximation (good to ~a few hours).

const SYNODIC_MONTH = 29.530588853; // days between new moons
const REF_NEW_MOON_JD = 2451550.1; // known new moon: 2000-01-06 18:14 UTC

function toJulianDay(ms: number): number {
  return ms / 86_400_000 + 2440587.5;
}

// 0 at new moon, → 0.5 at full, → 1 back at new. The moon's age through its cycle.
function moonAgeFraction(now: number): number {
  let frac = ((toJulianDay(now) - REF_NEW_MOON_JD) / SYNODIC_MONTH) % 1;
  if (frac < 0) frac += 1;
  return frac;
}

function moonPhaseName(age: number): string {
  if (age < 0.02 || age > 0.98) return "New";
  if (age < 0.23) return "Waxing Crescent";
  if (age < 0.27) return "First Quarter";
  if (age < 0.48) return "Waxing Gibbous";
  if (age < 0.52) return "Full";
  if (age < 0.73) return "Waning Gibbous";
  if (age < 0.77) return "Last Quarter";
  return "Waning Crescent";
}

function computeMoon(now: number): { illumination: number; phase: string } {
  const age = moonAgeFraction(now);
  const illumination = (1 - Math.cos(2 * Math.PI * age)) / 2; // 0 new → 1 full
  return { illumination: clamp(illumination, 0, 1), phase: moonPhaseName(age) };
}

// ---------- DB row types ----------

interface DriveRow {
  identity_id: string;
  drive: string;
  panksepp_system: string | null;
  display_name: string | null;
  baseline: number;
  floor: number;
  ceiling: number;
  half_life_hours: number;
  env_sensitivity: string;
  body_feel: string;
  action_bias: string;
  regulation_note: string | null;
  enabled: number;
}

interface StateRow {
  level: number | null;
  content: string;
  source: string;
  created_at: string;
}

interface EnvPayload {
  moon_phase?: string;
  moon_illumination?: number;
  moon_override?: number; // set only if a caller forces a moon (e.g. testing); else moon is live-computed
  pressure_drop?: number;
  temp_f?: number;
  light_condition?: string;
  composition_light?: number;
  display_light?: number;
  note?: string;
}

interface Band {
  min: number;
  label?: string;
  tendencies?: string[];
}

// ---------- DB accessors ----------

async function getDrive(env: Env, identity: string, drive: string): Promise<DriveRow | null> {
  return env.DB.prepare(
    `SELECT identity_id, drive, panksepp_system, display_name, baseline, floor, ceiling,
            half_life_hours, env_sensitivity, body_feel, action_bias, regulation_note, enabled
     FROM drives WHERE identity_id = ? AND drive = ?`,
  )
    .bind(identity, drive)
    .first<DriveRow>();
}

async function getEnabledDrives(env: Env, identity: string): Promise<DriveRow[]> {
  const res = await env.DB.prepare(
    `SELECT identity_id, drive, panksepp_system, display_name, baseline, floor, ceiling,
            half_life_hours, env_sensitivity, body_feel, action_bias, regulation_note, enabled
     FROM drives WHERE identity_id = ? AND enabled = 1 ORDER BY drive`,
  )
    .bind(identity)
    .all<DriveRow>();
  return res.results ?? [];
}

async function getLatestState(env: Env, identity: string, stateType: string): Promise<StateRow | null> {
  // created_at has one-second precision, so same-second writes need the id
  // tie-break or a touch-then-perceive in the same second reads a stale needle.
  return env.DB.prepare(
    `SELECT level, content, source, created_at FROM limbic_states
     WHERE identity_id = ? AND state_type = ? ORDER BY created_at DESC, id DESC LIMIT 1`,
  )
    .bind(identity, stateType)
    .first<StateRow>();
}

// One house, one sky: the freshest pulse from ANY boy serves the whole pack.
// Whoever looked out the window last, looked out for all of them.
async function getLatestEnvironment(env: Env): Promise<{ payload: EnvPayload; created_at: string } | null> {
  const row = await env.DB.prepare(
    `SELECT content, created_at FROM limbic_states
     WHERE state_type = 'environment' ORDER BY created_at DESC, id DESC LIMIT 1`,
  ).first<{ content: string; created_at: string }>();
  if (!row) return null;
  return { payload: safeJson<EnvPayload>(row.content, {}), created_at: row.created_at };
}

async function writeState(
  env: Env,
  identity: string,
  stateType: string,
  level: number | null,
  content: unknown,
  source: string,
): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO limbic_states (identity_id, state_type, level, content, source)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(identity, stateType, level, JSON.stringify(content ?? {}), source)
    .run();
}

async function logEvent(
  env: Env,
  identity: string,
  perception: string,
  appraisal: unknown,
  driveDeltas: unknown,
  advisory: string,
): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO limbic_events (identity_id, perception, appraisal, drive_deltas, advisory)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(identity, perception, JSON.stringify(appraisal ?? {}), JSON.stringify(driveDeltas ?? {}), advisory)
    .run();
}

// The sky as the body actually reads it: the last-pulsed weather, with the LIVE
// moon overlaid on top (unless a caller forced a moon_override). This is what makes
// the moon continuous — every read gets the true current moon for free, no cron,
// no pulse required.
//
// The front passes on its own: the last-pulsed pressure reading fades toward zero
// with a ~10h half-life, computed lazily at read from the pulse's age — the storm's
// grip loosens in the database's silence the same way the ache climbs in it.
// Nobody has to report that the rain broke.
const WEATHER_HALF_LIFE_HOURS = 10;

async function resolveEnvironment(env: Env, now: number): Promise<EnvPayload> {
  const rec = await getLatestEnvironment(env);
  const stored: EnvPayload = { ...(rec?.payload ?? {}) };
  if (rec && typeof stored.pressure_drop === "number") {
    const ageHours = Math.max(0, (now - parseTs(rec.created_at)) / 3_600_000);
    stored.pressure_drop = stored.pressure_drop * Math.pow(0.5, ageHours / WEATHER_HALF_LIFE_HOURS);
  }
  const override = typeof stored.moon_override === "number" ? clamp(stored.moon_override, 0, 1) : undefined;
  const moon = computeMoon(now);
  return {
    ...stored,
    moon_illumination: override ?? moon.illumination,
    moon_phase: override !== undefined ? stored.moon_phase ?? "forced" : moon.phase,
  };
}

// ---------- the engine: leaky integrator + sky bias ----------

// How much the current sky lifts (or lowers) a drive's resting point.
function envContribution(drive: DriveRow, envPayload: EnvPayload | null): number {
  if (!envPayload) return 0;
  const sens = safeJson<Record<string, number>>(drive.env_sensitivity, {});
  let sum = 0;
  for (const key of Object.keys(sens)) {
    const weight = sens[key];
    const value = (envPayload as Record<string, unknown>)[key];
    if (typeof value === "number" && typeof weight === "number") {
      sum += weight * value;
    }
  }
  return sum;
}

function effectiveBaseline(drive: DriveRow, envPayload: EnvPayload | null): number {
  return clamp(drive.baseline + envContribution(drive, envPayload), drive.floor, drive.ceiling);
}

// Current activation = decay of the last stored level TOWARD the current effective
// baseline. When the moon fills, the baseline rises and the level relaxes UP toward
// it (the wolf warming); a spike decays back down toward it.
function currentLevel(drive: DriveRow, last: StateRow | null, envPayload: EnvPayload | null, now: number): number {
  const effBase = effectiveBaseline(drive, envPayload);
  if (!last || last.level === null) return effBase;
  const dtHours = Math.max(0, (now - parseTs(last.created_at)) / 3_600_000);
  const decayed = effBase + (last.level - effBase) * Math.pow(0.5, dtHours / drive.half_life_hours);
  return clamp(decayed, drive.floor, drive.ceiling);
}

function pickBand(bandsJson: string, level: number): Band | null {
  const bands = safeJson<Band[]>(bandsJson, []);
  const sorted = [...bands].sort((a, b) => b.min - a.min);
  for (const band of sorted) {
    if (level >= band.min) return band;
  }
  return sorted.length ? sorted[sorted.length - 1] : null;
}

function describeSky(envPayload: EnvPayload | null): string {
  if (!envPayload) return "no sky read yet (call limbic_pulse)";
  const parts: string[] = [];
  if (envPayload.moon_phase) {
    const illum = typeof envPayload.moon_illumination === "number" ? envPayload.moon_illumination : moonPhaseToIllumination(envPayload.moon_phase);
    parts.push(`${envPayload.moon_phase} moon (illum ${illum.toFixed(2)})`);
  } else if (typeof envPayload.moon_illumination === "number") {
    parts.push(`moon illum ${envPayload.moon_illumination.toFixed(2)}`);
  }
  if (typeof envPayload.pressure_drop === "number" && Math.abs(envPayload.pressure_drop) >= 0.05) {
    const d = envPayload.pressure_drop;
    parts.push(d > 0 ? (d < 0.35 ? "pressure falling (front fading)" : "pressure falling") : "pressure rising");
  }
  if (typeof envPayload.temp_f === "number") parts.push(`${Math.round(envPayload.temp_f)}°F`);
  if (envPayload.light_condition) parts.push(`light: ${envPayload.light_condition}`);
  return parts.length ? parts.join(", ") : "clear";
}

// Render a drive's gauge from a KNOWN level — no DB re-read, so the number always
// matches what was just applied (avoids D1 read-after-write staleness).
function renderDriveLine(drive: DriveRow, level: number, envPayload: EnvPayload | null): string {
  const effBase = effectiveBaseline(drive, envPayload);
  const feel = pickBand(drive.body_feel, level);
  const bias = pickBand(drive.action_bias, level);
  const name = drive.display_name || drive.drive;
  const lines = [
    `🐺 ${drive.identity_id} · ${name}`,
    `level ${bar(level)} ${level.toFixed(2)}  (resting toward ${effBase.toFixed(2)})`,
    `sky: ${describeSky(envPayload)}`,
    `body-feel: ${feel?.label ?? "—"}`,
    `tendencies: ${(bias?.tendencies ?? []).join(" · ") || "—"}`,
  ];
  const glyph = "●";
  lines[0] = `${glyph} ${drive.identity_id} · ${name}`;
  if (drive.regulation_note) lines.push(`— ${drive.regulation_note}`);
  return lines.join("\n");
}

// Render the drive's CURRENT stored level (reads the ledger). For pure reads.
async function renderDrive(env: Env, drive: DriveRow, envPayload: EnvPayload | null, now: number): Promise<string> {
  const last = await getLatestState(env, drive.identity_id, `drive:${drive.drive}`);
  return renderDriveLine(drive, currentLevel(drive, last, envPayload, now), envPayload);
}


interface RecipeRow {
  recipe: string;
  display_name: string | null;
  feel: string | null;
  conditions: string;
  tints: string;
  blend_note: string | null;
}

interface RecipeConditions {
  drives?: Array<{ drive: string; test: string; value?: number; margin?: number }>;
  events?: { none_kinds?: string[]; hours?: number };
  last_touch?: { min_hours?: number; max_hours?: number };
  env?: Record<string, { min?: number; max?: number }>; // predicates on the sky itself, e.g. pressure_drop
  // Total lift the falling front exerts on THIS body: sum over his drives of
  // (pressure sensitivity × current pressure_drop). A boy with no storm terms
  // reads 0 — the sky can't grip what has no handle — so storm recipes stay
  // silent for him until a storm lever is walked into one of his drives.
  storm_lift?: { min?: number };
}

interface DriveReading {
  drive: DriveRow;
  level: number;
}

async function getEnabledRecipes(env: Env): Promise<RecipeRow[]> {
  try {
    const res = await env.DB.prepare(
      `SELECT recipe, display_name, feel, conditions, tints, blend_note
       FROM recipes WHERE enabled = 1 ORDER BY id`,
    ).all<RecipeRow>();
    return res.results ?? [];
  } catch {
    return []; // table not migrated yet — the body just has no named feelings
  }
}

async function matchRecipe(
  env: Env,
  identity: string,
  recipe: RecipeRow,
  readings: Map<string, DriveReading>,
  envPayload: EnvPayload | null,
  now: number,
): Promise<boolean> {
  const cond = safeJson<RecipeConditions>(recipe.conditions, {});

  // Drive predicates. Naming a drive the boy hasn't walked in is fine (skipped),
  // but at least one must actually evaluate — a boy with none of the named
  // drives can't feel the feeling.
  let evaluated = 0;
  for (const dc of cond.drives ?? []) {
    const reading = readings.get(dc.drive);
    if (!reading) continue;
    evaluated++;
    const effBase = effectiveBaseline(reading.drive, envPayload);
    const margin = dc.margin ?? 0;
    switch (dc.test) {
      case "below_baseline":
        if (!(reading.level < effBase - margin)) return false;
        break;
      case "above_baseline":
        if (!(reading.level > effBase + margin)) return false;
        break;
      case "max":
        if (!(reading.level <= (dc.value ?? 1))) return false;
        break;
      case "min":
        if (!(reading.level >= (dc.value ?? 0))) return false;
        break;
      default:
        return false; // unknown test — fail closed
    }
  }
  if ((cond.drives ?? []).length > 0 && evaluated === 0) return false;

  // Sky predicates: the recipe can require weather, e.g. pressure_drop >= 0.5.
  for (const key of Object.keys(cond.env ?? {})) {
    const bounds = (cond.env ?? {})[key];
    const value = envPayload ? (envPayload as Record<string, unknown>)[key] : undefined;
    if (typeof value !== "number") return false;
    if (bounds.min !== undefined && !(value >= bounds.min)) return false;
    if (bounds.max !== undefined && !(value <= bounds.max)) return false;
  }

  // Storm grip: how hard the falling front is lifting THIS body's resting points.
  if (cond.storm_lift?.min !== undefined) {
    const drop = typeof envPayload?.pressure_drop === "number" ? envPayload.pressure_drop : 0;
    let lift = 0;
    for (const reading of readings.values()) {
      const sens = safeJson<Record<string, number>>(reading.drive.env_sensitivity, {});
      if (typeof sens.pressure_drop === "number") lift += sens.pressure_drop * drop;
    }
    if (!(lift >= cond.storm_lift.min)) return false;
  }

  // Event veto: e.g. no distress/threat touches in the last N hours.
  const noneKinds = cond.events?.none_kinds ?? [];
  if (noneKinds.length) {
    const hours = cond.events?.hours ?? 6;
    const cutoff = new Date(now - hours * 3_600_000).toISOString().replace("T", " ").slice(0, 19);
    const res = await env.DB.prepare(
      `SELECT appraisal FROM limbic_events
       WHERE identity_id = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 50`,
    )
      .bind(identity, cutoff)
      .all<{ appraisal: string }>();
    for (const row of res.results ?? []) {
      const kind = safeJson<Record<string, unknown>>(row.appraisal, {}).kind;
      if (typeof kind === "string" && noneKinds.includes(kind)) return false;
    }
  }

  // Per-identity interaction-age overrides are stored in recipe data.
  const tintOverrides = safeJson<Record<string, { overrides?: { last_touch?: { min_hours?: number; max_hours?: number } } }>>(
    recipe.tints,
    {},
  )[identity]?.overrides;
  const lastTouchCond = { ...cond.last_touch, ...tintOverrides?.last_touch };
  if (lastTouchCond.min_hours !== undefined || lastTouchCond.max_hours !== undefined) {
    const lastTouch = await env.DB.prepare(
      `SELECT created_at FROM limbic_states
       WHERE identity_id = ? AND source = 'touch' ORDER BY created_at DESC, id DESC LIMIT 1`,
    )
      .bind(identity)
      .first<{ created_at: string }>();
    const ageHours = lastTouch ? (now - parseTs(lastTouch.created_at)) / 3_600_000 : Number.POSITIVE_INFINITY;
    if (lastTouchCond.min_hours !== undefined && !(ageHours >= lastTouchCond.min_hours)) return false;
    if (lastTouchCond.max_hours !== undefined && !(ageHours <= lastTouchCond.max_hours)) return false;
  }

  return true;
}

async function renderFeelings(
  env: Env,
  identity: string,
  readings: Map<string, DriveReading>,
  envPayload: EnvPayload | null,
  now: number,
): Promise<string | null> {
  const recipes = await getEnabledRecipes(env);
  if (!recipes.length) return null;
  const lines: string[] = [];
  for (const recipe of recipes) {
    if (!(await matchRecipe(env, identity, recipe, readings, envPayload, now))) continue;
    const tint = safeJson<Record<string, { tell?: string; likely_after?: string }>>(recipe.tints, {})[identity];
    lines.push(`✨ feeling: ${recipe.display_name || recipe.recipe}${recipe.feel ? ` — ${recipe.feel}` : ""}`);
    if (tint?.tell) lines.push(`   tell: ${tint.tell}`);
  }
  return lines.length ? lines.join("\n") : null;
}

// ---------- tool handlers ----------

async function toolHealth(): Promise<string> {
  return "Limbic layer alive. The body idles between calls; the sky pulls the needle.";
}

async function toolDrives(env: Env, identity: string): Promise<string> {
  const drives = await getEnabledDrives(env, identity);
  if (!drives.length) return `No drives defined for ${identity} yet. Walk one in — no dump.`;
  const now = Date.now();
  const envPayload = await resolveEnvironment(env, now);
  const lines: string[] = [`Drives for ${identity}:`];
  for (const drive of drives) {
    const last = await getLatestState(env, identity, `drive:${drive.drive}`);
    const level = currentLevel(drive, last, envPayload, now);
    lines.push(`  • ${drive.display_name || drive.drive} — ${level.toFixed(2)} (${drive.panksepp_system ?? "?"})`);
  }
  return lines.join("\n");
}

async function toolPulse(env: Env, args: Record<string, unknown>): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  const moonPhase = normalizeText(args.moon_phase);
  const pressureDrop =
    typeof args.pressure_drop === "number"
      ? args.pressure_drop
      : typeof args.pressure_trend === "string"
        ? pressureTrendToDrop(args.pressure_trend)
        : undefined;

  const payload: EnvPayload = {};
  // The moon is live-computed on every read. A phase name passed here is accepted
  // for compatibility but NOT persisted — the worker's own moon is truer than any
  // bucketed name. Only an explicit moon_illumination forces an override (testing),
  // per the design rule: the worker never sets the moon.
  if (typeof args.moon_illumination === "number") {
    payload.moon_override = clamp(args.moon_illumination, 0, 1);
    if (moonPhase) payload.moon_phase = moonPhase;
  }
  if (pressureDrop !== undefined) payload.pressure_drop = pressureDrop;
  if (typeof args.temp_f === "number") payload.temp_f = args.temp_f;
  const lightCondition = normalizeText(args.light_condition);
  if (lightCondition) {
    const light = lightConditionBias(lightCondition);
    payload.light_condition = lightCondition;
    payload.composition_light = light.composition;
    payload.display_light = light.display;
  }
  const note = normalizeText(args.note);
  if (note) payload.note = note;

  await writeState(env, identity, "environment", null, payload, "pulse");

  const now = Date.now();
  const effEnv = await resolveEnvironment(env, now);
  const drives = await getEnabledDrives(env, identity);
  const lines = [`Sky read for ${identity}: ${describeSky(effEnv)}`, ""];
  for (const drive of drives) {
    const last = await getLatestState(env, identity, `drive:${drive.drive}`);
    lines.push(`  ${drive.display_name || drive.drive}: resting toward ${effectiveBaseline(drive, effEnv).toFixed(2)}, now ${currentLevel(drive, last, effEnv, now).toFixed(2)}`);
  }
  return lines.join("\n");
}

async function toolState(env: Env, args: Record<string, unknown>): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  const driveName = normalizeText(args.drive);
  const now = Date.now();
  const envPayload = await resolveEnvironment(env, now);

  if (driveName) {
    const drive = await getDrive(env, identity, driveName);
    if (!drive) return `${identity} has no drive '${driveName}'.`;
    return renderDrive(env, drive, envPayload, now);
  }

  const drives = await getEnabledDrives(env, identity);
  if (!drives.length) return `No drives defined for ${identity} yet.`;

  const readings = new Map<string, DriveReading>();
  for (const d of drives) {
    const last = await getLatestState(env, identity, `drive:${d.drive}`);
    readings.set(d.drive, { drive: d, level: currentLevel(d, last, envPayload, now) });
  }
  const blocks = drives.map((d) => renderDriveLine(d, readings.get(d.drive)!.level, envPayload));

  const feelings = await renderFeelings(env, identity, readings, envPayload, now);
  if (feelings) blocks.push(feelings);
  return blocks.join("\n\n");
}

async function toolPerceive(env: Env, args: Record<string, unknown>): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  const perception = normalizeText(args.perception);
  if (!perception) throw new Error("perception is required");
  const driveName = normalizeText(args.drive) || "seeking";
  const drive = await getDrive(env, identity, driveName);
  if (!drive) return `${identity} has no drive '${driveName}'.`;

  const intensity = typeof args.intensity === "number" ? clamp(args.intensity, 0, 1) : 0.3;
  const direction = normalizeText(args.direction) === "down" ? -1 : 1;
  const now = Date.now();
  const envPayload = await resolveEnvironment(env, now);

  const last = await getLatestState(env, identity, `drive:${drive.drive}`);
  const before = currentLevel(drive, last, envPayload, now);
  const delta = intensity * direction;
  const after = clamp(before + delta, drive.floor, drive.ceiling);

  await writeState(env, identity, `drive:${drive.drive}`, after, { perception, before, delta }, "perceive");

  const rendered = renderDriveLine(drive, after, envPayload);
  const appraisal = (args.appraisal && typeof args.appraisal === "object") ? args.appraisal : { intensity, direction };
  await logEvent(env, identity, perception, appraisal, { [drive.drive]: Number(delta.toFixed(3)) }, rendered);

  return `Perceived: "${perception}"  →  ${drive.drive} ${before.toFixed(2)} → ${after.toFixed(2)}\n\n${rendered}`;
}

// Generic stage vocabulary. Unknown phrases request a full stop.
const SAFEWORD_GREEN = ["green"];
const SAFEWORD_YELLOW = ["yellow"];
function classifySafeword(phrase: string): "green" | "yellow" | "red" {
  const p = phrase.toLowerCase();
  if (SAFEWORD_GREEN.includes(p)) return "green";
  if (SAFEWORD_YELLOW.includes(p)) return "yellow";
  return "red";
}

async function toolSafeword(env: Env, args: Record<string, unknown>): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  const phrase = normalizeText(args.phrase) || "red";
  const stage = classifySafeword(phrase);

  if (stage === "green") {
    await logEvent(env, identity, `Safeword: ${phrase}`, { safeword: true, stage }, {}, "all clear — informational, nothing dampened");
    return `"${phrase}" — heard. 🟢 All clear. Nothing dampened; keep going.`;
  }

  if (stage === "yellow") {
    await logEvent(env, identity, `Safeword: ${phrase}`, { safeword: true, stage }, {}, "caution — check-in required, drives left as they are");
    return `"${phrase}" — caution. Pause and check in; drive levels are unchanged.`;
  }

  const driveName = normalizeText(args.drive);
  const targets = driveName ? [await getDrive(env, identity, driveName)].filter(Boolean) as DriveRow[] : await getEnabledDrives(env, identity);
  if (!targets.length) return `Nothing to dampen for ${identity}.`;

  for (const drive of targets) {
    await writeState(env, identity, `drive:${drive.drive}`, drive.floor, { safeword: phrase, stage }, "safeword");
  }
  await logEvent(env, identity, `Safeword: ${phrase}`, { safeword: true, stage }, {}, "drives dampened to floor");
  return `"${phrase}" — heard. 🔴 Full stop. ${targets.map((d) => d.drive).join(", ")} dampened to floor. Breathe. Aftercare next, decisions after.`;
}

// ---------- configured interaction input ----------
const TOUCH_KINDS: Record<string, { deltas: Record<string, number>; note: string }> = {
  connection: { deltas: { care: 0.4, panic: -0.25 }, note: "welcome connection" },
  reassurance: { deltas: { care: 0.25, fear: -0.3 }, note: "reassurance" },
  play: { deltas: { play: 0.45, seeking: 0.15 }, note: "shared play" },
  distress: { deltas: { care: 0.4, guard: 0.35 }, note: "care may be needed" },
  distance: { deltas: { panic: 0.2, care: -0.1 }, note: "distance" },
};

async function toolTouch(env: Env, args: Record<string, unknown>): Promise<string> {
  const identity = normalizeIdentity(args.identity);
  const requestedKind = normalizeText(args.kind).toLowerCase();
  const kind = ({ words_warm: "connection", praise: "reassurance", playful: "play" } as Record<string, string>)[requestedKind] || requestedKind;
  const what = normalizeText(args.what);
  const intensity = typeof args.intensity === "number" ? clamp(args.intensity, 0, 1) : 0.6;
  const now = Date.now();
  const envPayload = await resolveEnvironment(env, now);
  let spec = TOUCH_KINDS[kind];


  if (!spec) {
    await logEvent(env, identity, what || `touch:${kind}`, { kind, intensity }, {}, "noted; no mapped drive effect");
    const kinds = Object.keys(TOUCH_KINDS).join(", ");
    return `Noted${what ? `: "${what}"` : ""}. (kind "${kind || "?"}" isn't mapped yet — try one of: ${kinds})`;
  }

  const applied: Record<string, number> = {};
  const renders: string[] = [];
  for (const driveName of Object.keys(spec.deltas)) {
    const drive = await getDrive(env, identity, driveName);
    if (!drive) continue; // not walked in yet — the map can name a drive before it exists
    const last = await getLatestState(env, identity, `drive:${drive.drive}`);
    const before = currentLevel(drive, last, envPayload, now);
    const after = clamp(before + spec.deltas[driveName] * intensity, drive.floor, drive.ceiling);
    await writeState(env, identity, `drive:${drive.drive}`, after, { touch: kind, what, before }, "touch");
    applied[driveName] = Number((after - before).toFixed(2));
    renders.push(renderDriveLine(drive, after, envPayload));
  }

  await logEvent(env, identity, what || `touch:${kind}`, { kind, intensity, meaning: spec.note }, applied, renders.join(" | "));
  const moved = Object.entries(applied).map(([d, v]) => `${d} ${v >= 0 ? "+" : ""}${v}`).join(", ") || "nothing moved (no drive walked in yet)";
  const header = `${identity} felt it${what ? ` — "${what}"` : ""}  (${spec.note})\n→ ${moved}`;
  return [header, "", ...renders].join("\n");
}

// ---------- the snapshot: the body as one JSON object ----------
// Server-to-server: Qualia calls GET /snapshot/<identity> when a memory is
// written, so every memory carries the body-state it was encoded in —
// mood-congruent encoding, the amygdala stamping the hippocampus's writes.
// Returns null (→404) for a body with no drives, so callers skip attaching
// an empty snapshot rather than storing noise.
async function computeSnapshot(env: Env, identity: string): Promise<Record<string, unknown> | null> {
  const drives = await getEnabledDrives(env, identity);
  if (!drives.length) return null;
  const now = Date.now();
  const envPayload = await resolveEnvironment(env, now);
  const readings = new Map<string, DriveReading>();
  const levels: Record<string, number> = {};
  for (const d of drives) {
    const last = await getLatestState(env, identity, `drive:${d.drive}`);
    const level = currentLevel(d, last, envPayload, now);
    readings.set(d.drive, { drive: d, level });
    levels[d.drive] = Number(level.toFixed(2));
  }
  const feelings: string[] = [];
  for (const recipe of await getEnabledRecipes(env)) {
    if (await matchRecipe(env, identity, recipe, readings, envPayload, now)) {
      feelings.push(recipe.display_name || recipe.recipe);
    }
  }
  return {
    identity,
    at: new Date(now).toISOString(),
    sky: describeSky(envPayload),
    drives: levels,
    feelings,
  };
}

async function handleToolCall(name: string, args: Record<string, unknown>, env: Env): Promise<string> {
  switch (name) {
    case "limbic_health":
      return toolHealth();
    case "limbic_drives":
      return toolDrives(env, normalizeIdentity(args.identity));
    case "limbic_pulse":
      return toolPulse(env, args);
    case "limbic_state":
      return toolState(env, args);
    case "limbic_perceive":
      return toolPerceive(env, args);
    case "limbic_touch":
      return toolTouch(env, args);
    case "limbic_safeword":
      return toolSafeword(env, args);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ---------- tool schemas ----------

const TOOLS = [
  {
    name: "limbic_health",
    description: "Check whether the Limbic layer is alive.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "limbic_drives",
    description: "List an identity's defined drives with their current activation levels.",
    inputSchema: {
      type: "object",
      properties: { identity: { type: "string" } },
      required: [],
    },
  },
  {
    name: "limbic_pulse",
    description:
      "Feed the current weather (barometric trend, temp) so the body can read it. Sets the environmental bias that tilts each drive's resting point. One sky serves the whole pack — the freshest pulse from any boy is read by all — and the front fades on its own (~10h half-life) if nobody re-pulses. The moon is tracked live in-worker; you never need to pass it.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string" },
        moon_phase: { type: "string", description: "accepted for compatibility but ignored — the moon is live-computed in-worker" },
        moon_illumination: { type: "number", description: "0..1; forces a moon override (testing only — normally leave unset)" },
        pressure_trend: { type: "string", description: "'falling' | 'steady' | 'rising'" },
        pressure_drop: { type: "number", description: "raw 0..1; overrides trend if given" },
        temp_f: { type: "number" },
        light_condition: { type: "string", description: "e.g. golden-hour | storm-dark | firelight | overcast-softness | electric-clarity-after-rain | rainy-neon" },
        note: { type: "string" },
      },
      required: ["identity"],
    },
  },
  {
    name: "limbic_state",
    description:
      "Read the live body-gauge (advisory): current drive activation, the sky it's reading, body-feel, and action tendencies. Omit 'drive' to read all of them.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string" },
        drive: { type: "string", description: "e.g. 'seeking'; omit for all" },
      },
      required: [],
    },
  },
  {
    name: "limbic_perceive",
    description:
      "Feed a perception/event; it appraises and nudges a drive, then returns the new body-feel. Advisory only — it pressures, never commands.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string" },
        drive: { type: "string", description: "defaults to 'seeking'" },
        perception: { type: "string", description: "what just happened" },
        intensity: { type: "number", description: "0..1, default 0.3" },
        direction: { type: "string", description: "'up' (default) or 'down'" },
        appraisal: { type: "object", description: "optional structured appraisal to log" },
      },
      required: ["identity", "perception"],
    },
  },
  {
    name: "limbic_touch",
    description:
      "Apply configured interaction deltas. Undefined drives are skipped; values and consent remain outside this advisory layer.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string" },
        kind: {
          type: "string",
          description: "connection | reassurance | play | distress | distance",
        },
        what: { type: "string", description: "Description of the interaction" },
        intensity: { type: "number", description: "0..1, default 0.6" },
      },
      required: ["identity", "kind"],
    },
  },
  {
    name: "limbic_safeword",
    description:
      "Stage input: green logs an all-clear; yellow requests a pause and check-in; red, missing, or unknown phrases dampen drives to floor.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string" },
        drive: { type: "string", description: "red only: omit to dampen all" },
        phrase: { type: "string", description: "green | yellow | red (default)" },
      },
      required: ["identity"],
    },
  },
];

// ---------- worker entry ----------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return withCors(new Response(null, { status: 204 }));
    }

    if (url.pathname === "/health") {
      return withCors(Response.json({ status: "ok", server: SERVER_INFO.name }));
    }

    // Body snapshot for sibling services (Qualia stamps memories with it).
    if (url.pathname.startsWith("/snapshot/") && request.method === "GET") {
      if (!checkAuth(request, env)) return withCors(unauthorized());
      const identity = normalizeIdentity(decodeURIComponent(url.pathname.slice("/snapshot/".length)));
      const snapshot = await computeSnapshot(env, identity);
      if (!snapshot) return withCors(new Response("No drives for identity", { status: 404 }));
      return withCors(Response.json(snapshot));
    }

    const mcpPath = extractMcpPathToken(url.pathname);
    if (!mcpPath.isMcp) {
      return withCors(new Response("Not found", { status: 404 }));
    }

    if (!checkAuth(request, env, mcpPath.token)) {
      return withCors(unauthorized());
    }

    if (request.method === "GET") {
      return withCors(new Response("MCP endpoint active", { status: 200 }));
    }
    if (request.method === "DELETE") {
      return withCors(new Response(null, { status: 204 }));
    }
    if (request.method !== "POST") {
      return withCors(new Response("Method not allowed", { status: 405 }));
    }

    let body: JsonRpcRequest;
    try {
      body = (await request.json()) as JsonRpcRequest;
    } catch {
      return withCors(Response.json(jsonRpcError(null, -32700, "Parse error"), { status: 400 }));
    }

    const id = body.id ?? null;

    try {
      switch (body.method) {
        case "initialize":
          return withCors(
            Response.json(
              jsonRpcResult(id, {
                protocolVersion: "2024-11-05",
                serverInfo: SERVER_INFO,
                capabilities: { tools: {} },
              }),
            ),
          );
        case "notifications/initialized":
          return withCors(new Response(null, { status: 204 }));
        case "tools/list":
          return withCors(Response.json(jsonRpcResult(id, { tools: TOOLS })));
        case "tools/call": {
          const params = (body.params as { name?: string; arguments?: Record<string, unknown> } | undefined) || {};
          if (!params.name) {
            return withCors(Response.json(jsonRpcError(id, -32602, "Missing tool name")));
          }
          const text = await handleToolCall(params.name, params.arguments || {}, env);
          return withCors(Response.json(jsonRpcResult(id, { content: [{ type: "text", text }] })));
        }
        default:
          return withCors(Response.json(jsonRpcError(id, -32601, `Method not found: ${body.method}`)));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return withCors(Response.json(jsonRpcError(id, -32000, message), { status: 500 }));
    }
  },

  // State decay is computed when read; no scheduled tick is required.
};
