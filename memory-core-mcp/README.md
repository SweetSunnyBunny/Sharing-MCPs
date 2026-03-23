# Memory Core MCP

Memory Core is a SQLite-backed memory server for AI companions. It provides
structured storage, full-text search, semantic retrieval, and optional unified
loading alongside sibling MCPs such as `qualia-mcp` and `companion-memory`.

## Included

- `memory_core_server.py`: main MCP server
- `unified_memory_server.py`: optional entry point that also loads sibling MCPs
- `memory_core_daemon.py`: optional background processor and cache refresher
- `Start-MemoryCore.ps1` / `start-memory-core.bat`: local launchers

## Not Included

This shared copy intentionally excludes private runtime data and local machine
state such as databases, caches, generated images, logs, and personal content.

## Quick Start

```powershell
python -m pip install -r requirements.txt
.\Start-MemoryCore.ps1 -Mode server
```

Or run the unified server:

```powershell
.\Start-MemoryCore.ps1 -Mode unified
```

Or run the daemon:

```powershell
.\Start-MemoryCore.ps1 -Mode daemon
```

## Claude Code MCP Config

```json
{
  "mcpServers": {
    "memory-core": {
      "command": "python",
      "args": ["path/to/memory-core-mcp/memory_core_server.py"]
    }
  }
}
```

For the unified entry point:

```json
{
  "mcpServers": {
    "memory": {
      "command": "python",
      "args": ["path/to/memory-core-mcp/unified_memory_server.py"]
    }
  }
}
```

## Optional Environment Variables

Create a `.env` or set shell env vars if your sibling repos live elsewhere:

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

`unified_memory_server.py` looks for sibling folders named
`companion-memory-mcp` or `companion-memory`, and `qualia-mcp` or `qualia`.

The daemon now ships in a safe default state:
- conversation auto-tagging only runs when configured conversation folders exist
- cache files default to the shared repo instead of your live workspace

This repo still contains example/default identity content from the original
project structure. If you are adapting it for your own companions, replace
those identity-specific defaults with your own setup.
