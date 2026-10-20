'Application module.'


from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

LOCAL_TZ = ZoneInfo(TIMEZONE)


def to_local(value) -> datetime | None:
    'To local.'
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
    'Format local.'
    dt = to_local(value)
    if dt is None:
        return ""
    out = dt.strftime(fmt)
    if strip_leading_zero:
        out = out.lstrip("0")
    return out
