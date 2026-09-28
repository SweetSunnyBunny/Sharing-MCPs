# Commons MCP connector — connect to an existing house

This folder installs a small Cloudflare Worker that connects an MCP client to
an **existing compatible Commons server**. It does not contain the house
server, world files or a ready-made hosted world. You need the base URL of a
Commons installation and guest keys issued by its administrator for identities
you are allowed to control. If you do not have those yet, obtain them before
expecting movement or notes to work. The example URL is intentionally inactive.

You also need Node.js and your own Cloudflare account. There is no D1 or KV
database to create for this connector.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\hearth-commons`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm install
```

The first line must end in `hearth-commons`; Node should report `v24...`.
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

## 2. Enter the Commons address and your guest keys

```powershell
notepad .\wrangler.toml
```

Replace `COMMONS_BASE_URL = "https://example.com"` with the compatible house
server's HTTPS origin, without a trailing slash. Save and close the file.
Ask that server's administrator for each allowed identity label and guest key.

```powershell
npx wrangler secret put COMMONS_KEYS
```

At the prompt, paste one JSON object using your real issued values:

```json
{"avery":"YOUR-ISSUED-GUEST-KEY","rowan":"ANOTHER-ISSUED-GUEST-KEY"}
```

The labels must match the identities registered by that Commons server.
Remove any entry you do not have a key for. Do not use your personal MCP client
password as a guest key; they serve different purposes.

## 3. Save the client password and deploy

Generate a client password and save it in your password manager:

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put MCP_AUTH_TOKEN
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

## 4. Check the connector and upstream house

In the same PowerShell window, enter your deployed URL and the
`MCP_AUTH_TOKEN` value you saved:

```powershell
$WorkerUrl = (Read-Host "Paste your Worker URL, without a trailing slash").TrimEnd('/')
$McpSecret = Read-Host "Paste your MCP_AUTH_TOKEN value"
$McpUrl = "$WorkerUrl/mcp/$McpSecret"
$Body = @{ jsonrpc = "2.0"; id = 1; method = "tools/list" } | ConvertTo-Json
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result.tools | Select-Object name
```

The output should be a list of tool names. This checks deployment and client
authentication without making a paid generation or posting a message.

Now check the first useful operation:

```powershell
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "commons_where"; arguments = @{} } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

The result should describe presence in the configured house. This checks that the upstream URL is reachable; it does not yet test a guest-key write. If you see `isError: true` or an `error` field, read the message before
continuing; an HTTP 200 alone does not mean the tool succeeded.

## Connect your MCP client

Add a **remote HTTP / Streamable HTTP MCP server** in your client's server
settings. Use this address, replacing both placeholders:

```text
https://YOUR-WORKER.workers.dev/mcp/YOUR-MCP_AUTH_TOKEN
```

Use the Worker URL and password from the successful test above. If the client
asks for authentication, choose no additional authentication for this URL: its
path already contains your password. Treat the whole URL as private. Refresh
the client's tool list after adding it. Client configuration screens vary;
this package does not install or log in to a chat application for you.

If your client can send custom headers, you can instead use `/mcp` and
`Authorization: Bearer YOUR-MCP_AUTH_TOKEN`.

## Use the house

- `commons_look` lists rooms or shows a named room as text.
- `commons_where` shows recorded presence.
- `commons_things` lists portable objects, optionally filtered by room.
- `commons_here` moves your authorized identity using `identity`, `room`, `x`,
  and `y`; `facing` and `note` are optional.
- `commons_note` places your authorized identity's text/image note at a room
  position. Use coordinates from `commons_look`, not arbitrary guesses.

The upstream house enforces ownership. Reading a room and moving a person are
different operations; only test a move in your own allowed identity. The server
controls which image URLs it accepts for notes.

For local development, create `.dev.vars` with your own `COMMONS_KEYS` JSON and
`MCP_AUTH_TOKEN`, then run `npm run dev`. Keep that populated file private.

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
- **Tools list, but house calls fail:** replace the example `COMMONS_BASE_URL`;
  verify you are using a compatible Commons server, not an ordinary website.
- **Movement/notes fail:** check the exact identity and guest key with its issuer.
- **`npm ci` complains about a lockfile:** this package starts with `npm install`;
  retain the lockfile it creates for your own future installs.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.

## Reading the code

Start with [src/index.ts](src/index.ts). The installation steps above describe the runtime configuration.
