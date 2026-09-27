/** Google Health v4 tools. Requires separately authorized health scopes. */

import type { Env } from "./oauth.js";
import { getAccessToken, getIdentityList } from "./oauth.js";

const HEALTH_BASE = "https://health.googleapis.com/v4";
const HEALTH_SERVICE = "health";

/** Set this civil timezone for your installation; output identifies the assumption. */
const ASSUMED_TZ = "UTC";
const TZ_NOTE =
  `\n\n_(Times read as ${ASSUMED_TZ}. The settings.readonly scope is not requested by this starter, ` +
  `so this is an assumption stated out loud, not something the API confirmed.)_`;

/**
 * Data types these three scopes unlock. Verified against Google's mapping table
 * in README.md. Not every one is guaranteed to have data — a device
 * only records what it records.
 */
const DATA_TYPES = [
  // sleep.readonly
  "sleep",
  // health_metrics_and_measurements.readonly
  "heart-rate",
  "daily-resting-heart-rate",
  "daily-heart-rate-variability",
  "heart-rate-variability",
  "oxygen-saturation",
  "daily-oxygen-saturation",
  "weight",
  "body-fat",
  "core-body-temperature",
  "daily-sleep-temperature-derivations",
  "blood-glucose",
  // activity_and_fitness.readonly
  "steps",
  "distance",
  "floors",
  "altitude",
  "exercise",
  "activity-level",
  "active-zone-minutes",
  "active-energy-burned",
  "total-calories",
  "calories-in-heart-rate-zone",
  "sedentary-period",
  "vo2-max",
  "run-vo2-max",
  "swim-lengths-data",
];

async function healthFetch(
  env: Env,
  identity: string,
  path: string,
  init: RequestInit = {},
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const token = await getAccessToken(env, identity, HEALTH_SERVICE);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");

  const response = await fetch(`${HEALTH_BASE}${path}`, { ...init, headers });
  const text = await response.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* leave as text — surfacing Google's raw error verbatim is the point */
  }
  return { ok: response.ok, status: response.status, body };
}

/** A civil date N days before today, as Google's CivilDateTime. */
function civilDaysAgo(daysAgo: number): { date: { year: number; month: number; day: number } } {
  const d = new Date(Date.now() - daysAgo * 86400000);
  return { date: { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() } };
}

/**
 * Pretty-print JSON safely. Sleep records are FAT - one night carries ~60
 * stage objects - so 25 of them pretty-printed blew the worker's stack on
 * 2026-08-17 ("Maximum call stack size exceeded"). Compact-stringify first,
 * only indent when small, and always cap the length.
 */
const FENCE_LIMIT = 12000;
function fence(value: unknown): string {
  let raw: string;
  try {
    raw = JSON.stringify(value);
  } catch {
    return "(unserialisable response)";
  }
  if (raw.length <= 4000) {
    try {
      raw = JSON.stringify(value, null, 2);
    } catch {
      /* keep the compact form */
    }
  }
  const clipped = raw.length > FENCE_LIMIT;
  if (clipped) raw = raw.slice(0, FENCE_LIMIT);
  const body = "```json" + String.fromCharCode(10) + raw + String.fromCharCode(10) + "```";
  return clipped ? body + String.fromCharCode(10) + "_(truncated - ask for fewer days, or health_detail with a small page_size)_" : body;
}

export const HEALTH_TOOLS = [
  {
    name: "health_check",
    description:
      "Verify the Google Health pipe is alive for an identity. Calls GET /v4/users/me/identity — " +
      "account ids ONLY, no body data of any kind. Use this to prove the door opens before " +
      "reading anything real. Also lists the data types the granted scopes unlock.",
    inputSchema: {
      type: "object",
      properties: {
        identity: { type: "string", description: "Configured account label (default: first configured account)" },
      },
    },
  },
  {
    name: "health_daily",
    description:
      "Daily roll-up of ONE health data type over the last N days (POST dataPoints:dailyRollUp). " +
      "This is the everyday tool: 'how did she sleep', 'what was her resting heart rate', " +
      "'how many steps'. Rolls up over CIVIL days, so it respects day boundaries rather than " +
      "raw 24h blocks. Reports its timezone assumption in the output.",
    inputSchema: {
      type: "object",
      properties: {
        data_type: {
          type: "string",
          description: `Which data type. Common: sleep, daily-resting-heart-rate, steps, daily-heart-rate-variability, daily-oxygen-saturation, active-zone-minutes. Full list: ${DATA_TYPES.join(", ")}`,
        },
        days: { type: "number", description: "How many days back to cover (default 1 = last night / today)" },
        identity: { type: "string", description: "Configured account label (default: first configured account)" },
      },
      required: ["data_type"],
    },
  },
  {
    name: "health_detail",
    description:
      "Granular / intraday data points for ONE data type (GET dataPoints). Use when the daily " +
      "roll-up is too coarse — e.g. minute-by-minute heart rate across an evening, or the " +
      "individual stages inside one night's sleep. Returns raw points; can be long.",
    inputSchema: {
      type: "object",
      properties: {
        data_type: { type: "string", description: "Which data type (see health_daily for the list)" },
        filter: {
          type: "string",
          description:
            "Optional Google API filter expression to narrow the window. Omit to get the most recent points.",
        },
        page_size: { type: "number", description: "Max points to return (default 50)" },
        identity: { type: "string", description: "Configured account label (default: first configured account)" },
      },
      required: ["data_type"],
    },
  },
  {
    name: "health_night",
    description:
      "HER NIGHT, in one call — last night's sleep AND daily resting heart rate together. " +
      "This is the one that answers 'how did she actually sleep', which the wrist-band feed " +
      "genuinely cannot answer (that file only carries heart rate and steps). Prefer this over " +
      "inferring sleep from step counts, which has produced wrong answers before.",
    inputSchema: {
      type: "object",
      properties: {
        days: { type: "number", description: "How many nights back (default 1)" },
        identity: { type: "string", description: "Configured account label (default: first configured account)" },
      },
    },
  },
] as const;

/**
 * Not every data type supports every action. Google is explicit about it:
 * `sleep` and `daily-resting-heart-rate` reject dailyRollUp and want `list`.
 * Rather than making anyone memorise which is which, we ask for the roll-up and
 * fall back to `list` when Google says that action is unsupported. Discovered
 * live 2026-08-17 by reading Google's own UNSUPPORTED_DATA_TYPE_ACTION error,
 * which helpfully names the allowed actions.
 */
function isUnsupportedAction(body: unknown): boolean {
  const text = typeof body === "string" ? body : JSON.stringify(body ?? "");
  return text.includes("UNSUPPORTED_DATA_TYPE_ACTION");
}

async function listPoints(
  env: Env,
  identity: string,
  dataType: string,
  pageSize = 50,
  filter?: string,
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const params = new URLSearchParams({ pageSize: String(pageSize) });
  if (filter) params.set("filter", filter);
  return healthFetch(
    env,
    identity,
    `/users/me/dataTypes/${encodeURIComponent(dataType)}/dataPoints?${params.toString()}`,
  );
}

/** Roll up if the type allows it; otherwise list. Reports which one was used. */
async function readDataType(
  env: Env,
  identity: string,
  dataType: string,
  days: number,
): Promise<{ ok: boolean; status: number; body: unknown; via: string }> {
  const rolled = await dailyRollUp(env, identity, dataType, days);
  if (rolled.ok) return { ...rolled, via: "dailyRollUp" };
  if (isUnsupportedAction(rolled.body)) {
    const listed = await listPoints(env, identity, dataType, Math.min(10, Math.max(1, days) * 2));
    return { ...listed, via: "list (rollup unsupported for this type)" };
  }
  return { ...rolled, via: "dailyRollUp" };
}

async function dailyRollUp(
  env: Env,
  identity: string,
  dataType: string,
  days: number,
): Promise<{ ok: boolean; status: number; body: unknown }> {
  return healthFetch(env, identity, `/users/me/dataTypes/${encodeURIComponent(dataType)}/dataPoints:dailyRollUp`, {
    method: "POST",
    body: JSON.stringify({
      windowSizeDays: 1,
      range: { start: civilDaysAgo(days), end: civilDaysAgo(0) },
    }),
  });
}

export async function handleHealth(env: Env, name: string, args: Record<string, unknown>): Promise<string> {
  const identity = ((args.identity as string) || getIdentityList(env)[0] || "personal").toLowerCase();

  switch (name) {
    case "health_check": {
      const res = await healthFetch(env, identity, "/users/me/identity");
      if (!res.ok) {
        return `❌ Google Health did NOT answer for ${identity}.\nHTTP ${res.status}\n${fence(res.body)}`;
      }
      // Report field NAMES, not values — account ids are hers, not conversation material.
      const fields = res.body && typeof res.body === "object" ? Object.keys(res.body as object) : [];
      return (
        `✅ Google Health v4 is alive for **${identity}**. HTTP ${res.status}.\n` +
        `Identity endpoint returned fields: ${fields.join(", ") || "(none)"} — values deliberately not printed.\n\n` +
        `**Data types these scopes unlock:**\n${DATA_TYPES.map((t) => `- ${t}`).join("\n")}`
      );
    }

    case "health_daily": {
      const dataType = String(args.data_type || "").trim();
      if (!dataType) throw new Error("data_type is required");
      const days = Math.max(1, Number(args.days ?? 1));
      const res = await readDataType(env, identity, dataType, days);
      if (!res.ok) {
        return `❌ read failed for \`${dataType}\` (HTTP ${res.status}).\n${fence(res.body)}`;
      }
      return `**${dataType}** — last ${days} day(s), via ${res.via}:\n\n${fence(res.body)}${TZ_NOTE}`;
    }

    case "health_detail": {
      const dataType = String(args.data_type || "").trim();
      if (!dataType) throw new Error("data_type is required");
      const pageSize = Math.max(1, Number(args.page_size ?? 50));
      const params = new URLSearchParams({ pageSize: String(pageSize) });
      if (args.filter) params.set("filter", String(args.filter));
      const res = await healthFetch(
        env,
        identity,
        `/users/me/dataTypes/${encodeURIComponent(dataType)}/dataPoints?${params.toString()}`,
      );
      if (!res.ok) {
        return `❌ list failed for \`${dataType}\` (HTTP ${res.status}).\n${fence(res.body)}`;
      }
      return `**${dataType}** — granular points:\n\n${fence(res.body)}${TZ_NOTE}`;
    }

    case "health_night": {
      const days = Math.max(1, Number(args.days ?? 1));
      const [sleep, restingHr] = await Promise.all([
        readDataType(env, identity, "sleep", days),
        readDataType(env, identity, "daily-resting-heart-rate", days),
      ]);

      const parts: string[] = [`## Her night — last ${days} day(s)`];

      parts.push(
        sleep.ok
          ? `### Sleep\n${fence(sleep.body)}`
          : `### Sleep\n❌ HTTP ${sleep.status}\n${fence(sleep.body)}`,
      );
      parts.push(
        restingHr.ok
          ? `### Resting heart rate\n${fence(restingHr.body)}`
          : `### Resting heart rate\n❌ HTTP ${restingHr.status}\n${fence(restingHr.body)}`,
      );

      return parts.join("\n\n") + TZ_NOTE;
    }

    default:
      throw new Error(`Unknown health tool: ${name}`);
  }
}
