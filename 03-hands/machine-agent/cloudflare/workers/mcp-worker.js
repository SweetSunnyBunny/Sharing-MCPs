/**
 * Cloudflare Worker that acts as a streamable-http MCP server.
 * Receives MCP JSON-RPC requests from Claude, translates tool calls
 * into HTTP POST /tools/invoke on the machine agent, and returns results.
 *
 * Environment variables (set via wrangler.jsonc or `wrangler secret put`):
 *   MACHINE_AGENT_URL  - e.g. "https://machine-agent.example.com"
 *   MACHINE_AGENT_KEY  - optional Bearer token for the machine agent
 *   MCP_AUTH_TOKEN     - REQUIRED Bearer token clients must send to this Worker;
 *                        /mcp answers 503 to everyone until it is set
 *   SERVER_NAME        - which server spec to serve (e.g. "clipboard")
 */

import { SERVERS } from "./tool-specs.js";

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
// Constant-time string comparison, so a wrong token is rejected in the same
// time however many leading characters happen to match.
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------------------------------------------------------------------------
// Machine agent HTTP client
// ---------------------------------------------------------------------------
async function invokeRemoteTool(env, toolName, args) {
  if (["anam_discover", "anam_invoke", "anam_job", "anam_result"].includes(toolName) &&
      (!env.MCP_AUTH_TOKEN || !env.MACHINE_AGENT_KEY)) {
    throw new Error("Anam gateway requires authenticated connector and machine-agent credentials");
  }
  const url = `${env.MACHINE_AGENT_URL}/tools/invoke`;
  const headers = { "Content-Type": "application/json", Accept: "application/json" };
  if (env.MACHINE_AGENT_KEY) headers["Authorization"] = `Bearer ${env.MACHINE_AGENT_KEY}`;

  // Wait a little LONGER than the bridge will. The terminal kills a command at
  // its own timeout (120s by default, or whatever the caller passed) and answers
  // with what it has. At the old fixed 90s the Worker gave up first, so a command
  // still running was reported as failed and a retry ran it twice.
  const toolSeconds = toolName === "terminal_execute" && Number(args?.timeout) > 0 ? Number(args.timeout) : 120;
  const waitSeconds = Math.min(toolSeconds, 900) + 10;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), waitSeconds * 1000);
  let resp;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ tool: toolName, arguments: args }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error(`Machine agent timed out after ${waitSeconds} seconds while invoking ${toolName}`);
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }

  const text = await resp.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    const detail = text.trim().slice(0, 300) || "empty non-JSON response";
    throw new Error(`Machine agent HTTP ${resp.status}: ${detail}`);
  }

  if (resp.ok && body.ok) return body.result;
  throw new Error(body.error || `Machine agent HTTP ${resp.status}`);
}

// ---------------------------------------------------------------------------
// MCP protocol helpers
// ---------------------------------------------------------------------------
function serverInfo(spec) {
  return {
    name: spec.displayName || "Machine Agent MCP",
    version: "0.1.0",
  };
}

function toolSchema(tool) {
  const properties = {};
  const required = [];
  for (const p of tool.params) {
    const prop = {};
    // Map Python-ish types to JSON Schema
    const t = p.type.replace(/ \| None/g, "").replace(/\s/g, "");
    if (t === "str") prop.type = "string";
    else if (t === "int") prop.type = "integer";
    else if (t === "float" || t === "number") prop.type = "number";
    else if (t === "bool") prop.type = "boolean";
    else if (t.startsWith("list[") || t.startsWith("List[")) prop.type = "array";
    else if (t.startsWith("dict") || t.startsWith("Dict")) prop.type = "object";
    else prop.type = "string";

    if (p.type.includes("None")) {
      prop.type = [prop.type, "null"];
    }

    if ("default" in p) {
      const literal = {True: "true", False: "false", None: "null"}[p.default] ?? p.default;
      try { prop.default = JSON.parse(literal); } catch { /* Non-JSON Python defaults remain optional. */ }
    }
    if (p.description) prop.description = p.description;
    properties[p.name] = prop;
    if (!("default" in p)) required.push(p.name);
  }
  return {
    name: tool.name,
    description: tool.doc,
    ...(tool.annotations ? { annotations: tool.annotations } : {}),
    inputSchema: {
      type: "object",
      properties,
      ...(required.length ? { required } : {}),
    },
  };
}

function jsonRpcOk(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function jsonRpcError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

const MAX_IMAGE_BASE64_CHARS = 7_000_000;

function toolResultContent(result) {
  // The Anam gateway already returns validated MCP content, not a JSON
  // description of an image. Keep all block types and structured results.
  if (result?.status === "completed" && Array.isArray(result.content)) {
    return result.content;
  }
  if (result && result.type === "image") {
    const data = result.data;
    const mimeType = result.mimeType;
    if (typeof data !== "string" || !data) {
      throw new Error("Machine agent returned an image without base64 data");
    }
    if (data.length > MAX_IMAGE_BASE64_CHARS) {
      throw new Error("Machine agent image payload exceeds the 5 MB transport limit");
    }
    if (typeof mimeType !== "string" || !mimeType.startsWith("image/")) {
      throw new Error("Machine agent returned an invalid image MIME type");
    }
    return [{ type: "image", data, mimeType }];
  }

  const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
  return [{ type: "text", text }];
}


// ---------------------------------------------------------------------------
// Handle a single JSON-RPC message
// ---------------------------------------------------------------------------
async function handleMessage(msg, env, spec) {
  const { id, method, params } = msg;

  switch (method) {
    case "initialize":
      return jsonRpcOk(id, {
        protocolVersion: "2025-03-26",
        capabilities: { tools: { listChanged: false } },
        serverInfo: serverInfo(spec),
      });

    case "notifications/initialized":
    case "notifications/cancelled":
      return null; // no response for notifications

    case "ping":
      return jsonRpcOk(id, {});

    case "tools/list":
      return jsonRpcOk(id, {
        tools: spec.tools.map(toolSchema),
      });

    case "tools/call": {
      const toolName = params?.name;
      const args = params?.arguments || {};
      const tool = spec.tools.find((t) => t.name === toolName);
      if (!tool) {
        return jsonRpcOk(id, {
          isError: true,
          content: [{ type: "text", text: `Unknown tool: ${toolName}` }],
        });
      }
      try {
        const result = await invokeRemoteTool(env, toolName, args);
        return jsonRpcOk(id, {
          content: toolResultContent(result),
          ...(result?.status === "completed" ? {
            isError: !!result.isError,
            ...(result.structuredContent ? { structuredContent: result.structuredContent } : {}),
          } : {}),
        });
      } catch (err) {
        return jsonRpcOk(id, {
          isError: true,
          content: [{ type: "text", text: `Error: ${err.message}` }],
        });
      }
    }

    default:
      return jsonRpcError(id, -32601, `Method not found: ${method}`);
  }
}

// ---------------------------------------------------------------------------
// HTTP handler
// ---------------------------------------------------------------------------
async function handleMcpRequest(request, env, spec) {
  // Accept header determines response format
  const accept = request.headers.get("Accept") || "";
  let body;
  try {
    body = await request.json();
  } catch {
    // A body that is not JSON is the client's error, not a Worker crash (500).
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Handle batch or single
  const messages = Array.isArray(body) ? body : [body];
  const responses = [];

  for (const msg of messages) {
    const resp = await handleMessage(msg, env, spec);
    if (resp !== null) responses.push(resp);
  }

  // If the client accepts SSE, send as SSE events (streamable-http spec)
  if (accept.includes("text/event-stream")) {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        for (const resp of responses) {
          controller.enqueue(encoder.encode(`event: message\ndata: ${JSON.stringify(resp)}\n\n`));
        }
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });
  }

  // Otherwise plain JSON
  if (responses.length === 0) return new Response("", { status: 202 });
  if (responses.length === 1) {
    return new Response(JSON.stringify(responses[0]), {
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify(responses), {
    headers: { "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Worker entry point
// ---------------------------------------------------------------------------
export default {
  async fetch(request, env) {
    const serverName = env.SERVER_NAME;
    const spec = SERVERS[serverName];
    if (!spec) {
      return new Response(JSON.stringify({ error: `Unknown server: ${serverName}` }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }

    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response(JSON.stringify({ ok: true, server: spec.displayName }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // MCP endpoint: /mcp or /mcp/<token> (path-based auth)
    const mcpMatch = url.pathname.match(/^\/mcp(?:\/(.+))?$/);
    if (mcpMatch) {
      // Support path-based auth: /mcp/<token> (matches pattern used by hearth, qualia, etc.)
      const pathToken = mcpMatch[1];
      const expectedToken = env.MCP_AUTH_TOKEN;
      // Fail closed. This Worker forwards to a bridge that runs commands on the
      // PC, so a Worker deployed without its token must refuse everyone rather
      // than serve everyone (the check used to be skipped when the secret was unset).
      if (!expectedToken) {
        return new Response(JSON.stringify({ jsonrpc: "2.0", error: { code: -32001, message: "Server not configured: MCP_AUTH_TOKEN is not set" } }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      }
      const authHeader = request.headers.get("Authorization") || "";
      const headerOk = safeEqual(authHeader, `Bearer ${expectedToken}`);
      const pathOk = safeEqual(pathToken || "", expectedToken);
      if (!headerOk && !pathOk) {
        return new Response(JSON.stringify({ jsonrpc: "2.0", error: { code: -32001, message: "Unauthorized" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (request.method === "POST") {
        return handleMcpRequest(request, env, spec);
      }

      // GET - SSE endpoint for server-initiated messages
      if (request.method === "GET") {
        return new Response("SSE endpoint - POST to send messages", { status: 405 });
      }

      // DELETE - session termination
      if (request.method === "DELETE") {
        return new Response("", { status: 200 });
      }
    }

    return new Response("Not found", { status: 404 });
  },
};
