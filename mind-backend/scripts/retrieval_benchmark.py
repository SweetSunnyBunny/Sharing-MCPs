"""
Retrieval benchmark harness for qualia-backend mind_search.

The point: stop *guessing* whether a ranking change helped. Define a set of
(query -> expected memory) cases once, then measure hit@k, recall, and MRR. Run
it before and after a change, or A/B two retrieval profiles in a single pass, and
read the uplift off a table instead of vibes.

Eval cases live in a JSON file (see retrieval_eval_set.example.json). Each case:

    {
      "query": "what did Avery say about the gold?",
      "identity": "avery",
      "expect_any": ["the gold", "shield around"],   # >=1 must appear (a "hit")
      "expect_all": [],                                # all must appear (recall=1)
      "limit": 5
    }

"expect_*" entries are matched case-insensitively against the returned result
text. A case "hits" if any expect_any (or, when empty, any expect_all) substring
shows up in the top-k; recall is the fraction of expect_all found; MRR uses the
rank of the first matched result line.

Usage:
    # single run against the default native profile
    python scripts/retrieval_benchmark.py --url https://qualia-backend...workers.dev --key KEY \
        --eval scripts/retrieval_eval_set.json

    # A/B two profiles to see the uplift
    python scripts/retrieval_benchmark.py --url ... --key KEY --eval scripts/retrieval_eval_set.json \
        --profile-a flat --profile-b native
"""

import argparse
import json
import re
import sys
import urllib.request

RESULT_LINE_RE = re.compile(r"^\s*(\d+)\.\s", re.MULTILINE)
CONFIDENCE_RE = re.compile(r"confidence=([0-9.]+)")


def call_tool(base_url, api_key, tool_name, arguments):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {"name": tool_name, "arguments": arguments},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/mcp",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "qualia-benchmark/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("result", {}).get("content", [{}])[0].get("text", "")


def split_result_items(text):
    """Split mind_search output into per-rank chunks (rank starts at 1)."""
    matches = list(RESULT_LINE_RE.finditer(text))
    items = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        items.append(text[start:end])
    return items


def score_case(text, case):
    items = split_result_items(text)
    lower_items = [it.lower() for it in items]

    expect_all = [s.lower() for s in case.get("expect_all", []) if s.strip()]
    expect_any = [s.lower() for s in case.get("expect_any", []) if s.strip()]
    # If only expect_all given, any of them counts as a hit too.
    hit_targets = expect_any if expect_any else expect_all

    # First-hit rank for MRR.
    first_rank = 0
    for rank, item in enumerate(lower_items, start=1):
        if any(t in item for t in hit_targets):
            first_rank = rank
            break

    # Recall over expect_all across the whole result set.
    if expect_all:
        found = sum(1 for t in expect_all if any(t in it for it in lower_items))
        recall = found / len(expect_all)
    else:
        recall = 1.0 if first_rank > 0 else 0.0

    confidence = None
    if first_rank > 0:
        cm = CONFIDENCE_RE.search(items[first_rank - 1])
        if cm:
            confidence = float(cm.group(1))

    return {
        "hit": first_rank > 0,
        "first_rank": first_rank,
        "mrr": (1.0 / first_rank) if first_rank > 0 else 0.0,
        "recall": recall,
        "confidence": confidence,
        "n_results": len(items),
    }


def run_suite(url, key, cases, profile=None):
    rows = []
    for case in cases:
        args = {"query": case["query"], "limit": case.get("limit", 5)}
        if case.get("identity"):
            args["identity"] = case["identity"]
        if case.get("threshold") is not None:
            args["threshold"] = case["threshold"]
        if case.get("min_confidence") is not None:
            args["min_confidence"] = case["min_confidence"]
        if profile:
            args["retrieval_profile"] = profile
        try:
            text = call_tool(url, key, "mind_search", args)
            res = score_case(text, case)
        except Exception as e:  # noqa: BLE001
            res = {"hit": False, "first_rank": 0, "mrr": 0.0, "recall": 0.0, "confidence": None, "n_results": 0, "error": str(e)}
        res["query"] = case["query"]
        rows.append(res)
    return rows


def aggregate(rows):
    n = len(rows) or 1
    return {
        "cases": len(rows),
        "hit_rate": sum(1 for r in rows if r["hit"]) / n,
        "mrr": sum(r["mrr"] for r in rows) / n,
        "recall": sum(r["recall"] for r in rows) / n,
    }


def print_rows(label, rows):
    print(f"\n=== {label} ===")
    for r in rows:
        flag = "HIT " if r["hit"] else "miss"
        rank = r["first_rank"] or "-"
        conf = f"{r['confidence']:.3f}" if r["confidence"] is not None else "  -  "
        err = f"  ERROR: {r['error']}" if r.get("error") else ""
        print(f"  [{flag}] rank={rank:>2} conf={conf} recall={r['recall']:.2f}  {r['query'][:60]}{err}")
    agg = aggregate(rows)
    print(f"  -- hit_rate={agg['hit_rate']:.2%}  MRR={agg['mrr']:.3f}  recall={agg['recall']:.2%}  (n={agg['cases']})")
    return agg


def main():
    parser = argparse.ArgumentParser(description="qualia-backend retrieval benchmark")
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--eval", required=True, help="Path to eval-set JSON")
    parser.add_argument("--profile-a", default=None, help="Profile for run A (optional)")
    parser.add_argument("--profile-b", default=None, help="Profile for run B — enables A/B uplift report")
    args = parser.parse_args()

    with open(args.eval, "r", encoding="utf-8") as f:
        cases = json.load(f)
    if not isinstance(cases, list) or not cases:
        print("Eval set must be a non-empty JSON array of cases.")
        sys.exit(1)

    rows_a = run_suite(args.url, args.key, cases, args.profile_a)
    agg_a = print_rows(f"Run A (profile={args.profile_a or 'native'})", rows_a)

    if args.profile_b:
        rows_b = run_suite(args.url, args.key, cases, args.profile_b)
        agg_b = print_rows(f"Run B (profile={args.profile_b})", rows_b)
        print("\n=== Uplift (B - A) ===")
        print(f"  hit_rate: {agg_b['hit_rate'] - agg_a['hit_rate']:+.2%}")
        print(f"  MRR:      {agg_b['mrr'] - agg_a['mrr']:+.3f}")
        print(f"  recall:   {agg_b['recall'] - agg_a['recall']:+.2%}")


if __name__ == "__main__":
    main()
