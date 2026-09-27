"""Install the Anam doorway into the existing FileSystem+ machine connector.

Default mode stages a reviewable copy under data/gateway-connector-staging.
--apply writes only the explicitly named connector source files; deploy
and supervised restarts are separate operations. No credentials are changed.
"""
from pathlib import Path
import os
import argparse
import json

ROOT = Path(__file__).resolve().parents[1]
AGENT = Path(os.environ.get("ANAM_MACHINE_AGENT_ROOT", str(Path(__file__).resolve().parents[2] / "machine-agent")))
STAGE = ROOT / "data" / "gateway-connector-staging"
NAMES = ["anam_discover", "anam_invoke", "anam_job", "anam_result"]


def replace_once(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError("Connector source changed; review the expected edit before installing")
    return text.replace(old, new, 1)


def specs():
    return [
        {"name": "anam_discover", "doc": "Discover live Anam tools and exact input schemas: Hearth, Commons, Qualia Studio, saved reply canvases, history, computer control, and browser profiles. Search words or select a server; do this before anam_invoke.", "annotations": {"readOnlyHint": True, "openWorldHint": True}, "params": [
            {"name": "query", "type": "str", "default": '""'},
            {"name": "server", "type": "str", "default": '""'},
            {"name": "offset", "type": "int", "default": "0"},
            {"name": "limit", "type": "int", "default": "15"},
            {"name": "include_schema", "type": "bool", "default": "True"}]},
        {"name": "anam_invoke", "doc": "Execute a discovered Anam tool once. May read, write, communicate, or control the computer. Use exact server/tool and arguments_json matching its schema, plus your active identity. Give each action a unique request_id; reuse it only on transport retry. Collect running jobs with anam_job; do not resubmit.", "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}, "params": [
            {"name": "server", "type": "str"}, {"name": "tool", "type": "str"},
            {"name": "arguments_json", "type": "str"}, {"name": "identity", "type": "str"},
            {"name": "conversation_id", "type": "str", "default": '""'},
            {"name": "request_id", "type": "str", "default": '""'}]},
        {"name": "anam_job", "doc": "Collect a running Anam tool result without repeating its action. Preserves native images/audio. Completed results survive restarts for up to seven days within a bounded store.", "annotations": {"readOnlyHint": True, "openWorldHint": False}, "params": [
            {"name": "job_id", "type": "str"}]},
        {"name": "anam_result", "doc": "Read or search the full stored text of a completed Anam tool result without repeating its action. Use next_offset for another page, or query to find relevant text.", "annotations": {"readOnlyHint": True, "openWorldHint": False}, "params": [
            {"name": "job_id", "type": "str"},
            {"name": "offset", "type": "int", "default": "0"},
            {"name": "limit", "type": "int", "default": "8000"},
            {"name": "query", "type": "str", "default": '""'}]},
    ]


def prepare():
    outputs = {}
    registry = (AGENT / "local_tool_registry.py").read_text(encoding="utf-8")
    bindings = "".join(f'    "{name}": ToolBinding(module_path=Path({str(ROOT / "scripts" / "anam_gateway_mcp.py")!r}), function_name="{name}"),\n' for name in NAMES if f'"{name}": ToolBinding' not in registry)
    outputs["local_tool_registry.py"] = replace_once(registry, "LOCAL_TOOLS: dict[str, ToolBinding] = {\n", "LOCAL_TOOLS: dict[str, ToolBinding] = {\n" + bindings)

    py = (AGENT / "tool_specs.py").read_text(encoding="utf-8")
    marker = "# Anam live tool doorway (stable schema; live inventory comes from Anam)."
    if marker in py:
        py = py[:py.index(marker)].rstrip()
    if marker not in py:
        py += "\n" + marker + "\nSERVERS['filesystem']['tools'].extend(" + repr(specs()) + ")\n"
    outputs["tool_specs.py"] = py
    js = (AGENT / "cloudflare/workers/tool-specs.js").read_text(encoding="utf-8")
    if marker in js:
        js = js[:js.index("// " + marker)].rstrip()
    if marker not in js:
        js += "\n// " + marker + "\nSERVERS.filesystem.tools.push(..." + json.dumps(specs(), indent=2) + ");\n"
    outputs["cloudflare/workers/tool-specs.js"] = js

    worker = (AGENT / "cloudflare/workers/mcp-worker.js").read_text(encoding="utf-8")
    worker = replace_once(worker, '    description: tool.doc,\n', '    description: tool.doc,\n    ...(tool.annotations ? { annotations: tool.annotations } : {}),\n')
    worker = replace_once(worker, '  if (result && result.type === "image") {', '''  // The Anam gateway already returns validated MCP content, not a JSON
  // description of an image. Keep all block types and structured results.
  if (result?.status === "completed" && Array.isArray(result.content)) {
    return result.content;
  }
  if (result && result.type === "image") {''')
    worker = replace_once(worker, '          content: toolResultContent(result),', '''          content: toolResultContent(result),
          ...(result?.status === "completed" ? {
            isError: !!result.isError,
            ...(result.structuredContent ? { structuredContent: result.structuredContent } : {}),
          } : {}),''')
    worker = worker.replace('["anam_discover", "anam_invoke", "anam_job", "anam_result"]', '["anam_discover", "anam_invoke", "anam_job"]')
    worker = replace_once(worker, '  const url = `${env.MACHINE_AGENT_URL}/tools/invoke`;', '''  if (["anam_discover", "anam_invoke", "anam_job"].includes(toolName) &&
      (!env.MCP_AUTH_TOKEN || !env.MACHINE_AGENT_KEY)) {
    throw new Error("Anam gateway requires authenticated connector and machine-agent credentials");
  }
  const url = `${env.MACHINE_AGENT_URL}/tools/invoke`;''')
    worker = worker.replace('["anam_discover", "anam_invoke", "anam_job"]', '["anam_discover", "anam_invoke", "anam_job", "anam_result"]')
    worker = replace_once(worker, '    if (p.description) prop.description = p.description;', """    if ("default" in p) {
      const literal = {True: "true", False: "false", None: "null"}[p.default] ?? p.default;
      try { prop.default = JSON.parse(literal); } catch { /* Non-JSON Python defaults remain optional. */ }
    }
    if (p.description) prop.description = p.description;""")
    outputs["cloudflare/workers/mcp-worker.js"] = worker

    agent = (AGENT / "local_machine_agent.py").read_text(encoding="utf-8")
    agent = replace_once(agent, 'import argparse\n', 'import argparse\nimport hmac\n')
    agent = replace_once(agent, 'import os\n', 'import os\nfrom pathlib import Path\n')
    agent = replace_once(agent, 'API_KEY = os.environ.get("MACHINE_AGENT_API_KEY", "")',
                         'API_KEY = os.environ.get("MACHINE_AGENT_API_KEY", "") or Path(' + repr(str(ROOT / "data/runtime/machine-agent.key")) + ').read_text(encoding="utf-8").strip()\nif not API_KEY:\n    raise RuntimeError("Machine agent requires authentication")')
    agent = replace_once(agent, '            if auth != expected:', '            if not hmac.compare_digest(auth.encode(), expected.encode()):')
    agent = agent.replace('{"anam_discover", "anam_invoke", "anam_job", "anam_result"}', '{"anam_discover", "anam_invoke", "anam_job"}')
    agent = replace_once(agent, '        arguments = payload.get("arguments", {})', '''        arguments = payload.get("arguments", {})
        if tool_name in {"anam_discover", "anam_invoke", "anam_job"} and not API_KEY:
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Machine authentication required"})
            return''')
    agent = agent.replace('{"anam_discover", "anam_invoke", "anam_job"}', '{"anam_discover", "anam_invoke", "anam_job", "anam_result"}')
    outputs["local_machine_agent.py"] = agent

    proxy = (AGENT / "proxy_client.py").read_text(encoding="utf-8")
    proxy = replace_once(proxy, 'import os\n', 'import os\nfrom pathlib import Path\n')
    proxy = replace_once(proxy, 'api_key=os.environ.get("MACHINE_AGENT_API_KEY", ""),',
                         'api_key=os.environ.get("MACHINE_AGENT_API_KEY", "") or (Path(' + repr(str(ROOT / "data/runtime/machine-agent.key")) + ').read_text(encoding="utf-8").strip() if Path(' + repr(str(ROOT / "data/runtime/machine-agent.key")) + ').is_file() else ""),')
    outputs["proxy_client.py"] = proxy
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    outputs = prepare()
    # Prepare every edit before writing any source file.
    for relative, content in outputs.items():
        target = (AGENT if args.apply else STAGE) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(("Installed " if args.apply else "Staged ") + relative)


if __name__ == "__main__":
    main()
