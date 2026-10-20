"""Expose the existing authenticated machine agent to Anam over local stdio.

The persistent shell remains owned by machine-agent, not this disposable proxy.
Exclude the gateway entrypoints here to avoid recursive Anam-to-Anam calls.
"""
import sys
import base64
from pathlib import Path
import os

MACHINE_ROOT = Path(os.environ.get("ANAM_MACHINE_AGENT_ROOT", str(Path(__file__).resolve().parents[2] / "machine-agent")))


def rehydrate(result):
    from fastmcp.utilities.types import Image
    if isinstance(result, dict):
        # fs_get_file_info also uses type=image for ordinary file metadata.
        if result.get("type") == "image" and not (result.get("success") is True and result.get("is_file") is True):
            payload = result.get("data") or result.get("base64")
            if not payload:
                raise ValueError("Image payload is missing")
            mime = result.get("mimeType") or result.get("mime_type") or "image/png"
            return Image(data=base64.b64decode(payload, validate=True), format=mime.split("/")[-1])
        return {k: rehydrate(v) for k, v in result.items()}
    if isinstance(result, list):
        return [rehydrate(v) for v in result]
    return result


def build_server(server):
    if server not in {"filesystem", "terminal"}:
        raise ValueError("Expected filesystem or terminal")
    sys.path.insert(0, str(MACHINE_ROOT))
    import server_factory
    from tool_specs import SERVERS
    spec = SERVERS[server]
    SERVERS[server] = {**spec, "tools": [t for t in spec["tools"]
        if t["name"] not in {"anam", "anam_discover", "anam_invoke", "anam_job", "anam_result"}]}
    server_factory._rehydrate_result = rehydrate
    return server_factory.create_server(server)


if __name__ == "__main__":
    build_server(sys.argv[1]).run(transport="stdio")
