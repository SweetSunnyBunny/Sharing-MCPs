# Anam Code Guide

This is the human map of Anam: start with what you want to change, then follow the named landmark into the code.

Every code landmark added for this guide begins with `ANAM GUIDE:`. In VS Code, open the folder and press `Ctrl+Shift+F` to search the whole app. Inside one file, `Ctrl+F` is enough.

## Start with what you want to change

| I want to... | Start here | Follow it to | Search for |
|---|---|---|---|
| Change the whole app's colors, fonts, or a theme preset | `api/settings.py` | `static/css/main.css`, `static/js/app.js`, `static/js/theme-boot.js` | `ANAM GUIDE: HOUSE COLOR PRESETS` |
| Change one identity's default colors, bubble, check size, voice, or room | `config.py` | `static/js/app.js`, `static/css/anam.css` | `ANAM GUIDE: IDENTITY COLORS AND VOICES` |
| Add something to the Settings page | `static/settings.html` | `static/js/settings.js`, `api/settings.py` | `ANAM GUIDE: ADD A SETTING` |
| Change the main chat page's visible structure | `static/index.html` | the matching file in `static/css/` and behavior in `static/js/` | `ANAM GUIDE: MAIN CHAT PAGE` |
| Change the hero banner at the top of chat (size, portrait, thought) | `static/css/anam.css` (SECTION 4) | `static/index.html`, `static/js/sanctuary-viewer.js`, MOBILE section for phone sizes | `ANAM GUIDE: HERO BANNER` |
| Change message bubble colors | `static/css/anam.css` | `config.py`, `static/js/app.js`, and theme tokens in `api/settings.py` | `ANAM GUIDE: MESSAGE BUBBLE COLORS` |
| Change the message box or send button | `static/css/anam.css` | `static/index.html`, `static/js/chat.js` | `ANAM GUIDE: CHAT INPUT COLORS` |
| Change what happens when Send is pressed | `static/js/chat.js` | `static/js/websocket.js` or `api/chat_http.py` | `ANAM GUIDE: SEND A CHAT MESSAGE` |
| Change how replies appear or type onto the screen | `static/js/chat.js` | `static/css/anam.css` | `ANAM GUIDE: DRAW A STREAMING REPLY` |
| Change what the server does with a chat message | `api/chat.py` | `services/chat_pipeline.py` | `ANAM GUIDE: CHAT SERVER ENTRY` |
| Change prompt/history/tool preparation or saving | `services/chat_turn_prep.py` / `services/chat_turn_finalize.py` | `services/chat_pipeline.py` | `ANAM GUIDE: CHAT TURN PIPELINE` |
| Change which AI provider answers | `services/provider_router.py` | provider implementation in `services/` and settings in `api/settings.py` | `ANAM GUIDE: CHOOSE THE AI PROVIDER` |
| Change Codex thread, instruction, or tool streaming behavior | `services/codex_app_server.py` | `services/cli_mcp_config.py`, `services/mcp_bridge.py`, and `docs/CODEX_COMPANION_SETUP.md` | `CodexAppServerClient` or `stream_codex_app_server` |
| Change an identity's active personality prompt | `prompts/<identity>.md` | prompt loading in the provider service | search the identity's name under `prompts/` |
| Change an identity's autowake/free-time program | `programs/<identity>.md` | `services/program_loader.py`, `services/autowake.py` | the session type, such as `free_time` |
| Change schedules, timers, or care signals | `api/autowake.py` | `services/autowake.py` (APScheduler lives here) and the Orchestrator tab | `/api/autowake` |
| Change the Hub | `static/hub.html` | `static/js/hub.js`, `static/css/hub.css`, `api/hub.py` | the visible Hub card label or API path |
| Change free voice input or conversation mode | `static/js/chat.js` | `api/voice.py`, transcription services, and `static/js/voice.js` for playback | `_initVoiceInput` or `/api/voice` |
| Change ElevenLabs Live Call behavior | `static/js/live-call.js` | `services/speech_engine_live.py`, `api/voice.py`, `scripts/setup_speech_engines.py` | `ANAM GUIDE: ELEVENLABS LIVE CALL ENTRY POINTS` |
| Change voice playback or TTS | `static/js/voice.js` | `api/voice.py`, `services/local_tts.py`, `services/elevenlabs_tts.py` | `/api/voice` |
| Change images, documents, audio, GIFs, or videos | the matching file in `api/` | upload/render code in `static/js/chat.js` | the API prefix, such as `/api/images` |
| Change story-state behavior | `static/js/story-state.js` | `api/story_state.py`, `services/story_state.py` | `/api/story-state` |
| Change Discord or Telegram behavior | `services/platform_bridge.py` | `api/platform.py`, `services/discord_mentions_bridge.py`, settings in `config.py` | `PLATFORM_BRIDGE` |
| Change database tables | `db/schema.py` | a tracked migration in the same file | `SCHEMA =` or `_migration_` |
| Add a new API feature | a new or existing file in `api/` | `server.py` if it is a new router; a `services/` file for substantial logic | `ANAM GUIDE: ADD A NEW API AREA` |
| Add a new browser page | `static/<page>.html` | its JS/CSS, then a page route in `server.py` | `ANAM GUIDE: SERVE BROWSER PAGES` |
| Fix the free Kokoro voice (slow, silent, "won't play") | `services/local_tts.py` | `services/kokoro_worker.py` (the voice's own process), `static/js/voice.js` (player timeouts), `.env` `KOKORO_FASTAPI_URL=` empty = no Docker | `ANAM GUIDE: FREE LOCAL VOICES (KOKORO)` |
| Stop Windows parking Anam on the slow cores | `services/power_qos.py` | `core/lifespan.py`, `scripts/anam_stack.py` | `ANAM GUIDE: POWER THROTTLING` |
| Change what a boy sees waiting from the Commons | `services/context_hooks.py` | the `commons_pings` hook; ledger `commons_private/pings.json`, served by the Commons `/api/pings` | `commons_pings` |
| Change how the Commons pings people | `C:/path/to/example.com/commons/server/commons_server.py` | `_queue_ping`, `_deliver_nearby`, `_reach_owner`; manual in `commons/README.md` (Pings) | `_queue_ping` |

## The three-layer rule for Settings

A normal setting has three pieces:

1. **What you see:** add the input, select, checkbox, or button in `static/settings.html`.
2. **What it does in the browser:** wire its event, load its current value, and save changes in `static/js/settings.js`.
3. **What the server stores:** add or extend `GET`/`PUT` behavior in `api/settings.py`.

Most settings do **not** need a schema migration. The `settings` table in `db/schema.py` stores arbitrary string keys. Use `_get_setting()` and `_set_setting()` in `api/settings.py`, validate the value at the API boundary, and invalidate any runtime cache that reads it.

Tests for a setting normally live in `tests/test_<feature>.py`. Theme and appearance examples are in `tests/test_theme_settings.py`.

## The color map

Anam has two color layers on purpose:

- **House theme:** page background, cards, Owner's bubble, shared text, borders, glass, lace, and fonts. Defaults live in `static/css/main.css`; curated preset values come from `_THEME_PRESETS` in `api/settings.py` and are applied by `static/js/app.js` plus `static/js/theme-boot.js`.
- **Identity theme:** each boy's gingham, accent, bubble, check size, and voice. Defaults live in `IDENTITIES` in `config.py`; `App.applyIdentityTheme()` in `static/js/app.js` turns them into CSS variables; `static/css/anam.css` paints the bubble.

Per-boy bubble choices from Settings sit on top of those defaults. That is why changing a color can involve more than one file: `config.py` defines the identity default, the theme API defines shared presets and saved overrides, JavaScript applies the winning tokens, and CSS draws them.

To add a whole-app vibe preset:

1. Add the preset to `_THEME_PRESETS` in `api/settings.py`.
2. Give it the shared `tokens` and any full-app `chrome` overrides it needs.
3. Add its little button icon to `PRESET_ICONS` inside `Settings.loadThemePreset()` in `static/js/settings.js`, unless your CDN button art will supply it.
4. Add or update coverage in `tests/test_theme_settings.py`.

## The chat journey

```text
static/index.html
    -> static/js/chat.js: Chat.sendMessage()
    -> usual path: static/js/websocket.js -> api/chat.py: /ws/chat
        -> services/chat_pipeline.py
    OR HTTP/SSE fallback: Chat._sendViaHttp() -> api/chat_http.py
    -> both paths use:
        services/chat_turn_prep.py
        -> services/provider_router.py
            -> claude_subprocess.py / claude_pty.py / claude_api.py
               / codex_app_server.py / codex_subprocess.py / openai_provider.py
        -> services/chat_turn_finalize.py
    -> stream events return to static/js/chat.js
    -> Chat.onStreamStart() / onStreamDelta() / onStreamEnd()
```

The provider boundary deliberately shares one stream-event shape. If you change an event name or payload, check both the backend sender and the matching handler in `static/js/chat.js`, plus saved-message metadata in finalization.

## Identity and prompt files

- `prompts/<identity>.md` is the active chat identity prompt read by the provider implementations.
- `prompts/modes/roleplay.md` and `prompts/modes/dnd.md` add conversation-mode rules.
- `programs/<identity>.md` controls structured autowake session programs and provides the longer program thread.
- `prompts/full/` is not referenced by the current runtime. Treat it as reference/source material unless a sync workflow explicitly says otherwise.
- `config.py` holds the identity's app-facing metadata: colors, voice ID, room, identity documents, and character/bonded type.

## Frontend ownership

The browser has no build step. HTML is structure, CSS is appearance, and JavaScript is behavior.

| Area | HTML | JavaScript | CSS | Backend |
|---|---|---|---|---|
| Main chat | `static/index.html` | `app.js`, `chat.js`, `websocket.js` | `main.css`, `anam.css` (one sheet: chat + identity + sidebar + sanctuary + canvas + mobile sections) | `api/chat.py`, `api/chat_http.py` |
| Settings | `static/settings.html` | `settings.js`, `theme-boot.js` | `settings.css`, `main.css` | `api/settings.py`, `api/autowake.py` |
| Hub | `static/hub.html` | `hub.js` | `hub.css`, `main.css` | `api/hub.py` and focused `services/hub_*.py` files |
| Diagnostics | `static/diagnostics.html` | `diagnostics.js` | `diagnostics.css` | `/health` in `server.py` and status endpoints |
| Game room | `static/gameroom.html` | `gameroom.js` | `gameroom.css` | `api/games.py` |
| Sanctuary | section in `static/index.html` | `sanctuary-viewer.js` | the SANCTUARY section of `anam.css` | `api/sanctuary.py` |
| Live calls (button removed from the composer 2026-07-10; backend remains) | `live-call.js` (no longer loaded by index.html) | `chat.js` voice orb | voice section of `anam.css` | `api/voice.py`, `services/speech_engine_live.py` |

The chat app's CSS is ONE sheet: `static/css/anam.css`, with a table of contents at the top — search for `SECTION N:` to jump (CHAT, IDENTITY, SIDEBAR, SANCTUARY, CANVAS, MOBILE). The MOBILE section holds phone-specific overrides and MUST stay the last section in the file. When a change looks right on desktop but wrong on the phone, search the same class name in the MOBILE section before changing the base rule. Shared foundation (tokens, fonts, fairy lights, typing dots) lives in `static/css/main.css`, loaded by every page.

## Server and service ownership

- `server.py` assembles the FastAPI app, installs middleware, includes API routers, serves HTML pages, and mounts `/static` last.
- `core/lifespan.py` owns startup and shutdown of shared infrastructure.
- `api/` owns HTTP and WebSocket boundaries: request parsing, validation, and response shapes.
- `services/` owns the substantial behavior behind those routes.
- `db/database.py` owns pooled SQLite connections; `db/schema.py` owns tables and migrations.
- `config.py` owns environment variables, paths, app-wide defaults, and identity metadata.
- `tests/` mirrors behavior by feature. Prefer a focused test before the whole suite while iterating.

## Files that are usually not the edit point

- `data/` is live runtime data, uploads, logs, and `anam.db`. Back it up; do not use it as source code.
- `cache/`, `__pycache__/`, `.pytest_cache/`, and `.venv/` are generated/runtime folders.
- `uv.lock` is generated dependency lock data. Change `pyproject.toml` or dependency inputs, then refresh the lock through the package workflow.
- Retired code archives and local backup copies were removed on 2026-09-08; use Git history for code recovery. Program history lives in the OneDrive Vault.
- `static/vendor/` contains vendored libraries; edit Anam's wrapper code instead when possible.

For ElevenLabs Live Call setup, run `python scripts/setup_speech_engines.py --dry-run`
first, then run it without `--dry-run` after the API key has Conversational AI
read/write permissions. The script creates one private Speech Engine per voiced
bonded identity and stores only their non-secret IDs beside the existing private
voice configuration. Ordinary free voice mode remains a separate control.

## When something breaks — recipes you can run yourself (2026-09-01)

Every recipe here is a thing that actually broke and was actually fixed; the
commands are the ones that proved it. Buttons live beside `start-anam.bat`.

**The house.** `status-anam.bat`; `curl 127.0.0.1:8790/api/health` (Anam — a
401 still means alive) and `curl 127.0.0.1:8791/api/health` (Commons/pages).
Restart one service with `python scriptsnam_stack.py restart anam|pages`.
The supervisor allows THREE restarts per service per hour; a fourth prints
"restart suppressed" and keeps the OLD code running — check the last three
"Restarting" lines in `logsnam-stack.log` and wait the oldest out. Logs:
`logsnam.log`, `logs\pages.log`, `logsnam-stack.log`.

**Everything sluggish.** Windows 11 efficiency mode was parking the whole
Anam tree on the slow cores (voice: 29s → 2.8s the moment it was lifted).
`services/power_qos.py` exempts the server, its voice worker, and every
supervisor child at start. Query state shows "default" even while it is
happening; the only proof is to set never-throttle and re-time.

**Kokoro won't play.** `.env` must say `KOKORO_FASTAPI_URL=` (empty =
embedded voice, no Docker; `config.py` treats the file as the last word). A
`[WinError 10061]` from `services.local_tts` means something re-pointed it at
the retired sidecar. The voice runs in `services/kokoro_worker.py`; a
paragraph is ~1s. The phone player waits 120s (`static/js/voice.js`) — reload
the page on the phone after changing it.

**The Commons.** Pings leave word for a boy's NEXT natural wake (ledger
`commons_private/pings.json`, hook `commons_pings`, tool `commons_pings`);
only the knock wakes. Your pings go to your phone shade + band (wrist-gated).
Tests: `python run_house_tests.py` in `commons\`; public liveness:
`python check_live.py` after any restart.

**Your bridge browser.** `open-my-browser.bat` / `close-my-browser.bat` open
and close only your Chrome profile with debug port 9231 (a taskbar-opened
Chrome never has the port).

## A safe little change loop

1. Search this guide by what you want to change.
2. Search the named `ANAM GUIDE:` landmark.
3. Read the surrounding function or CSS section before editing.
4. Check whether the same feature has phone CSS, an HTTP fallback, or a second provider path.
5. Run the focused test, then broader tests in proportion to the change.
6. Reload the browser. The asset version is computed at server startup, so a running process may need its normal restart before it serves a newly computed version.

If a feature is not in this guide yet, search its visible label, element ID, API path, or WebSocket event name. Those four strings are the best breadcrumbs through Anam.

## Theme buttons (send / attach / home / pictures)

The four app buttons — send, attach, home, and pictures — default to Owner's
pink pastel PNGs. Each vibe theme can have its own set of cute button art.

**Where everything lives:**
- The map of which theme uses which images: `static/js/app.js` — search for
  the landmark `ANAM GUIDE: OWNER'S CUTE BUTTON ART LIVES HERE`
  (the `THEME_APP_ICONS` object).
- The image files themselves: `static/assets/icons/themes/<preset>/`
  with one file per button: `send`, `attach`, `home`, `pictures`.
- Preset names must match the theme allowlist in `api/settings.py`
  (`_THEME_PRESETS`): sunrise-pink, dusky-rose, soft-blush,
  moonlit-lavender, goth, eighties-neon, halloween, fall.

Any theme NOT in the map keeps the pink defaults. If a listed file is
missing, the button quietly falls back to pink — nothing breaks.

### Halloween

Halloween already has an entry in `THEME_APP_ICONS` pointing at placeholder
SVGs (pumpkin send button and friends). To use your own art:

1. Make your four images (PNG is fine).
2. Drop them in `static/assets/icons/themes/halloween/` as
   `send.png`, `attach.png`, `home.png`, `pictures.png`.
3. In `static/js/app.js`, change the halloween entry's `.svg` endings
   to `.png`.
4. Reload the app with the halloween vibe selected. Done.

### Adding a brand-new theme's buttons (e.g. goth)

1. Create the folder `static/assets/icons/themes/goth/` and put your four
   images in it.
2. Copy the halloween block inside `THEME_APP_ICONS` in `static/js/app.js`,
   rename the key to `'goth'`, and point the four paths at your files.
3. Reload. Switching to that vibe swaps the buttons; switching away
   restores the pink ones automatically.

(Fall also has placeholder SVGs already, same as halloween.)

## What you have — the full inventory (audited 2026-07-10)

Every file in Anam was traced to its users on this date. This is the honest
map of what exists, what it's for, and what's intentionally sleeping.
If a file isn't listed in a category below, it shouldn't exist — feel free to ask.

### Browser pages and what each one loads

| Page | JS it loads | CSS it loads |
|---|---|---|
| `index.html` (chat) | utils, markdown, websocket, voice, canvas, voice-orb, chat, sidebar, sanctuary-viewer, story-state, app | main.css, anam.css |
| `hub.html` | theme-boot, utils, vendor d3 (dispatch/quadtree/timer/force), hub | main.css, hub.css |
| `settings.html` | theme-boot, utils, settings | main.css, settings.css |
| `diagnostics.html` | theme-boot, utils, diagnostics | main.css, settings.css, diagnostics.css |
| `gameroom.html` | theme-boot, utils, gameroom | main.css, gameroom.css |
| login / access-denied pages | inline HTML inside `api/auth.py` | inline styles |

All 6 CSS files and every JS file above are live. `sw.js` (the offline
service worker) precaches exactly these 6 CSS files and every JS file the
pages load — nothing stale.

### Dormant on purpose — do NOT delete

- `static/js/live-call.js` + `static/vendor/elevenlabs-client-1.14.1.iife.js`
  — the ElevenLabs Live Call feature. Button removed from the composer
  2026-07-10; the code sleeps here in case we ever want it back. Not loaded
  by any page.
- `services/autowake_smoke.py` — a standalone smoke-test script you run by
  hand (`python services/autowake_smoke.py --identity ...`). Imported by
  nothing, used by humans.

### Backend census

- `api/` — 24 router files, every one imported and registered in
  `server.py`. No orphans.
- `services/` — 87 modules, all imported somewhere, plus the one
  hand-run script above.
- `scripts/` — 10 live entries, each hand-run or started by
  `start-services.bat` / bootstrap.

### Deleted in the morning audit (2026-07-10)

- `bach-passacaglia.png`, `bach-playing.png` — stray images at repo root.
- `static/assets/backgrounds/chat-bg.png` — orphaned, referenced nowhere
  (and the now-empty `backgrounds/` folder).
- `static/assets/fonts/` — empty leftover dir (real fonts: `static/fonts/`).
- `services/sanctuary_reader.py` — zero importers; `api/sanctuary.py` does
  the sanctuary work itself.

### Deep clean, same day (2026-07-10, ultracode audit — 110 agents, every
### finding adversarially verified, full test suite green after)

Deleted outright (dead code inside live files):

- 4 whole API routers nothing called: `api/triggers.py` (the trigger SYSTEM
  lives on via local tools + `services/trigger_engine.py`), `api/digests.py`
  (scribe digests happen in-process), `api/platform.py`, `api/brother.py`.
- ~18 dead routes inside live routers (voice `/test` `/quota` `/elevenlabs`
  `/speak-json`, settings `/viewers` `/mcp` cc-permissions GET/DELETE,
  messages `/bookmarked`, images `/register`, videos `/all`, games
  `/identities`, autowake `/my-schedules`, chat_http `/debug_metadata`,
  identity `/continuity`, sanctuary `/inbox/unread`, activity ping/recent).
- ~30 dead functions across `services/` and `api/` (each grep-verified
  zero callers), the old disabled-hooks feature, the lazy tool-category
  remnants in `mcp_bridge`, and `db/mind_dashboard_api_server.py` (748
  lines; its endpoints were ported into `api/hub.py` long ago).
- Dead frontend: the continuity card cluster, farewell handlers (server
  half removed 2026-05-10), the Groq transcription path, pack-hall/story-
  picker sidebar remnants — plus all their CSS components across the five
  stylesheets. Config: `VIEWER_URLS`, `SESSIONS_FILE`, sanctuary path
  constants, `IDENTITY_DOCS_DIR`, identity `docs` lists.

Archived in July to `archive/cleanup-2026-07-10/`, then removed from the checkout in the 2026-09-08 cleanup (Git history retains the tracked files):

- 8 one-shot/finished scripts (backfills, seeders, importers, exporters)
  and `anam_hub_mcp.py` (a complete hub MCP server that was never
  registered anywhere) with its test.
- 19 orphaned art files (old hub wellness PNGs, pre-CDN button PNGs,
  hub-nav SVGs, precached-but-never-shown icons) — kept as local masters
  in case the CDN copies ever need re-uploading.
- The Cinzel Decorative font (offered by no picker), `templates/login.html`
  and `denied.html` (both pages render inline from `api/auth.py`), and the
  retired mind-dashboard HTML.
- Empty `scheduler/` and `templates/` dirs removed.

Also fixed while in there: `sw.js` now precaches `theme-boot.js` and
`story-state.js` (they were loaded but never cached), and the reflow-forcing
`offsetHeight` reads use `void` so linters stop flagging them.
