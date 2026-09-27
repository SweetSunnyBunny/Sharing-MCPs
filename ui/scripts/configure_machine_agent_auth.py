"""Set the existing machine proxies' upstream credential without printing it.

Run before restarting the newly authenticated machine agent. The eight
existing Workers keep their public MCP authentication tokens unchanged.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import os
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKERS = Path(os.environ.get("ANAM_MACHINE_AGENT_ROOT", str(Path(__file__).resolve().parents[2] / "machine-agent"))) / "cloudflare" / "workers"
NAMES = ["clipboard", "desktop-control", "terminal", "filesystem", "krita", "muse-tts", "books-tools", "obsidian"]


def command(args, payload=None):
    result = subprocess.run(["cmd", "/c", "wrangler", *args], cwd=WORKERS,
                            input=payload, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise RuntimeError("Wrangler failed for " + args[-1] + "; credential was not printed")
    return result.stdout


def verify(name):
    result = command(["secret", "list", "--name", name + "-mcp"])
    parsed = json.loads(result[result.index("["):])
    if "MCP_AUTH_TOKEN" not in {entry["name"] for entry in parsed}:
        raise RuntimeError(name + " is missing its existing public MCP auth token")
    return name


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name in pool.map(verify, NAMES):
            print("Verified public authentication: " + name, flush=True)
    path = ROOT / "data/runtime/machine-agent.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    key = path.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise RuntimeError("Machine key must contain at least 32 characters")
    # Deploy secrets sequentially; a failure stops before the local auth gate
    # is enabled, so the existing proxies continue to operate during setup.
    for name in NAMES:
        command(["secret", "bulk", "--name", name + "-mcp"], json.dumps({"MACHINE_AGENT_KEY": key}))
        print("Configured upstream authentication: " + name, flush=True)


if __name__ == "__main__":
    main()
