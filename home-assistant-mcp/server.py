'Local mcp adapter.'

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from fastmcp import FastMCP


logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("ha-mcp")

HA_URL = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

if not HA_TOKEN:
    log.warning("HA_TOKEN is empty - all tool calls will fail with 401")
if not HA_URL:
    log.warning("HA_URL is empty - all tool calls will fail")

mcp = FastMCP("home-assistant")


def _headers():
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


def _safe_path(*segments):
    return "/".join(urllib.parse.quote(str(segment), safe="") for segment in segments)


def _ha_request(method, path, data=None):
    'Ha request.'
    url = f"{HA_URL}/api/{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=_headers(), method=method)
    log.info("%s /api/%s", method, path)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if not raw:
                return {"status": "ok"}
            if "application/json" in content_type:
                return json.loads(raw)

            text = raw.decode("utf-8", errors="replace").strip()
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return {"rendered": text}
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        log.error("%s /api/%s -> %s %s: %s", method, path, e.code, e.reason, err_body[:300])
        return {"error": f"HTTP {e.code}: {e.reason}", "detail": err_body[:500]}
    except Exception as e:
        log.error("%s /api/%s failed: %s", method, path, e)
        return {"error": str(e)}


def ha_get(path):
    return _ha_request("GET", path)


def ha_post(path, data=None):
    return _ha_request("POST", path, data)


def _truncate_result(text, limit=50000):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for marker in ("\n  },", "\n},", "\n]", "\n  }"):
        idx = cut.rfind(marker)
        if idx > limit // 2:
            return cut[: idx + len(marker)] + f"\n... (truncated, {len(text)} total chars)"
    return cut + f"\n... (truncated, {len(text)} total chars)"


def _respond(result):
    return _truncate_result(json.dumps(result, indent=2, default=str))


@mcp.tool()
def ha_get_states() -> str:
    'Ha get states.'
    return _respond(ha_get("states"))


@mcp.tool()
def ha_get_entity(entity_id: str) -> str:
    'Ha get entity.'
    return _respond(ha_get(f"states/{_safe_path(entity_id)}"))


@mcp.tool()
def ha_get_entity_attributes(entity_id: str) -> str:
    'Ha get entity attributes.'
    result = ha_get(f"states/{_safe_path(entity_id)}")
    if isinstance(result, dict) and "error" in result:
        return _respond(result)
    if not isinstance(result, dict):
        return _respond({"error": "Unexpected entity response shape"})
    return _respond({
        "entity_id": result.get("entity_id", entity_id),
        "attributes": result.get("attributes", {}),
    })


@mcp.tool()
def ha_get_entities_by_domain(domain: str) -> str:
    'Ha get entities by domain.'
    domain = domain.lower().strip()
    states = ha_get("states")
    if isinstance(states, dict) and "error" in states:
        return _respond(states)
    return _respond([s for s in states if s.get("entity_id", "").startswith(f"{domain}.")])


@mcp.tool()
def ha_call_service(
    domain: str,
    service: str,
    entity_id: str | None = None,
    extra_data: dict | None = None,
) -> str:
    'Ha call service.'
    data = {}
    if entity_id:
        data["entity_id"] = entity_id
    if extra_data:
        data.update(extra_data)
    return _respond(ha_post(f"services/{_safe_path(domain, service)}", data))


@mcp.tool()
def ha_get_services() -> str:
    'Ha get services.'
    return _respond(ha_get("services"))


@mcp.tool()
def ha_get_areas() -> str:
    'Ha get areas.'
    return _respond(ha_post("template", {"template": "{{ areas() | list | tojson }}"}))


@mcp.tool()
def ha_fire_event(event_type: str, event_data: dict | None = None) -> str:
    'Ha fire event.'
    return _respond(ha_post(f"events/{_safe_path(event_type)}", event_data or {}))


@mcp.tool()
def ha_get_history(entity_id: str) -> str:
    'Ha get history.'
    query = urllib.parse.urlencode({"filter_entity_id": entity_id})
    return _respond(ha_get(f"history/period?{query}&minimal_response"))


@mcp.tool()
def ha_search_entities(query: str) -> str:
    'Ha search entities.'
    q = query.lower()
    states = ha_get("states")
    if isinstance(states, dict) and "error" in states:
        return _respond(states)
    return _respond([
        {"entity_id": s["entity_id"], "state": s["state"], "friendly_name": s.get("attributes", {}).get("friendly_name", "")}
        for s in states
        if q in s.get("entity_id", "").lower() or q in s.get("attributes", {}).get("friendly_name", "").lower()
    ])


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "http":
        port = int(os.environ.get("MCP_PORT", "8812"))
        log.info("Starting Home Assistant MCP server over HTTP on :%s (HA_URL=%s, token=%s)", port, HA_URL, "set" if HA_TOKEN else "MISSING")
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        log.info("Starting Home Assistant MCP server over stdio - HA_URL=%s, token=%s", HA_URL, "set" if HA_TOKEN else "MISSING")
        mcp.run(transport="stdio")
