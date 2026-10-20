// Portrait/background manifest client with exact asset paths and bounded caching.

import type { Env } from './types';

export interface PortraitManifest {
  generated_at?: string;
  folders?: Record<string, string>;
  portraits?: Record<string, { moods: Record<string, string> }>;
  backgrounds?: Record<string, { base: string | null; variants: Record<string, string> }>;
}

export async function fetchManifest(env: Env): Promise<PortraitManifest | null> {
  const base = (env.ASSET_CDN_BASE || '').replace(/\/+$/, '');
  if (!base) return null;
  try {
    const resp = await fetch(`${base}/portraits/manifest.json`, {
      cf: { cacheEverything: true, cacheTtlByStatus: { '200-299': 300, '400-499': 0, '500-599': 0 } },
    } as RequestInit);
    if (!resp.ok) return null;
    return (await resp.json()) as PortraitManifest;
  } catch {
    return null;
  }
}

/** Resolve an identity (any case) to its exact CDN folder name. */
export function manifestFolder(m: PortraitManifest, identity: string): string | null {
  const lower = identity.toLowerCase().trim();
  if (m.folders && m.folders[lower]) return m.folders[lower];
  for (const k of Object.keys(m.portraits || {})) {
    if (k.toLowerCase() === lower) return k;
  }
  return null;
}

/** Mood key -> exact repo path map for an identity, or null if unknown. */
export function moodsFor(m: PortraitManifest, identity: string): Record<string, string> | null {
  const folder = manifestFolder(m, identity);
  if (!folder) return null;
  return m.portraits?.[folder]?.moods ?? null;
}

/** Look up a mood tolerantly: exact, spaces->underscores, underscores->spaces. */
export function resolveMood(moods: Record<string, string>, requested: string): string | null {
  const raw = requested.toLowerCase().trim();
  const underscored = raw.replace(/\s+/g, '_');
  const spaced = raw.replace(/_+/g, ' ');
  for (const key of [raw, underscored, spaced]) {
    if (moods[key]) return key;
  }
  return null;
}
