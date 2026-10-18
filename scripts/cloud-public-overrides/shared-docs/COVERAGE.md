# What is included?

Checked on 2026-10-18 against maintained application and local service sources.
There are 36 independent package folders. Source coverage, offline tests and
configured-account connectivity are separate claims.

## Current application and service code

| Capability | Included implementation | What you supply |
| --- | --- | --- |
| Chat, providers, tools and browser bridge | ui | Your prompts and authentication |
| Memory, continuity, archives and Sketchbook | mind-backend; optional qualia-mcp adapter | Your cloud resources and identities |
| Advisory drives and persistent red/green regulation | limbic | Your definitions; fictional starters are included |
| Shared rooms, moods and presence | hearth-hub | New state database and identities |
| Fictional social feed, DMs and appearance references | world-feed backed by ui | Your cast and optional images; automation starts off |
| Image generation/editing | photos-mcp | Your API account or separate optional subscription login |
| Speech and multi-voice dialogue | elevenlabs-mcp | Your account and voices |
| Files, shell, desktop, Krita and Obsidian | machine-agent and standalone tools | Your apps and files |
| Audio/video analysis | audio-visualizer | FFmpeg, dependencies, media and optional Groq key |
| Home Assistant | home-assistant-mcp | Your server and token |
| Wrist replies, queue and vitals | watch-mcp | Anam, notification client and optional sensor feed |
| Timers, orbs, audio and daily wins | anam-touch-mcp | Anam and selected optional services |
| Companion communication | crosstalk-mcp | Anam and configured roster |
| Commons remote OAuth MCP | Updated hearth-commons | A compatible separately hosted world |
| Coloring, stickers, studio ledger and council | Engines in ui | Your art, histories and identities |
| Android notification/reply bridge | ui/wearable/AnamCompanion | Your build, phone, watch and server |
| Discord, Google, social, Alexa and image browsing | Their individual folders | Your accounts and deployments |

Other standalone packages remain independent portable distributions. Their presence
does not mean a particular installation currently selects them. The UI discovers
your own MCP configuration and copies no original tool account access.

## Deliberately outside this download

| System | Boundary |
| --- | --- |
| Full Commons world | Coupled to authored maps, rooms, art and saves. This refresh ships its connector. A portable world release needs a reviewed fictional starter and asset licensing. |
| Phone SSH/device adapter | Separate implementation, Termux/ADB setup, addresses and keys. |
| General browser adapter | Separate lifecycle service and profiles. The included ChatGPT bridge manages its configured profile. |
| Android voice/listening, NFC rooms and SMS radio | Separate apps from the notification/reply client. Endpoints do not install these apps. Original wake-word and contact settings are excluded. |
| Scoped cloud Home Assistant touch Worker | Household entity/scene settings need separate review; the general local Home Assistant adapter is included. |
| Local Commons and legacy Aisling adapters | Separate from OAuth; still require the world server. |
| Whole-stack supervisor, page hosting and tunnel | Workstation-specific startup/routing. Packages have independent setup guides; UI watchdog requires explicit service configuration. |
| Retained Discord extras and archived worlds | Their presence is not evidence that they are prerequisites. Cloud Discord is included. |
| Custom skills and authored content | Review individually. Private prompts, memories, biographies, conversations, stories, media and home layouts are excluded. |
| Upstream apps and connectors | Supply your own browsers, model clients, plugins, editors, runtimes, providers and accounts. |

SMS starts with an empty contact allowlist. The bundled Android notification client
is not an SMS radio. Image subscription availability depends on the chosen account
and endpoint; offline tests do not certify entitlement or future availability.

Start with [START_HERE.md](../START_HERE.md), get one chat working, then add what
you want. See [the refresh report](../REFRESH_REPORT.md) for the audit.
