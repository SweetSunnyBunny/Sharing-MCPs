# Reading the Sharing MCPs code

Each package is independently installable. Start with its README for configuration,
then follow the entry points below. The [UI guide](01-home/ui/CODE_GUIDE.md) follows one
chat message through the browser, API, provider and database.

## Package map

| Package | Responsibility | Read first |
| --- | --- | --- |
| [alexa-skill](05-reach/alexa-skill/README.md) | Alexa interaction models and Worker | [src/index.ts](05-reach/alexa-skill/src/index.ts), [wrangler.toml](05-reach/alexa-skill/wrangler.toml) |
| [audio-visualizer](04-create/audio-visualizer/README.md) | Audio/video analysis MCP | [audio_mcp_server.py](04-create/audio-visualizer/audio_mcp_server.py), [sound_to_image.py](04-create/audio-visualizer/sound_to_image.py) |
| [books-mcp](06-world/books-mcp/README.md) | Book tools | [run_server.py](06-world/books-mcp/run_server.py), [books_server.py](06-world/books-mcp/books_server.py) |
| [celestial-weather](06-world/celestial-weather/README.md) | Astronomy and weather tools | [run_server.py](06-world/celestial-weather/run_server.py), [server.py](06-world/celestial-weather/server.py) |
| [desktop-control](03-hands/desktop-control/README.md) | Local desktop tools | [desktop_control_server.py](03-hands/desktop-control/desktop_control_server.py) |
| [discord-backend](05-reach/discord-backend/README.md) | Discord Worker MCP | [src/index.ts](05-reach/discord-backend/src/index.ts), [src/tools.ts](05-reach/discord-backend/src/tools.ts), [src/discord.ts](05-reach/discord-backend/src/discord.ts) |
| [discord-scribe](05-reach/discord-scribe/README.md) | Discord recording and transcription | [src/index.ts](05-reach/discord-scribe/src/index.ts), [src/recorder.ts](05-reach/discord-scribe/src/recorder.ts), [src/transcriber.ts](05-reach/discord-scribe/src/transcriber.ts) |
| [easel-ai](01-home/easel-ai/README.md) | Image gallery and prompt workshop | [src/main.ts](01-home/easel-ai/src/main.ts), [src/server.ts](01-home/easel-ai/src/server.ts), [src/db.ts](01-home/easel-ai/src/db.ts) |
| [elevenlabs-mcp](04-create/elevenlabs-mcp/README.md) | Speech Worker MCP | [src/index.ts](04-create/elevenlabs-mcp/src/index.ts) |
| [filesystem-mcp](03-hands/filesystem-mcp/README.md) | Permitted local file tools | [run_server.py](03-hands/filesystem-mcp/run_server.py), [server.py](03-hands/filesystem-mcp/server.py) |
| [gdrive-mcp](05-reach/gdrive-mcp/README.md) | Local Google Drive tools | [run_server.py](05-reach/gdrive-mcp/run_server.py), [server.py](05-reach/gdrive-mcp/server.py), [auth/](05-reach/gdrive-mcp/auth/) |
| [gmail-mcp](05-reach/gmail-mcp/README.md) | Local Gmail tools | [run_server.py](05-reach/gmail-mcp/run_server.py), [server.py](05-reach/gmail-mcp/server.py), [auth/](05-reach/gmail-mcp/auth/) |
| [google-cloud-mcp](05-reach/google-cloud-mcp/README.md) | Cloud Google account tools | [src/index.ts](05-reach/google-cloud-mcp/src/index.ts), [src/oauth.ts](05-reach/google-cloud-mcp/src/oauth.ts), [migrations/](05-reach/google-cloud-mcp/migrations/) |
| [commons-game](01-home/commons-game/README.md) | Browser game and local HTTP world server | [setup_commons.py](01-home/commons-game/setup_commons.py), [server/commons_server.py](01-home/commons-game/server/commons_server.py), [world/](01-home/commons-game/world/) |
| [hearth-commons](02-mind/hearth-commons/README.md) | Connector to an external Commons server | [src/index.ts](02-mind/hearth-commons/src/index.ts) |
| [hearth-hub](02-mind/hearth-hub/README.md) | Shared workspace/state service | [src/index.ts](02-mind/hearth-hub/src/index.ts), [src/tools/](02-mind/hearth-hub/src/tools/), [migrations/](02-mind/hearth-hub/migrations/) |
| [krita-mcp](03-hands/krita-mcp/README.md) | Tools for installed Krita | [run_server.py](03-hands/krita-mcp/run_server.py), [server.py](03-hands/krita-mcp/server.py), [plugin/](03-hands/krita-mcp/plugin/) |
| [limbic](02-mind/limbic/README.md) | Advisory emotional-state service | [src/index.ts](02-mind/limbic/src/index.ts), [migrations/](02-mind/limbic/migrations/), [examples/](02-mind/limbic/examples/) |
| [machine-agent](03-hands/machine-agent/README.md) | Local machine tools and cloud proxy | [run_server.py](03-hands/machine-agent/run_server.py), [server_factory.py](03-hands/machine-agent/server_factory.py), [local_tool_registry.py](03-hands/machine-agent/local_tool_registry.py), [tool_specs.py](03-hands/machine-agent/tool_specs.py) |
| [memory-core-mcp](02-mind/memory-core-mcp/README.md) | Separate local memory implementation | [memory_core_server.py](02-mind/memory-core-mcp/memory_core_server.py), [unified_memory_server.py](02-mind/memory-core-mcp/unified_memory_server.py), [memory_core_daemon.py](02-mind/memory-core-mcp/memory_core_daemon.py) |
| [mind-backend](02-mind/mind-backend/README.md) | Cloud Qualia memory and continuity | [src/index.ts](02-mind/mind-backend/src/index.ts), [src/query-signals.ts](02-mind/mind-backend/src/query-signals.ts), [migrations/](02-mind/mind-backend/migrations/) |
| [obsidian-mcp](03-hands/obsidian-mcp/README.md) | Tools for an installer-selected vault | [run_server.py](03-hands/obsidian-mcp/run_server.py), [server.py](03-hands/obsidian-mcp/server.py) |
| [photos-mcp](04-create/photos-mcp/README.md) | Image generation adapter | [server.py](04-create/photos-mcp/server.py) |
| [qualia-mcp](02-mind/qualia-mcp/README.md) | Local stdio adapter to cloud Qualia | [qualia_server.py](02-mind/qualia-mcp/qualia_server.py) |
| [shared-docs](shared-docs/README.md) | Cross-service architecture and requirements | [CONNECTIONS.md](shared-docs/CONNECTIONS.md), [COVERAGE.md](shared-docs/COVERAGE.md), [SERVICE-MAP.md](shared-docs/SERVICE-MAP.md) |
| [social-backend](05-reach/social-backend/README.md) | Cloud social and world tools | [src/index.ts](05-reach/social-backend/src/index.ts), [src/tools/](05-reach/social-backend/src/tools/), [src/lib/](05-reach/social-backend/src/lib/) |
| [terminal-mcp](03-hands/terminal-mcp/README.md) | Local shell tools | [run_server.py](03-hands/terminal-mcp/run_server.py), [terminal_server.py](03-hands/terminal-mcp/terminal_server.py) |
| [tumblr-mcp](05-reach/tumblr-mcp/README.md) | Tumblr tools | [run_server.py](05-reach/tumblr-mcp/run_server.py), [server.py](05-reach/tumblr-mcp/server.py) |
| [twitter-mcp](05-reach/twitter-mcp/README.md) | X/Twitter tools | [run_stdio.py](05-reach/twitter-mcp/run_stdio.py), [server.py](05-reach/twitter-mcp/server.py), [run_server.py](05-reach/twitter-mcp/run_server.py) |
| [ui](01-home/ui/README.md) | Anam chat application | [CODE_GUIDE.md](01-home/ui/CODE_GUIDE.md), [server.py](01-home/ui/server.py), [config.py](01-home/ui/config.py) |
| [world-feed](01-home/world-feed/README.md) | Starter for the sibling UI World Feed | [setup_world.py](01-home/world-feed/setup_world.py), [start_world_feed.py](01-home/world-feed/start_world_feed.py), [examples/](01-home/world-feed/examples/) |
| [home-assistant-mcp](03-hands/home-assistant-mcp/README.md) | Home Assistant state and service calls | [server.py](03-hands/home-assistant-mcp/server.py) |
| [watch-mcp](01-home/watch-mcp/README.md) | Wrist replies, vitals and gated messages | [server.py](01-home/watch-mcp/server.py) |
| [anam-touch-mcp](01-home/anam-touch-mcp/README.md) | Timers, orbs, audio, daily wins and configured SMS | [server.py](01-home/anam-touch-mcp/server.py) |
| [crosstalk-mcp](01-home/crosstalk-mcp/README.md) | Companion communication through Anam | [server.py](01-home/crosstalk-mcp/server.py) |
| [world-tools-mcp](06-world/world-tools-mcp/README.md) | World information tools | [run_server.py](06-world/world-tools-mcp/run_server.py), [world_tools_server.py](06-world/world-tools-mcp/world_tools_server.py) |
| [youtube-mcp](05-reach/youtube-mcp/README.md) | YouTube tools | [run_server.py](05-reach/youtube-mcp/run_server.py), [server.py](05-reach/youtube-mcp/server.py), [auth/](05-reach/youtube-mcp/auth/) |

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
