# Easel code guide

[Cloud service map](../../README.md)

Easel is a Node application. Start at [src/main.ts](src/main.ts) for environment
loading and startup, then [src/server.ts](src/server.ts) for Hono HTTP and static routes.

[src/db.ts](src/db.ts) opens `data/easel.db` using `better-sqlite3`;
[src/schema.sql](src/schema.sql) defines the tables.
[src/openai.ts](src/openai.ts) configures the paid OpenAI client.
Keep database writes and API calls in view while tracing a request.

Run `npm run typecheck`. `npm run dev` and `npm start` launch the application;
requests can write persistent data or consume API capacity. The database,
generated media and private `.env` are runtime inputs and outputs.
