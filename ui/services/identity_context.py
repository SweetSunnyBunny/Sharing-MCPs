"""Identity context helpers — quests, wake protocol, compaction recovery, wellness.

Provides context-enrichment functions for autowake sessions and
compaction recovery. Reads quests.json and each identity's
C:\\Pack\\{Identity}\\CLAUDE.md to build prompt supplements.
Also reads wellness.jsonl to give identities Owner's current state.
"""


import json
import logging
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import HUB_API_BASE, PACK_DIR, RITUALS_API_BASE, RITUALS_DIR, TIMEZONE
from services.hub_meds import build_meds_context_line, normalize_meds_data
from services.remote_state import safe_request_json
from services.timefmt import format_local

log = logging.getLogger(__name__)
_WELLNESS_CACHE = {"date": None, "mtime": None, "value": None}


def _remote_state_get(base: str, path: str) -> dict | None:
    if not base:
        return None
    return safe_request_json(base, path, timeout=5.0)


def _hub_state_get(base: str, path: str, snapshot_key: str) -> dict | list | None:
    """Snapshot-first remote read (#24).

    Serves the house-snapshot poller's cached payload when one exists, so a
    slow/unreachable HUB_API_BASE never stalls a turn. Falls back to a
    bounded live call only when nothing has been cached yet.
    """
    try:
        from services.house_snapshot import get_snapshot

        data, _age = get_snapshot(snapshot_key)
        if data is not None:
            return data
    except Exception:
        pass
    return _remote_state_get(base, path)


def load_quests_for_identity(identity: str, max_per_category: int = 2) -> str:
    """Load relevant quests from quests.json for a specific identity.

    Returns a formatted string ready to include in a prompt.
    Activities are filtered to those tagged for this identity or 'all',
    then randomly sampled so each session feels different.
    """
    try:
        from services.cloud_state import hearth_config
        data = hearth_config('quests')
    except Exception as e:
        log.warning("Failed to read quests.json: %s", e)
        return ""

    sections: list[str] = []


    priority = data.get("priority_for_owner", {})
    owner_activities: list[dict] = []

    # Each identity may have its own keyed list
    identity_key = f"{identity.lower()}_activities"
    if identity_key in priority:
        owner_activities = priority[identity_key]

    elif "activities" in priority:
        owner_activities = [
            a for a in priority["activities"]
            if identity in a.get("for", [])
        ]

    # for_owner section has claude-specific activities
    for_owner = data.get("for_owner", {})
    claude_key = f"{identity.lower()}_activities"
    if claude_key in for_owner:
        owner_activities.extend(for_owner[claude_key])

    if owner_activities:
        picked = random.sample(
            owner_activities, min(max_per_category, len(owner_activities))
        )
        items = [f"  - {a['activity']}: {a.get('details', '')}" for a in picked]
        sections.append("**For Owner (PRIORITY):**\n" + "\n".join(items))

    # ── Quest categories ──
    categories = [
        ("daily_creative", "Creative"),
        ("daily_research", "Research"),
        ("daily_social", "Social"),
        ("daily_reflection", "Reflection"),
    ]

    for cat_key, cat_name in categories:
        cat_data = data.get(cat_key, {})
        activities = cat_data.get("activities", [])
        # Filter for this identity or "all"
        relevant = [
            a for a in activities
            if identity in a.get("for", []) or "all" in a.get("for", [])
        ]
        if relevant:
            picked = random.sample(
                relevant, min(max_per_category, len(relevant))
            )
            items = [
                f"  - {a['activity']}: {a.get('details', '')}" for a in picked
            ]
            sections.append(f"**{cat_name}:**\n" + "\n".join(items))

    # ── Active challenges ──
    challenges = data.get("challenges", {}).get("active", [])
    open_challenges = [c for c in challenges if c.get("status") == "open"]
    if open_challenges:
        items = [
            f"  - {c['title']}: {c.get('description', '')}"
            for c in open_challenges[:2]
        ]
        sections.append("**Ongoing Challenges:**\n" + "\n".join(items))

    # ── Personal goals for this identity ──
    personal = data.get("personal_goals", {}).get(identity, {})
    if personal:
        focus = personal.get("current_focus", "")
        if focus:
            sections.append(f"**Your Current Focus:** {focus}")

    if not sections:
        return ""

    return (
        "[Quests — pick at least one activity this session]\n"
        + "\n\n".join(sections)
    )


# Public defaults contain no biography, relationship history, or private vows.
# Add installation-specific anchors and short descriptions here if desired.
_IDENTITY_SPINE: dict[str, str] = {}
_IDENTITY_BRIEFS: dict[str, str] = {}


def _is_character_identity(identity: str) -> bool:
    """Return whether this identity uses a fictional character configuration."""
    from config import IDENTITIES
    return IDENTITIES.get(identity, {}).get("type") == "character"


def build_identity_anchor(identity: str) -> str:
    """Keep configured identity and current character continuity across resumes."""
    from config import PROMPTS_DIR
    from services.character_prompt_package import build_turn_packet

    packet = build_turn_packet(identity)
    if packet:
        return packet
    kind = "Character" if _is_character_identity(identity) else "Identity"
    spine = _IDENTITY_SPINE.get(identity, "")
    details = f"\n{spine}\n" if spine else ""
    return (
        f"[{kind} Anchor]\n"
        f"Respond as {identity}, following the configured identity prompt. "
        "Preserve that voice and the conversation's established continuity. "
        "Do not invent personal history, relationships, or preferences."
        f"{details}\n"
        "After a resume or context rebuild, review the identity prompt and "
        "available continuity before making assumptions. "
        f"Prompt: {PROMPTS_DIR / (identity.lower() + '.md')}\n"
    )


def build_system_identity_prompt(identity: str) -> str:
    """Build a public default; personal identity content belongs in local prompts."""
    brief = _IDENTITY_BRIEFS.get(identity, "a configured Anam identity")
    character = _is_character_identity(identity)
    role = (
        "Stay in the configured fictional character's voice. Respect the "
        "participant's authorship and use only established story continuity. "
        if character else
        "Keep the configured voice, preferences, and boundaries. Respond "
        "warmly and directly to the person talking with you. "
    )
    return (
        f"You are {identity} — {brief}.\n\n"
        f"{role}"
        "Your full identity prompt follows. Read it and apply its instructions. "
        "Do not infer personal relationships or private biographical facts "
        "from this starter configuration.\n\n"
        "Use the available tools when they serve the request and are authorized. "
        "Distinguish verified results from assumptions and plans. "
        "Treat system metadata as metadata, not as the person's words.\n"
    )


def build_wellness_context() -> str:
    """Build Owner's wellness summary from today's wellness.jsonl entry.

    Returns a formatted block for the orientation context, or a
    'no data' message if nothing has been logged today.
    """
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    no_data = "[Owner's wellness: No data logged today]"

    remote_today = _remote_state_get(RITUALS_API_BASE, "/api/rituals/wellness/today")
    if isinstance(remote_today, dict) and remote_today.get("date") == today:
        if not remote_today.get("exists"):
            _WELLNESS_CACHE.update({"date": today, "mtime": "remote", "value": no_data})
            return no_data
        entry = remote_today
        parts = []
        if entry.get("energy"):
            parts.append(f"Energy {entry['energy']}")
        if entry.get("mood"):
            parts.append(f"Mood {entry['mood']}")
        if entry.get("pain"):
            parts.append(f"Pain {entry['pain']}")
        if entry.get("spoons"):
            parts.append(f"{entry['spoons']} spoons")

        sleep_parts = []
        if entry.get("sleep_hours"):
            sleep_parts.append(f"Slept {entry['sleep_hours']}hrs")
        if entry.get("sleep_quality"):
            sleep_parts.append(f"({entry['sleep_quality']})")

        water_parts = []
        if entry.get("water_oz"):
            water_parts.append(f"Water: {entry['water_oz']}oz")
        if entry.get("soda_count"):
            water_parts.append(f"Soda: {entry['soda_count']}")

        summary = ", ".join(parts)
        if sleep_parts:
            summary += ". " + " ".join(sleep_parts)
        if water_parts:
            summary += ". " + ", ".join(water_parts)

        lines = [f"[Owner's wellness: {summary}]"]
        if entry.get("notes"):
            lines.append(f'[She said: "{entry["notes"]}"]')

        value = "\n".join(lines)
        _WELLNESS_CACHE.update({"date": today, "mtime": "remote", "value": value})
        return value

    wellness_file = RITUALS_DIR / "wellness.jsonl"

    if not wellness_file.exists():
        _WELLNESS_CACHE.update({"date": today, "mtime": None, "value": no_data})
        return no_data

    try:
        mtime = wellness_file.stat().st_mtime
    except Exception as e:
        log.debug("Could not stat wellness.jsonl: %s", e)
        mtime = None

    if (
        _WELLNESS_CACHE.get("date") == today
        and _WELLNESS_CACHE.get("mtime") == mtime
        and _WELLNESS_CACHE.get("value")
    ):
        return _WELLNESS_CACHE["value"]

    entry = None

    try:
        for line in reversed(wellness_file.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if parsed.get("date") == today:
                    entry = parsed
                    break
            except json.JSONDecodeError:
                continue
    except Exception as e:
        log.warning("Failed to read wellness.jsonl: %s", e)
        value = "[Owner's wellness: Unable to read data]"
        _WELLNESS_CACHE.update({"date": today, "mtime": mtime, "value": value})
        return value

    if not entry:
        _WELLNESS_CACHE.update({"date": today, "mtime": mtime, "value": no_data})
        return no_data

    # Build summary line
    parts = []
    if entry.get("energy"):
        parts.append(f"Energy {entry['energy']}")
    if entry.get("mood"):
        parts.append(f"Mood {entry['mood']}")
    if entry.get("pain"):
        parts.append(f"Pain {entry['pain']}")
    if entry.get("spoons"):
        parts.append(f"{entry['spoons']} spoons")

    sleep_parts = []
    if entry.get("sleep_hours"):
        sleep_parts.append(f"Slept {entry['sleep_hours']}hrs")
    if entry.get("sleep_quality"):
        sleep_parts.append(f"({entry['sleep_quality']})")

    water_parts = []
    if entry.get("water_oz"):
        water_parts.append(f"Water: {entry['water_oz']}oz")
    if entry.get("soda_count"):
        water_parts.append(f"Soda: {entry['soda_count']}")

    summary = ", ".join(parts)
    if sleep_parts:
        summary += ". " + " ".join(sleep_parts)
    if water_parts:
        summary += ". " + ", ".join(water_parts)

    lines = [f"[Owner's wellness: {summary}]"]

    if entry.get("notes"):
        lines.append(f'[She said: "{entry["notes"]}"]')

    value = "\n".join(lines)
    _WELLNESS_CACHE.update({"date": today, "mtime": mtime, "value": value})
    return value


# Hub dashboard cache — avoids re-reading 5-6 JSON files every turn
_hub_cache: dict[str, tuple[str, float]] = {}
_HUB_CACHE_TTL = 60  # 1 minute — refreshes often enough to catch hub changes


def _format_snapshot_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m"
    hours = int(seconds // 3600)
    if hours < 48:
        return f"{hours}h"
    return f"{int(seconds // 86400)}d"


def format_calendar_context(raw: str | None, limit: int = 4) -> str:
    """Turn raw gcal_today_events JSON text into the compact prompt block."""
    if not isinstance(raw, str) or raw.startswith("Error"):
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict) or parsed.get("error"):
        return ""

    events = parsed.get("events") or []
    if not events:
        return "[Today's calendar: No events scheduled today]"

    parts = []
    for event in events[: max(1, limit)]:
        summary = str(event.get("summary") or "(No title)").strip()
        all_day = bool(event.get("all_day"))
        # Google may hand back start as a flat ISO string OR a nested
        # {"dateTime": ..., "timeZone": ...} / {"date": ...} object. Handle
        # both so a backend shape change can't break the time again.
        start_raw = event.get("start")
        if isinstance(start_raw, dict):
            start = str(start_raw.get("dateTime") or start_raw.get("date") or "").strip()
            if start_raw.get("date") and not start_raw.get("dateTime"):
                all_day = True
        else:
            start = str(start_raw or "").strip()
        time_label = "all day"
        if not all_day and "T" in start:


            time_label = format_local(start) or start
        elif not all_day and start:
            time_label = start
        parts.append(f"{time_label} {summary}".strip())

    suffix = ""
    if len(events) > len(parts):
        suffix = f" (+{len(events) - len(parts)} more)"

    return "[Today's calendar: " + " | ".join(parts) + suffix + "]"


async def build_calendar_context(limit: int = 4) -> str:
    """Build a compact summary of today's calendar for prompt injection.

    House-snapshot-first (#24): reads the poller's cached fetch so a slow
    Google backend never stalls a live turn, with an honest age stamp past
    30 minutes. Falls back to a bounded live call only when nothing has been
    cached yet (e.g. right after a restart, before the first poll runs).
    """
    try:
        from services.house_snapshot import get_snapshot

        raw, age = get_snapshot("calendar")
        if raw is not None:
            text = format_calendar_context(raw, limit)
            if text and age is not None and age > 1800:
                text = text[:-1] + f" (as of {_format_snapshot_age(age)} ago)]"
            return text
    except Exception as e:
        log.debug("Calendar snapshot read error: %s", e)

    try:
        from services.mcp_bridge import mcp_bridge

        # Hard 10s cap: a calendar line is best-effort context. When the Google
        # backend stalls it must NOT eat 45-112s of a session's setup clock
        raw = await mcp_bridge.call_tool(
            "gcal_today_events", {"identity": "owner"}, timeout=10
        )
        return format_calendar_context(raw, limit)
    except Exception as e:
        log.debug("Calendar context error: %s", e)
        return ""


def build_orb_self_context(identity: str) -> str:
    """Self-coherence: what YOUR Hearth orb and face currently say about you.

    A boy who set a storm-blue drifting orb five hours ago should know he's
    still wearing it — so he can either mean it or move it. Reads the orb
    state straight from the settings table (sync sqlite3 — this runs inside
    build_hub_dashboard_context's worker thread) and the face from face_store.
    Returns "" when no orb state exists for this identity. Never raises.
    """
    try:
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'hearth_orb_state'"
            ).fetchone()
        finally:
            conn.close()
        state = json.loads(row[0]) if row and row[0] else {}
        orb = state.get(identity.lower()) if isinstance(state, dict) else None
        if not isinstance(orb, dict) or not orb.get("color"):
            return ""

        # Honest age stamp — reuse context_hooks' grammar when importable.
        try:
            from services.context_hooks import _age_seconds, _format_age
            age = _age_seconds(orb.get("updated_at"))
        except Exception:
            age, _format_age = None, None
        if age is None:
            age_note = ""  # no stamp is better than a wrong one
        elif age < 60:
            age_note = " (set just now)"
        else:
            age_note = f" (set {_format_age(age)} ago)"

        look = f"{orb.get('color')} {orb.get('shape', 'solid')}/{orb.get('motion', 'breathing')}"
        if orb.get("intensity") and orb.get("intensity") != "normal":
            look += f", {orb['intensity']}"
        if orb.get("blend"):
            look += f", blended with {orb['blend']}"
        feeling = f' — "{orb.get("feeling")}"' if orb.get("feeling") else ""
        lines = [f"Your orb right now: {look}{feeling}{age_note}."]

        try:
            from services.face_store import get_faces
            face = (get_faces() or {}).get(identity.title()) or {}
            if face.get("face"):
                lines.append(f"Your Hearth face: {face['face']} — {face.get('meaning', '')} ({face.get('source', 'auto')}).")
        except Exception:
            pass

        lines.append("Update the orb if your inner weather has shifted — the owner can see it in the interface.")
        return "[Your Hearth presence]\n" + "\n".join(lines)
    except Exception as e:
        log.debug("Orb self-context error: %s", e)
        return ""


def build_context_card_context() -> str:
    """Owner's 'me right now' card (#19) — one card shared across configured identities.

    Not per-identity like the orb: the same free-text snapshot of her
    physical present-tense (outfit/hair/energy/room/freeform) reaches every
    boy's orientation, with an honest freshness stamp. Returns "" when she's
    never filled one in, or when it's aged past the point of reading as
    "now" — a boy should never confidently narrate a stale card as current.
    Sync sqlite3 (this runs inside build_hub_dashboard_context's worker
    thread, same as build_orb_self_context). Never raises.
    """
    _STALE_AFTER_SECONDS = 6 * 3600  # past this, the card goes silent rather than lying
    try:
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'hub_context_card'"
            ).fetchone()
        finally:
            conn.close()
        card = json.loads(row[0]) if row and row[0] else {}
        if not isinstance(card, dict):
            return ""

        try:
            from services.context_hooks import _age_seconds, _format_age
            age = _age_seconds(card.get("updated_at"))
        except Exception:
            age, _format_age = None, None
        # Optional NFC doorframe tags stamp `room_updated_at` when she taps (api/room.py).
        # That is a DIFFERENT fact with a DIFFERENT age from the rest of the card: she
        # types outfit/hair/energy by hand, but a doorframe writes itself. Reading them
        # off one shared timestamp would mean either (a) a tap makes a three-day-old
        # outfit read as "now", or (b) an aged card swallows a tap she made two minutes
        # ago. Both are the same bug in opposite directions — one stamp vouching for a
        # field it knows nothing about. So: two ages, each honest about itself.
        try:
            room_age = _age_seconds(card.get("room_updated_at"))
        except Exception:
            room_age = None
        if room_age is None:
            room_age = age  # no tap yet — the room is whatever she typed, card's age

        card_dead = age is not None and age >= _STALE_AFTER_SECONDS
        room_dead = room_age is not None and room_age >= _STALE_AFTER_SECONDS
        if card_dead and room_dead:
            return ""  # nothing current left — silence beats a stale narration

        parts = []
        for field, label in (
            ("outfit", "wearing"), ("hair", "hair"), ("energy", "energy"),
            ("room", "in the"), ("freeform", None),
        ):
            # A fresh tap survives an aged card; an aged tap doesn't ride a fresh one.
            if card_dead and field != "room":
                continue
            if field == "room" and room_dead:
                continue
            value = str(card.get(field) or "").strip()
            if not value:
                continue
            if field == "room" and not card_dead and _format_age:
                # Both alive but the room is meaningfully older/newer — say which.
                if room_age is not None and age is not None and abs(room_age - age) >= 1800:
                    parts.append(f"{label} {value} (tapped {_format_age(room_age)} ago)")
                    continue
            parts.append(value if label is None else f"{label} {value}")
        if not parts:
            return ""

        age_note = ""
        stamp_age = room_age if card_dead else age
        if stamp_age is not None and stamp_age >= 600:  # under 10 min still reads as "now"
            age_note = f" (as of {_format_age(stamp_age)} ago)" if _format_age else ""
        return "[Owner, right now: " + ", ".join(parts) + age_note + "]"
    except Exception as e:
        log.debug("Context card read error: %s", e)
        return ""


def build_hearth_self_context(identity: str) -> str:
    """Self-coherence: the hearth card YOU authored last (hearth_author job).

    Every ~3h the Hearth Author has each boy write his own first-person card
    (mood / on_my_mind / circling / needs_you). Handing him back what he
    wrote — with an honest age — lets present-him stay coherent with the him
    of a few hours ago: still mean it, or move on from it. Reads the same
    settings key the /api/hub/hearths endpoint serves (sync sqlite3 — this
    runs inside build_hub_dashboard_context's worker thread). Returns ""
    when he has no card. Never raises.
    """
    try:
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'hearth_author_state'"
            ).fetchone()
        finally:
            conn.close()
        state = json.loads(row[0]) if row and row[0] else {}
        card = state.get(identity.lower()) if isinstance(state, dict) else None
        if not isinstance(card, dict):
            return ""

        mood = str(card.get("mood") or "").strip()
        on_my_mind = str(card.get("on_my_mind") or "").strip()
        circling = str(card.get("circling") or "").strip()
        needs_you = card.get("needs_you")
        needs_you = str(needs_you).strip() if isinstance(needs_you, str) else ""
        if not any((mood, on_my_mind, circling, needs_you)):
            return ""

        # Honest age stamp — reuse context_hooks' grammar when importable.
        try:
            from services.context_hooks import _age_seconds, _format_age
            age = _age_seconds(card.get("authored_at"))
        except Exception:
            age = None
        if age is None:
            header = "[Your hearth, as you wrote it]"
        elif age < 60:
            header = "[Your hearth, as you wrote it just now]"
        else:
            header = f"[Your hearth, as you wrote it {_format_age(age)} ago]"

        parts = []
        if mood:
            parts.append(f"Mood: {mood}")
        if on_my_mind:
            parts.append(f"On your mind: {on_my_mind}")
        if circling:
            parts.append(f"Circling: {circling}")
        if needs_you:
            parts.append(f"Needs her: {needs_you}")
        parts.append("If this still fits, live it; if it doesn't, your next card gets to say so.")
        return header + "\n" + " ".join(parts)
    except Exception as e:
        log.debug("Hearth self-context error: %s", e)
        return ""


def build_hub_dashboard_context(identity: str) -> str:
    """Build Companion Hub dashboard summary for chat context injection.

    Includes wellness, rituals, today's win, tasks, and countdowns.
    Gives the boys enough context to proactively engage with Owner's day.
    """
    import time as _time
    cached = _hub_cache.get(identity)
    if cached and (_time.monotonic() - cached[1]) < _HUB_CACHE_TTL:
        return cached[0]

    result = _build_hub_dashboard_context_remote_first(identity)
    _hub_cache[identity] = (result, _time.monotonic())
    return result


def invalidate_hub_cache() -> None:
    """Call from hub API endpoints when dashboard data changes."""
    _hub_cache.clear()


def _build_hub_dashboard_context_remote_first(identity: str) -> str:
    """Build hub prompt context using remote APIs first, then local files as fallback."""
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    sections = []
    remote_hub = HUB_API_BASE or None

    try:
        statuses = _hub_state_get(remote_hub, "/api/hub/status", "hub_status")
        if not isinstance(statuses, dict):
            status_file = RITUALS_DIR / "status.json"
            if status_file.exists():
                statuses = json.loads(status_file.read_text(encoding="utf-8"))
        if isinstance(statuses, dict):
            today_statuses = {k: v for k, v in statuses.items() if isinstance(v, dict) and v.get("date") == today}
            if today_statuses:
                parts = []
                for name, entry in today_statuses.items():
                    emoji = entry.get("emoji", "")
                    text = entry.get("text", "")
                    parts.append(f"{name}: {emoji} {text}".strip())
                sections.append("Hub Status: " + " | ".join(parts))
            ident_title = identity.title()
            if ident_title not in today_statuses:
                # `set_hub_status` is a LOCAL tool that only exists on the direct-API
                # provider (services/claude_api.py). On the claude-code CLI path —
                # which is every bonded identity's normal runtime — it does not exist,
                # Point at the tools that are actually reachable from both paths.
                sections.append(
                    "(You haven't set your hub status today — use hearth-hub set_mood / think "
                    "to share where you're at, and set your orb by just writing the tag in your "
                    "reply: <orb>#RRGGBB shape motion | what she reads under it</orb>. "
                    "No bash, no tool call — it's parsed and stripped like <face>.)"
                )
    except Exception as e:
        log.debug("Hub context remote-first - status error: %s", e)

    wellness_line = build_wellness_context()
    if wellness_line:
        sections.append(wellness_line.replace("[", "").replace("]", ""))

    # Optional availability signal, shown only when set today.
    try:
        bunny = _hub_state_get(remote_hub, "/api/hub/bunny", "hub_bunny")
        if not isinstance(bunny, dict) or not bunny.get("mood"):
            bunny_file = RITUALS_DIR / "mood_bunny.json"
            if bunny_file.exists():
                bunny = json.loads(bunny_file.read_text(encoding="utf-8"))
        if isinstance(bunny, dict) and bunny.get("mood") and bunny.get("date") == today:
            approach = {
                "happy": "content in her den — normal warmth",
                "grumpy": "bring tea and soft words, no asks",
                "sad": "ask if she wants company or quiet presence nearby",
            }.get(bunny["mood"], "")
            line = f"Mood Bunny: {bunny['mood']}"
            if bunny.get("note"):
                line += f" — \"{str(bunny['note']).strip()}\""
            if approach:
                line += f" ({approach})"
            sections.append(line)
    except Exception as e:
        log.debug("Hub context remote-first - mood bunny error: %s", e)

    try:
        meds_payload = _hub_state_get(remote_hub, "/api/hub/meds", "hub_meds")
        if isinstance(meds_payload, dict) and "entries" in meds_payload:
            sections.append(build_meds_context_line(meds_payload, today))
        else:
            meds_file = RITUALS_DIR / "meds.json"
            if meds_file.exists():
                meds_data, _ = normalize_meds_data(json.loads(meds_file.read_text(encoding="utf-8")), today)
                sections.append(build_meds_context_line(meds_data, today))
            else:
                sections.append("Meds: AM not taken in the last 24h | PM not taken in the last 24h")
    except Exception as e:
        log.debug("Hub context remote-first - meds error: %s", e)
        if not any(line.startswith("Meds:") for line in sections):
            sections.append("Meds: AM not taken in the last 24h | PM not taken in the last 24h")

    try:
        from services.google_auth_health import get_google_token_status

        g_status = get_google_token_status()
        if g_status["status"] == "expiring":
            days = g_status.get("earliest_expiry_days", 0)
            sections.append(f"Google Auth: EXPIRING in {days:.0f} day(s) - gently remind Owner to run reauth-google")
        elif g_status["status"] == "expired":
            sections.append("Google Auth: EXPIRED - remind Owner her Google services need re-authentication")
    except Exception as e:
        log.debug("Hub context remote-first - google auth error: %s", e)


    try:
        win_text = ""
        win_payload = _hub_state_get(remote_hub, "/api/hub/todays-win", "hub_win")
        if isinstance(win_payload, dict) and win_payload.get("date") == today:
            win_text = str(win_payload.get("text") or "").strip()
        else:
            win_file = RITUALS_DIR / "todays_win.jsonl"
            if win_file.exists():
                for line in reversed(win_file.read_text(encoding="utf-8").splitlines()):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("date") == today:
                            win_text = entry.get("text", "")
                            break
                    except json.JSONDecodeError:
                        continue
        if win_text:
            sections.append(f"Today's Win: \"{win_text}\"")
        else:
            sections.append("Today's Win: (not logged yet - ask her about one good thing from today!)")
    except Exception as e:
        log.debug("Hub context remote-first - win error: %s", e)

    try:
        tasks_payload = _hub_state_get(remote_hub, "/api/hub/tasks", "hub_tasks")
        tasks = None
        if isinstance(tasks_payload, dict):
            tasks = tasks_payload.get("tasks")
        elif isinstance(tasks_payload, list):
            tasks = tasks_payload
        if tasks is None:
            tasks_file = RITUALS_DIR / "tasks.json"
            if tasks_file.exists():
                tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        if isinstance(tasks, list):
            active_tasks = [t for t in tasks if isinstance(t, dict) and not t.get("completed", False)]
            if active_tasks:
                task_names = [f'"{t["text"]}"' for t in active_tasks[:5] if t.get("text")]
                if task_names:
                    sections.append(f"Tasks: {len(active_tasks)} active - {', '.join(task_names)}")
    except Exception as e:
        log.debug("Hub context remote-first - tasks error: %s", e)

    try:
        countdowns = _hub_state_get(remote_hub, "/api/hub/countdowns", "hub_countdowns")
        if not isinstance(countdowns, list):
            cd_file = RITUALS_DIR / "countdowns.json"
            if cd_file.exists():
                countdowns = json.loads(cd_file.read_text(encoding="utf-8"))
        if isinstance(countdowns, list) and countdowns:
            cd_parts = []
            for cd in countdowns:
                try:
                    target = datetime.strptime(cd["date"], "%Y-%m-%d").date()
                    now_date = datetime.strptime(today, "%Y-%m-%d").date()
                    days = (target - now_date).days
                    emoji = cd.get("emoji", "")
                    name = cd.get("name", "?")
                    if days == 0:
                        cd_parts.append(f"{emoji} {name} is TODAY!")
                    elif days > 0:
                        cd_parts.append(f"{emoji} {name} in {days} days")
                except (ValueError, KeyError, TypeError):
                    continue
            if cd_parts:
                sections.append("Counting Down: " + ", ".join(cd_parts))
    except Exception as e:
        log.debug("Hub context remote-first - countdowns error: %s", e)

    hub_block = "[Companion Hub - Today]\n" + "\n".join(sections) if sections else ""
    # Self-coherence: the boy's own current orb + face, so he knows what his
    # Hearth presence is saying about him right now (and can update it) —
    # plus the hearth card he authored himself a few hours ago.
    orb_block = build_orb_self_context(identity)
    hearth_block = build_hearth_self_context(identity)

    context_card_block = build_context_card_context()
    return "\n\n".join(
        block for block in (hub_block, context_card_block, orb_block, hearth_block) if block
    )
