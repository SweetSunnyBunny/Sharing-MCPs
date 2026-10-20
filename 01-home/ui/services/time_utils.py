"""UTC time helpers used for DB persistence and comparisons."""


from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso_epoch() -> tuple[str, int]:
    now = utc_now()
    return now.isoformat(), int(now.timestamp())


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
