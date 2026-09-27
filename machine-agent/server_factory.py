from __future__ import annotations

import base64
from typing import Any

try:
    from fastmcp import FastMCP
    from fastmcp.utilities.types import Image
except ImportError:
    from mcp.server.fastmcp import FastMCP
    Image = None

from proxy_client import invoke_tool
from tool_specs import SERVERS


def _build_signature(params: list[dict[str, str]]) -> str:
    parts = []
    for param in params:
        part = f"{param['name']}: {param['type']}"
        if "default" in param:
            part += f" = {param['default']}"
        parts.append(part)
    return ", ".join(parts)


def _build_arguments(params: list[dict[str, str]]) -> str:
    return ", ".join(f'"{param["name"]}": {param["name"]}' for param in params)


def _rehydrate_result(result: Any) -> Any:
    """Convert machine-agent transport envelopes back into native MCP content."""
    if isinstance(result, dict) and result.get("type") == "image":
        payload = result.get("data") or result.get("base64")
        if not payload:
            raise RuntimeError("Machine agent returned an image without base64 data")
        if Image is None:
            raise RuntimeError("FastMCP Image support is unavailable in this proxy")

        raw = base64.b64decode(payload)
        mime_type = str(result.get("mimeType") or result.get("mime_type") or "image/png")
        image_format = mime_type.split("/", 1)[-1].lower()
        if image_format == "jpg":
            image_format = "jpeg"
        return Image(data=raw, format=image_format)

    if isinstance(result, list):
        return [_rehydrate_result(item) for item in result]
    if isinstance(result, dict):
        return {key: _rehydrate_result(value) for key, value in result.items()}
    return result


def create_server(server_name: str) -> FastMCP:
    if server_name not in SERVERS:
        raise KeyError(f"Unknown server '{server_name}'")

    spec = SERVERS[server_name]
    mcp = FastMCP(spec["display_name"])

    for tool in spec["tools"]:
        signature = _build_signature(tool["params"])
        arguments = _build_arguments(tool["params"])
        source = f'''
@mcp.tool(name="{tool["name"]}")
async def {tool["name"]}({signature}) -> Any:
    """{tool["doc"]}"""
    return _rehydrate_result(await invoke_tool("{tool["name"]}", **{{{arguments}}}))
'''
        scope = {
            "mcp": mcp,
            "invoke_tool": invoke_tool,
            "_rehydrate_result": _rehydrate_result,
            "Any": Any,
        }
        exec(source, scope, scope)

    return mcp
