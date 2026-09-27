"""Manual autowake smoke-test runner.

Usage:
  python services/autowake_smoke.py --identity Avery
  python services/autowake_smoke.py --identity Claude --prompt "Twitter smoke test..."
"""

# ANAM GUIDE: AUTOWAKE MANUAL SMOKE TEST
# What: A little terminal script you run by hand to test that an autowake session works for one boy, without waiting for the real schedule.
# Called by: Nobody in the app — you run it yourself from a terminal (see Usage above). It just calls run_manual_smoke_test in services/autowake.py.
# Edit here when: You want to change the command-line options or what the test prints; the actual test logic lives in autowake.py.

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a one-off autowake smoke test.")
    parser.add_argument("--identity", required=True, help="Identity name, e.g. Avery or Claude")
    parser.add_argument(
        "--prompt",
        default="",
        help="Optional custom smoke-test prompt. Defaults to a Twitter auth check.",
    )
    parser.add_argument(
        "--session-name",
        default="manual_twitter_smoke",
        help="Label for the smoke-test session.",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()

    from services.autowake import run_manual_smoke_test

    result = await run_manual_smoke_test(
        args.identity,
        prompt=args.prompt or None,
        session_name=args.session_name,
    )

    print(f"ok: {result.get('ok')}")
    print(f"identity: {result.get('identity')}")
    print(f"session_name: {result.get('session_name')}")
    print(f"conversation_id: {result.get('conversation_id', '')}")
    print(f"message: {result.get('message')}")
    content = (result.get("content") or "").strip()
    if content:
        print("\n--- content ---\n")
        print(content)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
