// Hearth Hub — Cloudflare Worker providing Sanctuary state via MCP + REST API
// MCP: /mcp/<SECRET>
// REST: /api/sanctuary/*

import type { Env, JsonRpcRequest } from './lib/types';
import { jsonRpcResult, jsonRpcError, CORS_HEADERS } from './lib/mcp';
import { TOOLS, handle } from './tools/sanctuary';
import { handleApiRequest } from './api/sanctuary';
import { privateConfig } from './api/private-config';

const SERVER_INFO = { name: 'hearth-hub', version: '1.0.0' };
const SERVER_CAPABILITIES = { tools: {} };

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path.startsWith('/api/private/config/')) return privateConfig(request, env);

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // Health check
    if (path === '/health') {
      return Response.json({ status: 'ok', tools: TOOLS.length, tool_names: TOOLS.map(t => t.name) }, { headers: CORS_HEADERS });
    }

    // ========== REST API (for Anam viewer) ==========
    if (path.startsWith('/api/sanctuary')) {
      const result = await handleApiRequest(env, path, url);
      if (result) return result;
      return Response.json({ error: 'Not found' }, { status: 404, headers: CORS_HEADERS });
    }

    // ========== MCP endpoint ==========
    const expectedPrefix = `/mcp/${env.MCP_SECRET_PATH}`;
    if (!path.startsWith(expectedPrefix)) {
      return new Response('Not found', { status: 404 });
    }

    if (request.method === 'GET') {
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
      body = await request.json() as JsonRpcRequest;
    } catch {
      return Response.json(jsonRpcError(null, -32700, 'Parse error'), { status: 400, headers: CORS_HEADERS });
    }

    const id = body.id ?? null;

    try {
      let result: unknown;

      switch (body.method) {
        case 'initialize':
          result = { protocolVersion: '2024-11-05', serverInfo: SERVER_INFO, capabilities: SERVER_CAPABILITIES };
          break;

        case 'notifications/initialized':
          return new Response(null, { status: 204, headers: CORS_HEADERS });

        case 'tools/list': {
          const enabled = new Set(env.ENABLED_TOOLS || []);
          result = { tools: TOOLS.filter(t => enabled.has(t.name)).map(t => ({ name: t.name, description: t.description, inputSchema: t.inputSchema })) };
          break;
        }

        case 'tools/call': {
          const params = body.params as { name: string; arguments?: Record<string, unknown> };
          if (!params?.name) {
            return Response.json(jsonRpcError(id, -32602, 'Missing tool name'), { headers: CORS_HEADERS });
          }
          if (!(env.ENABLED_TOOLS || []).includes(params.name)) {
            return Response.json(jsonRpcError(id, -32602, `Unknown tool: ${params.name}`), { headers: CORS_HEADERS });
          }

          try {
            const toolResult = await handle(params.name, params.arguments || {}, env);
            result = { content: [{ type: 'text', text: toolResult }] };
          } catch (error) {
            const errMsg = error instanceof Error ? error.message : String(error);
            result = { content: [{ type: 'text', text: `Error: ${errMsg}` }], isError: true };
          }
          break;
        }

        default:
          return Response.json(jsonRpcError(id, -32601, `Method not found: ${body.method}`), { headers: CORS_HEADERS });
      }

      return Response.json(jsonRpcResult(id, result), { headers: CORS_HEADERS });
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return Response.json(jsonRpcError(id, -32603, `Internal error: ${errMsg}`), { status: 500, headers: CORS_HEADERS });
    }
  },
};
