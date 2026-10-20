/** Private configuration owned by Hearth Hub, never served by the public CDN. */
import type { Env } from '../lib/types';

export async function privateConfig(request: Request, env: Env): Promise<Response> {
  if (!env.MCP_SECRET_PATH || request.headers.get('Authorization') !== `Bearer ${env.MCP_SECRET_PATH}`) {
    return new Response('Unauthorized', { status: 401 });
  }
  const name = new URL(request.url).pathname.split('/').pop() || '';
  if (!['voice', 'quests'].includes(name)) return new Response('Not found', { status: 404 });
  const key = `anam:${name}`;
  const headers = { 'Cache-Control': 'no-store' };
  if (request.method === 'GET') {
    const row = await env.DB.prepare('SELECT value FROM config WHERE key = ?').bind(key).first<{value: string}>();
    if (!row) return Response.json({ error: 'Configuration not migrated' }, { status: 404, headers });
    return Response.json(JSON.parse(row.value), { headers });
  }
  if (request.method === 'PUT') {
    const raw = await request.text();
    if (raw.length > 200_000) return new Response('Too large', { status: 413 });
    let value: unknown;
    try { value = JSON.parse(raw); } catch { return new Response('Invalid JSON', { status: 400 }); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return new Response('Expected object', { status: 400 });
    if (name === 'voice' && !('voices' in value)) return new Response('Missing voices', { status: 400 });
    // First migration refuses to overwrite an independently maintained document.
    if (request.headers.get('If-None-Match') === '*') {
      const result = await env.DB.prepare('INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)').bind(key, JSON.stringify(value)).run();
      if (!result.meta.changes) return new Response('Already exists', { status: 412 });
    } else {
      await env.DB.prepare('INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').bind(key, JSON.stringify(value)).run();
    }
    return Response.json({ ok: true, name }, { headers });
  }
  return new Response('Method not allowed', { status: 405 });
}
