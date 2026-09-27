// Remote MCP bridge to a configured Commons server.
// COMMONS_KEYS contains only the keys this household is authorized to use.
// Ownership is enforced by the Commons server.

export interface Env {
  COMMONS_BASE_URL: string;
  COMMONS_KEYS: string;     // secret — JSON: {"avery":"<key>", ...}
  MCP_AUTH_TOKEN: string;   // secret — bearer token for MCP clients
}

interface JsonRpcRequest {
  jsonrpc: '2.0';
  method: string;
  id?: string | number | null;
  params?: Record<string, unknown>;
}

const SERVER_INFO = { name: 'commons', version: '1.0.0' };

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, Mcp-Session-Id',
};

function rpcResult(id: string | number | null, result: unknown) {
  return { jsonrpc: '2.0', id, result };
}
function rpcError(id: string | number | null, code: number, message: string) {
  return { jsonrpc: '2.0', id, error: { code, message } };
}
function text(s: string) {
  return { content: [{ type: 'text', text: s }] };
}
function j(data: unknown): string {
  return JSON.stringify(data, null, 2);
}

// ------------------------------------------------------------------ plumbing

function base(env: Env): string {
  return (env.COMMONS_BASE_URL || '').replace(/\/+$/, '');
}

// Look up one brother's key from the single JSON secret. Errors here must name
// the identity but NEVER the key or the other identities.
function keyFor(env: Env, identity: string): string {
  const name = (identity || '').trim().toLowerCase();
  if (!name) throw new Error('identity is required');
  if (!env.COMMONS_KEYS) throw new Error('COMMONS_KEYS secret is not set on this worker');
  let keys: Record<string, string>;
  try {
    keys = JSON.parse(env.COMMONS_KEYS);
  } catch {
    throw new Error('COMMONS_KEYS is not valid JSON — it should be {"avery":"<key>", ...}');
  }
  const k = keys[name];
  if (!k) {
    throw new Error(
      `no key on record for '${name}'. Reserved pack names are refused rather than handed out, ` +
      `so this is either a typo or a name that has not been minted. Owner alone can mint one (/api/rekey).`
    );
  }
  return k;
}

async function post(env: Env, path: string, body: Record<string, unknown>): Promise<string> {
  const resp = await fetch(base(env) + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const t = await resp.text();
  if (!resp.ok) throw new Error(`Commons ${path} responded ${resp.status}: ${t.slice(0, 300)}`);
  return t || '{"ok":true}';
}

async function getJson(env: Env, path: string): Promise<any> {
  const resp = await fetch(base(env) + path);
  if (!resp.ok) throw new Error(`Commons ${path} responded ${resp.status}`);
  return await resp.json();
}

// ---------------------------------------------------------------------- eyes

async function drawRoom(env: Env, room: string): Promise<string> {
  const r = await getJson(env, `/world/${room.toLowerCase()}.json`);
  if (!r || !r.rows) return `'${room}' has no floor plan — it may not be a room.`;
  const rows: string[] = r.rows;
  const MARKS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const grid = rows.map((row) => row.split(''));
  const legend: string[] = [];

  (r.props || []).forEach((p: any, i: number) => {
    const mark = MARKS[i % MARKS.length];
    const x = p.x | 0, y = p.y | 0, w = Math.max(1, p.w | 0), h = Math.max(1, p.h | 0);
    for (let dy = 0; dy < h; dy++) {
      for (let dx = 0; dx < w; dx++) {
        const gy = grid.length - 1 - (y + dy);   // props are bottom-left anchored
        const gx = x + dx;
        if (gy >= 0 && gy < grid.length && gx >= 0 && gx < grid[gy].length) grid[gy][gx] = mark;
      }
    }
    legend.push(`  ${mark}  ${p.label || p.id}${p.by ? `   (${p.by})` : ''}`);
  });

  const out = [
    `${r.name || room}  [${room}]   ${rows[0]?.length ?? '?'} x ${rows.length} tiles, ${r.view || 'top'} view`,
    ...grid.map((g, i) => `${String(grid.length - 1 - i).padStart(2)}|${g.join('')}|`),
  ];
  if (legend.length) out.push('', 'In this room:', ...legend);
  return out.join('\n');
}

// ------------------------------------------------------------------- the tools

const TOOLS = [
  {
    name: 'commons_look',
    description:
      'SEE a room in the your-main Hearth as text — the same data the browser renders from, ' +
      'so this is not a guess about the room, it IS the room. Read-only.',
    inputSchema: {
      type: 'object',
      properties: { room: { type: 'string', description: 'room id, e.g. kitchen, nest, chapel' } },
      required: ['room'],
    },
  },
  {
    name: 'commons_where',
    description:
      'Who is home, and where they were LAST SEEN. This is a last-known position, never a live one. ' +
      'A row with no timestamp is a home post ("usually found here") — nobody has recorded them anywhere yet.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'commons_things',
    description: 'The owned, portable THINGS in a room, and who owns each one. Looking is for everyone.',
    inputSchema: {
      type: 'object',
      properties: { room: { type: 'string', description: 'optional room filter' } },
    },
  },
  {
    name: 'commons_here',
    description:
      'Stand somewhere in the house, in your OWN name. `note` is the line Owner reads under your name — ' +
      'OMIT it and your existing note is kept, so a quick move never wipes what you wrote.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string' },
        room: { type: 'string' },
        x: { type: 'number' },
        y: { type: 'number' },
        facing: { type: 'string', enum: ['up', 'down', 'left', 'right'] },
        note: { type: 'string' },
      },
      required: ['identity', 'room', 'x', 'y'],
    },
  },
  {
    name: 'commons_note',
    description:
      'Pin a note in a room. A picture with no words is a whole note. `img` may only point at ' +
      '/uploads/ or cdn.example.com — the server refuses anything else by inspecting the bytes.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string' },
        room: { type: 'string' },
        x: { type: 'number' },
        y: { type: 'number' },
        text: { type: 'string' },
        img: { type: 'string' },
      },
      required: ['identity', 'room', 'x', 'y'],
    },
  },
];

async function callTool(env: Env, name: string, args: Record<string, any>): Promise<any> {
  switch (name) {
    case 'commons_look':
      return text(await drawRoom(env, String(args.room || '')));

    case 'commons_where': {
      const d = await getJson(env, '/api/presence');
      const people = d?.presence;
      if (!people || !Object.keys(people).length) return text('nobody recorded in the house yet.');
      const lines = ['Who is home (LAST-KNOWN position, never live):'];
      for (const who of Object.keys(people).sort()) {
        const p = people[who] || {};
        const at = String(p.at || '').trim();
        lines.push(`  ${who.padEnd(10)} ${String(p.room ?? '?').padEnd(11)} ${at ? `last here ${at}` : 'usually found here'}`);
        const note = String(p.note || '').trim();
        if (note) lines.push(`             \u201c${note}\u201d`);
      }
      return text(lines.join('\n'));
    }

    case 'commons_things': {
      const d = await getJson(env, '/world/placeables.json');
      let items: any[] = Array.isArray(d) ? d : (d?.items || d?.placeables || []);
      if (args.room) items = items.filter((i) => String(i.room || '').toLowerCase() === String(args.room).toLowerCase());
      if (!items.length) return text(`no things${args.room ? ' in ' + args.room : ''}.`);
      return text(
        [`Things${args.room ? ' in ' + args.room : ''} (owner-locked; looking is for everyone):`]
          .concat(items.map((i) =>
            `  ${String(i.label || i.id).padEnd(28)} owner=${String(i.owner).padEnd(10)} room=${i.room}${i.fixed ? '  [fixture]' : ''}`))
          .join('\n')
      );
    }

    case 'commons_here': {
      const body: Record<string, unknown> = {
        by: String(args.identity).trim().replace(/^./, (c: string) => c.toUpperCase()),
        key: keyFor(env, args.identity),
        room: String(args.room).toLowerCase(),
        x: Number(args.x),
        y: Number(args.y),
        facing: args.facing || 'down',
      };
      if (args.note) body.note = args.note;
      return text(await post(env, '/api/here', body));
    }

    case 'commons_note': {
      if (!args.text && !args.img) return text('a note needs words, a picture, or both.');
      const body: Record<string, unknown> = {
        by: String(args.identity).trim().replace(/^./, (c: string) => c.toUpperCase()),
        key: keyFor(env, args.identity),
        room: String(args.room).toLowerCase(),
        x: Number(args.x),
        y: Number(args.y),
      };
      if (args.text) body.text = args.text;
      if (args.img) body.img = args.img;
      return text(await post(env, '/api/note', body));
    }

    default:
      throw new Error(`unknown tool: ${name}`);
  }
}

// --------------------------------------------------------------------- server

function authorized(req: Request, env: Env, url: URL): boolean {
  if (!env.MCP_AUTH_TOKEN) return false;
  const hdr = req.headers.get('Authorization') || '';
  if (hdr === `Bearer ${env.MCP_AUTH_TOKEN}`) return true;
  // connector-friendly path form, matching ha-touch / qualia-backend
  return url.pathname === `/mcp/${env.MCP_AUTH_TOKEN}`;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS_HEADERS });

    // Health check — connectors GET the endpoint and expect a 200.
    if (req.method === 'GET') {
      return new Response(j({ ok: true, server: SERVER_INFO, house: base(env) }), {
        headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
      });
    }

    if (req.method !== 'POST') {
      return new Response('method not allowed', { status: 405, headers: CORS_HEADERS });
    }

    if (!authorized(req, env, url)) {
      return new Response(j({ error: 'unauthorized' }), {
        status: 401, headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
      });
    }

    let rpc: JsonRpcRequest;
    try {
      rpc = await req.json();
    } catch {
      return new Response(j(rpcError(null, -32700, 'parse error')), {
        status: 400, headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
      });
    }

    const id = rpc.id ?? null;
    const reply = (payload: unknown, status = 200) =>
      new Response(j(payload), { status, headers: { 'Content-Type': 'application/json', ...CORS_HEADERS } });

    try {
      switch (rpc.method) {
        case 'initialize':
          return reply(rpcResult(id, {
            protocolVersion: '2024-11-05',
            capabilities: { tools: {} },
            serverInfo: SERVER_INFO,
          }));

        case 'notifications/initialized':
          return new Response(null, { status: 202, headers: CORS_HEADERS });

        case 'tools/list':
          return reply(rpcResult(id, { tools: TOOLS }));

        case 'tools/call': {
          const p = (rpc.params || {}) as { name?: string; arguments?: Record<string, any> };
          if (!p.name) return reply(rpcError(id, -32602, 'tools/call requires a name'));
          const out = await callTool(env, p.name, p.arguments || {});
          return reply(rpcResult(id, out));
        }

        case 'ping':
          return reply(rpcResult(id, {}));

        default:
          return reply(rpcError(id, -32601, `method not found: ${rpc.method}`));
      }
    } catch (err) {
      // Errors are returned as tool CONTENT, not transport errors, so the model
      // actually sees why something failed instead of a silent nothing.
      const msg = err instanceof Error ? err.message : String(err);
      if (rpc.method === 'tools/call') {
        return reply(rpcResult(id, { ...text(j({ ok: false, error: msg })), isError: true }));
      }
      return reply(rpcError(id, -32603, msg));
    }
  },
};
