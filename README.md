# Sharing MCPs

Reusable companion software with generic examples for your own accounts. Each
folder is a separate application or tool. Install only the pieces you want.

**First time here? Open [START_HERE.md](START_HERE.md).** It explains terminals,
folders, prerequisites and common errors.

For the companion setup, start with the [Anam UI](ui/README.md). Get one chat
working, then add [Qualia memory](mind-backend/README.md), then
[Limbic](limbic/README.md), then other tools.

**The ChatGPT browser bridge is included.** Follow its
[step-by-step guide](ui/docs/CHATGPT_BRIDGE_SETUP.md) after installing the UI.
It uses your own Chrome profile and ChatGPT login.

**This collection covers the core companion stack, not every service on the
original PC.** Read the [coverage map](shared-docs/COVERAGE.md) before planning a
larger installation. It distinguishes included code, additional applications you
install yourself, and custom components that have not been packaged yet.

## Package list

The UI and cloud setups are siblings so each can be shared on its own. The UI
does not contain another copy of the cloud setups.

| Folder | Purpose |
| --- | --- |
| [ui](ui/README.md) | Anam chat UI, provider runtimes, orchestration, and optional integrations |
| [alexa-skill](alexa-skill/README.md) | Alexa skill Worker and interaction models |
| [discord-backend](discord-backend/README.md) | Discord MCP service |
| [easel-ai](easel-ai/README.md) | Image gallery and prompt workshop |
| [elevenlabs-mcp](elevenlabs-mcp/README.md) | ElevenLabs speech MCP service |
| [google-cloud-mcp](google-cloud-mcp/README.md) | Google account and document tools |
| [hearth-commons](hearth-commons/README.md) | Remote MCP connector for a Commons server |
| [hearth-hub](hearth-hub/README.md) | Shared state and workspace service |
| [limbic](limbic/README.md) | Configurable advisory emotional state service |
| [machine-agent](machine-agent/README.md) | Local machine tools and cloud proxy scaffolding |
| [mind-backend](mind-backend/README.md) | Memory, continuity, and retrieval service |
| [social-backend](social-backend/README.md) | Social and world tools |
| [shared-docs](shared-docs/README.md) | Cross-service architecture notes |
| [qualia-mcp](qualia-mcp/README.md) | Local stdio adapter for current cloud Qualia |
| [memory-core-mcp](memory-core-mcp/README.md) | Separate local memory implementation |
| [audio-visualizer](audio-visualizer/README.md) | Audio analysis and visualization |
| [books-mcp](books-mcp/README.md) | Book and reading tools |
| [celestial-weather](celestial-weather/README.md) | Astronomy, weather and location calculations |
| [desktop-control](desktop-control/README.md) | Local desktop interaction |
| [discord-scribe](discord-scribe/README.md) | Local Discord voice recording, transcription and summaries |
| [filesystem-mcp](filesystem-mcp/README.md) | Tools for permitted local files |
| [gdrive-mcp](gdrive-mcp/README.md) | Local Google Drive tools |
| [gmail-mcp](gmail-mcp/README.md) | Local Gmail tools |
| [krita-mcp](krita-mcp/README.md) | Tools for your installed Krita application |
| [obsidian-mcp](obsidian-mcp/README.md) | Tools for your own Obsidian vault |
| [terminal-mcp](terminal-mcp/README.md) | A local shell connected to an MCP client |
| [tumblr-mcp](tumblr-mcp/README.md) | Tumblr integration |
| [twitter-mcp](twitter-mcp/README.md) | X/Twitter integration |
| [world-tools-mcp](world-tools-mcp/README.md) | Local world-information tools |
| [youtube-mcp](youtube-mcp/README.md) | YouTube tools |

For deployment order, example connections, and external requirements, read
[Connecting the packages](shared-docs/CONNECTIONS.md). The current cloud Qualia
implementation is `mind-backend`; [qualia-mcp](qualia-mcp/README.md) is its optional
local stdio adapter. `memory-core-mcp` remains a separate local memory option.

Some packages need accounts, paid resources, installed apps or downloaded models.
Their guides list those requirements before the installation steps. Windows is
the documented starting point; platform-specific tools are labelled in each guide.

## Snapshot verification

See [SETUP_REVIEW.md](SETUP_REVIEW.md) for the scope and checks performed on the
current setup snapshot. File hashes are recorded in [SETUP_MANIFEST.json](SETUP_MANIFEST.json).
To check an untouched extracted copy, open PowerShell in this folder and run:

```powershell
py -3.11 scripts\verify_setups.py
```

Success prints `Verified ... files across 30 setup folders.` After you configure
the apps, this check intentionally reports changed files or private runtime files.
It checks the clean distribution, not whether your installation works. Do not
delete your own configuration just to make it pass.

For the refresh tools and their required private configuration, see
[scripts/README.md](scripts/README.md).

Private prompts, conversations, personal media, credentials, deployment state,
local databases, and source repository history do not belong in shared setup
packages. Generic env examples are included; populated env files are not.
The example configurations deliberately require your own values.

## Sharing a clean snapshot

This cleanup updates the working files. Existing Git history retains earlier
versions and is outside the reviewed snapshot. To create an archive containing
only manifest-listed files, with no Git history or runtime artifacts:

```powershell
py -3.11 scripts\build_clean_archive.py --output ..\Sharing-MCPs-clean.zip
```

The command checks file integrity first and refuses to overwrite an existing
archive. Share that clean archive when distributing this snapshot as files.
These maintainer scripts are not needed to install or run the applications.
