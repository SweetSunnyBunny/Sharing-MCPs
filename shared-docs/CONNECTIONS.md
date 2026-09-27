# Connecting the shared packages

The folders contain reusable application code and generic setup examples.
Runtime records, credentials, voices, personal prompts, and account configuration
are supplied by the person installing them. Generic `.env.example` and
`.dev.vars.example` files are safe templates; real env files stay local.

## Current memory and emotional state setup

1. Deploy [mind-backend](../mind-backend/README.md) for the current cloud Qualia.
   Its migrations, Worker code, retrieval modules, and setup instructions are
   included. Apply the schema to an empty database and configure your own
   identities using that package's starter instructions.
2. Deploy [limbic](../limbic/README.md) if you want advisory drive state. Apply its
   schema and configure your own drive definitions and baseline values. The
   generic starter examples demonstrate the structure without personal charts.
3. Use the same identity identifier across your UI prompts, Qualia identity
   records, and Limbic configuration. The UI bridge sends lowercase IDs.
4. Enable the optional Qualia-to-Limbic connection as described in the memory
   package. The service binding and its key belong to your own deployments.
5. Connect the UI to these deployed MCP endpoints. For clients that need a local
   stdio entry point, use `qualia-mcp` (see its README in the Sharing-MCPs collection). That folder points
   to the current service; the maintained implementation is `mind-backend`.

The older `memory-core-mcp` remains a separate local
memory option. It is not needed to deploy current cloud Qualia. Its optional
legacy integrations are described in its own README.

## Connecting the UI

An inactive `ui/mcp-servers.example.json` in the Sharing-MCPs collection shows the
exact HTTP configuration shape understood by the current UI bridge. After
deploying services and replacing placeholders privately, merge only the entries
you want into the `mcpServers` object in `ui/mcp-servers.json`. Keep the existing
tool taxonomy and review the enabled/critical server lists for your installation.
The example JSON is never loaded automatically.

Qualia and Limbic accept bearer headers at `/mcp`. The Hearth Hub example uses
its supported `/mcp/<secret>` endpoint. Keep configured secrets out of shared
copies. Anam can also discover your own local Claude MCP configuration; another
person's CLI credentials, plugin caches, and authentication files should never
be copied to reproduce that discovery.

For basic chat, follow the `ui/README.md` in the Sharing-MCPs collection. Add other services as
needed. Run `python scripts/anam_doctor.py --json` from `ui` for passive connection
diagnostics after configuring them.

## What must exist outside a package

| Integration | Required external item | Included setup |
| --- | --- | --- |
| Cloud Workers | Your Cloudflare account, newly created databases/indexes, and secrets | Each cloud package includes application code, schema, and Wrangler configuration |
| Local computer tools | Your running computer, permitted files, and optional installed apps | [machine-agent](../machine-agent/README.md) includes its local tool implementations and proxy scaffolds |
| Krita / Obsidian | Krita and selected documents; for the standalone Obsidian package, a Markdown vault folder is sufficient | Their packages include the reusable bridge code; the Obsidian app is not required for that package |
| Discord Scribe | Your bot and local speech model runtime; authenticated Claude CLI only for optional summaries | `discord-scribe/README.md` in the Sharing-MCPs collection lists Node and Python dependencies and startup steps |
| Commons connector | A separately hosted Commons world server | [hearth-commons](../hearth-commons/README.md) contains the MCP connector; it does not contain a world-server implementation |
| Alexa / smart home | Your Alexa skill and optional Home Assistant or music host | [alexa-skill](../alexa-skill/README.md) supplies Worker and interaction-model templates |
| Voices / image generation | Your provider accounts, API keys, and chosen voices/models | Speech/image packages document their providers; no personal voice recordings or reference images are supplied |
| OAuth tools | Your registered OAuth app, consent, and locally generated tokens | Google packages provide code and authorization helpers; token files are generated privately |

Accounts, installed applications and models are supplied by each installer.
Local memories, journals, libraries, caches, device settings and vault contents
are private runtime inputs and are deliberately excluded.

Some optional integrations also need reusable custom code that has not been
packaged here. For example, the Commons connector does not supply the world
engine, and UI phone/watch routes do not supply every device adapter. Those are
source-code gaps, separate from personal data. Read [COVERAGE.md](COVERAGE.md)
before trying to reproduce those features.
