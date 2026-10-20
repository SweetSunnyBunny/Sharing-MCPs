# Reading the Anam UI code

Start with [installation](README.md) to run the app, or follow this map to change
it. This guide describes this public starter. Optional servers and installer-owned
configuration are listed in [EXTERNAL_INPUTS.md](EXTERNAL_INPUTS.md).

## Follow one chat message

```text
static/index.html -> static/js/chat.js -> static/js/websocket.js
  -> api/chat.py (WebSocket) or api/chat_http.py (HTTP/SSE fallback)
  -> services/chat_pipeline.py
  -> services/chat_turn_prep.py (prompt, history and tools)
  -> services/provider_router.py -> selected provider implementation
  -> services/chat_turn_finalize.py (save the turn)
  -> static/js/chat.js (render streamed events)
```

Read the caller and callee together: API fields and stream events have both a
Python producer and a JavaScript consumer. Keep both chat transports working.

## Find the feature

| Feature | Start here | Follow into |
| --- | --- | --- |
| Startup and page routes | [server.py](server.py) | [core/lifespan.py](core/lifespan.py) |
| Environment and identity defaults | [config.py](config.py) | [prompts/](prompts/) |
| Chat layout and rendering | [static/index.html](static/index.html) | [static/js/chat.js](static/js/chat.js), [static/css/anam.css](static/css/anam.css) |
| Colors and fonts | [static/css/main.css](static/css/main.css) | [api/settings.py](api/settings.py), [static/js/app.js](static/js/app.js) |
| Settings controls | [static/settings.html](static/settings.html) | [static/js/settings.js](static/js/settings.js), [api/settings.py](api/settings.py) |
| Provider selection | [services/provider_router.py](services/provider_router.py) | Selected provider module in [services/](services/) |
| Codex sessions and tools | [services/codex_app_server.py](services/codex_app_server.py) | [Codex setup](docs/CODEX_COMPANION_SETUP.md) |
| Browser bridge | [Bridge setup](docs/CHATGPT_BRIDGE_SETUP.md) | [scripts/pack-browser.ps1](scripts/pack-browser.ps1) |
| MCP discovery and dispatch | [services/mcp_bridge.py](services/mcp_bridge.py) | [mcp-servers.example.json](mcp-servers.example.json), [services/anam_tool_gateway.py](services/anam_tool_gateway.py) |
| Autonomous sessions | [api/autowake.py](api/autowake.py) | [services/autowake.py](services/autowake.py), [services/program_loader.py](services/program_loader.py) |
| Voice | [api/voice.py](api/voice.py) | [static/js/voice.js](static/js/voice.js), [services/local_tts.py](services/local_tts.py) |
| Hub | [static/hub.html](static/hub.html) | [static/js/hub.js](static/js/hub.js), [api/hub.py](api/hub.py) |
| World Feed | [static/world-feed.html](static/world-feed.html) | [api/world_feed.py](api/world_feed.py), [services/world_feed.py](services/world_feed.py) |
| Discord and Telegram | [services/platform_bridge.py](services/platform_bridge.py) | Platform settings in [config.py](config.py) |
| Storage and migrations | [db/database.py](db/database.py) | [db/schema.py](db/schema.py) |
| Android wearable | [Client guide](wearable/AnamCompanion/README.md) | Client source and local build configuration |

Search a visible button label, element ID, API path or event name when a feature
is missing from the table. Existing `ANAM GUIDE:` comments are useful landmarks.
Tests are generally named `tests/test_<feature>.py`.

## Folder responsibilities

- [api/](api/) validates HTTP/WebSocket requests and shapes responses.
- [services/](services/) implements application behavior and provider integrations.
- [core/](core/) owns lifecycle, middleware and shared infrastructure.
- [db/](db/) owns connections, schema and migrations.
- [static/](static/) contains browser HTML, CSS, JavaScript and shipped assets.
- [prompts/](prompts/) contains generic identity and conversation-mode templates.
- [scripts/](scripts/README.md) contains diagnostics, setup and integration helpers.
- [tools/](tools/README.md) contains small utilities and optional hooks.
- [tests/](tests/) contains behavioral regression checks.
- [docs/](docs/README.md) contains provider walkthroughs.

The browser has no build step. `main.css` supplies shared tokens; `anam.css`
contains chat styling and responsive sections. Check mobile overrides when a
desktop change affects the phone layout.

## Changing a setting

1. Add its control to `static/settings.html`.
2. Load and save its value in `static/js/settings.js`.
3. Validate and store it through `api/settings.py`.
4. Invalidate any service cache and test the behavior.

Many settings fit the existing key/value settings table without a new column.
Schema changes belong in `db/schema.py`, with migration coverage for existing
installations.

## Make a small change and check it

From this `ui` folder, use the environment described in the installation README:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_theme_settings.py -q
node --test tests/test_frontend_loading.mjs
```

The Python command is an example for a theme change; select the relevant test
for another feature. Frontend tests need Node.js. Inspect the matching browser
screen after changing layout. Restart when new server code or a recalculated
startup asset version is needed. The [script index](scripts/README.md) identifies
checks that contact providers or accounts; unit tests do not establish login.

## Source and local state

`data/`, caches, virtual environments and populated `.env` files are created by
an installation. Preserve configuration and databases during reorganization.
Keep code history in Git instead of accumulating `.bak` copies or screenshots
in the source tree. Update dependency lockfiles through their package tooling.
Prefer changing application wrappers over files in `static/vendor/`.

Private programs and archives are not shipped; the installer supplies them.
The optional restart helper expects an external supervisor, which this package
does not install. [SHARING-NOTES.md](SHARING-NOTES.md) explains the reviewed generic
overlays and `EXPORT-MANIFEST.json` provenance. Integrity checks do not replace
content review before sharing newly edited files.
