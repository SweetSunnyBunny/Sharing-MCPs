# Public starter scope

This starter synchronizes reusable application code and browser assets from the current installation. Private prompts, conversation state, memory, credentials, account voice IDs, personal field notes, authored story history, runtime artifacts, and installation-specific tests are excluded or replaced with generic templates. Character package support remains available, but no private story package or story continuity ships.

Each configured identity has a generic prompt. Supply your own voices, MCP endpoints, account IDs, and optional integrations through local configuration. The emoji catalog is intentionally empty until you configure assets you can share.

`EXPORT-MANIFEST.json` records SHA-256 hashes for synchronized source and reviewed generic replacements. Exporting again requires a reviewed external privacy profile and external overlay directory; the public exporter contains no private replacement map. The scan is a configured check, not a guarantee that arbitrary new personal prose is safe. Review new source and overlays before distribution.

MCP connections start empty. Add your own server definitions in `mcp-servers.json`; taxonomy and tool discovery support remain included. World Feed starts with an empty example world and requires your own profiles, story references, and explicit activation.

An optional Android watch client is included in [wearable/AnamCompanion](wearable/AnamCompanion/README.md). Configure your own URL and machine key locally before building.
