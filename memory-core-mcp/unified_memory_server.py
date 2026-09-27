"""Memory Core server with explicitly configured optional local plugin support.

Current Qualia runs separately through qualia-mcp or mind-backend. It is not a
local register_qualia_tools plugin. No private sibling directory is imported.
"""
from __future__ import annotations
import importlib.util
import logging
import os
from pathlib import Path
from fastmcp import FastMCP

log = logging.getLogger(__name__)


def build_server() -> FastMCP:
    from memory_core_server import register_memory_core_tools
    server = FastMCP("unified-memory")
    count = register_memory_core_tools(server)
    log.info("Registered %s Memory Core tools", count)
    # This is an optional legacy extension, never a required sibling checkout.
    configured = os.getenv("COMPANION_MEMORY_PLUGIN_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve() / "companion_memory_server.py"
        if not path.is_file():
            raise RuntimeError("COMPANION_MEMORY_PLUGIN_DIR must contain companion_memory_server.py")
        spec = importlib.util.spec_from_file_location("companion_memory_server", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load the configured companion-memory plugin")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register = getattr(module, "register_companion_mind_tools", None)
        if not callable(register):
            raise RuntimeError("Configured plugin must export register_companion_mind_tools")
        register(server)
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_server().run()
