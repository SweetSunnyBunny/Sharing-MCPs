# Optional local inputs and services

The UI's application modules, browser assets, generic prompts, and test fixtures are included. Personal databases, logs, archives, voice recordings, world content and account credentials are created or supplied by each installation.

Some optional features also depend on custom implementations not yet packaged in this collection, including the full Commons world server, several device/tool adapters and separate mobile apps. A configuration option or tool name does not mean its server is included. See `shared-docs/COVERAGE.md` in the full Sharing-MCPs collection for that boundary. Custom skills also need their own review before sharing; their personal content should not be copied wholesale.

- Bundled machine tools: place `machine-agent` beside `ui`, or set `ANAM_MACHINE_AGENT_ROOT`. Python wrappers and Codex local fallbacks resolve its included tool modules from that path. Install/build that package as its README describes.
- Gmail and Drive OAuth helpers resolve the sibling `gmail-mcp` and `gdrive-mcp` packages by default. Authenticate with your own accounts. The optional YouTube Music credential store remains local data; no YouTube Music service is bundled.
- Default backup, canvas, voice, identity and roleplay archives now live under UI `data`; override the corresponding `ANAM_*` path variables when using your own existing vault. Claude skills default to your current home directory's `.claude/skills`.
- `index_archives.py` reads only `ANAM_SITE_ARCHIVE_DIR`, `ANAM_STORY_ARCHIVE_DIR`, `ANAM_CHAT_ARCHIVE_DIR`, plus the configured identity archive when explicitly enabled. `index_voice_archive.py` uses `ANAM_VOICE_VAULT_DIR`. Both require `QUALIA_MCP_URL` for your own deployment and only run when invoked. No historical records are supplied.
- Cloud Qualia, Limbic, Hearth, Google, Discord, Alexa and Commons require separate configured services; see each package README and the shared connection guide. Commons is a connector to a separately hosted world server, not a copy of someone else's world.
- The gateway Worker test uses the included sanitized fixture, not private `data/gateway-connector-staging` output. Re-run the connector staging tool only when modifying a configured machine-agent installation.

## Optional browser bridge

The included `scripts/pack-browser.ps1` manages a dedicated Chrome profile; no browser data is bundled. On Windows, set `CHROME_EXE` if Chrome is not in its standard location. Run `powershell -File scripts/pack-browser.ps1 -Action open -Visible` and sign into your own account once. The bridge uses `ANAM_CHATGPT_IDENTITY` (default `ChatGPT`), `ANAM_CHATGPT_CDP_PORT` (default `9225`) and `ANAM_BROWSER_PROFILES_DIR` (default `%LOCALAPPDATA%/Anam/BrowserProfiles`). `ANAM_BROWSER_SCRIPT` can replace the launcher. This optional website adapter may need updates when ChatGPT changes.

## Optional local services and content

The service watchdog is disabled until `ANAM_WATCHDOG_CONFIG` points to your own JSON list. Each entry specifies `name`, `args` (an executable plus arguments), optional `cwd`, `label`, and `ports` or `process_name`. Set `via_supervisor: true` for a command that asks your process manager to restart a service and exits. The original installation's process supervisor and domain tunnel are not requirements of this UI.

`ANAM_STORIES_DIR` defaults to `data/stories`; set a character's `story_branch` in `config.IDENTITIES` to select its `STATE.md`. `ANAM_COMMONS_KEYS_DIR` defaults to `data/commons-private` and contains only keys you obtain from your own Commons service. `ANAM_HEALTH_FRAME_DIR` defaults to `data/health-frame`. Newsletter gathering uses `ANAM_SITE_ARCHIVE_DIR`, `ANAM_SONGS_DIR` and `ANAM_GAZETTE_DIR` if supplied. These are your content, not bundled application dependencies.

Attachment analysis uses the included sibling `audio-visualizer` package. Its Python dependencies and FFmpeg must be installed separately; `AUDIO_MCP_OUTPUT_DIR` can select a shared cache. Image registration defaults to the UI's configured data and voice archive directories.

The Hearth comfort button can select `ANAM_COMFORT_IDENTITY`. Optional home effects require your own `ANAM_COMFORT_SCENE_ENTITY` plus `ANAM_COMFORT_LIGHT_SCENE`, or `ANAM_COMFORT_PLAYER_ENTITY` plus `ANAM_COMFORT_MUSIC` (with `ANAM_COMFORT_MEDIA_TYPE` if needed). Empty settings perform no home device action; no household scene, playlist or emotional/medical assumption is bundled.

The public build starts with no automatic personal schedule seeds. Create and enable your own schedules in Settings. Care signals and failsafe outreach default off; configure your preferred identity, contact thresholds and reminder times before enabling them. The displayed reminder times are generic examples, not imported health data.

Optional Watchtower outreach defaults off (`watchtower_enabled=false` in settings). Work-time suppression is empty until `watchtower_work_days` (0=Monday), `watchtower_work_start_hour` and `watchtower_work_end_hour` are configured. Inactivity escalation likewise defaults off and has no workdays. Wrist quiet-hour guards use generic 22:00–08:00 examples configurable through `ANAM_WRIST_QUIET_START`, `ANAM_WRIST_WAKE_WEEKDAY` and `ANAM_WRIST_WAKE_WEEKEND`.
