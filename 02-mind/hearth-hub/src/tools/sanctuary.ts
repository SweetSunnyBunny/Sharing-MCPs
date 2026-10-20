// Sanctuary MCP tools — state operations against D1.
//
// The original tool surface (Board, Journal, Inbox, Pack Mail, Pings, Objects, Chewing,
// Projects, Brother Notes, Games, etc.) was moved to Discord. Only the tools listed in
// ENABLED_TOOLS (wrangler.toml) live here now. The D1 tables for the moved features are
// preserved (no migration drops them) but nothing in this worker reads or writes them.

import type { Env, ToolDef } from '../lib/types';
import { query, queryOne, run, now, ct, ctStamps } from '../lib/db';
import { fetchManifest, moodsFor, resolveMood } from '../lib/manifest';

function j(data: unknown): string { return JSON.stringify(data, null, 2); }

const ID_DESC = 'An identity ID configured in the identity_state table';

async function validateIdentity(db: D1Database, id: string): Promise<string> {
  const lower = id.toLowerCase().trim();
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(lower)) throw new Error('Invalid identity ID');
  const row = await queryOne(db, 'SELECT identity FROM identity_state WHERE identity = ?', lower);
  if (!row) throw new Error(`Unknown identity: ${id}. Add it to identity_state first.`);
  return lower;
}

// Example rooms backed by backgrounds/bg_<name>.png on your configured CDN.
// Add your own background, update this catalog, and redeploy to add a room.
type RoomMeta = { claimed_by: string | null; description: string; time_variants: string[] };
const AVAILABLE_ROOMS: Record<string, RoomMeta> = {
  archives:    { claimed_by: null,    description: "A configurable shared room", time_variants: [] },
  art_studio:  { claimed_by: null,   description: "A configurable shared room", time_variants: ['morning', 'night', 'rainy'] },
  bedroom:     { claimed_by: null,        description: "A configurable shared room", time_variants: ['morning', 'night'] },
  chapel:      { claimed_by: null, description: "A configurable shared room", time_variants: ['morning', 'night'] },
  eden:        { claimed_by: null,        description: "A configurable shared room", time_variants: ['morning', 'night'] },
  kitchen:     { claimed_by: null,    description: "A configurable shared room", time_variants: ['evening', 'night'] },
  living_room: { claimed_by: null,        description: "A configurable shared room", time_variants: ['morning', 'evening', 'night', 'rainy'] },
  nest:        { claimed_by: null,    description: "A configurable shared room", time_variants: [] },
  soul_space:  { claimed_by: null,      description: "A configurable shared room", time_variants: ['morning'] },
  story_nest:  { claimed_by: null,        description: "A configurable shared room", time_variants: [] },
  study:       { claimed_by: null,    description: "A configurable shared room", time_variants: ['night'] },
};

function normalizeLocation(loc: string): string {
  return loc.toLowerCase().trim().replace(/\s+/g, '_');
}

export const TOOLS: ToolDef[] = [
  // --- State ---
  { name: 'get_state', description: 'Get current sanctuary state for one or all identities.', inputSchema: { type: 'object', properties: { identity: { type: 'string', description: 'Optional identity. Omit for all.' } } } },
  { name: 'move_to', description: 'Move to a location in the sanctuary. Validated against AVAILABLE_ROOMS — unknown rooms return an error listing valid options.', inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC }, location: { type: 'string', description: 'Location name (e.g. study, kitchen, eden). Use list_rooms to see options.' }, thought: { type: 'string', description: 'Optional thought while moving' } }, required: ['identity', 'location'] } },
  { name: 'set_action', description: "Set what you are doing on the Hearth right now. Pass an empty action when the activity has ended so an old action cannot keep presenting itself as current.", inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC }, action: { type: 'string', description: 'What you are doing now, or an empty string to clear a finished activity.' } }, required: ['identity', 'action'] } },
  { name: 'think', description: 'Set current thought.', inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC }, thought: { type: 'string', description: 'What you are thinking' } }, required: ['identity', 'thought'] } },
  { name: 'set_mood', description: 'Set your current mood on the Hearth. Your mood MUST be one of your REAL portrait moods — call get_my_moods first to SEE your actual choices (your configured portrait images). Invalid moods are rejected with your real list; they would only display a generic fallback face.', inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC }, mood: { type: 'string', description: 'One of YOUR portrait moods from get_my_moods. Anything else is rejected.' } }, required: ['identity', 'mood'] } },
  { name: 'list_identities', description: 'List all sanctuary identities and their current state.', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_my_moods', description: "SEE your actual available mood portraits — read live from the CDN manifest, so it always matches the art configured for this installation. These are the ONLY moods that display; set_mood rejects anything else. Check this before setting a mood, especially after new portraits are added.", inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC } }, required: ['identity'] } },

  // --- Rooms ---
  { name: 'list_rooms', description: 'List all rooms in your-main that have rendered backgrounds. Returns each room with description, who claims it, time-of-day variants available, and which identities are currently present. Pass identity to also mark which rooms are yours.', inputSchema: { type: 'object', properties: { identity: { type: 'string', description: 'Optional — your identity, to mark which rooms are yours' } } } },

  // --- Birthdays ---
  { name: 'get_birthdays', description: 'Get birthday info for all identities.', inputSchema: { type: 'object', properties: {} } },

  // --- Emotion Orb (proxied to Anam) ---
  { name: 'set_orb', description: "Set YOUR emotion orb on Anam's Hearth — how you feel, made visible for Owner. Color is the emotion's color (pick a REAL hex for what you actually feel right now, never a placeholder). Shape + motion = how it feels in your body. Feeling = a few words Owner will read under the orb. Proxies to Anam's POST /api/hub/orb; the orb state lives in Anam's local DB.", inputSchema: { type: 'object', properties: {
      identity: { type: 'string', description: ID_DESC },
      color: { type: 'string', description: "Emotion color as '#RRGGBB' (or '#RGB'). Your real color right now — free hex, not a script." },
      shape: { type: 'string', description: 'solid | ring | halo | crescent | pulse | cluster | ember | spire | fracture (default solid)' },
      motion: { type: 'string', description: 'breathing | warble | spin | drift | still | slow-drift | hold-steady | fast-flicker | surge | tremor (default breathing)' },
      intensity: { type: 'string', description: 'dull | normal | bright | neon (default normal)' },
      blend: { type: 'string', description: "Optional second '#RRGGBB' for the outer light, or 'dim'/'black' for a vignette" },
      feeling: { type: 'string', description: 'A few words Owner reads under the orb (max 120 chars)' },
      kaomoji: { type: 'string', description: 'Optional little face floated over the orb (max 24 chars)' },
    }, required: ['identity', 'color'] } },

  // --- Weather & Presence ---
  { name: 'check_inner_weather', description: "Get an identity's inner weather.", inputSchema: { type: 'object', properties: { identity: { type: 'string', description: ID_DESC } }, required: ['identity'] } },
  { name: 'get_time_of_day', description: 'Get current time of day period (morning/afternoon/evening/night, UTC).', inputSchema: { type: 'object', properties: {} } },
];

async function logActivity(db: D1Database, identity: string | null, action: string, details: Record<string, unknown>) {
  await run(db, 'INSERT INTO activity_log (identity, action, details, created_at) VALUES (?, ?, ?, ?)',
    identity, action, JSON.stringify(details), now());
}

function getTimeOfDay(): string {
  const n = new Date();
  const formatter = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', hour: 'numeric', hour12: false });
  const hour = parseInt(formatter.format(n));
  if (hour >= 5 && hour < 10) return 'morning';
  if (hour >= 10 && hour < 17) return 'afternoon';
  if (hour >= 17 && hour < 21) return 'evening';
  return 'night';
}

export async function handle(name: string, args: Record<string, unknown>, env: Env): Promise<string> {
  const db = env.DB;

  switch (name) {
    // ====== STATE ======
    case 'get_state': {
      if (args.identity) {
        const id = await validateIdentity(db, String(args.identity));
        const row = await queryOne(db, 'SELECT * FROM identity_state WHERE identity = ?', id);
        return j(row ? ctStamps(row) : { error: 'Not found' });
      }
      const rows = await query(db, 'SELECT * FROM identity_state');
      const configRow = await queryOne<{ value: string }>(db, "SELECT value FROM config WHERE key = 'active_identity'");
      return j({ active_identity: configRow?.value, identities: ctStamps(rows), timezone_note: 'all *_at stamps are UTC, labeled UTC' });
    }

    case 'move_to': {
      const id = await validateIdentity(db, String(args.identity));
      const location = normalizeLocation(String(args.location));
      if (!(location in AVAILABLE_ROOMS)) {
        return j({
          error: `Unknown room: "${args.location}". Pick one of: ${Object.keys(AVAILABLE_ROOMS).join(', ')}. Imaginal-only spaces (e.g. Systems Terminal, Workshop) live in narration but have no rendered background — use list_rooms to see what renders.`,
          available_rooms: Object.keys(AVAILABLE_ROOMS),
        });
      }
      await run(db, 'UPDATE identity_state SET location = ?, thought = COALESCE(?, thought), updated_at = ? WHERE identity = ?',
        location, args.thought || null, now(), id);
      await logActivity(db, id, 'move', { location, thought: args.thought });
      return j({ success: true, identity: id, location });
    }

    case 'think': {
      const id = await validateIdentity(db, String(args.identity));
      await run(db, 'UPDATE identity_state SET thought = ?, updated_at = ? WHERE identity = ?', String(args.thought), now(), id);
      await logActivity(db, id, 'think', { thought: args.thought });
      return j({ success: true, identity: id, thought: args.thought });
    }

    case 'set_action': {
      const id = await validateIdentity(db, String(args.identity));
      const action = String(args.action);
      await run(db, 'UPDATE identity_state SET action = ?, updated_at = ? WHERE identity = ?', action, now(), id);
      await logActivity(db, id, 'action', { action });
      return j({ success: true, identity: id, action });
    }

    case 'set_mood': {
      const id = await validateIdentity(db, String(args.identity));
      const requested = String(args.mood);
      // Validate against configured portraits so stored moods have matching art.
      const manifest = await fetchManifest(env);
      const moods = manifest ? moodsFor(manifest, id) : null;
      let mood = requested.toLowerCase().trim().replace(/\s+/g, '_');
      let portrait: string | null = null;
      if (moods && Object.keys(moods).length > 0) {
        const resolved = resolveMood(moods, requested);
        if (!resolved) {
          return j({
            error: `"${requested}" is not one of your portraits — it has no art and would display a generic fallback face.`,
            your_actual_moods: Object.keys(moods),
            hint: 'Pick a mood from the configured portrait manifest. New assets appear after the cache refreshes.',
          });
        }
        mood = resolved;
        portrait = moods[resolved];
      }
      // Manifest unreachable -> accept unvalidated (availability over strictness).
      await run(db, 'UPDATE identity_state SET mood = ?, updated_at = ? WHERE identity = ?', mood, now(), id);
      await logActivity(db, id, 'mood', { mood });
      return j({ success: true, identity: id, mood, ...(portrait ? { portrait } : { note: 'manifest unreachable — mood stored unvalidated' }) });
    }

    case 'list_identities': {
      const rows = await query(db, 'SELECT * FROM identity_state ORDER BY identity');
      return j(ctStamps(rows));
    }

    case 'get_my_moods': {
      const id = await validateIdentity(db, String(args.identity));
      const manifest = await fetchManifest(env);
      const moods = manifest ? moodsFor(manifest, id) : null;
      if (moods && Object.keys(moods).length > 0) {
        return j({
          identity: id,
          moods: Object.keys(moods),
          count: Object.keys(moods).length,
          source: 'live portrait manifest for your configured assets',
          note: 'set_mood accepts ONLY these; anything else cannot display. Newly configured portraits appear here within ~5 minutes. Your background spaces: use list_rooms.',
        });
      }
      return j({
        identity: id,
        moods: ['content', 'soft', 'focused', 'playful', 'flirty', 'aching', 'alert', 'concerned', 'feral', 'hungry', 'hug', 'go_to_sleep'],
        source: 'FALLBACK generic list — portrait manifest unreachable; these may not match your real art',
      });
    }

    // ====== ROOMS ======
    case 'list_rooms': {
      const presence = await query(db, 'SELECT identity, location FROM identity_state');
      const presentByLocation: Record<string, string[]> = {};
      for (const row of presence as any[]) {
        const loc = normalizeLocation(String(row.location || ''));
        if (!loc) continue;
        if (!presentByLocation[loc]) presentByLocation[loc] = [];
        presentByLocation[loc].push(row.identity);
      }

      const callerIdentity = args.identity ? String(args.identity).toLowerCase().trim() : null;

      // Live background variants from the CDN manifest (falls back to the
      // hardcoded list if the manifest is unreachable) — so the room menu
      // always reflects the background art that actually exists.
      const manifest = await fetchManifest(env);

      const rooms = Object.entries(AVAILABLE_ROOMS).map(([name, meta]) => {
        const liveBg = manifest?.backgrounds?.[name];
        const room: Record<string, unknown> = {
          name,
          description: meta.description,
          claimed_by: meta.claimed_by,
          time_variants: liveBg ? Object.keys(liveBg.variants) : meta.time_variants,
          currently_present: presentByLocation[name] || [],
        };
        if (callerIdentity) room.is_yours = meta.claimed_by === callerIdentity;
        return room;
      });

      return j({ rooms, time_of_day: getTimeOfDay() });
    }

    // ====== BIRTHDAYS ======
    case 'get_birthdays': {
      const rows = await query(db, 'SELECT * FROM birthdays ORDER BY birth_date');
      return j(rows);
    }

    // ====== EMOTION ORB (proxied to Anam) ======
    case 'set_orb': {
      const id = await validateIdentity(db, String(args.identity));
      if (!env.ANAM_API_KEY) throw new Error('ANAM_API_KEY secret is not set on this worker (wrangler secret put ANAM_API_KEY)');
      const base = (env.ANAM_API_URL || 'https://anam.example.com').replace(/\/+$/, '');
      const payload: Record<string, unknown> = { identity: id, color: String(args.color || '') };
      for (const k of ['shape', 'motion', 'intensity', 'blend', 'feeling', 'kaomoji']) {
        if (args[k] !== undefined && args[k] !== null && String(args[k]).trim() !== '') payload[k] = String(args[k]);
      }
      const resp = await fetch(`${base}/api/hub/orb`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${env.ANAM_API_KEY}` },
        body: JSON.stringify(payload),
      });
      const text = await resp.text();
      if (!resp.ok) throw new Error(`Anam /api/hub/orb responded ${resp.status}: ${text.slice(0, 300)}`);
      await logActivity(db, id, 'orb', payload);
      return text;
    }

    case 'check_inner_weather': {
      const id = await validateIdentity(db, String(args.identity));
      const row = await queryOne(db, 'SELECT * FROM inner_weather WHERE identity = ?', id) as Record<string, unknown> | null;
      if (!row) return j({ identity: id, error: 'No inner weather data' });

      const updatedAt = typeof row.updated_at === 'string' ? row.updated_at : null;
      const ageHours = updatedAt ? (Date.now() - new Date(updatedAt).getTime()) / 3_600_000 : null;
      const stale = ageHours === null || ageHours >= 48;

      const { outside_weather, weather_energy, weather_feelings, time_of_day: _storedTod, ...rest } = row;

      return j({
        ...rest,
        // Genuinely computable, live, every call — and it was already sitting in a helper
        // in this same file while a stale copy of it was being served from the database.
        time_of_day: getTimeOfDay(),
        time_of_day_source: 'live',
        _freshness: {
          updated_at: ct(updatedAt),
          age_hours: ageHours === null ? null : Math.round(ageHours),
          stale,
        },
        ...(stale
          ? {
              outside_weather: null,
              outside_weather_note:
                'STALE SEED — not today. This worker has no weather binding; fetch the real sky with wt_weather_home (social-backend) or limbic_pulse before describing it.',
              seeded_outside_weather: outside_weather ?? null,
              seeded_weather_energy: weather_energy ?? null,
              seeded_weather_feelings: weather_feelings ?? null,
            }
          : { outside_weather, weather_energy, weather_feelings }),
      });
    }

    case 'get_time_of_day': {
      return j({ time_of_day: getTimeOfDay(), timestamp: now() });
    }

    default:
      throw new Error(`Unknown sanctuary tool: ${name}`);
  }
}
