# What can I build from this download?

You can build your own companion chat system, memory services and many connected
tools. **This download does not yet contain every custom system from the larger
installation.** A visible UI option or a configured MCP name does not prove that
the service behind it is included.

Coverage was checked on 2026-09-27 against maintained source folders and configured
tool registries. Configuration shows that a tool is wired in; it does not prove
that the tool is currently reachable. See `SETUP_REVIEW.md` in the full collection
for the tests actually performed. No original accounts or personal records are
needed or supplied.

## Included: start here

These are source packages you can configure for your own installation. They are
not preconfigured hosted services. Follow each package's README and success check.

| What you want | Included folder or component | What you supply |
| --- | --- | --- |
| Companion chat and model providers | `ui`, including the ChatGPT browser bridge and Claude Code/Codex adapters | Your provider login or API credentials and your own prompts |
| Durable memory and continuity | `mind-backend`; optional local `qualia-mcp` adapter | Your Cloudflare resources and identity records |
| Advisory emotional drive state | `limbic` | Your own drive definitions and matching identity IDs; fictional starters are included |
| Shared rooms, moods and presence | `hearth-hub` | Your configured identities and new state database |
| Schedules, background activity and World Feed | Engines inside `ui` | Your schedules, profiles and world content; enable optional automation yourself |
| Local files, shell, desktop and creative applications | `machine-agent` and the individual local-tool packages | Your installed apps, chosen folders and access settings |
| Discord, Google, social and speech connections | Their separate cloud/MCP packages | Your own accounts, OAuth consent, bots, keys and provider choices |
| Audio and video analysis | `audio-visualizer` | Its Python/model dependencies, FFmpeg and your own media; see its README for URL handling |
| Alexa integration | `alexa-skill` | Your skill configuration and any home/music services you choose |
| Image browsing and prompt workshop | `easel-ai` | Your own images and configured services; this is not the missing Photos MCP below |
| Notification delivery and replies from Android | `ui/wearable/AnamCompanion` plus UI wearable routes | Your Android build/device and your own server connection |
| Connect to an existing Commons world | `hearth-commons` | A compatible world server; that server's implementation is not included |

The collection README lists all 30 folders. Not every folder is required for a
working companion. Basic chat works without installing the optional systems below.

## Partial or missing: optional custom systems

These are reusable code gaps, not requests to copy anyone's private data. Until
they are packaged, use only the included features or supply/build your own
compatible implementation. Installing an upstream app alone will not create a
missing custom MCP adapter.

| System | What is present | What is still outside this download |
| --- | --- | --- |
| Full Commons world | Remote connector and UI integration points | World server, gameplay engine and browser world client. A reusable release needs a fictional starter world, empty state and separately reviewed assets. The older Aisling service is a compatibility wrapper, not a second world engine. |
| Phone MCP | UI phone/backend routes | The separate phone MCP adapter and its device/app prerequisites |
| Watch MCP | UI wrist/outbox routes and the Android notification/reply client | The separate watch MCP adapter and any device-specific setup beyond the included client |
| Mobile voice/listening and room controls | UI integration endpoints | Two other Android apps: the voice/listening companion app and the room-tap app. They are separate from the included notification/reply client. |
| Home Assistant and touch | Optional Alexa/UI home effects | The standalone Home Assistant MCP adapter, touch Worker and local Anam touch adapter. Home Assistant itself is also an application you install/configure yourself. |
| General browser tools | ChatGPT-specific Chrome launcher/bridge | The separate browser MCP service. The included ChatGPT bridge does not provide the entire general-purpose browser tool service. |
| Image generation/editing through Photos MCP | UI image handling and the Easel gallery | The standalone Photos MCP implementation and its provider/configuration wiring |
| Cross-companion communication | UI crosstalk backend routes | The separate crosstalk MCP adapter that exposes them to tool clients |
| Local Commons tools | `hearth-commons` remote connector | The separate local Commons MCP adapter and its legacy Aisling compatibility wrapper; these still require the world server |
| Commons OAuth access | The older key-based `hearth-commons` connector | The newer OAuth/agent-forwarding Worker; it is a different implementation and is not included |
| Optional local Discord extras | Current cloud Discord integration | The separate persistent local bot service and its extra presence, notification, search and media workflows. Export only extras confirmed useful; basic cloud messaging is already represented. |
| Combined websites and content delivery | UI plus configurable content paths | Separate story/page hosting implementations and the combined page server. Personal stories, posts, images and archives must remain excluded. |
| Start and supervise the entire installation | Individual package launch instructions and configurable UI watchdog | The larger multi-service supervisor, machine-specific startup scripts and tunnel routing. A portable launcher would need its own generic configuration. |
| YouTube Music | Optional credential/configuration references | No YouTube Music service is bundled. The included YouTube tools do not imply music-library or playback integration. |
| Custom skills and helper commands | UI support for discovering your own skills and tools | The original custom skill library and some auxiliary helpers. Reusable instructions need individual review; personal identity/relationship content does not belong in a public starter. |

Other source folders, including Eidoverse and older hub/status projects, exist
outside this collection. Eidoverse is explicitly skipped by the inspected Anam
configuration. Their presence alone is not evidence that they are required or
maintained. Inventory them separately before adding them to a new installation.

## Tools supplied by other applications

The inspected tool configuration also refers to upstream tools. Their source
and installed runtimes are not part of this download:

| Optional tool | What someone needs to install or connect |
| --- | --- |
| Playwright MCP | `@playwright/mcp`, their own browser profiles and CDP configuration |
| Docker MCP gateway | Docker and their chosen catalog servers; the gateway does not come from this collection |
| Local code-search helper | The separate `@zvec/zvec-grep` package and their own indexed files |
| Hosted ElevenLabs creative tools or PixelLab | The provider's connector and their own authenticated account |
| Specialist remote voice connectors | Their chosen service and credentials; an independently reusable server implementation was not located in this audit |
| Codex document, spreadsheet, presentation, PDF, browser and app tools | Those product plugins in the recipient's own installation; their presence in a desktop chat does not bundle them into Anam |
| Claude frontend design tooling | The recipient's own official plugin installation |
| Unreal editor integration | A compatible running editor/MCP endpoint; discovery support does not include the editor |
| Optional local speech/model services | Their selected runtime, model files and voice configuration; these are separate from source-code availability |

These names describe optional integration points found during the inventory;
they are not additional prerequisites for basic chat.

## Why another person's tool list will be different

Anam can discover MCP servers from the installer's own global Claude configuration
and then apply its local overrides and enabled/skip lists. The public local
`mcp-servers.json` starts with no personal server entries; it does not disable
discovery of that installer's existing global tools. The inactive example file
shows how to add their own services.

The included gateway routes tool calls to configured servers. It does not install
those servers, provide their accounts or create the missing adapters above.
Check the tool list after configuring each service. A successful UI launch alone
does not establish that every optional tool is connected.

## Install yourself; do not copy from someone else's PC

- Model clients, browser applications, provider subscriptions and official
  plugins/connectors belong to the person installing the system. Plugin tools
  available inside one desktop app do not automatically become tools in Anam.
- Cloudflare, Discord, Google, speech/image providers and similar services need
  your own resources and credentials. The supplied source does not transfer
  account access or service quotas.
- Chrome logins, OAuth tokens, keys, device addresses and IDs are created locally.
  Generic `.env.example` and `.dev.vars.example` files show the format. Populated
  environment files must stay private.
- Memories, prompts, biographies, conversations, stories, photos, voices, home
  layouts and device lists are personal content. Start with fictional examples
  or create your own, even when the engine that reads them is shared.

## Best next additions for this collection

1. **Package the small missing MCP adapters first.** Start with Home Assistant,
   Photos and the UI-backed watch/touch/crosstalk tools. Each needs a requirements
   file, generic configuration, numbered setup steps and a read-only connection
   check. Explain exactly which UI routes, apps or cloud services it needs.
2. **Give device apps separate guides.** Distinguish notification/reply, voice
   calls/listening and room controls so people build the app they actually want.
3. **Ship the Commons engine with a tiny fictional world.** One room and one
   interaction would let someone verify the engine before creating their own
   locations and assets.
4. **Add an optional configurable launcher.** It should start only the services
   someone selected, report missing configuration and document how to stop them.
   The first installation should still work without a domain or tunnel.
5. **Test a clean installation with new accounts and empty databases.** Check one
   chat reply, a memory round-trip, a drive-state read and one selected external
   tool. Existing source checks do not certify those account-dependent steps.
6. **Keep this map and the distribution manifest together.** Review new source
   for private defaults and missing files before refreshing hashes and building
   a clean archive.

For your first installation, use `START_HERE.md` in the full collection. Get one
chat working, then add memory and only the tools you want. A complete recreation
of the larger PC setup requires the additional source listed above.
