


from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config import TIMEZONE

MED_VISIBILITY_WINDOW = timedelta(hours=24)
MED_HISTORY_RETENTION_DAYS = 14


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def previous_day(day_str: str) -> str:
    return (datetime.strptime(day_str, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")


def format_time_short(dt: Optional[datetime] = None) -> str:
    local_dt = (dt or now_local()).astimezone(ZoneInfo(TIMEZONE))
    hour = local_dt.hour
    minute = local_dt.minute
    suffix = "a" if hour < 12 else "p"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d}{suffix}"


def coerce_local_datetime(value: Optional[datetime | str] = None) -> datetime:
    """Resolve a reference MOMENT."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
        if parsed_date == now_local().date():
            dt = now_local()
        else:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TIMEZONE))
    else:
        dt = now_local()

    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(TIMEZONE))
    return dt.astimezone(ZoneInfo(TIMEZONE))


def _parse_legacy_time(day_str: str, value: str) -> Optional[datetime]:
    text = value.strip().lower()
    if not text:
        return None

    if text.endswith(("am", "pm")):
        clock_text = text
    elif text.endswith(("a", "p")):
        clock_text = f"{text}m"
    else:
        return None

    try:
        clock_time = datetime.strptime(clock_text, "%I:%M%p")
        day = datetime.strptime(day_str, "%Y-%m-%d")
    except ValueError:
        return None

    return datetime(
        day.year,
        day.month,
        day.day,
        clock_time.hour,
        clock_time.minute,
        tzinfo=ZoneInfo(TIMEZONE),
    )


def med_is_taken(entry: Any) -> bool:
    if isinstance(entry, dict):
        if "taken" in entry:
            return bool(entry.get("taken"))
        return bool(entry.get("time") or entry.get("display_time") or entry.get("taken_at") or entry.get("timestamp"))
    if isinstance(entry, str):
        return bool(entry.strip())
    return False


def parse_med_taken_at(entry: Any, day_str: Optional[str] = None) -> Optional[datetime]:
    if isinstance(entry, dict):
        if not med_is_taken(entry):
            return None
        raw_value = entry.get("taken_at") or entry.get("timestamp")
        if raw_value:
            try:
                dt = datetime.fromisoformat(str(raw_value))
            except ValueError:
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=ZoneInfo(TIMEZONE))
                return dt.astimezone(ZoneInfo(TIMEZONE))

        time_text = str(entry.get("time") or entry.get("display_time") or "").strip()
        if day_str and time_text:
            return _parse_legacy_time(day_str, time_text)
        return None

    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        if "T" in text:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                dt = None
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=ZoneInfo(TIMEZONE))
            return dt.astimezone(ZoneInfo(TIMEZONE))
        if day_str:
            return _parse_legacy_time(day_str, text)
    return None


def med_display_time(entry: Any, day_str: Optional[str] = None) -> Optional[str]:
    if isinstance(entry, str):
        text = entry.strip()
        return text or None

    if not isinstance(entry, dict):
        return None

    text = str(entry.get("time") or entry.get("display_time") or "").strip()
    if text:
        return text

    taken_at = parse_med_taken_at(entry, day_str)
    return format_time_short(taken_at) if taken_at else None


def format_age_label(age: Optional[timedelta]) -> Optional[str]:
    if age is None:
        return None

    total_seconds = max(0, int(age.total_seconds()))
    total_minutes = total_seconds // 60
    if total_minutes <= 0:
        return "just now"
    if total_minutes < 60:
        return f"{total_minutes}m ago"
    hours = total_minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def create_med_entry(moment: datetime, taken: bool = True) -> dict:
    local_moment = moment.astimezone(ZoneInfo(TIMEZONE))
    if taken:
        return {
            "taken": True,
            "time": format_time_short(local_moment),
            "taken_at": local_moment.isoformat(),
        }
    return {
        "taken": False,
        "updated_at": local_moment.isoformat(),
    }


def _normalize_med_entry(entry: Any, day_str: str) -> Optional[dict]:
    if entry is None:
        return None

    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        normalized = {"taken": True, "time": text}
        taken_at = parse_med_taken_at(text, day_str)
        if taken_at:
            normalized["taken_at"] = taken_at.isoformat()
        return normalized

    if not isinstance(entry, dict):
        return None

    normalized = dict(entry)
    taken = med_is_taken(entry)
    normalized["taken"] = taken

    if taken:
        taken_at = parse_med_taken_at(entry, day_str)
        time_text = med_display_time(entry, day_str)
        normalized.pop("updated_at", None)
        if time_text:
            normalized["time"] = time_text
        if taken_at:
            normalized["taken_at"] = taken_at.isoformat()
        return normalized

    normalized.pop("taken_at", None)
    normalized.pop("time", None)
    normalized.pop("display_time", None)
    return normalized


def normalize_meds_data(data: Any, reference: Optional[datetime | str] = None) -> tuple[dict, bool]:
    reference_dt = coerce_local_datetime(reference)
    cutoff = reference_dt.date() - timedelta(days=MED_HISTORY_RETENTION_DAYS)

    if not isinstance(data, dict):
        return {}, bool(data)

    normalized: dict[str, dict[str, dict]] = {}
    for day_str, entries in data.items():
        try:
            entry_day = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if entry_day < cutoff or not isinstance(entries, dict):
            continue

        normalized_day = {}
        for dose in ("am", "pm"):
            if dose not in entries:
                continue
            normalized_entry = _normalize_med_entry(entries.get(dose), day_str)
            if normalized_entry is not None:
                normalized_day[dose] = normalized_entry

        if normalized_day:
            normalized[day_str] = normalized_day

    return normalized, normalized != data


def visible_meds_state(data: Any, reference: Optional[datetime | str] = None) -> dict:
    reference_dt = coerce_local_datetime(reference)
    normalized, _ = normalize_meds_data(data, reference_dt)
    today = reference_dt.strftime("%Y-%m-%d")
    prior_day = previous_day(today)
    today_meds = normalized.get(today, {})
    prior_meds = normalized.get(prior_day, {})

    result = {"date": today, "doses": {}}
    for dose in ("am", "pm"):
        visible_entry = None
        visible_day = today
        stale = False

        current_entry = today_meds.get(dose)
        if med_is_taken(current_entry):
            visible_entry = current_entry
        elif current_entry is None:
            previous_entry = prior_meds.get(dose)
            previous_taken_at = parse_med_taken_at(previous_entry, prior_day)
            previous_age = reference_dt - previous_taken_at if previous_taken_at else None
            if med_is_taken(previous_entry) and previous_age is not None and timedelta(0) <= previous_age <= MED_VISIBILITY_WINDOW:
                visible_entry = previous_entry
                visible_day = prior_day
                stale = True

        taken_at = parse_med_taken_at(visible_entry, visible_day) if visible_entry else None
        age = reference_dt - taken_at if taken_at else None
        age_minutes = max(0, int(age.total_seconds() // 60)) if age is not None else None
        metadata = {
            "taken": bool(visible_entry),
            "stale": stale,
            "date": visible_day,
            "time": med_display_time(visible_entry, visible_day) if visible_entry else None,
            "taken_at": taken_at.isoformat() if taken_at else None,
            "age_minutes": age_minutes,
            "age_label": format_age_label(age),
            "status": "carried" if stale and visible_entry else ("taken" if visible_entry else ("cleared" if current_entry is not None else "missing")),
        }
        result["doses"][dose] = metadata
        result[dose] = metadata["time"]
        result[f"{dose}_date"] = metadata["date"]
        result[f"{dose}_stale"] = metadata["stale"]
        result[f"{dose}_taken"] = metadata["taken"]
        result[f"{dose}_taken_at"] = metadata["taken_at"]
        result[f"{dose}_age_minutes"] = metadata["age_minutes"]
        result[f"{dose}_age_label"] = metadata["age_label"]

    return result


def build_meds_context_line(data: Any, reference: Optional[datetime | str] = None) -> str:
    visible = visible_meds_state(data, reference)
    parts = []
    for dose in ("am", "pm"):
        metadata = visible["doses"][dose]
        if metadata["taken"] and metadata["time"]:
            prefix = f"{dose.upper()} last dose at" if metadata["stale"] else f"{dose.upper()} taken at"
            age = f" ({metadata['age_label']})" if metadata.get("age_label") else ""
            parts.append(f"{prefix} {metadata['time']}{age}")
        else:
            parts.append(f"{dose.upper()} not taken in the last 24h")
    return f"Meds: {' | '.join(parts)}"
