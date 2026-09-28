# Limbic — install the advisory state layer

This Worker keeps per-identity drives, time decay, interaction events and
derived feeling recipes. It supplies advisory state; values, judgment, consent
and boundaries still govern behavior. You need Node.js and a Cloudflare account
with Workers and D1. Qualia is optional: Limbic can be tested on its own.

Personal charts, authored descriptions and histories have been removed. The
starter below supplies fictional data so an empty installation can actually run.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\limbic`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `limbic`; Node should report `v24...`.
Wait for installation to finish before continuing. If PowerShell blocks
`npm.ps1`, use `npm.cmd` instead of `npm`, and `npx.cmd` instead of `npx`.
No global Wrangler installation is needed.

Sign in to your own [Cloudflare account](https://dash.cloudflare.com/):

```powershell
npx wrangler login
npx wrangler whoami
```

Approve the browser sign-in. `whoami` must show the account you want to deploy
into. A Worker is the small service this guide installs in that account.

## 2. Create the database

```powershell
npx wrangler d1 create limbic
notepad .\wrangler.toml
```

Replace the all-zero `database_id` with the ID Wrangler printed. Keep the DB
binding and `database_name = "limbic"`. Save, then run:

```powershell
npx wrangler d1 migrations apply limbic --remote
notepad .\examples\starter-drives.sql
```

The two migrations create the core tables **and the recipes table**. For a first
test you may leave the fictional `avery` and `rowan` labels in the example.
If changing them, change every occurrence in the SQL file and keep labels
consistent with Qualia/UI. Save and apply the example:

```powershell
npx wrangler d1 execute limbic --remote --file .\examples\starter-drives.sql
```

This adds six generic drives per identity and one sample recipe. Reapplying it
does not overwrite existing state or configuration.

## 3. Save the client password and deploy

Generate a client password and save it in your password manager:

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put LIMBIC_API_KEY
```

Paste the generated value when Wrangler asks for the secret. Do not paste the
command or its quotes. If prompted to create this Worker, use the name already
in `wrangler.toml`. Keep this value: your MCP client will need the same one.
Worker secrets are separate from local `.dev.vars` files.

Run the checks first. A dry run builds the Worker without deploying it.

```powershell
npm run typecheck
npm test
npx wrangler deploy --dry-run
npm run deploy
```

The last command installs the service in your Cloudflare account. Copy the
`https://...workers.dev` address printed at the end; this is your **Worker URL**.
Use that actual address below, without a trailing slash. `example.com` and
`YOUR-*` values in the supplied files are placeholders, not working services.

## 4. Check the first identity

In the same PowerShell window, enter your deployed URL and the
`LIMBIC_API_KEY` value you saved:

```powershell
$WorkerUrl = (Read-Host "Paste your Worker URL, without a trailing slash").TrimEnd('/')
$McpSecret = Read-Host "Paste your LIMBIC_API_KEY value"
$McpUrl = "$WorkerUrl/mcp/$McpSecret"
$Body = @{ jsonrpc = "2.0"; id = 1; method = "tools/list" } | ConvertTo-Json
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result.tools | Select-Object name
```

The output should be a list of tool names. This checks deployment and client
authentication without making a paid generation or posting a message.

Now check the first useful operation:

```powershell
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "limbic_drives"; arguments = @{ identity = "avery" } } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

You should see the configured drives for Avery, including seeking, care, play, fear, panic and guard. Use your own label if you renamed it. If you see `isError: true` or an `error` field, read the message before
continuing; an HTTP 200 alone does not mean the tool succeeded.

## Connect your MCP client

Add a **remote HTTP / Streamable HTTP MCP server** in your client's server
settings. Use this address, replacing both placeholders:

```text
https://YOUR-WORKER.workers.dev/mcp/YOUR-LIMBIC_API_KEY
```

Use the Worker URL and password from the successful test above. If the client
asks for authentication, choose no additional authentication for this URL: its
path already contains your password. Treat the whole URL as private. Refresh
the client's tool list after adding it. Client configuration screens vary;
this package does not install or log in to a chat application for you.

If your client can send custom headers, you can instead use `/mcp` and
`Authorization: Bearer YOUR-LIMBIC_API_KEY`.

## Customize the examples

Always pass `identity` in tool calls. Omitting it selects the unseeded label
`default`, not whichever identity you last used.

Each drive has a baseline, floor/ceiling, half-life, optional environmental
sensitivity, description bands and action bands. Empty sensitivities mean
weather and moon phase do not change the example baselines. `recipes.conditions`
combines drive thresholds, recent events and optional environment or
time-since-interaction predicates. `tints` can hold your own per-identity text.

Interaction kinds live in `TOUCH_KINDS` in `src/index.ts`: `connection`,
`reassurance`, `play`, `distress`, and `distance`. Missing drives are skipped.
The UI aliases `words_warm`, `praise` and `playful` map to the first three kinds.
Edit this map and your SQL configuration together, then redeploy code changes.

`limbic_safeword` accepts `green` (log only), `yellow` (pause/check-in without
changing levels), or `red` (dampen to floor). Missing or unknown phrases request
a full stop. Define your own vocabulary deliberately before changing this map.

## Optional Qualia connection and local development

In the sibling `mind-backend` Worker's `wrangler.toml`, add a `LIMBIC` service
binding whose `service` is this Worker's name (`limbic-backend` by default).
Set `LIMBIC_API_KEY` on Qualia to the same secret used here and redeploy Qualia.
Authenticated `GET /snapshot/<identity>` returns current levels and recipes.
An unconfigured identity returns 404. Neither Worker needs files from the other.

For local work, copy `.dev.vars.example` to `.dev.vars`, fill in your own key,
apply migrations and starter with `--local`, and run `npm run dev`.
`npm test` creates temporary in-memory databases and checks migrations, seeds,
UI payloads, recipes, authentication and stage behavior without live keys.

## If something goes wrong

- **`node` or `npm` is not recognized:** install Node, close PowerShell, and open
  it again from this package folder.
- **Wrangler shows the wrong account:** run `npx wrangler login`, then check
  `npx wrangler whoami` again.
- **401, 403, or 404 at the MCP URL:** check the exact Worker URL and secret.
  Opening the bare Worker home page is not an MCP test.
- **A command cannot find `package.json`:** your terminal is in the wrong folder.
- **A check fails:** stop at that step and use its first error message. Do not
  deploy a failed build. For deployed Worker logs, run `npx wrangler tail`;
  press Ctrl+C when finished.
- **Empty drives or unknown identity:** apply `starter-drives.sql` to the same
  remote database and pass the exact registered identity.
- **Missing `recipes` table:** apply both migrations, not just `0001`.
- **Node cannot load `node:sqlite`:** install Node 24, reopen PowerShell and retry.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.

## Reading the code

Start with [src/index.ts](src/index.ts), then [migrations/](migrations/), then [examples/](examples/). The installation steps above describe the runtime configuration.
Behavioral regression checks live in [tests/](tests/).
