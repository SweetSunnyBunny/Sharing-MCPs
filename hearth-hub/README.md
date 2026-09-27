# Hearth Hub — install shared presence and room state

This Worker stores presence, actions, moods, rooms, notes, projects, birthdays
and games in a Cloudflare D1 database. It offers MCP tools plus REST views used
by the Anam UI. You need Node.js and your own Cloudflare account. Basic state
tools work without the UI, portrait images, voice generation or another package.

The database starts without personal household data. Optional images need your
own HTTPS asset hosting; example CDN/Anam addresses do not serve real assets.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\hearth-hub`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `hearth-hub`; Node should report `v24...`.
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

## 2. Create and initialize the database

```powershell
npx wrangler d1 create hearth-hub-db
notepad .\wrangler.toml
```

Paste the printed database ID over the all-zero `database_id`. Keep
`binding = "DB"` and `database_name = "hearth-hub-db"`. Save, then run:

```powershell
npx wrangler d1 migrations apply hearth-hub-db --remote
notepad .\examples\starter-state.sql
```

For the first check, keep the fictional `avery` and `rowan` examples. Or replace
them with your own IDs, keeping `active_identity` consistent. Save and load:

```powershell
npx wrangler d1 execute hearth-hub-db --remote --file .\examples\starter-state.sql
```

The example registers identities, without importing rooms owned by other people,
birthdays or private biographies. Tools use IDs from `identity_state`.

## 3. Review settings and save the client password

In `wrangler.toml`, `WT_HOME_LAT` and `WT_HOME_LON` contain Greenwich example
coordinates; replace them if using home weather. `ENABLED_TOOLS` is the exact
tool allowlist. Keep `list_identities` and `get_state` for initial testing.
Remove `set_orb` unless connecting the optional Anam UI below. Save the file.

Generate a client password and save it in your password manager:

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put MCP_SECRET_PATH
```

Paste the generated value when Wrangler asks for the secret. Do not paste the
command or its quotes. If prompted to create this Worker, use the name already
in `wrangler.toml`. Keep this value: your MCP client will need the same one.
Worker secrets are separate from local `.dev.vars` files.

Run the checks first. A dry run builds the Worker without deploying it.

```powershell
npm run typecheck
npx wrangler deploy --dry-run
npm run deploy
```

The last command installs the service in your Cloudflare account. Copy the
`https://...workers.dev` address printed at the end; this is your **Worker URL**.
Use that actual address below, without a trailing slash. `example.com` and
`YOUR-*` values in the supplied files are placeholders, not working services.

## 4. Check registered identities

In the same PowerShell window, enter your deployed URL and the
`MCP_SECRET_PATH` value you saved:

```powershell
$WorkerUrl = (Read-Host "Paste your Worker URL, without a trailing slash").TrimEnd('/')
$McpSecret = Read-Host "Paste your MCP_SECRET_PATH value"
$McpUrl = "$WorkerUrl/mcp/$McpSecret"
$Body = @{ jsonrpc = "2.0"; id = 1; method = "tools/list" } | ConvertTo-Json
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result.tools | Select-Object name
```

The output should be a list of tool names. This checks deployment and client
authentication without making a paid generation or posting a message.

Now check the first useful operation:

```powershell
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "list_identities"; arguments = @{} } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

The result should include the identity rows you just added. Empty output means the starter was not loaded into this database. If you see `isError: true` or an `error` field, read the message before
continuing; an HTTP 200 alone does not mean the tool succeeded.

## Connect your MCP client

Add a **remote HTTP / Streamable HTTP MCP server** in your client's server
settings. Use this address, replacing both placeholders:

```text
https://YOUR-WORKER.workers.dev/mcp/YOUR-MCP_SECRET_PATH
```

Use the Worker URL and password from the successful test above. If the client
asks for authentication, choose no additional authentication for this URL: its
path already contains your password. Treat the whole URL as private. Refresh
the client's tool list after adding it. Client configuration screens vary;
this package does not install or log in to a chat application for you.

## Add images, UI integration and your own settings

For portraits and backgrounds, set `ASSET_CDN_BASE` to your own HTTPS asset
directory and host a compatible `portraits/manifest.json` beneath it. Image
paths in the manifest must exist. The shared fallback expects
`backgrounds/bg_nest.png`. Missing images do not prevent the text state tools.

For `set_orb`, run/configure your own Anam UI, set `ANAM_API_URL` to its reachable
origin, and set `ANAM_API_KEY` as a Worker secret to match the UI. Re-enable
`set_orb` in `ENABLED_TOOLS` and deploy again. Voice features require your own
`ELEVENLABS_API_KEY`; they are not part of the first state check.

Displayed timestamps and time-of-day selection use UTC. To change them, update
`src/lib/db.ts`, `src/api/sanctuary.ts` and `src/tools/sanctuary.ts` together.
The room catalog is an editable example. With no active identity configured,
the viewer intentionally returns no active identity.

The `/api/sanctuary/...` viewer routes are public in this implementation; keep
state there suitable for that audience. The MCP password does not turn those
viewer routes into private routes. Private configuration uses its separate
authenticated API.

For local development, create a `.dev.vars` file containing
`MCP_SECRET_PATH=your-own-local-test-password`. Apply the migrations and starter
with `--local`, then run `npm run dev`. That file and `.wrangler` stay local.

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
- **Identity rejected:** register it in `identity_state` and use its exact ID.
- **Missing table:** run the migration command with the correct remote database ID.
- **Broken portrait/background:** verify your CDN URL, manifest and image paths.
- **Orb call fails:** configure the optional Anam URL/key or leave `set_orb` disabled.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.
