// Social Backend — Unified Cloudflare Worker providing Twitter, Telegram, Moltbook, and World Tools as MCP
// Secret-path authenticated: /mcp/<MCP_SECRET_PATH>

import type { Env, JsonRpcRequest, ToolModule } from './lib/types';
import { jsonRpcResult, jsonRpcError, CORS_HEADERS } from './lib/mcp';

import worldTools from './tools/world-tools';
import telegram from './tools/telegram';


// Registry: all tool modules
const MODULES: ToolModule[] = [ worldTools, telegram ];

// Build flat tool lists and lookup
const ALL_TOOLS = MODULES.flatMap(m => m.tools);
const TOOL_INDEX = new Map<string, ToolModule>();
for (const mod of MODULES) {
  for (const tool of mod.tools) {
    TOOL_INDEX.set(tool.name, mod);
  }
}

const SERVER_INFO = { name: 'social-backend', version: '1.0.0' };
const SERVER_CAPABILITIES = { tools: {} };

function parseAllowedTools(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
}

function toolMatchesRule(toolName: string, rule: string): boolean {
  if (rule === '*') return true;
  if (rule.endsWith('*')) return toolName.startsWith(rule.slice(0, -1));
  return toolName === rule;
}

function getAllowedTools(env: Env) {
  const rules = parseAllowedTools(env.ALLOWED_TOOLS);
  if (!rules.length) return ALL_TOOLS;
  return ALL_TOOLS.filter(tool => rules.some(rule => toolMatchesRule(tool.name, rule)));
}

function isAllowedTool(env: Env, name: string): boolean {
  return getAllowedTools(env).some(tool => tool.name === name);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // Health check (public)
    if (path === '/health') {
      const tools = getAllowedTools(env);
      return Response.json({
        status: 'ok',
        tools: tools.length,
        modules: MODULES.length,
        tool_names: tools.map(t => t.name),
      });
    }

    // Secret path check
    const expectedPrefix = `/mcp/${env.MCP_SECRET_PATH}`;
    if (!path.startsWith(expectedPrefix)) {
      return new Response('Not found', { status: 404 });
    }

    // CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // GET — MCP Streamable HTTP spec requires responding to GET
    if (request.method === 'GET') {
      return new Response('MCP endpoint active', {
        status: 200,
        headers: { ...CORS_HEADERS, 'Content-Type': 'text/plain' },
      });
    }

    // DELETE — stateless, nothing to close
    if (request.method === 'DELETE') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // POST — main MCP JSON-RPC handler
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: CORS_HEADERS });
    }

    let body: JsonRpcRequest;
    try {
      body = await request.json() as JsonRpcRequest;
    } catch {
      return Response.json(
        jsonRpcError(null, -32700, 'Parse error'),
        { status: 400, headers: CORS_HEADERS },
      );
    }

    const id = body.id ?? null;

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
          return new Response(null, { status: 204, headers: CORS_HEADERS });

        case 'tools/list':
          result = {
            tools: getAllowedTools(env).map(t => ({
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
              { headers: CORS_HEADERS },
            );
          }

          if (!isAllowedTool(env, params.name)) {
            return Response.json(
              jsonRpcError(id, -32602, `Tool not allowed: ${params.name}`),
              { headers: CORS_HEADERS },
            );
          }

          const mod = TOOL_INDEX.get(params.name);
          if (!mod) {
            return Response.json(
              jsonRpcError(id, -32602, `Unknown tool: ${params.name}`),
              { headers: CORS_HEADERS },
            );
          }

          try {
            const toolResult = await mod.handle(params.name, params.arguments || {}, env);
            result = {
              content: [{ type: 'text', text: toolResult }],
            };
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
            { headers: CORS_HEADERS },
          );
      }

      return Response.json(jsonRpcResult(id, result), { headers: CORS_HEADERS });

    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return Response.json(
        jsonRpcError(id, -32603, `Internal error: ${errMsg}`),
        { status: 500, headers: CORS_HEADERS },
      );
    }
  },
};
