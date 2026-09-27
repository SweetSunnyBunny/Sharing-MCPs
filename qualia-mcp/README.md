# Qualia MCP client

A local stdio adapter for your deployed current Qualia service. The memory engine is in [mind-backend](../mind-backend/README.md); this folder forwards its current tools. Installing this adapter alone does not create a memory database or deploy the service.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\qualia-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\qualia-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Configure your own deployed service

First finish [Mind Backend setup](../mind-backend/README.md), including its database migrations and API key. You need the HTTPS `/mcp` URL and the same key configured for that Worker.

First installation only:

```powershell
Copy-Item -LiteralPath .\.env.example -Destination .\.env
notepad .\.env
```

Replace both placeholders in the local `.env`:

```dotenv
QUALIA_URL=https://your-own-worker.workers.dev/mcp
QUALIA_API_KEY=replace-with-your-own-worker-key
```

Keep the populated file private. If it already exists, edit it without copying over it. The adapter explicitly loads `.env` beside `qualia_server.py`, so client startup does not depend on a working directory. The key is sent as a Bearer header, not embedded in the URL.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\qualia_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "qualia-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/qualia-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/qualia-mcp/qualia_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `mind_health`, then `mind_schema_status`. A returned Worker response confirms the local adapter, network connection, credentials and remote tool catalog are working. For identity tools, use an identity registered in your own Mind database.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **URL/key validation error:** replace the template values and include `/mcp` in the URL.
- **401/403:** the adapter key must match the remote Worker key. Restart the client after changing `.env`.
- **404:** check the deployment hostname and `/mcp` route.
- **Database/table error:** apply the Mind Backend migrations; the adapter cannot create remote tables.
- **No connection:** the Worker must be deployed and reachable. A locally installed proxy is not an offline replacement.

## Existing installations

The old local `qualia_server.py` engine and its `register_qualia_tools` hook have
been retired from this sharing package. The current service owns its schema and
data. Copying an old `depths` folder into this directory does not migrate it.
Keep your own legacy data backup and design an explicit import into the current
schema if needed; no private migration payloads are supplied.

Run the adapter's local, network-free regression checks with
`python -m unittest discover -s tests -v`.
