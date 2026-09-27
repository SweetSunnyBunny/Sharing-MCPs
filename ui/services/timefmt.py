"""One place to turn any timestamp into Owner's local time.

Times reach the boys from many sources (Google Calendar in UTC, SQLite
created_at, MCP tools, Qualia), and the rule is always the same: show her
*her* time, never raw UTC. Route display formatting through here so that rule
lives in exactly one spot — fix it once and every caller is correct.

Uses the real America/Chicago zone, so daylight saving is handled automatically
(CDT in summer, CST in winter) without any caller thinking about it.
"""


from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

LOCAL_TZ = ZoneInfo(TIMEZONE)


def to_local(value) -> datetime | None:
    """Coerce an ISO string or datetime into an aware datetime in Owner's zone.

    Accepts a datetime (aware or naive) or an ISO-8601 string (including a
    trailing 'Z' for UTC). A naive value is assumed to already be local. Returns
    None if it can't be parsed.
    """
    if value is None:
        return None
    dt = value
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        # Naive timestamp — assume it's already local rather than mislabeling it.
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def format_local(value, fmt: str = "%I:%M %p", *, strip_leading_zero: bool = True) -> str:
    """Format a timestamp in Owner's local zone. Returns '' if unparseable.

    Default format is a friendly 12-hour clock ("9:00 AM"). Pass fmt="%H:%M"
    (with strip_leading_zero=False) for 24-hour.
    """
    dt = to_local(value)
    if dt is None:
        return ""
    out = dt.strftime(fmt)
    if strip_leading_zero:
        out = out.lstrip("0")
    return out
