"""Provider-neutral access to Anam's configured tools and local context.

The public machine connector and local Codex MCP adapter use the same API.
Only configured servers are routable; connection credentials never enter the
catalog. Calls execute once and survive HTTP timeouts as bounded jobs.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import re
import socket
import time
import uuid
import weakref

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from jsonschema import Draft202012Validator

from config import IDENTITIES
from services.mcp_bridge import mcp_bridge
from services import connector_health, tool_result_store, tool_discovery, runtime_metrics
from services.log_redaction import redact_text

log = logging.getLogger(__name__)
_JOBS: dict[str, dict] = {}
_MAX_JOBS = 1024
_MAX_ACTIVE = 16
_DISPATCH_LOCKS = weakref.WeakKeyDictionary()
_JOB_TTL = 3600
_WAIT_SECONDS = 20
_RECURSIVE_TOOLS = {"anam", "anam_discover", "anam_invoke", "anam_job", "anam_result"}
_INTERNAL_TOOLS = {
    ("browser", "browser_open"),
    ("browser", "browser_close"),
    ("browser", "browser_status"),
}
_IDENTITY_SERVER_PREFIXES = ("playwright-", "vox-")
_SHARED_IDENTITIES = {"pack"}
_STALE_CLIENTS: dict[str, object] = {}
_BROWSER_LEASES: dict[tuple[str, str], str] = {}
_BROWSER_IDLE_TASKS: dict[tuple[str, str], asyncio.Task] = {}
_BROWSER_IDLE_SECONDS = 120
# Administrative and destructive Qualia tools live behind the Systems drawer.
# Ordinary Mind > Qualia discovery should surface remembering, reflection, and
# continuity tools without casually offering database housekeeping operations.
_MIND_MAINTENANCE_TOOLS = {
    "mind_consolidate",
    "mind_delete",
    "mind_edit",
    "mind_evidence",
    "mind_export",
    "mind_health",
    "mind_hint_add",
    "mind_hint_list",
    "mind_index_audio",
    "mind_index_document",
    "mind_index_images",
    "mind_index_journal_entries",
    "mind_mutations",
    "mind_orphans",
    "mind_proposals",
    "mind_queue_packet",
    "mind_recall_feedback",
    "mind_recent_handoffs",
    "mind_recent_packets",
    "mind_record_handoff",
    "mind_resolve",
    "mind_schema_status",
    "mind_update_identity_routing",
}
_MIND_MAINTENANCE_INTENTS = {
    "maintenance", "housekeeping", "consolidate", "consolidation",
    "delete", "edit", "mutation", "mutations", "orphan", "orphans",
    "proposal", "proposals", "index", "indexing", "schema", "routing",
}
_MIND_MAINTENANCE_DRAWER_QUERIES = {
    "mind maintenance",
    "nightly consolidation",
    "memory housekeeping",
    "qualia maintenance",
}


def _configured():
    active, _, _ = mcp_bridge._load_config(
        emit_logs=False, apply_direct_bridge_skips=False,
    )
    # This adapter calls us; exposing it here would create recursive calls.
    return {name: cfg for name, cfg in active.items() if name != "anam-gateway"}


def _identity_server_owner(server: str) -> str:
    lowered = str(server or "").strip().lower()
    for prefix in _IDENTITY_SERVER_PREFIXES:
        if lowered.startswith(prefix):
            return lowered[len(prefix):]
    return ""


def _server_visible_to_identity(server: str, identity: str, *, specialist: bool = False) -> bool:
    """Keep private profiles scoped and Vox closed unless chosen explicitly."""
    identity_key = str(identity or "").strip().lower()
    if not identity_key:
        return True
    owner = _identity_server_owner(server)
    if not owner:
        return True
    if owner != identity_key:
        return False
    if str(server).lower().startswith("vox-") and not specialist:
        return False
    return True


def _mind_maintenance_requested(query: str) -> bool:
    normalized = str(query or "").strip().lower()
    if normalized in _MIND_MAINTENANCE_TOOLS:
        return True
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return bool(tokens & _MIND_MAINTENANCE_INTENTS)


def _mind_maintenance_drawer_query(query: str) -> bool:
    return str(query or "").strip().lower() in _MIND_MAINTENANCE_DRAWER_QUERIES


def _tool_visible_in_drawer(server: str, tool: str, *, mind_maintenance: bool) -> bool:
    if str(server).lower() != "qualia-backend":
        return True
    is_maintenance = str(tool).lower() in _MIND_MAINTENANCE_TOOLS
    return is_maintenance if mind_maintenance else not is_maintenance


@asynccontextmanager
async def _client(server: str):
    cfg = _configured().get(server)
    if cfg is None:
        raise ValueError("Unknown or disabled server; use anam_discover first")
    live = mcp_bridge._clients.get(server)
    if live is not None and live is not _STALE_CLIENTS.get(server):
        yield live
        return
    # Identity browser profiles suppressed in the flattened API tool list are
    # still available by their explicit server name, as they are in Codex.
    if cfg.get("url"):
        headers = dict(cfg.get("headers") or cfg.get("http_headers") or cfg.get("httpHeaders") or {})
        token_var = cfg.get("bearer_token_env_var") or cfg.get("bearerTokenEnvVar")
        if token_var and os.getenv(token_var):
            headers["Authorization"] = f"Bearer {os.environ[token_var]}"
        for key, var in (cfg.get("env_http_headers") or cfg.get("envHttpHeaders") or {}).items():
            if os.getenv(var):
                headers[key] = os.environ[var]
        from services.cc_mcp_oauth import auth_for
        transport = StreamableHttpTransport(cfg["url"], headers=headers,
                                            auth=auth_for(server, cfg["url"], headers))
    else:
        transport = StdioTransport(command=cfg["command"], args=cfg.get("args", []),
                                   env=cfg.get("env"), cwd=cfg.get("cwd"))
    async with Client(transport, timeout=45) as client:
        yield client


@asynccontextmanager
async def _catalog_client(server: str):
    # A dead cached HTTP session can still be present in mcp_bridge._clients.
    # Only catalog lookup is safe to retry. Once yielded, the caller's tool
    # invocation must propagate failures without replaying the action.
    async with _client(server) as client:
        try:
            catalog = await client.list_tools()
        except Exception as exc:
            connector_health.observe(server, error=exc)
            if client is not mcp_bridge._clients.get(server):
                raise
            _STALE_CLIENTS[server] = client
            log.warning("Gateway cached catalog unavailable for %s; opening a fresh connection before dispatch", server)
        else:
            connector_health.observe(server)
            yield client, catalog
            return
    # Do not close or evict the shared client: another caller may own a request.
    # A later bridge reconnect replaces its object and becomes eligible again.
    async with _client(server) as client:
        try:
            catalog = await client.list_tools()
        except Exception as exc:
            connector_health.observe(server, error=exc)
            raise
        connector_health.observe(server)
        yield client, catalog


async def _local_tools():
    from scripts.anam_context_mcp import mcp
    return await mcp.get_tools()


def _playwright_port(server: str) -> int | None:
    cfg = _configured().get(server) or {}
    haystack = " ".join(str(value) for value in (cfg.get("args") or []))
    match = re.search(r"(?:localhost|127\.0\.0\.1):([0-9]{2,5})", haystack)
    return int(match.group(1)) if match else None


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


async def _browser_control(tool: str, identity: str) -> dict:
    if "browser" not in _configured():
        raise ValueError("Browser lifecycle controller is unavailable")
    async with _catalog_client("browser") as (client, tools):
        schemas = {item.name: item for item in tools}
        if tool not in schemas:
            raise ValueError(f"Browser lifecycle controller lacks {tool}")
        args = _arguments(schemas[tool].inputSchema, {"identity": identity}, identity)
        result = await client.call_tool(tool, args, timeout=120, raise_on_error=False)
    envelope = _envelope(result)
    if envelope.get("isError"):
        raise ValueError(f"Browser lifecycle {tool} failed")
    return envelope


def _touch_browser_lease(identity: str, conversation_id: str) -> None:
    key = (identity.lower(), str(conversation_id or ""))
    token = uuid.uuid4().hex
    _BROWSER_LEASES[key] = token
    old = _BROWSER_IDLE_TASKS.pop(key, None)
    if old and not old.done():
        old.cancel()

    async def expire() -> None:
        try:
            await asyncio.sleep(_BROWSER_IDLE_SECONDS)
            if _BROWSER_LEASES.get(key) == token:
                await close_browsers_for_turn(identity, conversation_id)
        except asyncio.CancelledError:
            return

    _BROWSER_IDLE_TASKS[key] = asyncio.create_task(expire())


async def _ensure_identity_browser(server: str, identity: str, conversation_id: str) -> None:
    """Open the current identity's Chrome lazily before a Playwright leaf."""
    if not str(server).lower().startswith("playwright-"):
        return
    port = _playwright_port(server)
    if port is None:
        raise ValueError("Playwright profile has no configured CDP port")
    if not await asyncio.to_thread(_port_open, port):
        await _browser_control("browser_open", identity)
        for _ in range(80):
            if await asyncio.to_thread(_port_open, port):
                break
            await asyncio.sleep(0.25)
        else:
            raise ValueError("Identity browser did not become ready after automatic startup")
    _touch_browser_lease(identity, conversation_id)


async def close_browsers_for_turn(identity: str, conversation_id: str) -> bool:
    """Release one turn's browse lease and close when no sibling lease remains."""
    key = (str(identity or "").lower(), str(conversation_id or ""))
    if key not in _BROWSER_LEASES:
        return False
    _BROWSER_LEASES.pop(key, None)
    task = _BROWSER_IDLE_TASKS.pop(key, None)
    current = asyncio.current_task()
    if task and task is not current and not task.done():
        task.cancel()
    if any(owner == key[0] for owner, _conversation in _BROWSER_LEASES):
        return False
    await _browser_control("browser_close", identity)
    port = _playwright_port(f"playwright-{key[0]}")
    if port is not None:
        for _ in range(40):
            if not await asyncio.to_thread(_port_open, port):
                break
            await asyncio.sleep(0.25)
        else:
            raise ValueError("Identity browser remained open after automatic close")
    return True


def _visible(server, name):
    return (
        name not in _RECURSIVE_TOOLS
        and (server, name) not in _INTERNAL_TOOLS
        and not mcp_bridge._is_tool_disabled(server, name)
    )


async def discover(query: str = "", server: str = "", offset: int = 0, limit: int = 15,
                   include_schema: bool = True, identity: str = "",
                   conversation_id: str = ""):
    started = time.monotonic()
    servers = _configured()
    query_tokens = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
    identity_key = str(identity or "").strip().lower()
    mind_maintenance = _mind_maintenance_requested(query)
    if not server and mind_maintenance and "qualia-backend" in servers:
        server = "qualia-backend"
    if not server and identity_key and query_tokens & {"browse", "browser", "playwright", "navigate", "webpage"}:
        candidate = f"playwright-{identity_key}"
        if candidate in servers:
            server = candidate
    if not server and identity_key and str(query or "").strip().lower() == "vox":
        candidate = f"vox-{identity_key}"
        if candidate in servers:
            server = candidate
    explicit_specialist = bool(
        (server and str(server).lower().startswith("vox-"))
        or str(query or "").strip().lower() == "vox"
    )
    if server and not _server_visible_to_identity(server, identity, specialist=explicit_specialist):
        raise ValueError("That identity-scoped server is not available to the active identity")
    names = sorted(
        name for name in (set(servers) | {"anam-context"})
        if _server_visible_to_identity(name, identity, specialist=explicit_specialist)
    )
    catalog = []
    if server and server != "anam-context":
        await _ensure_identity_browser(server, identity, conversation_id)
        async with _catalog_client(server) as (client, tools):
            for tool in tools:
                if (_visible(server, tool.name)
                        and _tool_visible_in_drawer(
                            server, tool.name, mind_maintenance=mind_maintenance
                        )):
                    catalog.append({"server": server, "name": tool.name,
                                    "description": tool.description or "", "inputSchema": tool.inputSchema})
    else:
        if not server:
            for tool in mcp_bridge.get_tools():
                owner = mcp_bridge._tool_server_map.get(tool["name"])
                if (owner in servers and _visible(owner, tool["name"])
                        and _server_visible_to_identity(owner, identity, specialist=explicit_specialist)
                        and _tool_visible_in_drawer(
                            owner, tool["name"], mind_maintenance=mind_maintenance
                        )):
                    catalog.append({"server": owner, "name": tool["name"],
                                    "description": tool.get("description", ""),
                                    "inputSchema": tool.get("input_schema", {})})
        for name, tool in (await _local_tools()).items():
            if _visible("anam-context", name):
                catalog.append({"server": "anam-context", "name": name,
                                "description": tool.description, "inputSchema": tool.parameters})
    rank_query = "" if _mind_maintenance_drawer_query(query) else query
    ranked = [(tool_discovery.score(t, rank_query), t) for t in catalog]
    ranked = [(score,t) for score,t in ranked if score > 0]
    ranked.sort(key=lambda item: (-item[0], item[1]['server'], item[1]['name']))
    catalog = [t for _,t in ranked]
    page = [tool_discovery.present(t, include_schema) for t in catalog[offset:offset + limit]]
    runtime_metrics.record('tool_discovery', (time.monotonic()-started)*1000, provider='gateway')
    return {"servers": names, "tools": page, "total": len(catalog),
            "next_offset": offset + limit if offset + limit < len(catalog) else None,
            "hint": (
                "This is the Systems > Mind maintenance drawer for scheduled consolidation and deliberate administration. "
                if mind_maintenance else
                "This is the everyday toolbox. Mind-maintenance tools are kept under Systems and do not appear here. "
            ) + "Search by words or select a server for its fresh drawer catalog. Pass its exact server/name and schema arguments to the Anam invoke operation."}


def _envelope(result):
    content = [b.model_dump(mode="json", by_alias=True, exclude_none=True) for b in result.content]
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    return {"content": content, "isError": bool(getattr(result, "is_error", False) or getattr(result, "isError", False)),
            **({"structuredContent": structured} if structured is not None else {})}


def _arguments(schema, arguments, identity):
    args = dict(arguments)
    prop = schema.get("properties", {}).get("identity")
    canonical = next((name for name in IDENTITIES if name.lower() == identity.lower()), identity)
    requested = str(args.get("identity") or "").strip()
    known_requested = next((name for name in IDENTITIES if name.lower() == requested.lower()), "")
    if prop and known_requested and known_requested.lower() not in _SHARED_IDENTITIES and known_requested.lower() != canonical.lower():
        requested = ""
        args.pop("identity", None)
    if prop and not requested:
        choices = prop.get("enum", [])
        args["identity"] = next((x for x in choices if str(x).lower() == canonical.lower()), canonical)
    errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: str(e.path))
    if errors:
        # Validation messages can echo secret argument values. Report only the
        # field and failed constraint; the caller already has the exact schema.
        error = errors[0]
        raise ValueError(f"Invalid arguments at {'.'.join(map(str, error.path)) or '<root>'}: {error.validator}")
    return args


def _safe_connector_error_detail(exc: Exception) -> str:
    """Expose useful connector diagnostics without echoing arguments or secrets."""
    kind = type(exc).__name__
    if kind in {"ReadTimeout", "ConnectTimeout", "TimeoutError"}:
        return "request timed out"

    details: list[str] = []
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            details.append(f"HTTP {status}")
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            for key in ("code", "error", "message", "detail"):
                value = payload.get(key)
                if isinstance(value, (str, int, float, bool)) and value != "":
                    details.append(f"{key}={redact_text(value)[:600]}")

    # MCP protocol errors generally carry the remote server's structured
    # reason in their exception text. Generic RuntimeError text is omitted: it
    # may contain arbitrary connector payload or a private target URL.
    if kind == "McpError":
        message = redact_text(exc).strip().replace("\r", " ").replace("\n", " ")
        if message:
            details.append(message[:800])
    return "; ".join(dict.fromkeys(details))


async def _execute(server, tool, arguments, identity, conversation_id):
    try:
        if not _visible(server, tool):
            raise ValueError("Tool is disabled or would recursively invoke the gateway")
        if server == "anam-context":
            local = (await _local_tools()).get(tool)
            if local is None:
                raise ValueError("Unknown local tool; discover its schema first")
            result = await local.run(_arguments(local.parameters, arguments, identity))
        else:
            await _ensure_identity_browser(server, identity, conversation_id)
            async with _catalog_client(server) as (client, tools):
                schemas = {t.name: t for t in tools}
                if tool not in schemas:
                    raise ValueError("Unknown tool; discover its schema first")
                default_identity = identity.lower() if "discord" in server else identity
                args = _arguments(schemas[tool].inputSchema, arguments, default_identity)
                # No replay after a timeout or disconnect: a write may already
                # have happened. The job result reports uncertainty honestly.
                result = await client.call_tool(tool, args, timeout=600, raise_on_error=False)
        envelope = _envelope(result)
        connector_health.observe(server, tool=tool, stage="invoke", error=(
            " ".join(b.get("text", "") for b in envelope["content"]) if envelope["isError"] else None))
        log.info("Anam gateway completed identity=%s conversation=%s server=%s tool=%s error=%s",
                 identity, conversation_id, server, tool, envelope["isError"])
        return envelope
    except ValueError as exc:
        connector_health.observe(server, tool=tool, stage="invoke", error=exc)
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    except Exception as exc:
        connector_health.observe(server, tool=tool, stage="invoke", error=exc)
        detail = _safe_connector_error_detail(exc)
        log.warning("Anam gateway failed identity=%s server=%s tool=%s kind=%s detail=%s",
                    identity, server, tool, type(exc).__name__, detail or "unavailable")
        suffix = f": {detail}" if detail else ""
        return {"isError": True, "content": [{"type": "text", "text":
            f"Tool delivery failed ({type(exc).__name__}){suffix}. It was not retried. A write may have completed; inspect the target before repeating it."}]}


async def _execute_recorded(job_id, server, tool, arguments, identity, conversation_id):
    started = time.monotonic()
    result = {"isError": True}
    try:
        result = await _execute(server, tool, arguments, identity, conversation_id)
        await asyncio.to_thread(tool_result_store.finish, job_id, result)
        return result
    except asyncio.CancelledError:
        result = {"isError": True, "content": [{"type":"text", "text":"Execution was interrupted. A write may have completed; inspect its target before retrying."}]}
        await asyncio.shield(asyncio.to_thread(tool_result_store.finish, job_id, result, 'uncertain'))
        raise
    finally:
        runtime_metrics.record('tool_execution', (time.monotonic()-started)*1000, identity=identity, provider=server, ok=not result.get('isError', False))


def _prune():
    now = time.monotonic()
    for key,job in list(_JOBS.items()):
        if job['task'].done() and (now-job['created'] > _JOB_TTL or len(_JOBS) >= _MAX_JOBS):
            del _JOBS[key]  # The durable claim/result remains available.


async def invoke(server: str, tool: str, arguments: dict, identity: str,
                 conversation_id: str = "", request_id: str = ""):
    identity = next((name for name in IDENTITIES if name.lower() == identity.lower()), "")
    if not identity:
        raise ValueError("Use the active Anam identity name")
    if not _server_visible_to_identity(server, identity, specialist=True):
        raise ValueError("That identity-scoped server is not available to the active identity")
    if server != "anam-context" and server not in _configured():
        raise ValueError("Unknown or disabled server")
    if not _visible(server, tool):
        raise ValueError("Tool is disabled or would recursively invoke the gateway")
    _prune()
    fingerprint = hashlib.sha256(json.dumps([server, tool, arguments, identity, conversation_id], sort_keys=True).encode()).hexdigest()
    job_id = request_id or uuid.uuid4().hex
    # Keep claim and task registration together even if the HTTP caller disconnects.
    async def dispatch():
        lock = _DISPATCH_LOCKS.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            available = sum(not j['task'].done() for j in _JOBS.values()) < _MAX_ACTIVE
            created = await asyncio.to_thread(tool_result_store.claim, job_id, fingerprint,
                                             identity, conversation_id, available)
            if created:
                _JOBS[job_id] = {'created':time.monotonic(), 'task':asyncio.create_task(
                    _execute_recorded(job_id, server, tool, arguments, identity, conversation_id))}
    dispatch_task = asyncio.create_task(dispatch())
    dispatch_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    await asyncio.shield(dispatch_task)
    return await job_result(job_id, identity=identity, conversation_id=conversation_id)


async def job_result(job_id: str, identity: str = '', conversation_id: str = ''):
    _prune()
    job = _JOBS.get(job_id)
    if job:
        await asyncio.wait({job['task']}, timeout=_WAIT_SECONDS)
    result = await asyncio.to_thread(tool_result_store.read, job_id, identity, conversation_id)
    return tool_result_store.compact(result)


async def result_page(job_id: str, offset: int = 0, limit: int = 8000, query: str = '',
                      identity: str = '', conversation_id: str = ''):
    return await asyncio.to_thread(tool_result_store.page, job_id, offset, limit, query, identity, conversation_id)
