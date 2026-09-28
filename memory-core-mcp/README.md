# Memory Core MCP

A local SQLite memory server with keyword search, optional semantic models, and an optional background daemon. It is separate from the current cloud Qualia service; no cloud deployment is required for local storage.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\memory-core-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\memory-core-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Choose identities and optional models

First installation only, copy the generic template and edit your local copy:

```powershell
Copy-Item -LiteralPath .\.env.example -Destination .\.env
notepad .\.env
```

If `.env` already exists, edit it without copying over it. Set `MEMORY_CORE_IDENTITIES` to your own comma-separated labels. Leave path overrides commented unless needed; omitted paths use this package's own data/import folders. If overriding paths, use absolute paths.

For a simple first startup without model preloading, add:

```dotenv
MEMORY_CORE_PRELOAD_MODEL=false
LM_STUDIO_EMBED_ENABLED=false
LM_STUDIO_RERANK_ENABLED=false
LM_STUDIO_VISION_ENABLED=false
```

SQLite storage and keyword retrieval work without a model server. Semantic search needs a cached sentence-transformer model or a configured LM Studio embedding service. This server does not automatically download its text embedding model. To explicitly prepare the default model once, with internet access:

```powershell
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
```

The model uses disk space in your user cache. Alternatively install [LM Studio](https://lmstudio.ai/), load your chosen embedding/chat models, start its local server, and configure the matching URL and model IDs in `.env`. Do not expect the example model names to be installed automatically.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\memory_core_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "memory-core-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/memory-core-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/memory-core-mcp/memory_core_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `get_memory_stats`. An empty database is a successful fresh install. Save one clearly labeled test memory using an identity you configured, then search for its text before importing any real archive.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Slow first install:** Torch/transformer dependencies are large. Wait for pip to finish; interrupted downloads require rerunning the same install command.
- **Semantic search unavailable:** prepare the local model cache or configure LM Studio; plain storage is not evidence that a model endpoint is running.
- **Database locked:** close duplicate manually started servers and avoid running multiple daemons.
- **Missing plugin:** leave `COMPANION_MEMORY_PLUGIN_DIR` blank unless you have a compatible plugin of your own.

## Optional daemon and unified entry point

The main MCP server is enough to begin. For background processing, open a separate PowerShell window in this folder and run:

```powershell
.\.venv\Scripts\python.exe .\memory_core_daemon.py --host 127.0.0.1 --port 8766
```

It remains running until Ctrl+C. It is not a second MCP client connection. `unified_memory_server.py` is an alternative stdio entry point that can load an explicitly configured compatible plugin; do not configure both entry points against the same database without a reason. Use [qualia-mcp](../qualia-mcp/README.md) separately for current cloud Qualia.

## Included

- `memory_core_server.py`: main MCP server
- `unified_memory_server.py`: optional entry point for a configured local plugin
- `memory_core_daemon.py`: optional background processor and cache refresher
- `Start-MemoryCore.ps1` / `start-memory-core.bat`: local launchers

## Not Included

This shared copy intentionally excludes private runtime data and local machine
state such as databases, caches, generated images, logs, and personal content.

## Optional Environment Variables

Create a `.env` or set shell environment variables to select your own data or services. Omitted path settings use absolute package-local defaults; use absolute paths for explicit overrides:

- `MEMORY_CORE_DB_PATH`
- `MEMORY_CORE_IDENTITIES`
- `MEMORY_CORE_QUALIA_ROOT`
- `COMPANION_MEMORY_DIR`
- `MEMORY_CORE_PACK_MAIL_FILE`
- `MEMORY_CORE_VAULT_PATH`
- `MEMORY_CORE_JOURNAL_ROOT`
- `MEMORY_CORE_WEATHER_CACHE_PATH`
- `MEMORY_CORE_SMART_CONTEXT_CACHE_PATH`
- `MEMORY_CORE_MORNING_PACKET_CACHE_PATH`
- `MEMORY_CORE_DRIFT_PACKET_CACHE_PATH`
- `MEMORY_CORE_DAEMON_QUALIA_DEPTHS_DIR`
- `LM_STUDIO_CHAT_URL`
- `LM_STUDIO_MODEL`
- `LM_STUDIO_EMBED_MODEL`

`unified_memory_server.py` starts Memory Core on its own. To add a compatible legacy companion-memory plugin you own, set `COMPANION_MEMORY_PLUGIN_DIR` to a directory containing `companion_memory_server.py` with `register_companion_mind_tools(server)`. No plugin or sibling folder is required for Memory Core. Explicitly configured missing plugins report a startup error.

The current `qualia-mcp` package is a cloud proxy to `mind-backend`; configure it as a separate MCP server. It cannot be embedded through the removed legacy `register_qualia_tools` interface. Legacy local Qualia JSON imports default to `imports/qualia/depths`; they are optional user-provided records, not live cloud state. The daemon's weather cache and outgoing local mail default under this package's `data` folder. Weather refresh is disabled until you set `MEMORY_CORE_WEATHER_API_URL` to an Open-Meteo request for your own location.

The daemon now ships in a safe default state:
- conversation auto-tagging only runs when configured conversation folders exist
- cache files default to the shared repo instead of your live workspace

The starter uses generic companion names and generic resurfacing text. Supply your own identities and records locally.

Semantic search requires either a configured LM Studio embedding endpoint or a locally cached sentence-transformer model. Image embeddings require a configured vision endpoint or cached CLIP model. These model weights and applications are external dependencies, not missing project code. Optional acceleration/entity extraction can be installed from `requirements-optional.txt`; plain storage and keyword retrieval do not require them. No preexisting database, conversation archive, image memory, or model cache is included.

## Reading the code

Start with [memory_core_server.py](memory_core_server.py), then [unified_memory_server.py](unified_memory_server.py), then [memory_core_daemon.py](memory_core_daemon.py). The installation steps above describe the runtime configuration.
