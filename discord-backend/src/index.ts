// Cloud Discord — Cloudflare Worker providing Discord REST API as MCP tools
// Secret-path authenticated: /mcp/<MCP_SECRET_PATH>

import { getActiveDiscordTools, handleDiscordTool, parseAllowedTools } from './tools';
import { discordFetch, discordFetchMultipart, formatMessage } from './discord';
import { getIdentityConfig } from './identities';
import Anthropic from '@anthropic-ai/sdk';
import nacl from 'tweetnacl';
import { asToolResult } from './image-content';

interface Env {
  DISCORD_BOT_TOKEN: string;
  MCP_SECRET_PATH: string;
  DISCORD_PUBLIC_KEY: string;
  ANTHROPIC_API_KEY: string;
  ELEVENLABS_API_KEY: string;
  OPENAI_API_KEY: string;
  ALLOWED_TOOLS: string;
  [key: string]: string;
}

export interface ExecutionContext {
  waitUntil(promise: Promise<any>): void;
  passThroughOnException(): void;
}

// JSON-RPC types
interface JsonRpcRequest {
  jsonrpc: '2.0';
  method: string;
  id?: string | number;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

function jsonRpcResult(id: string | number | null, result: unknown): JsonRpcResponse {
  return { jsonrpc: '2.0', id, result };
}

function jsonRpcError(id: string | number | null, code: number, message: string, data?: unknown): JsonRpcResponse {
  return { jsonrpc: '2.0', id, error: { code, message, data } };
}

// MCP server info
const SERVER_INFO = {
  name: 'discord-backend',
  version: '1.0.0',
};

const SERVER_CAPABILITIES = {
  tools: {},
};

function resolveBotToken(env: Env, requestedIdentity: unknown): string {
  const identity = typeof requestedIdentity === 'string' ? requestedIdentity.trim().toLowerCase() : '';
  if (!identity) {
    return env.DISCORD_BOT_TOKEN;
  }

  const config = getIdentityConfig(identity);
  if (!config) {
    throw new Error(`Unknown identity: ${identity}`);
  }

  const token = env[config.token_env] || env.DISCORD_BOT_TOKEN;
  if (!token) {
    throw new Error(`No Discord bot token configured for identity: ${identity}`);
  }
  return token;
}

// Signature verification
async function verifyKey(
  request: Request,
  publicKeyHex: string,
): Promise<{ isValid: boolean; body: string }> {
  const signature = request.headers.get('x-signature-ed25519');
  const timestamp = request.headers.get('x-signature-timestamp');
  const body = await request.text();

  if (!signature || !timestamp) {
    return { isValid: false, body };
  }

  function hexToUint8Array(hex: string): Uint8Array {
    const arr = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      arr[i / 2] = parseInt(hex.substring(i, i + 2), 16);
    }
    return arr;
  }

  try {
    const isVerified = nacl.sign.detached.verify(
      new TextEncoder().encode(timestamp + body),
      hexToUint8Array(signature),
      hexToUint8Array(publicKeyHex),
    );
    return { isValid: isVerified, body };
  } catch (err) {
    return { isValid: false, body };
  }
}

async function handleVibeCommand(interaction: any, env: Env) {
  try {
    const channelId = interaction.channel_id;
    const appId = interaction.application_id;
    const token = interaction.token;

    console.log(`[vibe] Starting vibe check for channel ${channelId}`);

    // 1. Fetch last 20 messages
    console.log(`[vibe] Fetching messages...`);
    const res = await discordFetch(env.DISCORD_BOT_TOKEN, 'GET', `/channels/${channelId}/messages?limit=20`);
    if (!res.ok) {
      console.log(`[vibe] Fetch failed:`, res);
      throw new Error(`Failed to fetch messages: ${res.status}`);
    }

    const messages = res.data as any[];
    console.log(`[vibe] Fetched ${messages?.length || 0} messages.`);
    if (!messages || messages.length === 0) {
      console.log(`[vibe] No messages found, returning early.`);
      await updateInteractionResponse(appId, token, env.DISCORD_BOT_TOKEN, 'It\'s too quiet in here to catch a vibe.');
      return;
    }

    // 2. Format messages (oldest to newest)
    const formatted = messages.reverse().map(formatMessage).join('\n');
    console.log(`[vibe] Formatted trace length: ${formatted.length} chars`);

    // 3. Call Anthropic API
    console.log(`[vibe] Initializing Anthropic...`);
    const anthropic = new Anthropic({
      apiKey: env.ANTHROPIC_API_KEY,
    });

    console.log(`[vibe] Calling Anthropic API...`);

    const aiResponse = await anthropic.messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 200,
      temperature: 0.7,
      system: `Analyze the following recent chat log and provide a concise summary of what the chat is currently about, along with a read on the 'vibe'.

Instructions:
- Be direct and observant. No fluff.
- Summarize what they are talking about in 1-2 sentences, then state the vibe.
- Example: "They are debugging a deployment script that keeps 404ing. The vibe is frustrated but focused."`,
      messages: [
        { role: 'user', content: `Here is the recent chat history:\n\n${formatted}\n\nWhat's going on and what's the vibe?` }
      ]
    });

    console.log(`[vibe] Anthropic API returned success.`);
    const vibeSummary = (aiResponse.content[0] as any).text;

    // 4. Update Interaction
    console.log(`[vibe] Updating interaction response...`);
    await updateInteractionResponse(appId, token, env.DISCORD_BOT_TOKEN, `**Current Vibe:** ${vibeSummary}`);
    console.log(`[vibe] Done.`);
  } catch (error: any) {
    console.error('[vibe] Error in handleVibeCommand:', error);
    await updateInteractionResponse(interaction.application_id, interaction.token, env.DISCORD_BOT_TOKEN, 'Uh oh, the vibe check failed. Try again in a moment.');
  }
}

async function updateInteractionResponse(appId: string, token: string, botToken: string, content: string) {
  await discordFetch(botToken, 'PATCH', `/webhooks/${appId}/${token}/messages/@original`, { content });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // Discord Interactions Webhook
    if (path === '/interactions' && request.method === 'POST') {
      const { isValid, body } = await verifyKey(request, env.DISCORD_PUBLIC_KEY);
      if (!isValid) {
        return new Response('Bad request signature', { status: 401 });
      }

      const interaction = JSON.parse(body);

      // type 1: PING
      if (interaction.type === 1) {
        return Response.json({ type: 1 });
      }

      // type 2: Slash Command
      if (interaction.type === 2 && interaction.data?.name === 'vibe') {
        // Acknowledge the command and defer the response
        ctx.waitUntil(handleVibeCommand(interaction, env));
        return Response.json({ type: 5 }); // DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
      }

      // We will handle type 2 (Commands) here later
      return new Response('Unhandled interaction type', { status: 400 });
    }

    // Health check (public)
    if (path === '/health') {
      const allowed = parseAllowedTools(env.ALLOWED_TOOLS);
      return Response.json({ status: 'ok', tools: getActiveDiscordTools(allowed).length });
    }

    // Secret path check — all MCP endpoints require /mcp/<SECRET>
    const expectedPrefix = `/mcp/${env.MCP_SECRET_PATH}`;
    if (!path.startsWith(expectedPrefix)) {
      return new Response('Not found', { status: 404 });
    }

    // Direct file upload endpoint — bypasses MCP, accepts multipart/form-data
    // Usage: curl -F "channel_id=123" -F "file=@/path/to/file.mp3" -F "message=optional text" URL/mcp/<secret>/upload
    // For DMs: curl -F "user_id=123" -F "file=@/path/to/file.mp3" URL/mcp/<secret>/upload
    const subPath = path.slice(expectedPrefix.length);
    if (subPath === '/upload' && request.method === 'POST') {
      try {
        const contentLength = parseInt(request.headers.get('content-length') || '0', 10);
        const MAX_UPLOAD_SIZE = 25 * 1024 * 1024; // 25MB — Discord's file limit
        if (contentLength > MAX_UPLOAD_SIZE) {
          return Response.json(
            { error: `File too large. Maximum upload size is ${MAX_UPLOAD_SIZE / 1024 / 1024}MB.` },
            { status: 413 },
          );
        }
        const formData = await request.formData();
        const channelId = formData.get('channel_id') as string | null;
        const userId = formData.get('user_id') as string | null;
        const message = formData.get('message') as string | null;
        const file = formData.get('file') as File | null;

        if (!file) {
          return Response.json({ error: 'Missing "file" field' }, { status: 400 });
        }
        if (!channelId && !userId) {
          return Response.json({ error: 'Must provide "channel_id" or "user_id"' }, { status: 400 });
        }

        // Resolve target channel (DM if user_id, otherwise channel_id)
        let targetChannelId = channelId;
        if (!targetChannelId && userId) {
          const dmRes = await discordFetch(env.DISCORD_BOT_TOKEN, 'POST', '/users/@me/channels', { recipient_id: userId });
          if (!dmRes.ok) {
            return Response.json({ error: `Failed to open DM: ${JSON.stringify(dmRes.data)}` }, { status: 500 });
          }
          targetChannelId = (dmRes.data as any).id;
        }

        const buffer = await file.arrayBuffer();
        const fileData = new Uint8Array(buffer);
        const fileName = file.name || 'file';
        const contentType = file.type || 'application/octet-stream';

        const payload: any = {
          attachments: [{ id: 0, filename: fileName }],
        };
        if (message) payload.content = message;

        const res = await discordFetchMultipart(
          env.DISCORD_BOT_TOKEN, 'POST',
          `/channels/${targetChannelId}/messages`,
          payload, fileData, fileName, contentType,
        );

        if (!res.ok) {
          return Response.json({ error: `Discord API error`, details: res.data }, { status: 500 });
        }

        return Response.json({
          ok: true,
          message_id: (res.data as any).id,
          file_name: fileName,
          size: fileData.byteLength,
        });
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        return Response.json({ error: errMsg }, { status: 500 });
      }
    }

    // CORS headers for MCP — scoped, not wildcard
    const origin = request.headers.get('Origin') || '';
    const allowedOrigins = ['https://claude.ai', 'https://console.anthropic.com'];
    const corsOrigin = allowedOrigins.includes(origin) ? origin : '';
    const corsHeaders: Record<string, string> = {
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };
    if (corsOrigin) {
      corsHeaders['Access-Control-Allow-Origin'] = corsOrigin;
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    // GET — MCP Streamable HTTP requires responding to GET
    if (request.method === 'GET') {
      return new Response('MCP endpoint active', {
        status: 200,
        headers: { ...corsHeaders, 'Content-Type': 'text/plain' },
      });
    }

    // DELETE — stateless, nothing to close
    if (request.method === 'DELETE') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    // POST — main MCP JSON-RPC handler
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: corsHeaders });
    }

    const MAX_JSON_SIZE = 1 * 1024 * 1024; // 1MB
    const jsonContentLength = parseInt(request.headers.get('content-length') || '0', 10);
    if (jsonContentLength > MAX_JSON_SIZE) {
      return Response.json(
        jsonRpcError(null, -32600, 'Request body too large'),
        { status: 413, headers: corsHeaders },
      );
    }

    let body: JsonRpcRequest;
    try {
      body = await request.json() as JsonRpcRequest;
    } catch {
      return Response.json(
        jsonRpcError(null, -32700, 'Parse error'),
        { status: 400, headers: corsHeaders },
      );
    }

    const id = body.id ?? null;
    const allowed = parseAllowedTools(env.ALLOWED_TOOLS);

    try {
      let result: unknown;

      switch (body.method) {
        case 'initialize':
          result = {
            protocolVersion: '2024-11-05',
            serverInfo: SERVER_INFO,
            capabilities: SERVER_CAPABILITIES,
          };
          break;

        case 'notifications/initialized':
          // Client ack — no response needed for notifications
          return new Response(null, { status: 204, headers: corsHeaders });

        case 'tools/list':
          result = {
            tools: getActiveDiscordTools(allowed).map(t => ({
              name: t.name,
              description: t.description,
              inputSchema: t.inputSchema,
            })),
          };
          break;

        case 'tools/call': {
          const params = body.params as { name: string; arguments?: Record<string, unknown> };
          if (!params?.name) {
            return Response.json(
              jsonRpcError(id, -32602, 'Missing tool name'),
              { headers: corsHeaders },
            );
          }

          try {
            const token = resolveBotToken(env, params.arguments?.identity);
            const toolResult = await handleDiscordTool(
              token,
              params.name,
              params.arguments || {},
              allowed,
              {
                elevenLabsApiKey: env.ELEVENLABS_API_KEY,
                openAiApiKey: env.OPENAI_API_KEY,
                machineAgentUrl: env.MACHINE_AGENT_URL,
                machineAgentApiKey: env.MACHINE_AGENT_API_KEY,
                imageArchiveDir: env.IMAGE_ARCHIVE_DIR,
              },
            );
            result = asToolResult(toolResult);
          } catch (error) {
            const errMsg = error instanceof Error ? error.message : String(error);
            result = {
              content: [{ type: 'text', text: `Error: ${errMsg}` }],
              isError: true,
            };
          }
          break;
        }

        default:
          return Response.json(
            jsonRpcError(id, -32601, `Method not found: ${body.method}`),
            { headers: corsHeaders },
          );
      }

      return Response.json(jsonRpcResult(id, result), { headers: corsHeaders });

    } catch (error) {
      console.error('[mcp] Internal error:', error);
      return Response.json(
        jsonRpcError(id, -32603, 'Internal error'),
        { status: 500, headers: corsHeaders },
      );
    }
  },
};
