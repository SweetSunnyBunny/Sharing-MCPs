# Google MCP — connect your own Google account

This Cloudflare Worker provides Drive, Docs, Sheets, Calendar and YouTube tools.
Optional Google Health tools are also included, subject to your account's API
access. You need Node.js, a Cloudflare account with Workers/D1, a Google account,
and a Google Cloud project. Start with the Drive service group; YouTube and
Health can be added later. No local Anam or other MCP package is required.

OAuth lets Google ask which account and permissions you approve. The local
helper obtains a refresh token; this Worker uses it to access your account.
That token and the downloaded client credentials must stay private.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\google-cloud-mcp`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `google-cloud-mcp`; Node should report `v24...`.
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

## 2. Create Google OAuth credentials

In the [Google Cloud Console](https://console.cloud.google.com/):

1. Create or select a project owned by you.
2. Enable **Google Drive API**, **Google Docs API**, **Google Sheets API** and
   **Google Calendar API**. These are the APIs covered by the helper's `drive`
   service group. Enable **YouTube Data API v3** only if adding YouTube.
3. Configure the project's OAuth consent screen / Google Auth Platform with
   your app name and contact details. If the app is in Testing, add your own
   Google account as a test user.
4. Create an OAuth client with application type **Desktop app**. The supplied
   helper starts a temporary loopback callback, so a web-app client is not the
   easiest choice for this guide.
5. Download the client's JSON credentials. Copy that downloaded file into this
   package's `scripts` folder and rename it **`client_secret.json`**.

Back in the package PowerShell window, confirm the file is in the right place:

```powershell
Test-Path .\scripts\client_secret.json
npm run oauth -- --identity personal --service drive
```

`Test-Path` should say `True`. The helper opens a browser; if it does not, copy
the authorization URL printed in the terminal into your browser. Sign in to the
intended account and approve the requested access. Return to the terminal.
Success creates `scripts/google_tokens.json`. `personal` is your chosen label,
not a special account. Use another lowercase label for additional accounts.

If adding YouTube, authorize it separately:

```powershell
npm run oauth -- --identity personal --service youtube
```

The helper merges tokens into the existing file. Do not upload either JSON file
to a repository or include it in a shared copy.

## 3. Create the token-cache database

```powershell
npx wrangler d1 create google-cloud-mcp-tokens
notepad .\wrangler.toml
```

Replace the all-zero `database_id` with the ID printed by Wrangler. Keep
`binding = "DB"` and `database_name = "google-cloud-mcp-tokens"`. To start with
the Drive group only, change `ALLOWED_TOOLS` to:

```toml
ALLOWED_TOOLS = "gdrive_*,gdocs_*,gsheets_*,gcal_*"
```

Save and apply the schema:

```powershell
npx wrangler d1 migrations apply google-cloud-mcp-tokens --remote
```

## 4. Upload your secrets

Generate a client password and save it in your password manager:

```powershell
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put MCP_SECRET_PATH
```

Paste the generated value when Wrangler asks for the secret. Do not paste the
command or its quotes. If prompted to create this Worker, use the name already
in `wrangler.toml`. Keep this value: your MCP client will need the same one.
Worker secrets are separate from local `.dev.vars` files.

Open your downloaded credential JSON privately:

```powershell
notepad .\scripts\client_secret.json
npx wrangler secret put GOOGLE_CLIENT_ID
npx wrangler secret put GOOGLE_CLIENT_SECRET
```

For `GOOGLE_CLIENT_ID`, paste the `client_id` value from the JSON. For
`GOOGLE_CLIENT_SECRET`, paste its `client_secret` value. Do not include JSON
field names or surrounding quotes. Then upload the complete generated token
JSON without copying it into a shell command:

```powershell
Get-Content -Raw .\scripts\google_tokens.json | npx wrangler secret put GOOGLE_TOKENS
npx wrangler secret list
```

The last command shows secret names, not values. Confirm all four names exist.

## 5. Build, deploy and read a few files

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
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "gdrive_list_files"; arguments = @{ identity = "personal"; max_results = 5 } } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

You should see up to five Drive files, or an empty list for an empty account. This operation reads metadata; it does not change a file. If you see `isError: true` or an `error` field, read the message before
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

If your client can send custom headers, you can instead use `/mcp` and
`Authorization: Bearer YOUR-MCP_SECRET_PATH`.

## Add accounts, YouTube or Google Health

For another account, repeat the OAuth helper with another label, such as
`--identity work --service drive`. Upload the new complete `GOOGLE_TOKENS` file
after each change. For YouTube, authorize `--service youtube`, then add
`youtube_*` (or selected exact names) to `ALLOWED_TOOLS` and redeploy.

Optional Health setup uses your own eligible Google Health OAuth client:

```powershell
npm run oauth -- --identity personal --service health --credentials scripts/health_client_secret.json
```

Create/download that client JSON yourself before running the command. If it is
a separate client, also set `GOOGLE_HEALTH_CLIENT_ID` and
`GOOGLE_HEALTH_CLIENT_SECRET`, upload the updated `GOOGLE_TOKENS`, and add
`health_*` to the allowlist. Installation cannot grant unavailable API scopes.
`src/health.ts` uses `ASSUMED_TZ`, defaulting to UTC; set your own IANA timezone
there if you enable these tools. No personal health data is supplied.

For code or allowlist changes, run `npm run deploy`. For replaced refresh
tokens, upload `GOOGLE_TOKENS` again. Google can expire or revoke authorizations;
follow the account's consent/error messages and rerun authorization when needed.

For local development, create a private `.dev.vars` with the same four secret
names, apply the D1 migration with `--local`, and run `npm run dev`.

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
- **`redirect_uri_mismatch`:** use a Desktop app OAuth client and rerun the helper.
- **`access_denied`:** verify the consent setup, your test-user entry and enabled APIs.
- **No refresh token:** review/revoke this app in your own Google account, then
  authorize again; existing tokens are not fixed by redeploying code.
- **`Unknown identity`:** use a label in the uploaded `GOOGLE_TOKENS` JSON.
- **`no such table: tokens`:** apply the D1 migration to the correct remote database.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.

## Reading the code

Start with [src/index.ts](src/index.ts), then [src/oauth.ts](src/oauth.ts), then [migrations/](migrations/). The installation steps above describe the runtime configuration.
