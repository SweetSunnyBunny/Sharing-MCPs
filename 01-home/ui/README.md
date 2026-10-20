# Anam: install the companion chat UI

Anam is the web interface for chatting with your own configured identities and
model providers. It includes the ChatGPT browser bridge, Claude Code and Codex
adapters, and optional cloud/tool connections. The examples contain no personal
memories or account credentials.

This first setup is for **Windows on your own computer**. You need an internet
connection for installation and one model provider you can authenticate with.
You do not need Discord, Cloudflare, Qualia, Limbic or every other package just
to get your first chat working.

## 1. Open this folder in PowerShell

Extract the download first. In File Explorer, open `Sharing-MCPs`, then open
`ui`. Click the address bar, type `powershell`, and press Enter.

Check that you are in the right place:

```powershell
Get-ChildItem pyproject.toml, uv.lock, server.py
```

All three files should be listed. If they are missing, open the `ui` folder
before continuing. Run the following commands one line at a time.

## 2. Install the dependencies

The easiest reproducible route uses **uv**, which installs Python and the exact
dependency versions in `uv.lock`. Install it once:

```powershell
winget install --id astral-sh.uv --exact
```

Close PowerShell, reopen it in `ui`, then run:

```powershell
uv --version
uv python install 3.11
uv sync --locked --python 3.11
```

If `winget` is unavailable, use the Windows instructions on
[uv's official installation page](https://docs.astral.sh/uv/getting-started/installation/),
then reopen PowerShell and continue with `uv --version`.

The first install includes document, audio and model libraries and can download
large files. Let it finish. **Success means** the commands return without an
error and `.venv\Scripts\python.exe` exists. You do not need to activate `.venv`.
The `--locked` option prevents silently choosing a different dependency set;
see [uv's lockfile explanation](https://docs.astral.sh/uv/concepts/projects/sync/).

## 3. Create your local configuration

Only copy the example if you do not already have a `.env`:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Find these five settings and make them exactly:

```dotenv
ANAM_ENV=development
ANAM_PUBLIC_URL=http://127.0.0.1:8790
SITE_URL=http://127.0.0.1:8790
ANAM_ALLOW_NO_AUTH=true
ANAM_USE_DIRECT_API=false
```

Leave the Discord credentials and optional keys empty for now. Save the file
as `.env`, not `.env.txt`, and close Notepad. This login-free first test is only
for your own computer; the start command below binds to localhost.

## 4. Start Anam

```powershell
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8790
```

**Expected result:** the terminal reports application startup and the server URL.
Keep that window open. In your browser, open:

**http://127.0.0.1:8790**

The interface and example identities should appear. A loaded page confirms the
UI works; choose a provider in the next step before expecting a chat reply.

## 5. Choose ONE chat provider and save it

Open **Settings → System → LLM Provider**. Choose one route:

| Provider | What you do |
| --- | --- |
| **Claude Code CLI** | Install Claude Code using [its official setup guide](https://code.claude.com/docs/en/setup), run `claude` in a terminal and complete its sign-in. Confirm `claude --version` works in a new terminal. In Anam choose **Claude Code CLI**, keep **Fast (-p subprocess)**, and click **Save**. |
| **ChatGPT (browser bridge)** | Follow [the ChatGPT bridge guide](docs/CHATGPT_BRIDGE_SETUP.md). It shows how to open the included Chrome profile, sign in, and save profile `ChatGPT` / port `9225`. No API key is needed for this route. |
| **Codex** | Follow [the Codex setup guide](docs/CODEX_COMPANION_SETUP.md), authenticate your own CLI, then select **Codex** and save its provider settings. |
| An API or local model provider | Select the provider you actually have, enter its requested key or local server URL and model, then click **Save**. Your provider's usage terms and charges apply. |

**Click Save even on a fresh installation.** Setting `ANAM_USE_DIRECT_API=false`
in `.env` does not select your provider in the database. If you just installed a
CLI, restart the Anam terminal so it sees the new executable on your PATH.

Use **Test Connection** for an initial check, then return to chat, pick an example
identity and send a short greeting. **You are finished with basic setup when a
completed reply appears.** A CLI being found or a browser port answering is not
the same as a successful authenticated reply.

## 6. Start it again later

Open PowerShell in `ui` and rerun the start command from step 4. Your settings and
conversations are kept locally under `data`. Stop the server with `Ctrl+C`.
Do not repeat the example-file copy over a configuration you have already edited.

## Add your own identities and services

- Edit the included generic prompt files in `prompts` to describe your own
  identities. `config.py` contains the registry, with display names such as
  `Avery`. Prompt filenames and MCP identity IDs use lowercase slugs such as
  `avery`; keep those slugs consistent across Qualia and Limbic. Do not blindly
  lowercase the registry's display-name keys.
- Add memory using the `mind-backend` folder's README, then add `limbic` if wanted.
  In the full collection, `shared-docs/CONNECTIONS.md` explains the connection order.
- `mcp-servers.json` starts with no external server connections. The inactive
  `mcp-servers.example.json` shows the correct shape. Replace its placeholders
  privately and merge the entries you want into `mcpServers`; retain the rest of
  your existing tool configuration. Restart Anam after editing connections.
- Schedules and outreach start disabled or empty. Create and enable your own
  routines in Settings after ordinary chat works.
- For fictional social media with your chosen characters, open `world-feed/README.md`
  in the full collection. That separate starter folder has a fictional cast,
  launch helper and beginner guide. It uses this UI; no story archive is required.
- Optional computer tools, archives and home effects are described in
  [EXTERNAL_INPUTS.md](EXTERNAL_INPUTS.md). The Android client has its own
  [installation guide](wearable/AnamCompanion/README.md).

## If something fails

| What you see | What to do |
| --- | --- |
| `uv` is not recognized | Install uv and reopen PowerShell. |
| `pyproject.toml` or `server.py` is missing | Open PowerShell in the extracted `ui` folder. |
| The lockfile needs updating | Use an intact matching `pyproject.toml` and `uv.lock` from this release. Do not delete the lockfile to bypass the check. |
| `No module named ...` | Rerun step 2 and use `.\.venv\Scripts\python.exe`, not an unrelated global Python. |
| Port 8790 is in use | Stop your earlier Anam terminal with `Ctrl+C`, or change both the configured URL and launch port. |
| Login is required on the first local test | Check `.env` has `ANAM_ENV=development` and `ANAM_ALLOW_NO_AUTH=true`, then restart. Keep the host `127.0.0.1`. |
| The page works but chat does not | Select a provider, fill its settings, click **Save**, and verify its own login/account before retrying. |
| Claude was just installed but Anam cannot find it | Open a new terminal, verify `claude --version`, and start Anam from that terminal. |
| Optional cloud tools show warnings | They stay unavailable until you deploy and configure them. Basic chat does not require them. |

For a passive diagnostic report, open a second PowerShell window in `ui`:

```powershell
.\.venv\Scripts\python.exe scripts\anam_doctor.py --json
```

Optional services can be reported unavailable until you configure them. Keep
logs and diagnostics private; do not post credentials when asking for help.

## Before making the UI reachable from the internet

Finish local chat first. Then configure your own hosting, HTTPS and Discord login:

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Set its OAuth2 redirect to your exact public URL followed by `/auth/callback`.
   Example: `https://chat.example.com/auth/callback`.
3. Put that application's Client ID and Client Secret in `DISCORD_CLIENT_ID`
   and `DISCORD_CLIENT_SECRET`. Set `ALLOWED_DISCORD_IDS` to your own Discord
   user ID, or a comma-separated list of authorized user IDs.
4. Set `ANAM_PUBLIC_URL` and `SITE_URL` to your actual HTTPS address,
   `ANAM_ENV=production`, and `ANAM_ALLOW_NO_AUTH=false`.
5. Restart, sign in through Discord, and confirm an unauthorized account cannot
   use the UI before sharing its address.

Discord login protects Anam; it is separate from your ChatGPT or model-provider
login. Hosting and TLS depend on your chosen infrastructure; no private domain,
tunnel configuration or cloud account is included.

## Advanced configuration and development

The following sections are reference material; they are not required for the
first local chat. [CODE_GUIDE.md](CODE_GUIDE.md) maps the code by visible feature.

### Shared path configuration

If you keep MCPs, vaults, or auth files outside the repo, put those machine-specific paths in one place instead of scattering them across multiple env files.

- Copy [.env.paths.example](.env.paths.example) to `.env.paths.local`, or use a shared file like `%USERPROFILE%\.config\anam\paths.env`
- Anam loads path files before `.env.local` and `.env`
- You can also point `ANAM_SHARED_ENV_FILE` at any custom path-env file

This keeps repo-safe env separate from local filesystem wiring.

## Core Env Vars

Local development:

- `ANAM_ENV=development`
- `ANAM_PUBLIC_URL=http://127.0.0.1:8790`
- `SITE_URL=http://127.0.0.1:8790`
- `ANAM_USE_DIRECT_API=false` keeps the CLI-oriented configuration; select and save Claude Code in Settings to use it
- For direct API backup, supply your own API key, then select and save **Anthropic API** in Settings; changing `ANAM_USE_DIRECT_API` alone does not override a saved provider

Production-critical:

- `ANAM_ENV=production`
- `ANAM_PUBLIC_URL=https://your-public-host`
- `SITE_URL=https://your-public-host` when Discord OAuth is enabled
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `ALLOWED_DISCORD_IDS`

Optional integrations:

- Claude Code CLI login for the primary runtime path
- `ANTHROPIC_API_KEY` only if you want the direct API backup path
- Twilio env vars for emergency calling
- VAPID keys for web push
- Identity-specific Discord and Telegram bot tokens for the platform bridge
- Path overrides from `.env.paths.local` / `paths.env` for MCP roots, Discord server map JSON, voice config, and Google auth files

## Additive Agent Features

- Claude-local skills remain authoritative. If Hermes Agent is installed, Anam also discovers a conservative allowlist from `ANAM_HERMES_SKILLS_DIR`; override the comma-separated list with `ANAM_HERMES_SKILLS_ALLOWLIST`.
- Hermes skill instructions receive a provider-compatibility note at runtime. Usage counts live in ignored runtime data, and `anam_stage_skill_improvement` only queues proposals for review.
- Claude Code and Codex receive the local `anam-context` MCP automatically, with exact history search/read tools plus skill telemetry/staging tools.
- Codex uses its app-server runtime by default. That gives Anam a resumable native Codex thread, streams thinking and tool activity, and carries the active identity plus companion conduct at developer-instruction authority. The older one-shot `exec` runtime remains available as a fallback.
- Anam injects its companion MCPs into Codex with an `anam_` namespace so personal global Codex tools can coexist without duplicate server names. Plugin-owned connectors such as Sites are discovered from the installed plugin; Unreal is added only while its localhost MCP endpoint is listening.
- Direct Anthropic API sessions can progressively disclose large MCP catalogs through `anam_tool_search`, `anam_tool_describe`, and `anam_tool_call`. OpenRouter/OpenAI-compatible models receive native MCP schemas by default because they follow concrete argument types more reliably; set `ANAM_OPENAI_MCP_TOOL_SEARCH=true` only to opt back into deferred discovery.
- OpenRouter tool rounds are progress-guarded rather than silently capped (`ANAM_OPENAI_MAX_TOOL_ROUNDS=0` means no fixed cutoff). Repeated failures force a final blocker report. Remote API file/shell tools block credential files and redact configured secret values.
- Provider `max_tokens` is the output budget, not the context-window size. Anam defaults OpenRouter to `8192` and accepts `256-65536`.
- Discord and Telegram replies upload allowlisted local response artifacts as native attachments while retaining their web metadata.
- Hub background projects accept optional `outcome`, `verification`, `boundaries`, `stop_when`, and `max_turns` fields. Continuation is capped at eight passes.

Run the passive diagnostic report without restarting or modifying the live app:

```powershell
.\.venv\Scripts\python.exe scripts\anam_doctor.py --json
```

Configuration is described in `config.py`, the example env files and feature
guides. Keep real secrets in your private configuration.

## Optional ElevenLabs Live Calls

The phone button beside free voice mode starts a Speech Engine call: ElevenLabs
handles realtime listening, turn detection, interruption, and voice playback,
while Anam's normal chat pipeline still owns identity, history, tools, streaming,
and persistence. Calls use ElevenLabs plan minutes; ordinary browser/Kokoro voice
mode remains free and separate.

```powershell
.\.venv\Scripts\python.exe scripts\setup_speech_engines.py --dry-run
.\.venv\Scripts\python.exe scripts\setup_speech_engines.py
```

The existing private ElevenLabs key needs Conversational AI read/write access.
The setup script is idempotent, creates or updates one engine per voiced bonded
identity, and stores only their non-secret IDs in the private voice config.

## App Shape

Anam is a single FastAPI app.

- The server renders the HTML shell
- `/static` serves the browser assets
- `/api/*` and `/ws/*` stay on the same app and same origin

## Runtime Preference

This project is Claude Code first, with a first-class additive Codex lane.

- Primary path: Claude Code CLI subprocess
- Backup path: direct Anthropic API mode
- Optional companion path: Codex app-server with native thread continuity
- Compatibility fallback: one-shot Codex `exec`

The CI smoke checks and local bootstrap now assume the Claude Code path by default.

## Testing

Development checks are optional for installation. From `ui`, install the test
extra and run the suite:

```powershell
uv sync --locked --python 3.11 --extra dev
.\.venv\Scripts\python.exe -m pytest -q
```

The tests now bootstrap the repository root automatically, so no manual `PYTHONPATH` setup is required.

## CI

GitHub Actions is scaffolded in `.github/workflows/tests.yml` and runs `pytest -q` on pushes and pull requests.

## Notes

- Runtime data and secrets should stay out of source control. See `.gitignore`.
- Startup now validates production-sensitive config and refuses to boot when required public URL or auth settings are invalid.

## Reading the code

Start with [CODE_GUIDE.md](CODE_GUIDE.md), then [server.py](server.py), then [config.py](config.py). The installation steps above describe the runtime configuration.
Behavioral regression checks live in [tests/](tests/).
