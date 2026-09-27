# Service map

This table maps the main included services. It is not an inventory of every
system in the larger installation. See [COVERAGE.md](COVERAGE.md) for missing
custom components and the boundary between source code and personal content.

| Folder | Responsibility |
|---|---|
| `ui` | Companion chat application, providers, tool gateway, and browser interface |
| `mind-backend` | Durable memory, semantic retrieval, relationships, continuity, and Sketchbook |
| `hearth-hub` | Shared presence, rooms, moods, notes, and private configuration |
| `limbic` | Optional advisory drive state with lazy decay and configurable interactions |
| `discord-backend` | Discord REST/MCP actions, voice/image delivery, optional local archival |
| `google-cloud-mcp` | Google Drive, Docs, Sheets, Calendar, YouTube, and optional Health |
| `social-backend` | Telegram and public weather, calendar, news, and web tools |
| `machine-agent` | Authenticated local clipboard, filesystem, terminal, desktop, Krita, and Obsidian tools |
| `hearth-commons` | MCP connector to a separately hosted Commons world |
| `elevenlabs-mcp` | Voice generation with expiring hosted audio |
| `alexa-skill` | Alexa custom skill integration with the UI and optional Home Assistant/music hosting |
| `easel-ai` | Local image gallery and prompt workshop |

Cloud services own network integrations and durable shared state. The machine
agent performs actions that require the computer. External services such as
Discord, Google, ElevenLabs, Cloudflare, Home Assistant, and a Commons server
require your own accounts or deployments.

No package requires the original developer's deployment or personal data.
Optional integrations are described in the corresponding package README.
