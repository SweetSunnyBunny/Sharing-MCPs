// REST API endpoints for Anam's sanctuary-viewer.js + the Anam server's reverse proxy
// at api/sanctuary.py.
//
// Most of the original surface (board, journal, pack-mail, projects, inbox, etc.) was
// removed when those features moved to Discord. Only the three endpoints actually
// consumed are kept here:
//   - /state              → sanctuary-viewer.js + services/sanctuary_reader.py
//   - /background/<id>    → sanctuary-viewer.js
//   - /portrait/<id>?mood → sanctuary-viewer.js

import type { Env } from '../lib/types';
import { query, queryOne, ct } from '../lib/db';
import { CORS_HEADERS } from '../lib/mcp';
import { fetchManifest, moodsFor, resolveMood } from '../lib/manifest';

function getTimeOfDay(): string {
  // Generic installation default; align this timezone with the tools module.
  const now = new Date();
  const formatter = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', hour: 'numeric', hour12: false });
  const hour = parseInt(formatter.format(now));
  if (hour >= 5 && hour < 10) return 'morning';
  if (hour >= 10 && hour < 17) return 'afternoon';
  if (hour >= 17 && hour < 21) return 'evening';
  return 'night';
}

function jsonResponse(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: CORS_HEADERS });
}

/** GET /api/sanctuary/state — full state for viewer */
export async function handleState(env: Env): Promise<Response> {
  const rows = await query(env.DB, 'SELECT * FROM identity_state');
  const configRow = await queryOne<{ value: string }>(env.DB, "SELECT value FROM config WHERE key = 'active_identity'");

  const identities: Record<string, any> = {};
  for (const row of rows) {
    const r = row as any;
    identities[r.identity.charAt(0).toUpperCase() + r.identity.slice(1)] = {
      location: r.location,
      action: r.action,
      thought: r.thought,
      mood: r.mood,
      focus: r.focus,
      last_updated: ct(r.updated_at),
    };
  }

  return jsonResponse({
    active_identity: configRow?.value ?? Object.keys(identities)[0] ?? null,
    identities,
    time_of_day: getTimeOfDay(),
  });
}

/** GET /api/sanctuary/portrait/:identity?mood=X — redirect to CDN.
 *
 * Resolve exact, case-sensitive paths from the configured CDN manifest.
 * Use legacy HEAD probes when that manifest cannot resolve the identity. */
export async function handlePortrait(env: Env, identity: string, url: URL): Promise<Response> {
  const mood = url.searchParams.get('mood') || 'Content';
  const cdnBase = env.ASSET_CDN_BASE;

  if (!cdnBase) {
    return jsonResponse({ error: 'ASSET_CDN_BASE not configured' }, 404);
  }

  const capIdentity = identity.charAt(0).toUpperCase() + identity.slice(1);
  const moodWord = mood.split(',')[0].trim();

  const manifest = await fetchManifest(env);
  const moods = manifest ? moodsFor(manifest, identity) : null;
  if (moods && Object.keys(moods).length > 0) {
    const resolved = resolveMood(moods, moodWord);
    const path = resolved
      ? moods[resolved]
      : (moods['thinking'] ?? moods['joy'] ?? Object.values(moods)[0]);
    if (path) return Response.redirect(`${cdnBase}/${encodeURI(path)}`, 302);
  }

  // Legacy probe chain (manifest unreachable / unknown identity)
  const capMood = moodWord.charAt(0).toUpperCase() + moodWord.slice(1).toLowerCase();
  const moodUrl = `${cdnBase}/portraits/${capIdentity}/Portrait_${capMood}.png`;
  const fallbackMood = 'Thinking';
  const fallbackUrl = `${cdnBase}/portraits/${capIdentity}/Portrait_${fallbackMood}.png`;

  const check = await fetch(moodUrl, { method: 'HEAD' });
  return Response.redirect(check.ok ? moodUrl : fallbackUrl, 302);
}

/** GET /api/sanctuary/background/:identity — redirect to CDN based on location + time */
export async function handleBackground(env: Env, identity: string): Promise<Response> {
  const cdnBase = env.ASSET_CDN_BASE;
  if (!cdnBase) {
    return jsonResponse({ error: 'ASSET_CDN_BASE not configured' }, 404);
  }

  const lowerIdentity = identity.toLowerCase();
  const row = await queryOne<{ location: string }>(env.DB, 'SELECT location FROM identity_state WHERE identity = ?', lowerIdentity);
  const location = (row?.location || 'unknown').toLowerCase().replace(/\s+/g, '_');
  const timeOfDay = getTimeOfDay();

  // Resolution order (first existing wins):
  //   1. Per-identity background  bg_<identity>.
  //      Drop a bg_<name>.png on the CDN and that identity auto-switches to it.
  //   2. Room background          bg_<location>.
  //   3. Shared fallback          bg_nest.
  // Time-of-day variant (bg_<x>_<time>.png) is preferred over the base at each tier.
  const candidates = [
    `${cdnBase}/backgrounds/bg_${lowerIdentity}_${timeOfDay}.png`,
    `${cdnBase}/backgrounds/bg_${lowerIdentity}.png`,
    `${cdnBase}/backgrounds/bg_${location}_${timeOfDay}.png`,
    `${cdnBase}/backgrounds/bg_${location}.png`,
    `${cdnBase}/backgrounds/bg_nest_${timeOfDay}.png`,
    `${cdnBase}/backgrounds/bg_nest.png`,
  ];

  for (const url of candidates) {
    const check = await fetch(url, { method: 'HEAD' });
    if (check.ok) return Response.redirect(url, 302);
  }

  // Installations using this fallback must provide bg_nest.png on their CDN.
  return Response.redirect(`${cdnBase}/backgrounds/bg_nest.png`, 302);
}

/** Route API requests */
export async function handleApiRequest(env: Env, path: string, url: URL): Promise<Response | null> {
  // Strip /api/sanctuary prefix
  const sub = path.replace(/^\/api\/sanctuary\/?/, '');

  if (sub === 'state' || sub === '') return handleState(env);

  // Portrait: portrait/<identity>
  const portraitMatch = sub.match(/^portrait\/(\w+)$/);
  if (portraitMatch) return handlePortrait(env, portraitMatch[1], url);

  // Background: background/<identity>
  const bgMatch = sub.match(/^background\/(\w+)$/);
  if (bgMatch) return handleBackground(env, bgMatch[1]);

  return null; // not matched
}
