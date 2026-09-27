# Social MCP — World Tools and Telegram

The code in this package currently loads **two modules**: World Tools and
Telegram. World Tools provides time, calendar information, weather, news and
URL reading. Telegram adds bot messages, photos and optional voice messages.
You need Node.js and Cloudflare. You can complete the first check without a
Telegram account or any paid model service. No D1 or KV database is required.

Older descriptions of this project mentioned X/Twitter, Moltbook, Tumblr and
Reddit. Those modules and their OAuth helpers are not shipped or registered in
this Worker. Adding their secret names does not enable those integrations.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\social-backend`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `social-backend`; Node should report `v24...`.
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

## 2. Set the allowed tools and example location

```powershell
notepad .\wrangler.toml
```

The supplied `ALLOWED_TOOLS` lists World Tools plus Telegram. If you only want
the initial World Tools setup, set it to `"wt_*"`. Keep `wt_time_now` available
for the first test. `WT_HOME_LAT` and `WT_HOME_LON` are Greenwich example
coordinates, not your location. Replace them and `WT_HOME_LABEL` if you want
`wt_weather_home` to report your own place; otherwise use `wt_weather_current`
with a location argument. Save the file.

## 3. Save the client password and deploy

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

## 4. Check a World Tool

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
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "wt_time_now"; arguments = @{ timezone_name = "UTC" } } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

The result should contain the current UTC time. This requires no Telegram token and sends no message. If you see `isError: true` or an `error` field, read the message before
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

## Optional: add your own Telegram bot

1. In Telegram, open the official **BotFather** account and use `/newbot`.
   Follow its prompts and keep the returned bot token private.
2. Open a chat with your new bot and send it a message such as `hello`. A bot
   cannot start a private conversation with a person who has never contacted it.
3. Obtain your chat ID using that bot's own API in PowerShell:

```powershell
$TelegramToken = Read-Host "Paste your new bot token"
$Updates = Invoke-RestMethod "https://api.telegram.org/bot$TelegramToken/getUpdates"
$Updates.result.message.chat | Select-Object id, type
```

If no chat appears, send a new message to the bot and repeat. Use the `id` for
the chat you intend this bot to use. Then save your configuration:

```powershell
npx wrangler secret put TELEGRAM_CONFIG
```

Paste a JSON object with your values, for example:

```json
{"avery":{"bot_token":"YOUR-BOT-TOKEN","chat_id":"YOUR-CHAT-ID"}}
```

You may choose another lowercase label. Re-enable `list_telegram_identities`,
`send_telegram_message` and whichever other Telegram tools you want in
`ALLOWED_TOOLS`, then run `npm run deploy`. First call
`list_telegram_identities`; it should list your label without exposing the token.
A later `send_telegram_message` call actually sends a message to that chat.

For voice messages, add `elevenlabs_voice_id` to that identity's JSON and set
`ELEVENLABS_API_KEY` with `npx wrangler secret put ELEVENLABS_API_KEY`. Voice
generation uses your ElevenLabs credits. Photos use reachable URLs, not paths
to files on your Windows computer.

## Local development and source layout

Create `.dev.vars` containing your own `MCP_SECRET_PATH`, and optionally
`TELEGRAM_CONFIG` / `ELEVENLABS_API_KEY`, then run `npm run dev`. Never distribute
the populated file. The active modules are `src/tools/world-tools.ts` and
`src/tools/telegram.ts`; `src/index.ts` defines the registry and allowlist.

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
- **Telegram identity missing:** check JSON syntax and upload `TELEGRAM_CONFIG` again.
- **Telegram chat not found:** message the bot first and verify its chat ID.
- **A tool is absent:** check `ALLOWED_TOOLS`; unsupported historical modules cannot
  be enabled merely by adding their names.
- **Weather/news fails while time works:** the upstream public data service may
  be unavailable or have rejected the request. Check the returned error.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.
