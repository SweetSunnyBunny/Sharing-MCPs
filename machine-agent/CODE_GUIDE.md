# Machine-agent source guide

The local bridge executes machine tools; MCP proxies expose those tools to clients. Start with the two call paths, then follow a concrete tool.

## Request flow

Client -> `run_server.py` -> `server_factory.py` -> `proxy_client.py` -> HTTP `/tools/invoke` -> `local_machine_agent.py` -> `local_tool_registry.py` -> the selected adapter.

| Module | What it owns |
| --- | --- |
| [run_server.py](run_server.py) | Proxy selection and stdio/HTTP arguments |
| [tool_specs.py](tool_specs.py) | Public names, argument signatures, descriptions, and ports |
| [server_factory.py](server_factory.py) | Generated FastMCP callables and image/result rehydration |
| [proxy_client.py](proxy_client.py) | Authenticated HTTP client and response/error envelopes |
| [local_machine_agent.py](local_machine_agent.py) | HTTP request validation, dispatch, and responses |
| [local_tool_registry.py](local_tool_registry.py) | Lazy imports, per-module locking, async execution, result serialization |
| [sidecar_processes.py](sidecar_processes.py) | Optional Playwright process ownership and health |
| [obsidian_adapter.mjs](obsidian_adapter.mjs) | Node bridge for Obsidian tools |

The generated callables preserve explicit signatures for MCP schema generation. Keep `tool_specs.py` and the registry mapping synchronized when adding a tool. Anam discovery/invocation tools delegate to Anam's maintained gateway script rather than duplicating its inventory.

## Local adapters

[tools/](tools/) contains `clipboard/`, `desktop-control/`, `filesystem/`, `krita/`, `obsidian/`, and `terminal/`. Follow the binding in `LOCAL_TOOLS` to the exact module/function; directory names alone are not the public tool registry. Some compatibility proxy names are backed outside this folder.

Read an adapter's own README before changing its state/config paths. The Obsidian folder has additional local instructions. Krita includes application-side plugin code as well as the MCP caller.

## Configuration and process boundaries

`MACHINE_AGENT_HOST`/`MACHINE_AGENT_PORT` configure the local bridge (loopback/8811 by default). `MACHINE_AGENT_API_KEY` supplies the credential; this installation also falls back to Anam's private runtime key file. Authentication is required for invocation. `GET /health` is a health endpoint, not evidence that a tool completed.

Proxies use `MACHINE_AGENT_BASE_URL`, `MACHINE_AGENT_API_KEY`, and `MACHINE_AGENT_TIMEOUT`. Their runner accepts `MACHINE_AGENT_SERVER`, `MCP_TRANSPORT`, `HOST`, and `PORT`. `MACHINE_AGENT_MANAGE_PLAYWRIGHT` and `PLAYWRIGHT_MCP_PORT` control the sidecar.

[scripts/](scripts/) contains process, tunnel, and startup-task helpers. [configs/](configs/) contains client examples. [cloudflare/](cloudflare/README.md) is a deployment scaffold. Launching the local bridge can start a Playwright sidecar, so it is not an offline test.

## Focused test

Run from this package directory so local imports resolve:

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_local_tool_registry.py
```

The test uses temporary adapter modules to prove that a slow module import does not block an unrelated one. It does not exercise real clipboard, desktop, shell, or vault actions.
