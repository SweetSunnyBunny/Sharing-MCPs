# Reading the Sharing MCPs code

Each package is independently installable. Start with its README for configuration,
then follow the entry points below. The [UI guide](ui/CODE_GUIDE.md) follows one
chat message through the browser, API, provider and database.

## Package map

| Package | Responsibility | Read first |
| --- | --- | --- |
| [alexa-skill](alexa-skill/README.md) | Alexa interaction models and Worker | [src/index.ts](alexa-skill/src/index.ts), [wrangler.toml](alexa-skill/wrangler.toml) |
| [audio-visualizer](audio-visualizer/README.md) | Audio/video analysis MCP | [audio_mcp_server.py](audio-visualizer/audio_mcp_server.py), [sound_to_image.py](audio-visualizer/sound_to_image.py) |
| [books-mcp](books-mcp/README.md) | Book tools | [run_server.py](books-mcp/run_server.py), [books_server.py](books-mcp/books_server.py) |
| [celestial-weather](celestial-weather/README.md) | Astronomy and weather tools | [run_server.py](celestial-weather/run_server.py), [server.py](celestial-weather/server.py) |
| [desktop-control](desktop-control/README.md) | Local desktop tools | [desktop_control_server.py](desktop-control/desktop_control_server.py) |
| [discord-backend](discord-backend/README.md) | Discord Worker MCP | [src/index.ts](discord-backend/src/index.ts), [src/tools.ts](discord-backend/src/tools.ts), [src/discord.ts](discord-backend/src/discord.ts) |
| [discord-scribe](discord-scribe/README.md) | Discord recording and transcription | [src/index.ts](discord-scribe/src/index.ts), [src/recorder.ts](discord-scribe/src/recorder.ts), [src/transcriber.ts](discord-scribe/src/transcriber.ts) |
| [easel-ai](easel-ai/README.md) | Image gallery and prompt workshop | [src/main.ts](easel-ai/src/main.ts), [src/server.ts](easel-ai/src/server.ts), [src/db.ts](easel-ai/src/db.ts) |
| [elevenlabs-mcp](elevenlabs-mcp/README.md) | Speech Worker MCP | [src/index.ts](elevenlabs-mcp/src/index.ts) |
| [filesystem-mcp](filesystem-mcp/README.md) | Permitted local file tools | [run_server.py](filesystem-mcp/run_server.py), [server.py](filesystem-mcp/server.py) |
| [gdrive-mcp](gdrive-mcp/README.md) | Local Google Drive tools | [run_server.py](gdrive-mcp/run_server.py), [server.py](gdrive-mcp/server.py), [auth/](gdrive-mcp/auth/) |
| [gmail-mcp](gmail-mcp/README.md) | Local Gmail tools | [run_server.py](gmail-mcp/run_server.py), [server.py](gmail-mcp/server.py), [auth/](gmail-mcp/auth/) |
| [google-cloud-mcp](google-cloud-mcp/README.md) | Cloud Google account tools | [src/index.ts](google-cloud-mcp/src/index.ts), [src/oauth.ts](google-cloud-mcp/src/oauth.ts), [migrations/](google-cloud-mcp/migrations/) |
| [hearth-commons](hearth-commons/README.md) | Connector to an external Commons server | [src/index.ts](hearth-commons/src/index.ts) |
| [hearth-hub](hearth-hub/README.md) | Shared workspace/state service | [src/index.ts](hearth-hub/src/index.ts), [src/tools/](hearth-hub/src/tools/), [migrations/](hearth-hub/migrations/) |
| [krita-mcp](krita-mcp/README.md) | Tools for installed Krita | [run_server.py](krita-mcp/run_server.py), [server.py](krita-mcp/server.py), [plugin/](krita-mcp/plugin/) |
| [limbic](limbic/README.md) | Advisory emotional-state service | [src/index.ts](limbic/src/index.ts), [migrations/](limbic/migrations/), [examples/](limbic/examples/) |
| [machine-agent](machine-agent/README.md) | Local machine tools and cloud proxy | [run_server.py](machine-agent/run_server.py), [server_factory.py](machine-agent/server_factory.py), [local_tool_registry.py](machine-agent/local_tool_registry.py), [tool_specs.py](machine-agent/tool_specs.py) |
| [memory-core-mcp](memory-core-mcp/README.md) | Separate local memory implementation | [memory_core_server.py](memory-core-mcp/memory_core_server.py), [unified_memory_server.py](memory-core-mcp/unified_memory_server.py), [memory_core_daemon.py](memory-core-mcp/memory_core_daemon.py) |
| [mind-backend](mind-backend/README.md) | Cloud Qualia memory and continuity | [src/index.ts](mind-backend/src/index.ts), [src/query-signals.ts](mind-backend/src/query-signals.ts), [migrations/](mind-backend/migrations/) |
| [obsidian-mcp](obsidian-mcp/README.md) | Tools for an installer-selected vault | [run_server.py](obsidian-mcp/run_server.py), [server.py](obsidian-mcp/server.py) |
| [photos-mcp](photos-mcp/README.md) | Image generation adapter | [server.py](photos-mcp/server.py) |
| [qualia-mcp](qualia-mcp/README.md) | Local stdio adapter to cloud Qualia | [qualia_server.py](qualia-mcp/qualia_server.py) |
| [shared-docs](shared-docs/README.md) | Cross-service architecture and requirements | [CONNECTIONS.md](shared-docs/CONNECTIONS.md), [COVERAGE.md](shared-docs/COVERAGE.md), [SERVICE-MAP.md](shared-docs/SERVICE-MAP.md) |
| [social-backend](social-backend/README.md) | Cloud social and world tools | [src/index.ts](social-backend/src/index.ts), [src/tools/](social-backend/src/tools/), [src/lib/](social-backend/src/lib/) |
| [terminal-mcp](terminal-mcp/README.md) | Local shell tools | [run_server.py](terminal-mcp/run_server.py), [terminal_server.py](terminal-mcp/terminal_server.py) |
| [tumblr-mcp](tumblr-mcp/README.md) | Tumblr tools | [run_server.py](tumblr-mcp/run_server.py), [server.py](tumblr-mcp/server.py) |
| [twitter-mcp](twitter-mcp/README.md) | X/Twitter tools | [run_stdio.py](twitter-mcp/run_stdio.py), [server.py](twitter-mcp/server.py), [run_server.py](twitter-mcp/run_server.py) |
| [ui](ui/README.md) | Anam chat application | [CODE_GUIDE.md](ui/CODE_GUIDE.md), [server.py](ui/server.py), [config.py](ui/config.py) |
| [world-feed](world-feed/README.md) | Starter for the sibling UI World Feed | [setup_world.py](world-feed/setup_world.py), [start_world_feed.py](world-feed/start_world_feed.py), [examples/](world-feed/examples/) |
| [world-tools-mcp](world-tools-mcp/README.md) | World information tools | [run_server.py](world-tools-mcp/run_server.py), [world_tools_server.py](world-tools-mcp/world_tools_server.py) |
| [youtube-mcp](youtube-mcp/README.md) | YouTube tools | [run_server.py](youtube-mcp/run_server.py), [server.py](youtube-mcp/server.py), [auth/](youtube-mcp/auth/) |

## Common patterns

Python packages usually separate `run_server.py` (launch/configuration) from the
server module that registers tools. Read a tool registration, its handler, and
its return value together. Some packages expose one direct server file instead.

Cloud Workers begin in `src/index.ts`. Follow imports to tool handlers and
service modules; `wrangler.toml` describes bindings, while `migrations/` holds
schema changes. The package's `package.json` lists supported check commands.
Easel and Discord Scribe are Node applications.

World Feed reuses the sibling UI backend. Qualia's cloud implementation is
`mind-backend`; `qualia-mcp` is a transport adapter and `memory-core-mcp` is a
separate implementation. The [connection guide](shared-docs/CONNECTIONS.md)
explains deployment order; the [coverage map](shared-docs/COVERAGE.md) identifies
external applications and code not included in this collection.

## Keep the map useful

Keep package boundaries and entry paths stable. Add focused modules beside their
callers when they have a clear responsibility. Explain non-obvious behavior and
side effects. Update the package README when configuration, a launch command or
the first-run check changes. Keep code history in Git; generated state, local
configuration, installed dependencies and backup files stay outside the distribution.

## Verify a reviewed distribution

From this collection folder:

```powershell
python -B scripts/verify_setups.py
```

This verifies reviewed file hashes and detects runtime artifacts or additions.
It deliberately fails after local configuration changes. Do not delete working
configuration to satisfy it. Run relevant package tests for behavior, and review
new content before selectively updating its manifest entries.

The [maintainer tools](scripts/README.md) describe exports and clean ZIP creation.
Public UI code includes reviewed generic overlays; a blind copy from a private
installation can overwrite those adaptations. [SETUP_REVIEW.md](SETUP_REVIEW.md)
records the scope and limits of snapshot checks.
