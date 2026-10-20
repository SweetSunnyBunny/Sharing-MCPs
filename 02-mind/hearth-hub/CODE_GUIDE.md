# Hearth code guide

[Cloud service map](../../README.md)

Start at [src/index.ts](src/index.ts), then choose the interface being changed:
[tools/sanctuary.ts](src/tools/sanctuary.ts) handles MCP tools and
[api/sanctuary.ts](src/api/sanctuary.ts) handles REST endpoints.
[lib/](src/lib/) holds database helpers, manifests, MCP envelopes and shared types.
[api/private-config.ts](src/api/private-config.ts) supplies private configuration.

Run `npm run typecheck`. `npm run db:migrate` changes the remote database;
`npm run db:migrate:local` targets the local database. Read the target explicitly.
`seed_data.py` and `scripts/generate-import.mjs` are data preparation/import
tools, separate from normal startup. Keep their SQL inputs and all versioned
migrations with the service.
