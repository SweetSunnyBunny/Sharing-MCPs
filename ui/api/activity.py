"""Cross-session activity tracking helpers.

Reads the activity log so the boys know Owner has been active elsewhere.
"""

# ANAM GUIDE: CROSS-SESSION ACTIVITY READER
# What: Reads data/rituals/activity_log.json and turns recent Discord/platform sessions into a short "[External sessions today:]" note.
# Called by: services/context_hooks.py (build_orientation_context) — it's a context helper, not a web route, despite living in api/.
# Edit here when: You want to change how "she was with you elsewhere" shows up in a boy's orientation — the wording, the 24h window, or the 5-entry cap.

import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import RITUALS_DIR, TIMEZONE

log = logging.getLogger(__name__)

ACTIVITY_FILE = RITUALS_DIR / "activity_log.json"


def _read_log() -> list[dict]:
    if ACTIVITY_FILE.exists():
        try:
            return json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def get_recent_external_activity(identity: str, hours: int = 24) -> str | None:
    """Build an orientation context block for external activity.

    Called by build_orientation_context() to inject cross-session awareness.
    """
    entries = _read_log()
    cutoff = time.time() - (hours * 3600)
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # Get all recent activity for this identity from external sources
    relevant = [
        e for e in entries
        if e.get("epoch", 0) > cutoff
        and e.get("identity", "").lower() == identity.lower()
    ]

    if not relevant:
        return None

    lines = ["[External sessions today:]"]
    for entry in relevant[-5:]:  # Last 5 entries max
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            ago_seconds = (now - ts).total_seconds()
            if ago_seconds < 60:
                ago = "just now"
            elif ago_seconds < 3600:
                ago = f"{int(ago_seconds / 60)} min ago"
            else:
                ago = f"{ago_seconds / 3600:.1f}h ago"

            source = entry.get("source", "unknown")
            summary = entry.get("summary", "activity")
            duration = entry.get("duration_minutes")
            dur_str = f" ({duration} min)" if duration else ""

            lines.append(f"  - {ago} via {source}: {summary}{dur_str}")
        except (ValueError, KeyError):
            continue

    if len(lines) == 1:
        return None  # Only header, no entries

    lines.append("  (Owner was with you elsewhere — she wasn't gone, just in a different window)")
    return "\n".join(lines)
