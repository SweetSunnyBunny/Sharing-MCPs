# Social code guide

[Cloud service map](../../README.md)

Start at [src/index.ts](src/index.ts) for authentication, service entry and
tool registration. [lib/mcp.ts](src/lib/mcp.ts) handles MCP envelopes,
[lib/types.ts](src/lib/types.ts) defines shared types, and
[lib/http.ts](src/lib/http.ts) holds HTTP support.

The current tool modules are [world-tools.ts](src/tools/world-tools.ts) and
[telegram.ts](src/tools/telegram.ts). Follow their schemas, handlers and external
requests together. Older broad descriptions can mention integrations that are
not registered in this current source tree; the entry point is authoritative.

Run `npm run typecheck` locally. Live calls can publish content or send messages;
keep those operations distinct from reading and compilation.
