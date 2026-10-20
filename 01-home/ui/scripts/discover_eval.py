"Score the live gateway's discover against the pack's real plain-words queries."
import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "discover_eval.json"
URL = "http://127.0.0.1:8790/api/tool-gateway"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", default="juniper")
    parser.add_argument("--top", type=int, default=0, help="defaults to the fixture's top_n")
    args = parser.parse_args()

    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    top_n = args.top or spec.get("top_n", 5)
    key = os.getenv("ANAM_API_KEY") or dotenv_values(ROOT / ".env").get("ANAM_API_KEY")
    if not key:
        sys.exit("ANAM_API_KEY is not configured")

    hits = 0
    with httpx.Client(timeout=75, trust_env=False) as client:
        for row in spec["rows"]:
            response = client.post(URL, headers={"Authorization": f"Bearer {key}"}, json={
                "operation": "discover", "query": row["query"], "limit": 50,
                "include_schema": False, "identity": args.identity,
            })
            response.raise_for_status()
            names = [t["name"] for t in response.json().get("tools", [])]
            ranks = [names.index(n) + 1 for n in row["expect"] if n in names]
            best = min(ranks) if ranks else None
            ok = best is not None and best <= top_n
            hits += ok
            where = f"#{best}" if best else "not in top 50"
            top3 = ", ".join(names[:3])
            print(f"{'PASS' if ok else 'MISS'}  {where:>14}  [{row['by']}] {row['query']}")
            if not ok:
                print(f"{'':22}wanted {'/'.join(row['expect'])}; got {top3}")
    print(f"\n{hits}/{len(spec['rows'])} in the top {top_n}")


if __name__ == "__main__":
    main()
