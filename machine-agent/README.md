# Machine Agent — run the Windows machine-tool bridge

This package runs an authenticated local bridge for filesystem, terminal,
clipboard, desktop, Krita and Obsidian tools. MCP proxy processes connect a
client to that bridge. Use **Windows**, Python **3.11**, and Node.js **24** for
this setup. Desktop/clipboard tools need your logged-in interactive Windows
session; this is not a promise of equivalent Linux/macOS support.

Start with filesystem tools. Krita, Obsidian, Playwright and Anam are optional.
The Cloudflare files in this folder are deployment scaffolds; the local bridge
below does not need a Cloudflare account or an internet tunnel.

## 1. Open the folder and install into a private Python environment

Install Python 3.11 from [python.org](https://www.python.org/downloads/) and
Node.js 24 from [nodejs.org](https://nodejs.org/). Extract the download. In File
Explorer, open `Sharing-MCPs\machine-agent`, type `powershell` in the address
bar and press Enter. Run:

```powershell
Get-Location
py -3.11 --version
node --version
py -3.11 -m venv .venv
..venv\Scripts\python.exe -m pip install --upgrade pip
..venv\Scripts\python.exe -m pip install -r requirements.txt
```

The folder should end in `machine-agent`. No environment activation is needed:
every Python command here explicitly uses this folder's `.venv`. If `py` is
missing, repair the Python installation with its launcher enabled, then reopen
PowerShell. Do not run `pip` from an unrelated Python installation.

## 2. Choose a key and start the bridge

```powershell
..venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save that generated value in your password manager. Then run:

```powershell
$env:MACHINE_AGENT_API_KEY = Read-Host "Paste the generated bridge key"
$env:MACHINE_AGENT_MANAGE_PLAYWRIGHT = "0"
..venv\Scripts\python.exe .\local_machine_agent.py
```

This starts the bridge at **http://127.0.0.1:8811** and deliberately leaves the
optional Playwright sidecar off for the first test. Keep this terminal running.
The environment settings last only for this window; repeat them when restarting.
The program does not automatically read a `.env` file.

## 3. Check it from a second terminal

Open a second PowerShell window in the same folder and run:

```powershell
Invoke-RestMethod http://127.0.0.1:8811/health
$BridgeKey = Read-Host "Paste the same bridge key"
$Body = @{ tool = "fs_list_directory"; arguments = @{ path = (Get-Location).Path } } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8811/tools/invoke -Method Post -Headers @{ Authorization = "Bearer $BridgeKey" } -ContentType "application/json" -Body $Body | ConvertTo-Json -Depth 8
```

Health should show `ok: true`. The second result should list this package's
files. It verifies authentication and a real read-only filesystem call. Health
alone does not prove that every optional application adapter is installed.

## 4. Connect a local MCP client

Keep the bridge running. In the client, add a **stdio MCP server** with:

- **Command:** the full path to `machine-agent\.venv\Scripts\python.exe`.
- **Arguments:** the full path to `machine-agent\run_server.py`, then
  `filesystem`, then `--transport`, then `stdio`.
- **Environment:** `MACHINE_AGENT_BASE_URL=http://127.0.0.1:8811` and
  `MACHINE_AGENT_API_KEY` set to your saved bridge key.

For example, if you extracted to `C:\MCP-Starter\Sharing-MCPs`, a client using
`mcpServers` JSON can use this shape. Replace the example key locally:

```json
{
  "mcpServers": {
    "machine-filesystem": {
      "command": "C:/MCP-Starter/Sharing-MCPs/machine-agent/.venv/Scripts/python.exe",
      "args": ["C:/MCP-Starter/Sharing-MCPs/machine-agent/run_server.py", "filesystem", "--transport", "stdio"],
      "env": {
        "MACHINE_AGENT_BASE_URL": "http://127.0.0.1:8811",
        "MACHINE_AGENT_API_KEY": "YOUR-PRIVATE-BRIDGE-KEY"
      }
    }
  }
}
```

Reload your client's MCP servers and try listing this package folder. The proxy
does not launch the bridge for you. Other shipped groups include `clipboard`,
`desktop-control`, `terminal`, `krita` and `obsidian`; `tool_specs.py` lists their
catalogs. Historical optional groups can appear there without an installed
local adapter, so begin with a group whose implementation is included.

The proxy may use `MACHINE_AGENT_KEY_FILE` pointing explicitly to your own local
text key file instead of a key environment variable. A nonempty key variable
takes precedence. There is no search for another installation's credentials.
The bridge itself still requires `MACHINE_AGENT_API_KEY` in its environment.

## Optional applications

**Obsidian:** install/open Obsidian with your own vault. From this package folder:

```powershell
Push-Location .\tools\obsidian
npm ci
npm run build
Pop-Location
```

Before starting the bridge, set
`$env:OBSIDIAN_VAULT_PATH = "C:\path\to\your\vault"` in its terminal, replacing
the path with your actual vault. Restart the bridge if it was already running.

**Krita:** install Krita, close it, then follow the included
[plugin guide](tools/krita/plugin/README.md). Copy the plugin folder and
`.desktop` file into Krita's `pykrita` directory, enable it in Python Plugin
Manager, restart Krita, and keep Krita open while using its tools. The bridge
and the Krita plugin are separate pieces.

**Playwright:** the sidecar is optional. Review `sidecar_processes.py` before
enabling `MACHINE_AGENT_MANAGE_PLAYWRIGHT`; it manages a separate browser-tool
process. Basic filesystem tools do not need it.

**Anam gateway:** optional `anam_*` tools need the sibling `ui` package running
and its own configured credentials. Set `ANAM_ROOT` to your UI folder if it is
not the sibling `ui` directory. The other groups do not need Anam.

## Optional HTTP proxy and tests

To run a proxy for a client that accepts local HTTP, use another terminal,
set the bridge URL/key in that terminal, then run:

```powershell
$env:MACHINE_AGENT_BASE_URL = "http://127.0.0.1:8811"
$env:MACHINE_AGENT_API_KEY = Read-Host "Paste the same bridge key"
..venv\Scripts\python.exe .\run_server.py filesystem --transport streamable-http --host 127.0.0.1 --port 8793
```

Its MCP endpoint is `http://127.0.0.1:8793/mcp`. This proxy has no independent
client-authentication gate; keep it on loopback. Remote cloud clients cannot
reach `127.0.0.1` on your computer. A remote deployment requires your own
authenticated network arrangement; the `cloudflare` folder is a separate
advanced scaffold, not something to deploy unchanged.

Optional development checks:

```powershell
..venv\Scripts\python.exe -m pip install pytest
..venv\Scripts\python.exe -m pytest tests -q
```

## If something goes wrong

- **Bridge requires authentication:** set `MACHINE_AGENT_API_KEY` in the same
  terminal before starting it. A `.env` file alone is not loaded.
- **Connection refused:** keep the bridge terminal running; check `/health`.
- **401:** the client key and bridge key must match exactly.
- **No desktop/screenshot:** run in your interactive Windows desktop session,
  not an unattended service session.
- **Optional tool fails:** install/build its adapter and restart the bridge.
- **Port 8811 is busy:** stop the older bridge or use `--port 8812`, then update
  all clients' `MACHINE_AGENT_BASE_URL` to match.

Use Ctrl+C to stop each running process. This package creates its own clipboard
history/screenshots when those tools are used; keep that data and your keys out
of any copy you share with someone else.

## Reading the code

Start with [run_server.py](run_server.py), then [server_factory.py](server_factory.py), then [local_tool_registry.py](local_tool_registry.py), then [tool_specs.py](tool_specs.py). The installation steps above describe the runtime configuration.
Behavioral regression checks live in [tests/](tests/).
