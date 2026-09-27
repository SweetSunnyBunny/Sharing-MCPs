# Qualia / Mind Backend — install durable cloud memory

This is the current Qualia memory service. It stores identity-scoped memories,
feelings, journals, relationships, anticipations, creative work, Sketchbook and
coordinated recall. You need your own Cloudflare account with Workers, D1,
Workers AI and Vectorize available. D1 stores records; Vectorize stores search
vectors; Workers AI makes the embeddings. These services may incur charges.

You can install this service on its own. The Anam UI, Limbic and the
`qualia-mcp` stdio adapter are optional. All 19 migrations are included. No
personal memories or biographies are supplied.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\mind-backend`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `mind-backend`; Node should report `v24...`.
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

## 2. Create the empty database and search index

```powershell
npx wrangler d1 create qualia
notepad .\wrangler.toml
```

Copy the new `database_id` printed by the first command into `wrangler.toml`,
replacing the all-zero ID. Keep `binding = "DB"` and `database_name = "qualia"`.
Save and close Notepad, then run:

```powershell
npx wrangler d1 migrations apply qualia --remote
npx wrangler vectorize create qualia-vectors --dimensions=768 --metric=cosine
```

Accept the migration prompt for your new database. Do not import old private
databases into this starter. The index name and 768 dimensions match the
shipped embedding configuration.

## 3. Register identities

For the easiest first test, keep the fictional `avery`, `rowan` and `pack` labels
in `examples/starter-identities.sql`. Or open that file and change both IDs and
display names to your own before applying it:

```powershell
notepad .\examples\starter-identities.sql
npx wrangler d1 execute qualia --remote --file .\examples\starter-identities.sql
```

`pack` is a shared-memory scope. Use the same lowercase identity IDs in your UI,
Limbic and MCP calls. The example can be reapplied without overwriting existing
identities or routing profiles. Creating a label here does not add a biography.

## 4. Save the client password and deploy

Generate a client password and save it in your password manager:

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put MIND_API_KEY
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

## 5. Check the schema and first memory

In the same PowerShell window, enter your deployed URL and the
`MIND_API_KEY` value you saved:

```powershell
$WorkerUrl = (Read-Host "Paste your Worker URL, without a trailing slash").TrimEnd('/')
$McpSecret = Read-Host "Paste your MIND_API_KEY value"
$McpUrl = "$WorkerUrl/mcp/$McpSecret"
$Body = @{ jsonrpc = "2.0"; id = 1; method = "tools/list" } | ConvertTo-Json
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result.tools | Select-Object name
```

The output should be a list of tool names. This checks deployment and client
authentication without making a paid generation or posting a message.

Now check the first useful operation:

```powershell
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "mind_schema_status"; arguments = @{} } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

The response should describe the installed schema, without a missing-table error. If you see `isError: true` or an `error` field, read the message before
continuing; an HTTP 200 alone does not mean the tool succeeded.

After the read-only check succeeds, you can create a clearly marked test memory:

```powershell
$Body = @{ jsonrpc = "2.0"; id = 3; method = "tools/call"; params = @{ name = "mind_store"; arguments = @{ identity = "avery"; content = "Installation test: this is fictional starter data."; tags = @("installation-test") } } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

Use your own registered ID if you renamed `avery`. A returned stored observation
confirms a database write; this operation also uses the embedding service.
Keep the returned ID if you want to remove this test record later.

## Connect your MCP client

Add a **remote HTTP / Streamable HTTP MCP server** in your client's server
settings. Use this address, replacing both placeholders:

```text
https://YOUR-WORKER.workers.dev/mcp/YOUR-MIND_API_KEY
```

Use the Worker URL and password from the successful test above. If the client
asks for authentication, choose no additional authentication for this URL: its
path already contains your password. Treat the whole URL as private. Refresh
the client's tool list after adding it. Client configuration screens vary;
this package does not install or log in to a chat application for you.

If your client can send custom headers, you can instead use `/mcp` and
`Authorization: Bearer YOUR-MIND_API_KEY`.

## Optional integrations and development

- **Anam UI:** use this Worker's URL and key in your UI configuration. The
  Worker does not need a local Anam source tree.
- **Stdio-only MCP clients:** install the sibling `qualia-mcp` adapter and point
  it at this deployment. It forwards the current tools; it is not a second store.
- **Limbic:** deploy the sibling `limbic` package, use matching identity IDs,
  and add this block to `wrangler.toml`:

```toml
[[services]]
binding = "LIMBIC"
service = "limbic-backend"
```

Set `LIMBIC_API_KEY` on this Worker to the same value used by your Limbic Worker,
then deploy again. Change `service` if you changed that Worker's name.

The existing cron runs periodic continuity work. Review `[triggers]` in
`wrangler.toml` if you want to change its schedule. Identity routing can be
adjusted through `mind_update_identity_routing` after registering the identity.

For local development, copy `.dev.vars.example` to `.dev.vars`, fill in your
key, and apply migrations and starter rows with `--local` instead of `--remote`.
Then run `npm run dev`. Cloud Workers AI/Vectorize still require account setup;
the tests use local mocks and need no credentials. `LOCAL_SIDECAR_URL` is an
optional separately administered embedding HTTP service, not a missing local
file. Leave it unset for this cloud install. Changing `EMBED_MODEL` requires a
matching Vectorize dimension and reindexing.

See [COORDINATED-MEMORY.md](COORDINATED-MEMORY.md),
[migration notes](migrations/README.md) and [script notes](scripts/README.md)
for the deeper memory and evaluation workflow.

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
- **`FOREIGN KEY constraint failed` / unknown identity:** apply the starter and use
  its exact lowercase IDs.
- **Missing table:** check the database ID and apply all migrations to `--remote`.
- **Embedding or Vectorize error:** confirm Workers AI is available and the index
  is named `qualia-vectors` with 768 dimensions.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.
