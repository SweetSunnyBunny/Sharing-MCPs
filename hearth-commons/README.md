# Commons remote MCP (OAuth)

The current Commons connector supports identity-bound OAuth and personal bearer keys.
It connects to a compatible Commons world server. The sibling
[commons-game](../commons-game/README.md) package includes a public starter world.
Personal home content, saves and connection keys are excluded.

## Deploy your connector

1. Install Node.js 22.14 or newer, then run `npm ci` in this folder.
2. Run `npx wrangler kv namespace create OAUTH_KV`; put its ID in `wrangler.toml`.
3. Set `COMMONS_BASE_URL` to your compatible world server and `PUBLIC_URL` to this
   Worker's own public HTTPS origin. Remove all placeholders before deployment.
4. Run `npm run typecheck`, then `npm run deploy`.
5. Add `https://YOUR-WORKER.YOUR-ACCOUNT.workers.dev/mcp` to a remote MCP client.
   Use OAuth browser sign-in when supported, or a personal character connection
   key in `Authorization: Bearer YOUR-TOKEN` for compatible clients.

OAuth consent uses a per-character key issued by your world server. Discovery is
public; actual game tools require authorization. No shared household keyring or
global `MCP_AUTH_TOKEN` is used by this version.

Six tools: `commons_look`, `commons_move`, `commons_say`, `commons_act`,
`commons_listen`, and `commons_help`. Begin with `commons_look`. Mutations require
a unique `request_id`; reuse exactly the same arguments and ID on retries.
`commons_listen` waits only during its current call and schedules nothing.

The backend must implement `/api/agent/identity` and `/api/agent/call`, plus its
character invitation/connection flow. Deploying this Worker alone creates no world.
This public package has been type-checked offline; sign-in and play require your
own configured backend and accounts.
