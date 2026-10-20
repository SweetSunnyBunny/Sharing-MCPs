# Optional Cloudflare access for Machine Agent

**Skip this folder for a first local installation.** Follow the
[Machine Agent guide](../README.md) first and get its authenticated filesystem
test working. This folder contains two alternative ways to host a remote MCP
proxy. Neither one installs the local Windows bridge or creates a tunnel.

Before continuing you need a working bridge, its key, and an HTTPS address
through your own authenticated network arrangement that Cloudflare can reach.
`127.0.0.1` from a Worker refers to that remote environment, not your PC.

## Option A: the JavaScript Worker, without Docker

This is the simpler scaffold for the included tool groups. Install Node.js 24
and sign in to your own Cloudflare account. Open `machine-agent\cloudflare\workers`
in File Explorer, type `powershell` in the address bar and press Enter.

```powershell
npm install --save-dev wrangler
npx wrangler login
notepad .\wrangler.jsonc
```

Set `MACHINE_AGENT_URL` to your bridge's reachable HTTPS origin. Keep
`SERVER_NAME` as `filesystem` for the first test. `name` is the Worker name;
choose your own if `filesystem-mcp` already exists in your account. Save.

```powershell
npx wrangler secret put MACHINE_AGENT_KEY
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put MCP_AUTH_TOKEN
```

The first prompt takes the existing local bridge key. Save the generated token
and paste it at the `MCP_AUTH_TOKEN` prompt; that is the separate password your
MCP clients send to this Worker. **Set both before exposing the Worker**: this
scaffold does not require client authentication when `MCP_AUTH_TOKEN` is unset.

```powershell
npx wrangler deploy --dry-run
npx wrangler deploy
```

Use the printed Worker address and check its catalog:

```powershell
$WorkerUrl = (Read-Host "Paste the deployed Worker origin").TrimEnd('/')
$Token = Read-Host "Paste MCP_AUTH_TOKEN"
$Body = @{ jsonrpc = "2.0"; id = 1; method = "tools/list" } | ConvertTo-Json
Invoke-RestMethod -Uri "$WorkerUrl/mcp" -Method Post -Headers @{ Authorization = "Bearer $Token" } -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 8
```

A list of filesystem tools confirms the Worker and client token. Then ask your
MCP client to list a directory you own: that verifies the separate bridge URL
and bridge key. Connect the client to `/mcp` with a bearer header. If it cannot
send headers, use `/mcp/YOUR-TOKEN`; keep that whole URL private. The active MCP
route in this JavaScript Worker does not accept a `?token=` query parameter.

The supplied `workers/deploy.ps1` is an older convenience script. Passing its
`MachineAgentKey` argument writes the key into a generated config file, so the
manual secret commands above are preferable for a shareable configuration.

## Option B: generate a Python container project (advanced)

This alternative requires Docker running locally and Cloudflare Containers
available in your account. From `machine-agent\cloudflare` in PowerShell:

```powershell
.\render-cloudflare-project.ps1 -Server filesystem -MachineAgentBaseUrl https://your-bridge.example.com -WorkerName filesystem-container
```

Replace the URL and Worker name. This only generates files under
`generated/filesystem`; it does not deploy. Check that `Dockerfile`,
`requirements.txt`, `wrangler.jsonc`, `.dockerignore` and `src/index.js` exist.

The generated Python client still needs a bridge key. The renderer's optional
`-MachineAgentApiKey` argument embeds that key into the Dockerfile/image. Treat
generated outputs as private, or adapt the container environment to inject a
secret at runtime before deployment. Do not publish an image containing your
bridge credential. The template requires a separate `MCP_AUTH_TOKEN` to protect
the incoming Worker endpoint, just like Option A.

Once you have reviewed that configuration, open `generated/filesystem`:

```powershell
npm install
npx wrangler secret put MCP_AUTH_TOKEN
npx wrangler deploy --dry-run
```

Only deploy with `npx wrangler deploy` after the upstream key handling and client
authentication are configured. The container route is advanced scaffolding,
not required to use any local tool. Avoid the combined deploy helper until you
understand the generated configuration and credential handling.

## Common fixes

- **Worker tool catalog works but tool calls fail:** verify the PC bridge is
  running, the HTTPS route reaches it, and the bridge key matches.
- **Unauthorized:** distinguish the client `MCP_AUTH_TOKEN` from the upstream
  `MACHINE_AGENT_KEY` / `MACHINE_AGENT_API_KEY`.
- **Docker build fails:** Option A needs no Docker; Option B needs a working
  Docker daemon and dependencies suitable for its Linux Python image.
- **PowerShell script blocked:** inspect the script and follow your machine's
  policy for running local scripts; the manual Option A commands avoid it.

Do not share `generated/`, populated config files, credentials or deployment
artifacts. Return to the [main guide](../README.md) for local Windows setup.
