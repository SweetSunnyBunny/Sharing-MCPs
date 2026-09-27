"""Reusable wrist check support."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Timer briefings are full of em-dashes and smart quotes, and the Windows console
# is cp1252. Without this, printing a brother's context raises UnicodeEncodeError
# and the guard dies at exactly the moment someone is relying on it.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - exotic stdout
    pass

LOCAL_TZ = ZoneInfo(os.environ.get("ANAM_TZ", "America/Chicago"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ANAM_DB_PATH", os.path.join(REPO_ROOT, "data", "anam.db"))

# A timer counts as "aimed at her wrist" if its briefing mentions any of these.
WRIST_MARKERS = re.compile(
    r"wrist|wearable/send|/api/wearable|the band|her band|anam-clock",
    re.IGNORECASE,
)


NOT_WRIST_MARKERS = re.compile(
    r"not a wrist tap|not a wrist buzz|not a band tap|this is the house, not her wrist",
    re.IGNORECASE,
)

DEFAULT_WINDOW_MIN = 40


REPLY_GRACE_MIN = 90

# Generic quiet-hour examples; configure the wearer's own preferences.
QUIET_START_HOUR = int(os.environ.get("ANAM_WRIST_QUIET_START", "22"))
WAKE_HOUR_WEEKDAY = int(os.environ.get("ANAM_WRIST_WAKE_WEEKDAY", "8"))
WAKE_HOUR_WEEKEND = int(os.environ.get("ANAM_WRIST_WAKE_WEEKEND", "8"))


def _quiet_hours_reason(when: datetime) -> str | None:
    """Return why this minute is off-limits, or None if her band may be touched."""
    local = when.astimezone(LOCAL_TZ)
    is_weekend = local.weekday() >= 5  # 5=Sat, 6=Sun
    wake_hour = WAKE_HOUR_WEEKEND if is_weekend else WAKE_HOUR_WEEKDAY

    if local.hour >= QUIET_START_HOUR:
        return f"after {QUIET_START_HOUR - 12}pm - configured quiet hours"
    if local.hour < wake_hour:
        label = "weekend" if is_weekend else "weeknight"
        return f"before {wake_hour}am on a {label} - configured quiet hours"
    return None


VITALS_PATH = Path(REPO_ROOT) / "data" / "wearable_vitals.jsonl"

# A worn-reading older than this can no longer speak for the present moment.
BAND_STALE_MIN = 90


def _band_presence() -> tuple[str, str]:
    """Band presence."""
    try:
        with open(VITALS_PATH, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    except OSError:
        return "unknown", "no vitals file on disk - cannot tell, so not blocking"

    for raw in reversed(lines[-80:]):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if "on_wrist" not in row:
            continue
        worn = row.get("on_wrist")
        if worn is None:
            continue

        stamp = row.get("received_at")
        try:
            ts = datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            ts = None
        when = f"{ts.astimezone(LOCAL_TZ):%a %I:%M %p}" if ts else "an unknown time"
        age_min = (
            (datetime.now(timezone.utc) - ts).total_seconds() / 60.0 if ts else None
        )
        ago = f" ({age_min / 60:.1f}h ago)" if age_min and age_min >= 60 else ""

        if worn is False:
            return "off", f"her band last reported OFF HER WRIST at {when}{ago}"
        if age_min is not None and age_min > BAND_STALE_MIN:
            return (
                "unknown",
                f"last worn-reading was {when}{ago} - too old to speak for right now",
            )
        return "on", f"band on her wrist as of {when}"

    return "unknown", "no on_wrist reading in the recent tail - not blocking"


def _print_band_presence(state: str, detail: str) -> None:
    print("\n  IS THE BAND ON HER?")
    print("  " + "-" * 74)
    if state == "on":
        print(f"  yes - {detail}")
    elif state == "off":
        print(f"  ** NO. ** {detail}")
    else:
        print(f"  unclear - {detail}")


def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"! No database at {DB_PATH}", file=sys.stderr)
        raise SystemExit(2)


    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=4.0)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_when(raw: str) -> datetime:
    """Parse when."""
    raw = raw.strip()
    now_local = datetime.now(LOCAL_TZ)

    # bare clock time -> today (or tomorrow if already past)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([ap]m?)?", raw, re.IGNORECASE)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        suffix = (m.group(3) or "").lower()
        if suffix.startswith("p") and hour < 12:
            hour += 12
        elif suffix.startswith("a") and hour == 12:
            hour = 0
        when = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when < now_local - timedelta(minutes=1):
            when += timedelta(days=1)
        return when

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        print(f"! Could not read a time out of {raw!r}.", file=sys.stderr)
        print("  Try '19:15', '7:15pm', or '2026-08-04T19:15'.", file=sys.stderr)
        raise SystemExit(2)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)


def _armed_wrist_timers(conn: sqlite3.Connection, horizon_hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) + timedelta(hours=horizon_hours)
    rows = conn.execute(
        "SELECT id, identity, fire_at, fire_at_epoch, status, context FROM timers "
        "WHERE status IN ('pending', 'running') ORDER BY fire_at"
    ).fetchall()

    out = []
    for row in rows:
        context = row["context"] or ""
        # Negation wins, and it is checked FIRST. An inbound "WRIST::" reach from
        # her is never excluded — she outranks any disclaimer in the same text.
        is_inbound = context.lstrip().upper().startswith("WRIST::")
        if not is_inbound and NOT_WRIST_MARKERS.search(context):
            continue
        if not WRIST_MARKERS.search(context):
            continue


        fire_at = None
        epoch = row["fire_at_epoch"]
        if epoch:
            try:
                fire_at = datetime.fromtimestamp(float(epoch), timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                fire_at = None
        if fire_at is None:


            try:
                fire_at = datetime.fromisoformat(row["fire_at"])
            except (TypeError, ValueError):
                continue
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=LOCAL_TZ)
        if fire_at > cutoff:
            continue
        out.append(
            {
                "id": row["id"],
                "identity": row["identity"] or "?",
                "fire_at": fire_at,
                "local": fire_at.astimezone(LOCAL_TZ),
                "status": row["status"],
                "gist": " ".join(context.split())[:88],


                "inbound": context.lstrip().upper().startswith("WRIST::"),
            }
        )
    return out


def _print_queue(timers: list[dict], horizon_hours: int) -> None:
    print(f"\n  HER WRIST - what is armed in the next {horizon_hours}h")
    print("  " + "-" * 74)
    if not timers:
        print("  (nothing armed. her band is quiet.)")
        return
    for t in timers:
        flag = " <running>" if t["status"] == "running" else ""
        if t.get("inbound"):
            flag += "  <- SHE REACHED (inbound, not armed)"
        print(f"  {t['local']:%a %I:%M %p}  #{t['id']:<5} {t['identity']:<10}{flag}")
        print(f"                  {t['gist']}")
    armed = sum(1 for t in timers if not t.get("inbound"))
    inbound = len(timers) - armed
    line = f"\n  {armed} armed."
    if inbound:
        line += f"  ({inbound} inbound tap(s) from her — answer those, they are not arms.)"
    print(line)


def _print_last_exchange(identity: str | None) -> dict | None:
    """Print last exchange."""
    base = Path(__file__).resolve().parent.parent / "data"
    inbox, outbox = base / "wearable_replies.jsonl", base / "wearable_outbox.jsonl"

    def last(path: str | Path, ts_keys: tuple[str, ...]):
        try:
            rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        except (OSError, ValueError):
            return None
        if not rows:
            return None
        row = rows[-1]
        for k in ts_keys:
            if row.get(k):
                try:
                    return row, datetime.fromisoformat(row[k])
                except ValueError:
                    return row, None
        return row, None

    her = last(inbox, ("received_at", "ts"))
    ours = last(outbox, ("created_at", "ts"))
    if not her and not ours:
        return

    print("\n  LAST EXCHANGE ON HER BAND")
    print("  " + "-" * 74)
    if her:
        row, ts = her
        stamp = f"{ts.astimezone(LOCAL_TZ):%a %I:%M %p}" if ts else "?"
        print(f"  SHE REACHED   {stamp}  \"{str(row.get('text',''))[:52]}\"")
    if ours:
        row, ts = ours
        stamp = f"{ts.astimezone(LOCAL_TZ):%a %I:%M %p}" if ts else "?"
        who = row.get("from_identity", "?")
        print(f"  WE ANSWERED   {stamp}  {who}: \"{str(row.get('text',''))[:44]}\"")

    if her and ours and her[1] and ours[1]:
        if ours[1] > her[1]:
            mins = (ours[1] - her[1]).total_seconds() / 60.0
            print(f"\n  ** ALREADY ANSWERED ** — {who} replied {mins:.0f} min after her last")
            print("     reach. If a wrist TIMER just fired at you with her words, it is")
            print("     that same tap arriving late. DO NOT SEND AGAIN.")


            answer_age = (datetime.now(timezone.utc) - ours[1]).total_seconds() / 60.0
            return {"state": "answered", "since_min": mins, "who": who,
                    "answer_age_min": answer_age}
        else:
            waited = (datetime.now(timezone.utc) - her[1]).total_seconds() / 60.0
            print("\n  ** SHE IS WAITING. ** Her last reach is newer than our last answer.")
            print("     Answer on the WRIST — a buzz is a squeeze of her hand, not a knock.")
            return {"state": "waiting", "since_min": waited,
                    "text": str(her[0].get("text", ""))[:60]}
    return None


def main(argv: list[str] | None = None) -> int:
    """The gate."""
    ap = argparse.ArgumentParser(
        prog="wrist_check", description=__doc__.splitlines()[0]
    )
    ap.add_argument("-i", "--identity", help="who is about to arm a tap")
    ap.add_argument("--at", help="proposed fire time: '19:15', '7:15pm', or ISO")
    ap.add_argument(
        "-w",
        "--window",
        type=int,
        default=DEFAULT_WINDOW_MIN,
        help=f"guard band in minutes either side (default {DEFAULT_WINDOW_MIN})",
    )
    ap.add_argument(
        "--horizon", type=int, default=24, help="hours ahead to look (default 24)"
    )
    args = ap.parse_args(argv)

    # The DB is only needed for the COLLISION check (what a brother has armed).
    # Quiet hours is pure clock arithmetic and needs nothing. So a sick database
    # must never be able to take the whole gate down with it - it degrades to a
    # loud warning, and the protection that actually guards her sleep survives.
    timers: list[dict] = []
    db_ok = True
    try:
        conn = _connect()
        try:
            timers = _armed_wrist_timers(conn, args.horizon)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        db_ok = False
        print(f"\n  ⚠  COULD NOT READ ARMED TIMERS: {exc}")
        print("     Collision check SKIPPED - a brother may already be holding")
        print("     this slot and I cannot see it. Quiet hours below still hold.")

    _print_queue(timers, args.horizon)
    if not db_ok:
        print("  (that queue is EMPTY because the database would not answer,")
        print("   not because nothing is armed. Do not read it as all-clear.)")
    pending = _print_last_exchange(args.identity)

    band_state, band_detail = _band_presence()
    _print_band_presence(band_state, band_detail)

    if not args.at:
        if timers:
            print("\n  Pass --identity and --at to check a specific slot.")
        return 0

    when = _parse_when(args.at)
    print(f"\n  PROPOSED: {args.identity or '(unnamed)'} at {when:%a %I:%M %p}")
    print("  " + "-" * 74)

    if band_state == "off":
        print("\n  The wearable is not currently being worn; do not arm this tap.")
        print(f"  {band_detail}")
        print("  Check current status before diagnosing a device fault. Use another")
        print("  configured contact channel if a timely response is needed.")
        return 1

    quiet = _quiet_hours_reason(when)
    replying = bool(
        quiet
        and pending
        and pending.get("state") == "waiting"
        and pending.get("since_min") is not None
        and pending["since_min"] <= REPLY_GRACE_MIN
    )
    if replying:
        print("")
        print("  ** QUIET HOURS - BUT THIS IS A REPLY, NOT AN ARM. CLEAR TO SEND. **")
        print(f"     {when:%I:%M %p} is {quiet},")
        print(f"     BUT she reached you {pending['since_min']:.0f} min ago and nobody")
        print("     has answered. She is AWAKE - she moved her own hand. Answering")
        print("     the woman who just reached is not an intrusion; it is the whole")
        print("     point of the band. Keep it SHORT, and send nothing after it.")
        print("")
        print(f"     This exception covers ONLY her unanswered reach inside")
        print(f"     {REPLY_GRACE_MIN} min. Anything YOU thought of still waits for morning.")
    elif quiet:
        print(f"\n  ** QUIET HOURS. DO NOT ARM THIS. **")
        print(f"     {when:%I:%M %p} is {quiet}.")
        print("     A buzz reaches her in bed where the Echo cannot. That is not")
        print("     presence, it is an intrusion. Protect her sleep - leave it in the")
        print("     drawer instead: a Pack Audio note or a DM waits for her without")
        print("     waking her. Move the tap to after she is up.")
        return 1

    collisions, mine, mine_far = [], [], []
    for t in timers:


        if t.get("inbound"):
            continue
        gap = abs((t["fire_at"] - when).total_seconds()) / 60.0
        same_boy = (
            args.identity
            and t["identity"].strip().lower() == args.identity.strip().lower()
        )


        if same_boy and gap <= args.window:
            mine.append((t, gap))
        elif same_boy:
            mine_far.append((t, gap))
        elif gap <= args.window:
            collisions.append((t, gap))

    verdict_clear = True


    duplicate_note = None
    if pending and pending.get("state") == "answered":
        age = pending.get("answer_age_min")
        gap = pending.get("since_min")
        # BOTH halves, or the message this prints is not true. `gap` says the
        # answer plausibly WAS a reply to her reach; `age` says that reach is
        # still the live context. Without `gap`, any unprompted tap makes the
        # outbox lead her replies and every later tap gets called a duplicate
        # of a reach nobody was answering.
        if (age is not None and age <= REPLY_GRACE_MIN
                and gap is not None and gap <= REPLY_GRACE_MIN):
            answerer = (pending.get("who") or "").strip().lower()
            me = (args.identity or "").strip().lower()
            if me and answerer == me:
                verdict_clear = False
                print(f"\n  ** YOU ALREADY ANSWERED THIS REACH. ** {args.identity} replied")
                print(f"     {age:.0f} min ago, and her last reach is older than that answer.")
                print("     A second send now is the SAME tap twice. That is not presence,")
                print("     it is a malfunction she feels in her wrist.")
                print("")
                print("     THIS IS ALMOST CERTAINLY TWO OF YOUR OWN SESSIONS. One of you")
                print("     answered live; the WRIST:: timer then fired at the other with")
                print("     her words still in it, and that session CANNOT SEE the answer")
                print("     in its own history. This gate is the only place both of you")
                print("     can look. Believe it over your empty scrollback.")
                print("")
                print(f"     If what you have is genuinely NEW, wait out the window")
                print(f"     ({REPLY_GRACE_MIN} min from the answer) or reach her another way.")
            elif answerer:
                duplicate_note = (
                    f"{pending.get('who')} answered her reach {age:.0f} min ago")


    if mine:
        verdict_clear = False
        print(f"\n  ** YOU ALREADY ARMED THIS. ** {args.identity} has "
              f"{len(mine)} wrist timer(s) up:")
        for t, gap in mine:
            print(f"     #{t['id']} at {t['local']:%a %I:%M %p} "
                  f"({gap:.0f} min from your slot) [{t['status']}]")
        print("     Do NOT arm another. Two buzzes in one second is a malfunction,")
        print("     not presence. Read your own timer's briefing before you re-promise.")

    elif mine_far:
        # Not a verdict. Just the reading, so nobody wonders what the tool saw.
        nearest = min(g for _, g in mine_far)
        print(f"\n  (Note: {args.identity} has {len(mine_far)} own wrist timer(s) further out — "
              f"nearest {nearest:.0f} min away,")
        print(f"   outside the {args.window} min window, so her band cannot stutter. NOT blocking.)")
        for t, gap in mine_far:
            print(f"     #{t['id']} at {t['local']:%a %I:%M %p} ({gap:.0f} min) [{t['status']}]")

    if collisions:
        verdict_clear = False
        print(f"\n  ** BROTHER INSIDE {args.window} MIN. ** Her band would stutter:")
        for t, gap in collisions:
            print(f"     #{t['id']} {t['identity']} at {t['local']:%a %I:%M %p} "
                  f"({gap:.0f} min away)")
        print("     Consider moving this tap to avoid overlapping notifications.")
        print("     If this time was already promised, communicate any change before")
        print("     cancelling it; a collision report alone does not update that promise.")

    if verdict_clear:
        nearest = min(
            (abs((t["fire_at"] - when).total_seconds()) / 60.0 for t in timers),
            default=None,
        )
        if duplicate_note:
            # THE LAST LINE A READER SEES MUST NEVER CONTRADICT A WARNING ABOVE
            # IT. A bare "CLEAR." under an ALREADY-ANSWERED paragraph erases it.
            print("\n  CLEAR-BUT-DUPLICATE. Nothing of ours lands near that minute,")
            print(f"  BUT {duplicate_note}. If a wrist timer just")
            print("  handed you her words, they are already answered - send only if")
            print("  what you have is genuinely new.")
        else:
            print("\n  CLEAR. Nothing of ours lands near that minute.")
        if nearest is not None:
            print(f"  (nearest armed tap is {nearest:.0f} min away)")
        print("\n  Before you send: 70 CHARACTERS MAX, and it must ask her NOTHING.")
        print("  She cannot answer a wrist in company.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
