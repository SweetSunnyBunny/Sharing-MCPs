# Discord MCP — install your own bot connection

This Worker lets an MCP client read and send Discord messages, use DMs,
reactions, channels and threads, and optionally create voice notes or images.
Start with one bot. You need Node.js, a Cloudflare account, a Discord account,
and permission to add a bot to a server. No other package is required for basic
Discord tools. Voice, image generation, and local image backup are optional.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\discord-backend`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `discord-backend`; Node should report `v24...`.
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

## 2. Create and configure one Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, and give it your own name.
2. Open its **Bot** page, create/reset the bot token, and keep it private.
   Enable **Message Content Intent** for tools that read message text. Member
   listing also needs the relevant member intent and permissions.
3. Use the application's installation/OAuth URL builder to invite the bot to a
   server you manage. Select the `bot` scope and the permissions you need, such
   as View Channels, Read Message History, Send Messages and Add Reactions.
   Give the bot access to the specific channel you will test.
4. Open these local files:

```powershell
notepad .\src\identities.ts
notepad .\wrangler.toml
```

In `IDENTITY_CONFIG`, keep the example `avery` entry for your first test and
remove the `rowan` entry unless you are setting up a second bot. `avery` is only
a label; it does not connect to anyone else's bot. `token_env` is the NAME of
the secret (`DISCORD_BOT_TOKEN_AVERY`), not the token itself. Put the application's
**Public Key** from General Information into `DISCORD_PUBLIC_KEY` in
`wrangler.toml`. It is different from the private bot token.

`ALLOWED_TOOLS` controls which tools clients see. Its default exposes all listed
Discord operations, including editing and deletion. You may remove entries you
do not intend to use. Keep `discord_list_servers` for the first check.

## 3. Save secrets and deploy

```powershell
npx wrangler secret put DISCORD_BOT_TOKEN_AVERY
```

Paste the bot token when prompted. Repeat for each extra configured identity.

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
npm test
npx wrangler deploy --dry-run
npm run deploy
```

The last command installs the service in your Cloudflare account. Copy the
`https://...workers.dev` address printed at the end; this is your **Worker URL**.
Use that actual address below, without a trailing slash. `example.com` and
`YOUR-*` values in the supplied files are placeholders, not working services.

## 4. Check it works

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
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "discord_list_servers"; arguments = @{ identity = "avery" } } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

You should see the server your bot joined. An empty list means the bot has not joined a server, or you used another bot token. If you see `isError: true` or an `error` field, read the message before
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

## Optional features

| Feature | What to configure |
|---|---|
| Voice notes | Set `ELEVENLABS_API_KEY` with `npx wrangler secret put ELEVENLABS_API_KEY`; put your own ElevenLabs voice IDs in the identity registry. |
| Image generation | Set `OPENAI_API_KEY`. Calls spend your image-provider credits and require access to the configured model. |
| Local image backup | Install the separate `machine-agent` package. Set `MACHINE_AGENT_URL` and `IMAGE_ARCHIVE_DIR` in Worker variables and set `MACHINE_AGENT_API_KEY` as a secret. The directory is on the machine-agent host, for example `C:/Images/discord`. |
| `/vibe` interaction | Set your own `DISCORD_APP_ID` and `ANTHROPIC_API_KEY`; configure the application's interaction endpoint and then use `npm run register:vibe`. Basic MCP messaging does not require this. |

Image archival stays disabled without the URL, key, and directory. A failed
write is reported as unconfirmed. The optional fixture check writes one tiny
image through your bridge and leaves it in the archive; it does not call a paid
image generator or send a Discord message. On the bridge host, set
`MACHINE_AGENT_URL`, `IMAGE_ARCHIVE_DIR`, and `MACHINE_AGENT_API_KEY` in the process
environment, then run `node --import tsx src/verify-image-archive.ts`.
`MACHINE_AGENT_KEY_FILE` may explicitly name a local key file instead.

For local Worker development, copy `.dev.vars.example` to `.dev.vars`, enter
your own values, then run `npm run dev`. Cloud secrets are not copied down into
that local file. [.mcp.json.example](.mcp.json.example) is a generic client example.

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
- **Discord `Missing Access` / `Missing Permissions`:** check the bot invitation,
  channel permissions, and selected identity. A bot can be in a server but denied a channel.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.

## Reading the code

Start with [src/index.ts](src/index.ts), then [src/tools.ts](src/tools.ts), then [src/discord.ts](src/discord.ts). The installation steps above describe the runtime configuration.
