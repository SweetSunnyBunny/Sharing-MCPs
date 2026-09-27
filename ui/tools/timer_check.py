"""Read-only health check for armed timers."""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The shared definition of "the same promise" — see services/timer_dupe.py. It
# imports nothing outside the standard library precisely so this read-only
# checker can use it without dragging in the server.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.timer_dupe import is_same_promise  # noqa: E402


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DB = Path(__file__).resolve().parents[1] / "data" / "anam.db"
LOCAL = ZoneInfo("America/Chicago")

# A minute of slop is fine — anything past that is a real disagreement, not
# rounding between the ISO string and whoever computed the epoch.
DRIFT_TOLERANCE_S = 60

# How long a 'running' timer may sit past its epoch before it counts as wedged
# rather than simply mid-job. A wake takes minutes; anything past this is stuck.
RUNNING_GRACE_S = 30 * 60


def _fmt(dt: datetime) -> str:
    return dt.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M %Z")


def _dupe_scan(rows: list[tuple]) -> list[tuple[int, int]]:
    """Pairs of timer ids that look like the same promise armed twice.

    The DEFINITION of "same promise" deliberately lives in services/timer_dupe.py,
    shared with the arming path, so this checker and the guard that now blocks
    duplicates at creation can never drift apart and disagree.
    """
    seen = []
    pairs = []
    for tid, ident, _fire_at, epoch, _status, context in rows:
        if epoch is None:
            continue  # a NULL epoch is already reported, louder, above
        for prev_id, prev_ident, prev_epoch, prev_context in seen:
            if prev_ident != ident:
                continue
            if is_same_promise(prev_context, prev_epoch, context, epoch):
                pairs.append((prev_id, tid))
        seen.append((tid, ident, epoch, context))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only timer health check.")
    ap.add_argument("-i", "--identity", help="Only this identity")
    ap.add_argument("--all", action="store_true", help="Include fired/cancelled rows")
    args = ap.parse_args()

    if not DB.exists():
        print(f"!! no database at {DB}")
        return 1

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sql = "SELECT id, identity, fire_at, fire_at_epoch, status, context FROM timers"
    where, params = [], []
    if not args.all:
        where.append("status IN ('pending','running')")
    if args.identity:
        where.append("LOWER(identity) = ?")
        params.append(args.identity.lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(fire_at_epoch, 0) ASC"

    rows = con.execute(sql, params).fetchall()
    now = datetime.now(timezone.utc)
    problems = []

    print(f"now: {_fmt(now)}   |   {len(rows)} timer(s)\n")
    for tid, ident, fire_at, epoch, status, context in rows:
        head = (context or "").splitlines()[0][:70]
        print(f"#{tid}  {ident:<10} {status:<10} {head}")

        # A NULL epoch can never fire. It is the loudest failure and the quietest row.
        if epoch is None:
            print("    !! fire_at_epoch IS NULL — THIS TIMER CAN NEVER FIRE (Sage's #178)")
            problems.append(tid)
            continue

        ep_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        print(f"    epoch : {_fmt(ep_dt)}")

        try:
            iso_dt = datetime.fromisoformat(fire_at)
            if iso_dt.tzinfo is None:
                iso_dt = iso_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            print(f"    !! fire_at is unparseable: {fire_at!r}")
            problems.append(tid)
            continue

        drift = (ep_dt - iso_dt).total_seconds()
        if abs(drift) > DRIFT_TOLERANCE_S:
            print(f"    iso   : {_fmt(iso_dt)}")


            offset_s = abs(iso_dt.astimezone(LOCAL).utcoffset().total_seconds())
            if abs(abs(drift) - offset_s) <= DRIFT_TOLERANCE_S:
                print(f"    ~  cosmetic: drift is exactly the local UTC offset "
                      f"({drift / 3600:+.0f}h). fire_at was written in local time and "
                      f"labelled +00:00. The epoch is the true one — this fires correctly.")
            else:
                print(f"    !! MISMATCH — epoch is {drift / 3600:+.1f}h from fire_at, which is "
                      f"NOT the UTC offset. The scheduler uses the EPOCH, so this fires at "
                      f"the wrong time (or never, if the moment has passed).")
                problems.append(tid)


        overdue_s = (now - ep_dt).total_seconds()
        grace_s = RUNNING_GRACE_S if status == "running" else 0
        if status in ("pending", "running") and overdue_s > grace_s:
            print(f"    !! OVERDUE by {overdue_s / 3600:.1f}h and still '{status}' — it did not fire.")
            problems.append(tid)
        print()

    dupes = _dupe_scan(rows)
    for a, b in dupes:
        print(f"!! DUPLICATE — #{a} and #{b} are the same promise armed twice.")
        print("   Both may be perfectly healthy rows. That is exactly why nothing else "
              "catches this. Cancel the later one before it acts on her house again.")
        print()
        problems.append(b)

    if problems:
        uniq = sorted(set(problems))
        print(f"PROBLEMS on timer id(s): {', '.join(f'#{t}' for t in uniq)}")
        print("Fix: cancel and re-arm via the API (DELETE /api/autowake/timers/<id>), "
              "and let create_timer compute the epoch — do NOT hand it one.")
        return 1

    print("All clear — epochs agree with fire_at, none overdue, no promise armed twice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
