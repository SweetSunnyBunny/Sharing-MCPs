# UI scripts

Run from the parent `ui` folder unless the selected module says otherwise.
Start with [installation](../README.md) or the [code guide](../CODE_GUIDE.md).
Read the module usage before running a helper with your private configuration.

| File | Responsibility and effects |
| --- | --- |
| [anam_context_mcp.py](anam_context_mcp.py) | Runtime adapter: local context MCP and guarded skill staging. |
| [anam_doctor.py](anam_doctor.py) | Read-only installation diagnostic report. |
| [anam_gateway_mcp.py](anam_gateway_mcp.py) | Runtime adapter: stable gateway for provider and machine connectors. |
| [anam_machine_mcp.py](anam_machine_mcp.py) | Runtime adapter: stdio proxy to the configured machine agent. |
| [anam_terminal_host.py](anam_terminal_host.py) | Runtime helper: Windows ConPTY ownership and Ctrl+C handling. |
| [bootstrap.ps1](bootstrap.ps1) | Alternate developer bootstrap: installs dependencies, creates a missing .env and runs tests. Normal installation uses the README locked-uv workflow. |
| [check_anam_gateway.py](check_anam_gateway.py) | Live read-only tool inventory through the adapter. |
| [check_background_provider.py](check_background_provider.py) | Generates a provider test response; --research also requests research. Provider usage may be charged. |
| [check_codex_gateway.py](check_codex_gateway.py) | Live Codex app-server check using configured MCP connections. |
| [check_codex_runtime.py](check_codex_runtime.py) | Isolated diagnostic provider sessions and process cleanup. |
| [check_gateway_parity.py](check_gateway_parity.py) | Live local, machine-agent and bridge adapter agreement. |
| [check_world_feed_activation.py](check_world_feed_activation.py) | Read-only World Feed API paging, accounts and threads. |
| [commons_npc_worker.py](commons_npc_worker.py) | Runtime dialogue worker for a separately installed Commons server. |
| [configure_machine_agent_auth.py](configure_machine_agent_auth.py) | Writes upstream machine-proxy authentication configuration. |
| [export_shareable_ui.py](export_shareable_ui.py) | Maintainer export with explicit source, separate destination and reviewed private profile; supports --dry-run. |
| [gazette_gather.py](gazette_gather.py) | Reads configured local sources and writes a newsletter materials draft. |
| [index_archives.py](index_archives.py) | Imports explicitly configured local archives into Qualia. |
| [index_voice_archive.py](index_voice_archive.py) | Imports explicitly configured voice archives into Qualia. |
| [install_anam_gateway_connector.py](install_anam_gateway_connector.py) | Stages connector source by default; --apply installs the named files. |
| [mcp_duplicate_audit.py](mcp_duplicate_audit.py) | Reports duplicate MCP names; --apply changes resolution rules. |
| [pack-browser.ps1](pack-browser.ps1) | Opens the selected browser profile and debugging port; see the bridge walkthrough. |
| [refresh_server_map.py](refresh_server_map.py) | Reads Discord metadata and writes the local map/database/search indexes. |
| [restart_anam_when_idle.py](restart_anam_when_idle.py) | Queues a restart for an external supervisor after its idle check; supervisor not included. |
| [setup_speech_engines.py](setup_speech_engines.py) | Creates or updates ElevenLabs resources and local IDs; supports --dry-run. |
| [smoke_startup.py](smoke_startup.py) | Imports app/configuration without starting lifespan; does not prove account login. |

Live diagnostics can make network requests and start provider processes.
Optional integrations need installer-owned credentials or content; see
[external inputs](../EXTERNAL_INPUTS.md). Keep filenames stable because runtime
configuration may refer to their exact paths.
