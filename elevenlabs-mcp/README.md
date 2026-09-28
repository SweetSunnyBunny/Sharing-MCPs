# ElevenLabs MCP — install a small speech service

This Cloudflare Worker exposes two tools: `list_voices` and `text_to_speech`.
It uses your ElevenLabs API account and returns generated MP3s as links.
You need Node.js, a Cloudflare account with Workers/KV, an ElevenLabs API key,
and a voice ID you are permitted to use. No local Anam server is required.

Listing the configured voices makes no paid synthesis call. Generating speech
uses ElevenLabs credits. Audio links are public to anyone who has the link and
expire from KV after seven days; save clips yourself if you want to keep them.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\elevenlabs-mcp`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `elevenlabs-mcp`; Node should report `v24...`.
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

## 2. Choose your voice and create audio storage

In your [ElevenLabs account](https://elevenlabs.io/), create an API key with
access to text-to-speech. Open your voice library, choose a voice, and copy its
voice ID. The voice ID and API key are different values.

```powershell
notepad .\src\index.ts
```

Find `const VOICES`. Keep the `avery` example label for the first test and replace
its `YOUR-VOICE-ID` with your voice ID. You can rename the label or add more
entries later. Keep the numeric settings initially. Save and close Notepad.

```powershell
npx wrangler kv namespace create AUDIO
notepad .\wrangler.toml
```

Paste the returned namespace ID over `YOUR-KV-NAMESPACE-ID` under
`[[kv_namespaces]]`. Keep `binding = "AUDIO"`. KV holds temporary MP3 files.

## 3. Save secrets and deploy

```powershell
npx wrangler secret put ELEVENLABS_API_KEY
```

Paste your ElevenLabs API key at that prompt.

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
npx tsc --noEmit
npx wrangler deploy --dry-run
npm run deploy
```

The last command installs the service in your Cloudflare account. Copy the
`https://...workers.dev` address printed at the end; this is your **Worker URL**.
Use that actual address below, without a trailing slash. `example.com` and
`YOUR-*` values in the supplied files are placeholders, not working services.

## 4. Check without spending synthesis credits

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
$Body = @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "list_voices"; arguments = @{} } } | ConvertTo-Json -Depth 10
$Reply = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType "application/json" -Body $Body
$Reply.result | ConvertTo-Json -Depth 10
```

The result should show your configured label and voice ID. If it still says YOUR-VOICE-ID, edit the VOICES map and deploy again. If you see `isError: true` or an `error` field, read the message before
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

## First speech call and ongoing use

After the read-only checks, ask your MCP client to call `text_to_speech` with
`{"text":"Hello from my new speech server.","voice":"avery"}`. Replace `avery`
if you renamed it. This spends credits and returns an expiring `/audio/...mp3`
URL. Open that link to hear the result. The maintained default model is
`eleven_v3`; each request is limited to 1,200 characters. A raw voice ID can also
be used instead of a configured label. Generated requests enable ElevenLabs
history logging, so account history is another place to review clips.

For local development, copy `.dev.vars.example` to `.dev.vars`, fill in both
keys, and run `npm run dev`. A local audio URL is only reachable from clients
that can reach that local server; deploy before giving links to remote clients.

This is a remote speech MCP endpoint. It is separate from the UI's ChatGPT
conversation bridge and does not create a ChatGPT login or browser session.

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
- **Voice is still a placeholder:** replace `YOUR-VOICE-ID` in `VOICES` and redeploy.
- **ElevenLabs rejects synthesis:** check API-key permissions, available credits,
  model access and the voice ID in your own account.
- **Audio link says not found:** it may have expired or the KV binding is wrong.
- **`npm run typecheck` is missing:** this package uses `npx tsc --noEmit` instead.

Only generic examples belong in a shared copy. Keep your populated secret
files, account tokens, generated data, and `.wrangler` directory private.

## Reading the code

Start with [src/index.ts](src/index.ts). The installation steps above describe the runtime configuration.
