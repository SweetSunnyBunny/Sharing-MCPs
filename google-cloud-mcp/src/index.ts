/**
 * Google Cloud MCP - self-hosted Cloudflare Worker for Drive, Docs, Sheets, Calendar, and YouTube.
 *
 * MCP JSON-RPC over HTTP with path-token auth.
 * URL: https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/mcp/SECRET
 */

import type { Env } from "./oauth.js";
import { DRIVE_TOOLS, handleDrive } from "./drive.js";
import { YOUTUBE_TOOLS, handleYouTube } from "./youtube.js";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  method: string;
  id?: string | number;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string };
}

type ToolDefinition = (typeof DRIVE_TOOLS)[number] | (typeof YOUTUBE_TOOLS)[number];

const SERVER_INFO = { name: "google-cloud-mcp", version: "0.1.0" };
const ALL_TOOLS: ToolDefinition[] = [...DRIVE_TOOLS, ...YOUTUBE_TOOLS];

function jsonRpcResult(id: string | number | null, result: unknown): JsonRpcResponse {
  return { jsonrpc: "2.0", id, result };
}

function jsonRpcError(id: string | number | null, code: number, message: string): JsonRpcResponse {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function withCors(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  return new Response(response.body, { status: response.status, headers });
}

function extractMcpPath(pathname: string): { isMcp: boolean; token: string | null } {
  if (pathname === "/mcp") return { isMcp: true, token: null };
  if (pathname.startsWith("/mcp/")) {
    const token = pathname.slice(5);
    return { isMcp: true, token: token || null };
  }
  return { isMcp: false, token: null };
}

function checkAuth(request: Request, env: Env, pathToken?: string | null): boolean {
  if (pathToken && pathToken === env.MCP_SECRET_PATH) return true;
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${env.MCP_SECRET_PATH}` || auth === env.MCP_SECRET_PATH;
}

function parseAllowedTools(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function toolMatchesRule(toolName: string, rule: string): boolean {
  if (rule === "*") return true;
  if (rule.endsWith("*")) return toolName.startsWith(rule.slice(0, -1));
  return toolName === rule;
}

function getAllowedTools(env: Env): ToolDefinition[] {
  const rules = parseAllowedTools(env.ALLOWED_TOOLS);
  if (!rules.length) return ALL_TOOLS;
  return ALL_TOOLS.filter((tool) => rules.some((rule) => toolMatchesRule(tool.name, rule)));
}

function isAllowedTool(env: Env, name: string): boolean {
  return getAllowedTools(env).some((tool) => tool.name === name);
}

async function handleToolCall(env: Env, name: string, args: Record<string, unknown>): Promise<string> {
  if (!isAllowedTool(env, name)) {
    throw new Error(`Tool not allowed: ${name}`);
  }
  if (name.startsWith("gdrive_") || name.startsWith("gdocs_") || name.startsWith("gsheets_") || name.startsWith("gcal_")) {
    return handleDrive(env, name, args);
  }
  if (name.startsWith("youtube_")) return handleYouTube(env, name, args);
  throw new Error(`Unknown tool: ${name}`);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return withCors(new Response(null, { status: 204 }));
    }

    if (url.pathname === "/health") {
      const tools = getAllowedTools(env);
      return withCors(Response.json({ status: "ok", server: SERVER_INFO.name, tools: tools.length }));
    }

    const mcpPath = extractMcpPath(url.pathname);
    if (!mcpPath.isMcp) {
      return withCors(new Response("Not found", { status: 404 }));
    }

    if (!checkAuth(request, env, mcpPath.token)) {
      return withCors(new Response("Unauthorized", { status: 401 }));
    }

    if (request.method === "GET") {
      return withCors(new Response("MCP endpoint active", { status: 200 }));
    }

    if (request.method === "DELETE") {
      return withCors(new Response(null, { status: 204 }));
    }

    if (request.method !== "POST") {
      return withCors(new Response("Method not allowed", { status: 405 }));
    }

    let body: JsonRpcRequest;
    try {
      body = (await request.json()) as JsonRpcRequest;
    } catch {
      return withCors(Response.json(jsonRpcError(null, -32700, "Parse error"), { status: 400 }));
    }

    const id = body.id ?? null;

    try {
      switch (body.method) {
        case "initialize":
          return withCors(
            Response.json(
              jsonRpcResult(id, {
                protocolVersion: "2024-11-05",
                serverInfo: SERVER_INFO,
                capabilities: { tools: {} },
              }),
            ),
          );

        case "notifications/initialized":
          return withCors(new Response(null, { status: 204 }));

        case "tools/list":
          return withCors(Response.json(jsonRpcResult(id, { tools: getAllowedTools(env) })));

        case "tools/call": {
          const params = (body.params as { name?: string; arguments?: Record<string, unknown> } | undefined) || {};
          if (!params.name) {
            return withCors(Response.json(jsonRpcError(id, -32602, "Missing tool name")));
          }

          const text = await handleToolCall(env, params.name, params.arguments || {});
          return withCors(
            Response.json(
              jsonRpcResult(id, {
                content: [{ type: "text", text }],
              }),
            ),
          );
        }

        default:
          return withCors(Response.json(jsonRpcError(id, -32601, `Method not found: ${body.method}`)));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return withCors(Response.json(jsonRpcError(id, -32000, message), { status: 500 }));
    }
  },
};
