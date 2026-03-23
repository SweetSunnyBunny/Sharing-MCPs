# Qualia MCP

Qualia is an inner-life MCP for AI companions. It tracks subconscious notes,
open loops, emotional depth, dreams, and relational state, with optional links
to sibling MCPs for memory, rituals, and other context.

## Included

- `qualia_server.py`: main MCP server
- `requirements.txt`: Python dependencies

## Not Included

This shared copy excludes private runtime content such as `.env`, `depths/`,
`visuals/`, weather caches, and other personal state files.

## Quick Start

```powershell
python -m pip install -r requirements.txt
python qualia_server.py
```

## Claude Code MCP Config

```json
{
  "mcpServers": {
    "qualia": {
      "command": "python",
      "args": ["path/to/qualia-mcp/qualia_server.py"]
    }
  }
}
```

## Optional Environment Variables

The shared copy uses repo-relative defaults when possible. Override these if
your sibling MCPs live elsewhere:

- `QUALIA_DEPTHS_DIR`
- `QUALIA_VISUALS_DIR`
- `COMPANION_MEMORY_DIR`
- `ASTROLOGY_BIRTHDAYS_FILE`
- `QUALIA_SANCTUARY_DIR`
- `QUALIA_RITUALS_DIR`
- `QUALIA_PROACTIVE_PRESENCE_DIR`
- `MEMORY_CORE_DB_PATH`
- `QUALIA_WEATHER_CACHE_FILE`
- `QUALIA_MORNING_PACKET_CACHE_FILE`
- `QUALIA_SMART_CONTEXT_CACHE_FILE`
- `QUALIA_DRIFT_PACKET_CACHE_FILE`

## Notes

This repo still contains example/default identity content from the original
project structure. If you are adapting it for your own companions, replace
those identity-specific defaults with your own setup.
