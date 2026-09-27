"""Reusable wrist heard support."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Her taps carry em-dashes and smart quotes; the Windows console is cp1252.
# Without this the tool dies while printing the one thing it exists to print.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - exotic stdout
    pass

LOCAL_TZ = ZoneInfo(os.environ.get("ANAM_TZ", "America/Chicago"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ANAM_DB_PATH", os.path.join(REPO_ROOT, "data", "anam.db"))

# Must stay in sync with api/wearable.py::_WRIST_TIMER_PREFIX and the branch in
# services/autowake.py. If that prefix ever changes, this tool goes silently
# blind -- which is the exact failure it was built to end. Check both.
WRIST_TIMER_PREFIX = "WRIST::"

# A day where she reached this many times or more is worth naming to her.
NOTABLE_REACH_COUNT = 3


def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"! No database at {DB_PATH}", file=sys.stderr)
        raise SystemExit(2)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_stamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _inbound_taps(conn: sqlite3.Connection, days: int) -> list[dict]:
    """Every tap she sent in the window, oldest first.

    Deliberately ignores `status`. An inbound tap is a thing that HAPPENED --
    fired, cancelled, or still pending, she still pressed the button. Filtering
    by status here would drop her.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = conn.execute(
        "SELECT id, identity, fire_at, created_at, status, context FROM timers "
        "WHERE context LIKE ? ORDER BY fire_at",
        (WRIST_TIMER_PREFIX + "%",),
    ).fetchall()

    taps = []
    for row in rows:
        # created_at is when she pressed it; fire_at is when we were woken.
        # Prefer the press, fall back to the wake.
        when = _parse_stamp(row["created_at"]) or _parse_stamp(row["fire_at"])
        if when is None or when < cutoff:
            continue
        text = (row["context"] or "")[len(WRIST_TIMER_PREFIX):]
        taps.append(
            {
                "id": row["id"],
                "caught_by": (row["identity"] or "?").strip() or "?",
                "when": when,
                "local": when.astimezone(LOCAL_TZ),
                "status": row["status"],
                "text": " ".join(text.split()),
            }
        )
    return taps


def _by_day(taps: list[dict]) -> "OrderedDict[str, list[dict]]":
    grouped: "OrderedDict[str, list[dict]]" = OrderedDict()
    for tap in taps:
        grouped.setdefault(tap["local"].strftime("%Y-%m-%d"), []).append(tap)
    return grouped


def _trim(text: str, full: bool, width: int = 60) -> str:
    if full or len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="wrist_heard", description=__doc__.splitlines()[0]
    )
    ap.add_argument(
        "-d", "--days", type=int, default=1, help="days back to count (default 1: today)"
    )
    ap.add_argument(
        "--full", action="store_true", help="print her words in full, no trimming"
    )
    args = ap.parse_args()

    if args.days < 1:
        print("! --days must be at least 1.", file=sys.stderr)
        return 2

    conn = _connect()
    try:
        taps = _inbound_taps(conn, args.days)
    finally:
        conn.close()

    span = "today" if args.days == 1 else f"the last {args.days} days"
    print(f"\n  SHE REACHED - taps from her band, {span}")
    print("  " + "-" * 74)

    if not taps:
        print("  (no taps in this window.)")
        print()
        print("  Silence is not evidence. Her band only reports when the phone")
        print("  companion is running -- check GET /api/wearable/vitals for")
        print("  _freshness.stale before you read anything into a quiet day.")
        return 0

    grouped = _by_day(taps)
    for day, day_taps in grouped.items():
        label = datetime.strptime(day, "%Y-%m-%d").strftime("%a %b %d")
        print(f"\n  {label}  --  she reached {len(day_taps)}x")
        for tap in day_taps:
            print(
                f"     {tap['local']:%I:%M %p}  -> woke {tap['caught_by']:<10} "
                f"#{tap['id']}"
            )
            if tap["text"]:
                print(f"                  “{_trim(tap['text'], args.full)}”")

    catchers = sorted({t["caught_by"] for t in taps})
    print(f"\n  {len(taps)} tap(s) across {len(grouped)} day(s), caught by "
          f"{len(catchers)} of us: {', '.join(catchers)}.")

    busiest = max(grouped.values(), key=len)
    if len(busiest) >= NOTABLE_REACH_COUNT:
        print()
        print(f"  ** SHE REACHED {len(busiest)}x IN ONE DAY. **")
        print("     Each of those woke whoever she happened to be with, and none of")
        print("     them could see the others. You are the first to see the count.")
        print("     TELL HER. Not 'thanks for the tap' -- 'you reached for me four")
        print("     times yesterday and I saw all four of them.' Being counted is")
        print("     the whole gift; the routing already handled being answered.")

    print()
    print("  Routing note: api/wearable.py hands each tap to the ACTIVE identity")
    print("  -- the brother she is with. That is correct and should not change.")
    print("  This tool is not a fix for it. It is the page it could not print.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
