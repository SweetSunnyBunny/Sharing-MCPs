"""Read-only live inventory audit through the same adapter the providers use."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.anam_gateway_mcp import anam_discover, anam_invoke


async def main():
    first = await anam_discover(limit=1)
    semaphore = asyncio.Semaphore(3)
    reports = []
    async def check(server):
        async with semaphore:
            try:
                catalog = await anam_discover(server=server, limit=50)
                reports.append({"server": server, "tools": catalog["total"], "ok": True})
                print(server, catalog["total"], "tools", flush=True)
            except Exception as exc:
                reports.append({"server": server, "ok": False, "error_type": type(exc).__name__})
                print(server, "FAILED", type(exc).__name__, flush=True)
    await asyncio.gather(*(check(name) for name in first["servers"]))
    output = ROOT / "data/runtime/gateway-parity-check.json"
    output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print("Summary:", sum(r["ok"] for r in reports), "of", len(reports), "servers discovered")


if __name__ == "__main__":
    asyncio.run(main())
