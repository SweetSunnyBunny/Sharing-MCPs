// ElevenLabs text-to-speech MCP Worker with expiring hosted audio.
// Configure your own example voice below and set the required secrets.

export interface Env {
  ELEVENLABS_API_KEY: string; // secret — ElevenLabs API key
  MCP_AUTH_TOKEN: string;      // secret — bearer/path token for MCP clients
  AUDIO: KVNamespace;          // stores generated MP3s, served back at /audio/<id>
}


interface JsonRpcRequest {
  jsonrpc: '2.0';
  method: string;
  id?: string | number | null;
  params?: Record<string, unknown>;
}

type ContentBlock =
  | { type: 'text'; text: string }
  | { type: 'audio'; data: string; mimeType: string };

const SERVER_INFO = { name: 'elevenlabs', version: '1.0.0' };
const ELEVENLABS_API_BASE = 'https://api.elevenlabs.io/v1';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, Mcp-Session-Id',
};

// Configure your own voice IDs and preferences.
const VOICES: Record<string, { id: string; stability: number; similarity: number; style: number }> = {
  avery: { id: 'YOUR-VOICE-ID', stability: 0.5, similarity: 0.75, style: 0.0 },
};

const MAX_TTS_CHARS = 1200; // keep clips short; output is hosted in KV.

function rpcResult(id: string | number | null, result: unknown) {
  return { jsonrpc: '2.0', id, result };
}
function rpcError(id: string | number | null, code: number, message: string) {
  return { jsonrpc: '2.0', id, error: { code, message } };
}
function j(data: unknown): string {
  return JSON.stringify(data, null, 2);
}

// Base64-encode an ArrayBuffer without blowing the call stack on larger clips.
function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// ---------------------------------------------------------------- tools

const TOOLS = [
  {
    name: 'list_voices',
    description: "List your configured ElevenLabs voices (name + voice_id). Pass a name as the 'voice' argument to text_to_speech. Free — no credits.",
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'text_to_speech',
    description:
      "Generate spoken MP3 audio from text using an ElevenLabs voice (model eleven_v3). Returns an expiring hosted audio URL. Keep text short (<=1200 chars). COSTS ElevenLabs credits — use deliberately, one clip at a time. Supports v3 expression tags in the text like [softly], [laughs], [sighs], [whispers].",
    inputSchema: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'What to speak (<=1200 chars). v3 tags like [softly] are honored.' },
        voice: {
          type: 'string',
          description:
            "Voice: a configured voice label or raw ElevenLabs voice ID. Default: avery.",
        },
        model_id: { type: 'string', description: 'ElevenLabs model id (default eleven_v3).' },
      },
      required: ['text'],
    },
  },
];

async function handleTool(name: string, args: Record<string, unknown>, env: Env, origin: string): Promise<ContentBlock[]> {
  switch (name) {
    case 'list_voices': {
      const roster = Object.entries(VOICES).map(([n, v]) => ({ name: n, voice_id: v.id }));
      return [{ type: 'text', text: j({ voices: roster, note: "Use any name (case-insensitive) as text_to_speech 'voice', or a raw voice_id." }) }];
    }

    case 'text_to_speech': {
      if (!env.ELEVENLABS_API_KEY) throw new Error('ELEVENLABS_API_KEY secret is not set on this worker');
      const text = String(args.text || '').trim();
      if (!text) throw new Error('text is required');
      if (text.length > MAX_TTS_CHARS) {
        throw new Error(`text is ${text.length} chars; max ${MAX_TTS_CHARS} for inline audio. Shorten it, or split into clips.`);
      }
      const voiceArg = String(args.voice || 'avery').trim();
      const key = voiceArg.toLowerCase();
      let voiceId: string;
      let s: { stability: number; similarity: number; style: number };
      if (VOICES[key]) {
        voiceId = VOICES[key].id;
        s = VOICES[key];
      } else if (/^[A-Za-z0-9]{16,}$/.test(voiceArg)) {
        voiceId = voiceArg;
        s = { stability: 0.5, similarity: 0.75, style: 0.0 };
      } else {
        throw new Error(`Unknown voice "${voiceArg}". Known: ${Object.keys(VOICES).join(', ')} — or pass a raw ElevenLabs voice_id.`);
      }

      if (voiceId === 'YOUR-VOICE-ID') throw new Error('Configure a voice ID before generating speech.');
      const model = String(args.model_id || 'eleven_v3');
      const resp = await fetch(`${ELEVENLABS_API_BASE}/text-to-speech/${voiceId}?enable_logging=true`, {
        method: 'POST',
        headers: {
          'xi-api-key': env.ELEVENLABS_API_KEY,
          'Content-Type': 'application/json',
          'Accept': 'audio/mpeg',
        },
        body: JSON.stringify({
          text,
          model_id: model,
          output_format: 'mp3_44100_128',
          voice_settings: {
            stability: s.stability,
            similarity_boost: s.similarity,
            style: s.style,
            use_speaker_boost: true,
          },
        }),
      });

      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(`ElevenLabs responded ${resp.status}: ${t.slice(0, 300)}`);
      }

      const buf = await resp.arrayBuffer();
      // ChatGPT's connector discards inline audio blocks, so we stash the MP3 in KV
      // and hand back a public, unguessable, self-expiring URL the model CAN pass on.
      const objectKey = `${crypto.randomUUID()}.mp3`;
      await env.AUDIO.put(objectKey, buf, { expirationTtl: 604800, metadata: { voice: voiceArg } }); // 7-day auto-cleanup
      const audioUrl = `${origin}/audio/${objectKey}`;
      return [
        {
          type: 'text',
          text:
            `Generated ${(buf.byteLength / 1024).toFixed(1)} KB of speech in ${voiceArg}'s voice (model ${model}).\n\n` +
            `Playable / downloadable MP3 (expires in 7 days):\n${audioUrl}`,
        },
      ];
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ---------------------------------------------------------------- auth + router

function isAuthorized(request: Request, pathname: string, env: Env): boolean {
  if (!env.MCP_AUTH_TOKEN) return false; // secret unset -> locked
  if (pathname.startsWith('/mcp/')) {
    const token = decodeURIComponent(pathname.slice(5).split('/')[0] || '');
    if (token === env.MCP_AUTH_TOKEN) return true;
  }
  const auth = request.headers.get('Authorization') || '';
  return auth === `Bearer ${env.MCP_AUTH_TOKEN}` || auth === env.MCP_AUTH_TOKEN;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // Public audio delivery — serves a generated MP3 by its unguessable key.
    // No auth (the random key IS the capability); this is the link ChatGPT hands off.
    if (path.startsWith('/audio/')) {
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        return new Response('Method not allowed', { status: 405, headers: CORS_HEADERS });
      }
      const keyName = decodeURIComponent(path.slice('/audio/'.length));
      const val = await env.AUDIO.get(keyName, { type: 'arrayBuffer' });
      if (!val) return new Response('Not found or expired', { status: 404, headers: CORS_HEADERS });
      return new Response(val, {
        headers: { ...CORS_HEADERS, 'Content-Type': 'audio/mpeg', 'Cache-Control': 'public, max-age=86400' },
      });
    }

    if (path === '/health') {
      return Response.json(
        { status: 'ok', server: SERVER_INFO.name, tools: TOOLS.map(t => t.name), voices: Object.keys(VOICES) },
        { headers: CORS_HEADERS },
      );
    }

    if (path !== '/mcp' && !path.startsWith('/mcp/')) {
      return new Response('Not found', { status: 404 });
    }

    if (!isAuthorized(request, path, env)) {
      return new Response('Unauthorized', { status: 401, headers: CORS_HEADERS });
    }

    if (request.method === 'GET') {
      if ((request.headers.get('Accept') || '').includes('text/event-stream')) {
        return new Response(': elevenlabs stream open\n\n', {
          status: 200,
          headers: { ...CORS_HEADERS, 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        });
      }
      return new Response('MCP endpoint active', { status: 200, headers: { ...CORS_HEADERS, 'Content-Type': 'text/plain' } });
    }
    if (request.method === 'DELETE') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: CORS_HEADERS });
    }

    let body: JsonRpcRequest;
    try {
      body = (await request.json()) as JsonRpcRequest;
    } catch {
      return Response.json(rpcError(null, -32700, 'Parse error'), { status: 400, headers: CORS_HEADERS });
    }
    const id = body.id ?? null;

    try {
      switch (body.method) {
        case 'initialize':
          return Response.json(
            rpcResult(id, { protocolVersion: '2024-11-05', serverInfo: SERVER_INFO, capabilities: { tools: {} } }),
            { headers: CORS_HEADERS },
          );

        case 'notifications/initialized':
          return new Response(null, { status: 204, headers: CORS_HEADERS });

        case 'ping':
          return Response.json(rpcResult(id, {}), { headers: CORS_HEADERS });

        case 'tools/list':
          return Response.json(rpcResult(id, { tools: TOOLS }), { headers: CORS_HEADERS });

        case 'tools/call': {
          const params = body.params as { name?: string; arguments?: Record<string, unknown> } | undefined;
          if (!params?.name) {
            return Response.json(rpcError(id, -32602, 'Missing tool name'), { headers: CORS_HEADERS });
          }
          try {
            const content = await handleTool(params.name, params.arguments || {}, env, url.origin);
            return Response.json(rpcResult(id, { content }), { headers: CORS_HEADERS });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            return Response.json(rpcResult(id, { content: [{ type: 'text', text: `Error: ${msg}` }], isError: true }), { headers: CORS_HEADERS });
          }
        }

        default:
          return Response.json(rpcError(id, -32601, `Method not found: ${body.method}`), { headers: CORS_HEADERS });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return Response.json(rpcError(id, -32603, `Internal error: ${msg}`), { status: 500, headers: CORS_HEADERS });
    }
  },
};
