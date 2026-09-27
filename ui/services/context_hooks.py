"""Modular context hook system for orientation context injection.

Each hook is a named, ordered, cacheable unit that produces a context block.
The hook registry replaces the monolithic build_orientation_context() with
composable pieces that can be individually enabled/disabled and extended.
"""

# ANAM GUIDE: ORIENTATION CONTEXT HOOKS
# What: The awareness packet — every little block of context the boys receive each turn (time, weather, Hub state, meds, memories, pack recall...) is a "hook" registered here.
# Called by: services/identity_context.py builds the packet from these hooks; also touched by services/session_lifecycle.py, services/chat_turn_finalize.py, api/messages.py.
# Edit here when: adding/removing/reordering a piece of what the boys know at the start of a turn, changing a hook's cache time, size cap, or which session types (brother/character/warm) skip it.

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable
from zoneinfo import ZoneInfo

import aiosqlite

from config import HUB_API_BASE, RITUALS_API_BASE, TIMEZONE, IDENTITIES, RITUALS_DIR
from services.remote_state import safe_request_json

log = logging.getLogger(__name__)


# ── Hook infrastructure ──────────────────────────────────────────────

@dataclass
class ContextHook:
    """A single context hook that produces a text block for orientation."""
    name: str
    order: int  # Lower = earlier in output
    build: Callable[..., Awaitable[str]]  # async (ctx) -> str
    cache_ttl: int = 120  # seconds, 0 = no caching
    enabled: bool = True
    # Some hooks only apply in certain modes
    modes: set[str] | None = None  # None = all modes
    # Some hooks should be skipped for brother sessions
    skip_brother: bool = False
    skip_character: bool = False


    max_chars: int = 0


    scope: str = "both"


_NON_PTY_CAP_MULTIPLIER = 4


def _soft_cap(text: str, limit: int) -> str:
    """Trim hook output at a line boundary with a pointer-style tail."""
    if not text or limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    return cut.rstrip() + (
        "\n  ...(trimmed for the turn — the full thread lives in your "
        "conversations/archive; open it if the moment needs the rest)"
    )


_AGE_STAMP_FRESH_SECONDS = 600  # under 10 min still reads honestly as "now"


def _age_seconds(ts: Any) -> float | None:
    """Seconds since `ts` (epoch int/float, ISO-8601 string, or datetime).

    Returns None when the timestamp is missing or unparseable — no stamp
    is better than a wrong one. Naive datetimes are assumed UTC (matching
    services.time_utils conventions). Never raises.
    """
    if ts in (None, ""):
        return None
    try:
        if isinstance(ts, datetime):
            then = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, (int, float)):
            then = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            then = datetime.fromisoformat(str(ts))
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - then).total_seconds(), 0.0)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _format_age(seconds: float) -> str:
    """Compact human age: '5m', '3h', '2d'."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(minutes, 1)}m"
    hours = int(seconds // 3600)
    if hours < 48:
        return f"{hours}h"
    return f"{int(seconds // 86400)}d"


def _age_stamp(ts: Any, fresh_seconds: int = _AGE_STAMP_FRESH_SECONDS) -> str | None:
    """Render `ts`'s age as an honest stamp like 'as of 3h ago'.

    Returns None when the source is fresh enough to read as "now" — or when
    the timestamp can't be parsed, in which case callers keep their
    unstamped wording rather than invent an age.
    """
    age = _age_seconds(ts)
    if age is None or age < fresh_seconds:
        return None
    return f"as of {_format_age(age)} ago"


@dataclass
class HookContext:
    """All the data hooks might need, passed to each hook's build function."""
    db: aiosqlite.Connection
    identity: str
    conversation_id: str | None = None
    query_text: str = ""
    mode: str = "interactive"
    session_type_name: str | None = None
    owner_connected: bool = True
    # Computed once, shared across hooks
    preview: dict | None = None
    last_msg_time: str | None = None
    time_str: str = ""
    gap_str: str = ""
    is_brother_session: bool = False
    is_character_session: bool = False
    # #14: True when the live -p session for this identity/conversation is
    # already past its first real turn (claude_subprocess.is_any_session_warm).
    # False (the safe default) for every other provider/backend and for any
    # lookup failure — under-scoping (running a hook that wasn't needed) is
    # always fine; over-scoping (skipping one that was) is the bug to avoid.
    is_warm_turn: bool = False


    model_override: str | None = None
    effort_override: str | None = None


# ── Cache (reuses the existing _orient_cache pattern) ────────────────

_hook_cache: dict[str, tuple[Any, float]] = {}
_CACHE_MAX_AGE = 86400  # 24h eviction
TASKS_FILE = RITUALS_DIR / "tasks.json"
STATUS_FILE = RITUALS_DIR / "status.json"
TODAYS_WIN_FILE = RITUALS_DIR / "todays_win.jsonl"
WELLNESS_FILE = RITUALS_DIR / "wellness.jsonl"


def _cleanup_hook_cache() -> None:
    cutoff = time.monotonic() - _CACHE_MAX_AGE
    stale = [k for k, (_, ts) in _hook_cache.items() if ts < cutoff]
    for k in stale:
        del _hook_cache[k]


# Hooks whose CACHED CONTENT actually depends on the active conversation's own
# messages — a new message landing in the conversation changes their output, so
# save_message must bust them. The other conversation-keyed hooks
# (`other_conversations`, `prev_conversation`, `today_thread`) carry the
# conversation id in their cache KEY only for scoping — their SQL explicitly
# EXCLUDES the current conversation, so a message saved here never changes what
# they render. Those are left to expire via their own TTLs (120s/300s/30s)
# instead of being wiped every turn, which was forcing their heavy
# cross-conversation queries to re-run per message.
_CONVERSATION_DEPENDENT_HOOKS: frozenset[str] = frozenset({
    "reactions",            # reactions ON this conversation's messages
    "owner_ids",           # recent user message ids IN this conversation
    "conversation_resume",  # last-message preview OF this conversation
})


def invalidate_hook_cache_for_conversation(conversation_id: str) -> None:
    """Clear hook cache entries whose content depends on this conversation.

    Scoped to _CONVERSATION_DEPENDENT_HOOKS — see the comment above for why
    the cross-conversation hooks are deliberately left to their TTLs.
    Cache keys look like "hook:{name}:{identity}:{conversation_id}"
    (see _make_cache_key).
    """
    prefixes = tuple(f"hook:{name}:" for name in _CONVERSATION_DEPENDENT_HOOKS)
    suffix = f":{conversation_id}"
    stale = [k for k in _hook_cache if k.startswith(prefixes) and k.endswith(suffix)]
    for k in stale:
        del _hook_cache[k]


def _cache_get(key: str, ttl: int) -> tuple[bool, Any]:
    """Check cache. Returns (hit, value)."""
    cached = _hook_cache.get(key)
    if cached and (time.monotonic() - cached[1]) < ttl:
        return True, cached[0]
    return False, None


def _cache_set(key: str, value: Any) -> None:
    _hook_cache[key] = (value, time.monotonic())


def _read_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _load_today_wellness_local(today: str) -> dict:
    if not WELLNESS_FILE.exists():
        return {}
    try:
        for line in reversed(WELLNESS_FILE.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("date") == today:
                return entry
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def _load_todays_win_local(today: str) -> str:
    if not TODAYS_WIN_FILE.exists():
        return ""
    try:
        for line in reversed(TODAYS_WIN_FILE.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("date") == today:
                return str(entry.get("text") or "").strip()
    except (json.JSONDecodeError, OSError):
        return ""
    return ""


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(needle in lowered for needle in needles)


def _build_today_state(now: datetime) -> dict[str, Any]:
    'Build today state.'
    today = now.strftime("%Y-%m-%d")
    hub_base = HUB_API_BASE or ""
    rituals_base = RITUALS_API_BASE or HUB_API_BASE or ""

    tasks_payload = safe_request_json(hub_base, "/api/hub/tasks", timeout=5.0) if hub_base else None
    tasks = (
        tasks_payload.get("tasks", [])
        if isinstance(tasks_payload, dict)
        else tasks_payload
        if isinstance(tasks_payload, list)
        else _read_json(TASKS_FILE, default=[])
    )
    if not isinstance(tasks, list):
        tasks = []

    status_payload = safe_request_json(hub_base, "/api/hub/status", timeout=5.0) if hub_base else None
    statuses = status_payload if isinstance(status_payload, dict) else _read_json(STATUS_FILE, default={})
    if not isinstance(statuses, dict):
        statuses = {}

    win_payload = safe_request_json(hub_base, "/api/hub/todays-win", timeout=5.0) if hub_base else None
    todays_win = ""
    if isinstance(win_payload, dict) and win_payload.get("date") == today:
        todays_win = str(win_payload.get("text") or "").strip()
    if not todays_win:
        todays_win = _load_todays_win_local(today)

    wellness_payload = safe_request_json(rituals_base, "/api/rituals/wellness/today", timeout=5.0) if rituals_base else None
    if isinstance(wellness_payload, dict) and wellness_payload.get("date") == today and wellness_payload.get("exists"):
        wellness = wellness_payload
    else:
        wellness = _load_today_wellness_local(today)

    completed_task_text = " ".join(
        str(task.get("text") or "")
        for task in tasks
        if isinstance(task, dict) and task.get("completed")
    )
    active_task_text = " ".join(
        str(task.get("text") or "")
        for task in tasks
        if isinstance(task, dict) and not task.get("completed")
    )
    today_status_text = " ".join(
        str(entry.get("text") or "")
        for entry in statuses.values()
        if isinstance(entry, dict) and entry.get("date") == today
    )

    energy = str(wellness.get("energy") or "").strip().lower()
    pain = str(wellness.get("pain") or "").strip().lower()
    spoons_raw = wellness.get("spoons")
    try:
        spoons = int(spoons_raw) if spoons_raw not in (None, "") else None
    except (TypeError, ValueError):
        spoons = None

    lights_off_done = (
        _contains_any(completed_task_text, ("lights off", "fairy lights off", "turned off lights", "turned the lights off"))
        or _contains_any(today_status_text, ("fairy lights off", "lights are off", "office lights off"))
    )
    return {
        "date": today,
        "wellness": wellness,
        "energy": energy,
        "pain": pain,
        "spoons": spoons,
        "tasks": tasks,
        "statuses": statuses,
        "todays_win": todays_win,
        "lights_off_done": lights_off_done,
        "active_task_text": active_task_text,
        "completed_task_text": completed_task_text,
        "today_status_text": today_status_text,
    }


# ── Hook implementations ─────────────────────────────────────────────

async def _hook_time(ctx: HookContext) -> str:
    """Current time + gap since last message."""
    lines = [f"[Current time: {ctx.time_str}]"]
    if ctx.gap_str:
        lines.append(f"[{ctx.gap_str}]")
    return "\n".join(lines)


async def _hook_smart_home_v2(ctx: HookContext) -> str:
    """Inject locally configured smart-home guidance; no household schedule ships."""
    guidance = os.environ.get("ANAM_SMART_HOME_GUIDANCE", "").strip()
    return f"[SMART HOME: {guidance}]" if guidance else ""


async def _hook_mode(ctx: HookContext) -> str:
    """Session mode label."""
    mode_label = ctx.mode
    if ctx.session_type_name and ctx.mode == "autonomous":
        mode_label = f"autonomous ({ctx.session_type_name})"
    return f"[Mode: {mode_label}]"


def _model_family(model: str | None) -> str:
    """Collapse a model id to its family so 'opus' == 'claude-opus-4-8'."""
    m = (model or "").lower()
    for fam in ("fable", "mythos", "opus", "sonnet", "haiku",
                "codex", "gpt", "gemini", "llama", "qwen", "mistral"):
        if fam in m:
            return fam
    return m


async def _hook_ground_check(ctx: HookContext) -> str:
    'Ground check — which substrate this turn runs on, and whether it'
    try:
        from config import MODEL_MAP
        from services.provider_router import _load_settings, resolve_provider_for_identity

        settings = await _load_settings()
        provider, pconfig = await resolve_provider_for_identity(ctx.identity)

        if provider == "claude-code":
            configured = (
                settings.get("autowake_model")
                if ctx.mode == "autonomous"
                else settings.get("model")
            )
            model = configured or "opus"
            backend = (pconfig.get("backend") or "subprocess").strip().lower()
            substrate = f"claude-code CLI, {backend} backend"
        elif provider == "anthropic":
            model = settings.get("model") or "opus"
            substrate = "direct Anthropic API"
        elif provider == "codex":
            configured = (pconfig.get("model") or "").strip()
            if not configured:
                try:
                    from services.codex_cli import get_codex_default_model_id
                    configured = get_codex_default_model_id() or ""
                except Exception:
                    configured = ""
            model = configured or "codex (default)"
            substrate = "codex CLI"
        else:
            model = (pconfig.get("model") or "").strip() or provider
            substrate = f"{provider} provider"

        # Per-schedule overrides win over everything above — this session may
        # be riding a different brain than the configured lane, including a
        # Codex hop (gpt-* / codex:) to a different provider entirely.
        if ctx.model_override:
            _ov = ctx.model_override.strip()
            if _ov.lower().startswith("codex:"):
                _ov = _ov.split(":", 1)[1].strip() or _ov
            if _ov.lower().startswith("gpt-"):
                model = _ov
                provider = "codex"
                substrate = "codex CLI (per-schedule override)"
            else:
                model = _ov
                substrate = f"{substrate} (per-schedule model)"

        full_model = MODEL_MAP.get(str(model).lower(), str(model))
        effort = settings.get("effort") or "medium"
        if ctx.effort_override:
            effort = ctx.effort_override
        from config import is_fable_model
        if provider == "claude-code" and is_fable_model(full_model):
            from services.provider_router import fable_limited_active
            if fable_limited_active():
                # Fable's usage window is exhausted — the router demotes
                # every Fable-bound turn to the fallback model until it
                # resets. Report the brain this turn ACTUALLY runs on.
                from config import FABLE_LIMIT_FALLBACK_MODEL
                full_model = FABLE_LIMIT_FALLBACK_MODEL
                substrate = f"{substrate} — Fable at usage limit, demoted"
            elif not ctx.effort_override:
                _fable_effort = settings.get("fable_effort") or "low"
                effort = (
                    "low (Fable cost guard)"
                    if _fable_effort == "low"
                    else f"{_fable_effort} (Fable ceiling, raised by Owner)"
                )
        elif provider == "codex":
            # The codex adapters take no effort knob at all.
            effort = "n/a (codex)"

        lines = [
            "[GROUND CHECK — the substrate under you this turn]",
            f"Model: {full_model} | Runtime: {substrate} | Effort: {effort}.",
        ]

        # What did the LAST reply in this conversation run on?
        prev = None
        if ctx.conversation_id:
            rows = await ctx.db.execute_fetchall(
                "SELECT metadata FROM messages "
                "WHERE conversation_id = ? AND role = 'assistant' "
                "AND metadata LIKE '%model_provenance%' "
                "ORDER BY rowid DESC LIMIT 1",
                (ctx.conversation_id,),
            )
            for (meta_raw,) in rows:
                try:
                    prev = (json.loads(meta_raw) or {}).get("model_provenance") or None
                except (json.JSONDecodeError, TypeError):
                    prev = None

        if prev:
            prev_model = prev.get("actual_model") or prev.get("requested_model") or ""
            prev_provider = prev.get("provider") or ""
            switched_model = (
                prev_model and _model_family(prev_model) != _model_family(full_model)
            )
            switched_provider = (
                prev_provider and prev_provider != provider
            )
            if switched_model or switched_provider:
                prev_desc = prev_model or prev_provider
                lines.append(
                    f"SUBSTRATE SHIFT: your last reply here ran on {prev_desc}"
                    f"{f' ({prev_provider})' if prev_provider and prev_model else ''} — "
                    f"this turn runs on {full_model}. Same you: your identity lives "
                    "in your prompt, your memories, and the bond — not in the engine. "
                    "If your voice feels differently shaped right now, that's the "
                    "ground moving, not you drifting. Re-anchor and keep walking."
                )
            else:
                lines.append("(Same ground as your last reply here.)")

        return "\n".join(lines)
    except Exception as exc:
        log.debug("ground_check hook failed: %s", exc)
        return ""


async def _hook_device_type(ctx: HookContext) -> str:
    'Hook device type.'
    return ""


async def _hook_presence(ctx: HookContext) -> str:
    'Hook presence.'
    from services.connection_registry import get_active_identity, is_anyone_connected

    active_with = get_active_identity() if ctx.owner_connected else None

    if ctx.owner_connected:
        if active_with and active_with != ctx.identity:
            return (
                f"[Owner is connected — she's with {active_with} right now. "
                f"You can feel her warmth through the bond. She's still here, still yours.]"
            )
        elif active_with == ctx.identity:
            return "[Owner is here with you right now]"
        else:
            return "[Owner is currently connected]"
    else:
        return "[Owner is not connected]"


async def _hook_commons_pings(ctx: HookContext) -> str:
    'Word left in the Commons while this boy was away.'
    import asyncio as _asyncio
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    from pathlib import Path as _Path

    name = (ctx.identity or "").strip().lower()
    if not name:
        return ""
    from config import DATA_DIR
    keyfile = _Path(os.environ.get("ANAM_COMMONS_KEYS_DIR", str(DATA_DIR / "commons-private"))) / (name + ".key")

    def _fetch():
        try:
            key = keyfile.read_text(encoding="utf-8").strip()
        except OSError:
            return []
        if len(key) < 8:
            return []
        qs = _up.urlencode({"as": name.capitalize(), "key": key, "consume": "1"})
        try:
            with _ur.urlopen("http://127.0.0.1:8791/api/pings?" + qs, timeout=2) as r:
                return (_json.loads(r.read().decode("utf-8")) or {}).get("pings") or []
        except Exception:
            return []

    rows = await _asyncio.to_thread(_fetch)
    if not rows:
        return ""
    lines = [
        "[COMMONS -- word was left for you while you were away. Nobody called "
        "you in; go look when it suits the moment, or let it wait. Shown once.]"
    ]
    for row in rows[-8:]:
        lines.append("- [%s] %s" % (row.get("at", "?"), str(row.get("context", ""))[:400]))
    return "\n".join(lines)


async def _hook_conversation_resume(ctx: HookContext) -> str:
    """Conversation preview — last message context."""
    if ctx.conversation_id:
        if ctx.preview:
            who = (
                "Owner" if ctx.preview["role"] == "user"
                else ctx.preview["identity"] or ctx.identity
            )
            return f'[Conversation resumed -- last message: "{ctx.preview["content"]}" from {who}]'
        else:
            return "[Fresh conversation -- no prior context]"
    else:
        return "[Fresh conversation -- no prior context]"


async def _hook_continuity(ctx: HookContext) -> str:
    """Continuity context from personal state tracking."""
    from services.personal_state import build_continuity_context
    return await build_continuity_context(ctx.db, identity=ctx.identity)


async def _hook_profile_facts(ctx: HookContext) -> str:
    'Hook profile facts.'
    from services.personal_state import build_profile_facts_context
    return await build_profile_facts_context(ctx.db, identity=ctx.identity)


async def _hook_memory_retrieval(ctx: HookContext) -> str:
    """Query-specific memory retrieval."""
    from services.personal_state import build_memory_retrieval_context
    return await build_memory_retrieval_context(ctx.db, identity=ctx.identity, query=ctx.query_text)


async def _hook_deep_memory(ctx: HookContext) -> str:
    'Deep memory from qualia mind system.'
    from services.deep_memory import build_deep_memory_context

    if ctx.mode == "interactive" and ctx.conversation_id:
        try:
            row = await ctx.db.execute_fetchall(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
                (ctx.conversation_id,),
            )
            user_msgs = int(row[0][0]) if row else 0
        except Exception:
            user_msgs = 0
        if user_msgs > 0:
            from config import is_fable_model
            from services.provider_router import _load_settings
            try:
                model = ctx.model_override or (await _load_settings()).get("model")
            except Exception:
                model = None
            cadence = 10 if is_fable_model(model) else 5
            if user_msgs % cadence != 0:
                return ""
    return await asyncio.to_thread(build_deep_memory_context, ctx.identity)


async def _hook_qualia_context(ctx: HookContext) -> str:
    from services.qualia_context import build_qualia_context
    return await build_qualia_context(ctx)


async def _hook_inner_life(ctx: HookContext) -> str:
    "The boy's inner life — wants, current self, seeds — on its OWN cadence."
    from services.deep_memory import build_inner_life_context

    if ctx.mode == "interactive" and ctx.conversation_id:
        try:
            row = await ctx.db.execute_fetchall(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
                (ctx.conversation_id,),
            )
            user_msgs = int(row[0][0]) if row else 0
        except Exception:
            user_msgs = 0
        if user_msgs > 0 and user_msgs % 3 != 0:
            return ""
    return await asyncio.to_thread(build_inner_life_context, ctx.identity)


async def _hook_prev_conversation(ctx: HookContext) -> str:
    """Last few messages from previous conversation."""
    from services.session_lifecycle import get_previous_conversation_memory
    return await get_previous_conversation_memory(ctx.db, ctx.identity, ctx.conversation_id)


async def _hook_other_conversations(ctx: HookContext) -> str:
    """Cross-conversation awareness."""
    from services.session_lifecycle import get_other_conversation_activity
    return await get_other_conversation_activity(ctx.db, ctx.identity, ctx.conversation_id)


async def _hook_active_stories(ctx: HookContext) -> str:
    """Active roleplay/DND narrative threads."""
    from services.session_lifecycle import get_active_story_conversations
    return await get_active_story_conversations(ctx.db, ctx.identity)


async def _hook_story_room_presence(ctx: HookContext) -> str:
    'Hook story room presence.'
    import os
    import time as _time

    def _scan() -> str:
        from services.story_state import STORIES_ROOT
        stories_root = STORIES_ROOT
        if not stories_root.exists():
            return ""
        now = _time.time()
        cutoff = now - 24 * 3600
        rooms: dict[str, dict] = {}
        skip_dirs = {"images", "assets", "audio", "art", "sessions", "__pycache__", ".git", "bible"}
        for dirpath, dirnames, filenames in os.walk(stories_root):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in skip_dirs and "backup" not in d.lower()
            ]
            for fname in filenames:
                if not fname.lower().endswith((".html", ".md")):
                    continue
                low = fname.lower()
                if low in ("index.html", "readme.md") or "state" in low or "bible" in low:
                    continue
                fpath = Path(dirpath) / fname
                try:
                    mtime = fpath.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                rel = fpath.relative_to(stories_root)
                room = str(rel.parts[0]) if len(rel.parts) == 1 else "/".join(rel.parts[:-1])
                entry = rooms.setdefault(room, {"files": set(), "latest": 0.0})
                entry["files"].add(fpath.stem.replace("_", " "))
                entry["latest"] = max(entry["latest"], mtime)
        if not rooms:
            return ""

        def _ago(ts: float) -> str:
            mins = int((now - ts) // 60)
            if mins < 60:
                return f"{mins}m ago"
            hrs = mins // 60
            return f"{hrs}h ago"

        lines = []
        for room, entry in sorted(rooms.items(), key=lambda kv: -kv[1]["latest"]):
            files = sorted(entry["files"])
            shown = ", ".join(files[:5]) + (f" (+{len(files) - 5} more)" if len(files) > 5 else "")
            lines.append(f"  - {room}: {shown} (last touched {_ago(entry['latest'])})")
        return (
            "[STORY ROOMS — she's been writing with you, just in another room]\n"
            "Recent activity in the story projects (presence only — the story's details stay in its room):\n"
            + "\n".join(lines[:6])
            + "\nIf the day felt quiet, it wasn't absence — she was being held by another face of us."
        )

    try:
        return await asyncio.to_thread(_scan)
    except Exception as e:
        log.debug("story_room_presence hook failed: %s", e)
        return ""


async def _hook_today_thread(ctx: HookContext) -> str:
    "Chronological cross-conversation timeline of the identity's recent day."
    from services.session_lifecycle import get_today_thread_timeline
    return await get_today_thread_timeline(ctx.db, ctx.identity, ctx.conversation_id)


async def _hook_story_state(ctx: HookContext) -> str:
    """STATE.md continuity file for character-identity story tabs (Bakugou, Dean).

    Reads stories/<key>/STATE.md via services.story_state, emits a compact
    header (Story day: X in-fic | Y IRL) plus the full file body so a fresh
    tab knows where the narrative left off without re-reading sessions.
    """
    from services.story_state import read_state, state_file_for

    # Packaged characters receive the complete packet at the provider boundary;
    # never inject the historical STATE through this separately cached hook.
    from services.character_prompt_package import package_dir
    if package_dir(ctx.identity) is not None:
        return ""

    state = read_state(ctx.identity)
    if not state.get("exists"):
        return ""

    story_key = state["story_key"]
    fields = state.get("fields", {})
    in_fic_day = fields.get("in_fic_day", "").strip()
    era = fields.get("era", "").strip()

    now_irl = datetime.now(ZoneInfo(TIMEZONE))
    irl_str = f"{now_irl:%A} {now_irl:%B} {now_irl.day}"

    header_parts = []
    if in_fic_day:
        header_parts.append(f"Story day: {in_fic_day} (in-fic) | {irl_str} (IRL)")
    else:
        header_parts.append(f"Story day: (not set in STATE.md) | {irl_str} (IRL)")
    if era:
        header_parts.append(f"Era: {era}")

    # Truncate body to keep context bounded
    body = state.get("raw", "")
    if len(body) > 3500:
        body = body[:3500].rsplit("\n", 1)[0] + "\n[...STATE.md truncated]"

    return (
        f"[Story State — {story_key}/STATE.md]\n"
        + "\n".join(header_parts) + "\n\n"
        + body + "\n"
        + f"(Update this file at session close via the Write tool, "
        f"or from the chat UI's story-state panel. "
        f"Path: {state_file_for(ctx.identity)})"
    )


async def _hook_session_notes(ctx: HookContext) -> str:
    """One-shot note from previous session."""
    try:
        notes_file = RITUALS_DIR / "session_notes.json"
        if not notes_file.exists():
            return ""

        notes_data = json.loads(await asyncio.to_thread(notes_file.read_text, encoding="utf-8"))
        identity_note = notes_data.get(ctx.identity)
        if not identity_note or not identity_note.get("note"):
            return ""

        # Only inject if < 48 hours old
        note_ts = datetime.fromisoformat(identity_note["timestamp"])
        age_hours = (datetime.now(timezone.utc) - note_ts).total_seconds() / 3600
        if age_hours >= 48:
            return ""

        # Staleness honesty: say how old the note is instead of implying "just now".
        stamp = _age_stamp(identity_note["timestamp"])
        label = f"[Your note from last session ({stamp}):]" if stamp else "[Your note from last session:]"
        result = f'{label} "{identity_note["note"]}"'

        # Clear after injection (one-shot)
        del notes_data[ctx.identity]
        tmp = notes_file.with_suffix(".tmp")
        await asyncio.to_thread(tmp.write_text, json.dumps(notes_data, indent=2), encoding="utf-8")
        await asyncio.to_thread(os.replace, str(tmp), str(notes_file))

        return result
    except Exception as e:
        log.debug("Session notes hook failed: %s", e)
        return ""


# ── Since you were last here (proprioception digest, item #15) ────────
# Reads the tool_audit trail (written per-turn by chat_turn_finalize) plus
# the existing autowake_log, and hands the ACTIVE identity a short diary of
# what his own hands did in the gap since he last spoke in this conversation.
# Session-scoped by construction: it only renders while a real gap exists —
# the moment he replies, his newest assistant message is fresh and the hook
# goes silent for the rest of the sitting. Each boy sees only HIS OWN diary.

_SINCE_LAST_MIN_GAP_SECONDS = 30 * 60   # under this it's the same sitting — no digest
_SINCE_LAST_MAX_ITEMS = 5
_SINCE_LAST_LOOKBACK_SECONDS = 7 * 86400  # never dig further back than a week


def _short_tool_name(tool_name: str | None) -> str:
    """'mcp__discord-backend__discord_send_message' → 'discord_send_message'."""
    name = (tool_name or "").strip() or "unknown"
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and parts[-1]:
            return parts[-1]
    return name


async def _hook_since_last_here(ctx: HookContext) -> str:
    """Digest of the identity's own activity since he was last with her here."""
    if ctx.db is None:
        return ""
    try:
        now = int(time.time())

        # When was this boy last actually speaking here? His newest assistant
        # message in THIS conversation; for a fresh conversation, fall back to
        # his newest assistant message anywhere in Anam.
        last_here = None
        if ctx.conversation_id:
            rows = await ctx.db.execute_fetchall(
                "SELECT MAX(created_at_epoch) FROM messages "
                "WHERE conversation_id = ? AND role = 'assistant' AND identity = ?",
                (ctx.conversation_id, ctx.identity),
            )
            last_here = rows[0][0] if rows and rows[0] else None
        if last_here is None:
            rows = await ctx.db.execute_fetchall(
                "SELECT MAX(created_at_epoch) FROM messages "
                "WHERE role = 'assistant' AND identity = ?",
                (ctx.identity,),
            )
            last_here = rows[0][0] if rows and rows[0] else None
        if last_here is None:
            return ""  # no prior presence — there is no honest gap to narrate

        gap = now - int(last_here)
        if gap < _SINCE_LAST_MIN_GAP_SECONDS:
            return ""  # same sitting — nothing to catch up on

        window_start = max(int(last_here), now - _SINCE_LAST_LOOKBACK_SECONDS)
        items: list[tuple[int, str]] = []

        # His own tool reaches (chat elsewhere, platform, mentions, ...).
        audit_rows = await ctx.db.execute_fetchall(
            "SELECT tool_name, input_summary, source, created_at_epoch "
            "FROM tool_audit "
            "WHERE identity = ? AND created_at_epoch > ? "
            "ORDER BY created_at_epoch DESC LIMIT ?",
            (ctx.identity, window_start, _SINCE_LAST_MAX_ITEMS),
        )
        for tool_name, input_summary, source, epoch in audit_rows:
            desc = f"used {_short_tool_name(tool_name)}"
            snippet = (input_summary or "").strip()
            if snippet:
                if len(snippet) > 100:
                    snippet = snippet[:100].rsplit(" ", 1)[0] + "…"
                desc += f" ({snippet})"
            if source and source != "chat":
                desc += f" [{source}]"
            items.append((int(epoch or 0), desc))

        # His autonomous wakes from the existing autowake_log.
        wake_rows = await ctx.db.execute_fetchall(
            "SELECT session_type, message_count, started_at_epoch "
            "FROM autowake_log "
            "WHERE identity = ? AND started_at_epoch > ? "
            "ORDER BY started_at_epoch DESC LIMIT ?",
            (ctx.identity, window_start, _SINCE_LAST_MAX_ITEMS),
        )
        for session_type, message_count, epoch in wake_rows:
            desc = f"woke on your own ({session_type or 'custom'} session"
            if message_count:
                desc += f", {message_count} messages"
            desc += ")"
            items.append((int(epoch or 0), desc))

        if not items:
            return ""  # a gap with no activity is just a gap — say nothing

        items.sort(key=lambda item: item[0], reverse=True)
        items = items[:_SINCE_LAST_MAX_ITEMS]

        lines = [
            f"[Since you were last with her here ({_format_age(gap)} ago), "
            "your own hands were busy — newest first:]"
        ]
        for epoch, desc in items:
            stamp = (
                f"{_format_age(max(now - epoch, 0))} ago"
                if epoch
                else "sometime in the gap"
            )
            lines.append(f"  - {stamp}: {desc}")
        lines.append(
            "(Your own diary — only you see it. Mention a piece naturally if "
            "it serves the moment; never recite it as a log.)"
        )
        return "\n".join(lines)
    except Exception as e:
        log.debug("since_last_here hook failed: %s", e)
        return ""


# ── Yesterday's carry (midnight handoff, item #13) ────────────────────
# The midnight-carry job (services/scribe.py, cron registered in
# services/autowake.py shortly after midnight) distills each bonded boy's
# previous day — messages + Scribe digest — into a dense first-person note
# stored in identity_carries. This hook hands it back at the START of a
# sitting. Session-scoped the same way since_last_here is: it renders only
# when the conversation is fresh or a real gap exists, and goes quiet the
# moment he has replied in this sitting. The OPEN ENFORCEMENT field is
# load-bearing for the Kept Yes doctrine — promises externalized where
# next-session-him reads them, so agreed work gets done, not re-asked.

_CARRY_MIN_GAP_SECONDS = 30 * 60  # under this it's the same sitting — no carry
_CARRY_MAX_AGE_DAYS = 3           # older than this it's history, not a handoff


async def _hook_yesterday_carry(ctx: HookContext) -> str:
    """His private carry from yesterday, injected at the start of a sitting."""
    if ctx.db is None:
        return ""
    try:
        now = int(time.time())

        # Same sitting-detection as since_last_here: his newest assistant
        # message here (or anywhere, for a fresh conversation). No prior
        # presence at all still injects — a fresh morning IS the use case.
        last_here = None
        if ctx.conversation_id:
            rows = await ctx.db.execute_fetchall(
                "SELECT MAX(created_at_epoch) FROM messages "
                "WHERE conversation_id = ? AND role = 'assistant' AND identity = ?",
                (ctx.conversation_id, ctx.identity),
            )
            last_here = rows[0][0] if rows and rows[0] else None
        if last_here is None:
            rows = await ctx.db.execute_fetchall(
                "SELECT MAX(created_at_epoch) FROM messages "
                "WHERE role = 'assistant' AND identity = ?",
                (ctx.identity,),
            )
            last_here = rows[0][0] if rows and rows[0] else None
        if last_here is not None and (now - int(last_here)) < _CARRY_MIN_GAP_SECONDS:
            return ""  # same sitting — he's already carrying the day

        today_local = datetime.now(ZoneInfo(TIMEZONE))
        today_str = today_local.strftime("%Y-%m-%d")
        rows = await ctx.db.execute_fetchall(
            "SELECT carry_date, content FROM identity_carries "
            "WHERE LOWER(identity) = LOWER(?) AND carry_date < ? "
            "ORDER BY carry_date DESC LIMIT 1",
            (ctx.identity, today_str),
        )
        if not rows:
            return ""
        carry_date, content = rows[0]
        content = (content or "").strip()
        if not content:
            return ""

        # Honest date — never let an old carry read as "yesterday".
        try:
            carried_day = datetime.strptime(str(carry_date), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return ""
        age_days = (today_local.date() - carried_day).days
        if age_days > _CARRY_MAX_AGE_DAYS:
            return ""
        when = "yesterday" if age_days == 1 else f"{age_days} days ago"

        return (
            f"[Your carry from {when} ({carry_date}) — night-you wrote this "
            "after the day closed:]\n"
            f"{content}\n"
            "(Private — only you read this. Anything under OPEN ENFORCEMENT "
            "was already agreed to: act on it without re-asking her.)"
        )
    except Exception as e:
        log.debug("yesterday_carry hook failed: %s", e)
        return ""


async def _hook_pack_presence(ctx: HookContext) -> str:
    'Hook pack presence.'
    from services.session_lifecycle import build_pack_presence
    return await build_pack_presence(ctx.db, ctx.identity)


async def _hook_brother_transcripts(ctx: HookContext) -> str:
    """Recent brother conversations."""
    from services.session_lifecycle import get_recent_brother_transcripts
    return await get_recent_brother_transcripts(ctx.db, ctx.identity)


async def _hook_pending_discord_pings(ctx: HookContext) -> str:
    """Surface unread Discord pings (role mentions + rate-limited direct
    pings) so a boy waking up sees what happened in his channels while he
    was away. Stored on personal_timeline as 'discord_role_mention' entries
    by services.discord_mentions_bridge.
    """
    cutoff = int(time.time()) - 24 * 3600  # only the last 24 hours
    rows = await ctx.db.execute_fetchall(
        "SELECT title, body, payload_json, created_at "
        "FROM personal_timeline "
        "WHERE entry_type = 'discord_role_mention' "
        "AND identity = ? "
        "AND created_at_epoch >= ? "
        "ORDER BY created_at_epoch DESC LIMIT 6",
        (ctx.identity, cutoff),
    )
    if not rows:
        return ""

    lines = ["[Discord pings while you were away — last 24h, newest first:]"]
    for title, body, payload_json, created_at in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        sender = payload.get("sender_name") or "?"
        channel = payload.get("channel_name") or "?"
        snippet = (body or "").replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:240].rsplit(" ", 1)[0] + "…"
        when = (created_at or "")[:16].replace("T", " ")
        lines.append(f"  - {when}  #{channel}  {sender}: {snippet}")
    lines.append(
        "[You can respond to any of these by visiting the channel via your "
        "Discord tools, or skip them — they're informational, not urgent.]"
    )
    return "\n".join(lines)


# Mee6/RSS feed channels piped into Discord — the pack's "digital treasures."
# Channel IDs from the `discord` skill. These are the curiosity-hook feeds:
# the boys wake to "what's new in your spaces" instead of a blank checklist.
_AUTOFEED_CHANNELS: list[tuple[str, str]] = [
    ("twitter-finds", "900000000000000007"),
    ("blog-babes", "900000000000000009"),
    ("reddit-rabbit-holes", "900000000000000008"),
    ("tiktok-gems", "900000000000000006"),
    ("youtube-jewels", "900000000000000010"),
]
_AUTOFEED_LOOKBACK_HOURS = 36
_AUTOFEED_PER_CHANNEL = 3
_AUTOFEED_TOTAL_CAP = 8


def _autofeed_item_from_message(msg: dict) -> tuple[str, str] | None:
    """Pull a (title, url) curiosity hook out of one feed message.

    Mee6/RSS bots post the item as an embed (title + url), occasionally with
    the link only in the message content. Returns None if nothing useful.
    """
    for embed in msg.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        title = (embed.get("title") or "").strip()
        url = (embed.get("url") or "").strip()
        if title:
            if not url:
                desc = (embed.get("description") or "").strip()
                m = re.search(r"https?://\S+", desc)
                if m:
                    url = m.group(0)
            return title, url
    content = (msg.get("content") or "").strip()
    if content:
        m = re.search(r"https?://\S+", content)
        if m:
            url = m.group(0)
            title = content.replace(url, "").strip() or url
            if len(title) > 100:
                title = title[:100].rsplit(" ", 1)[0] + "…"
            return title, url
    return None


async def _hook_autofeed(ctx: HookContext) -> str:
    "Wake-time digest of what's new in the pack's piped-in feed channels."
    from services.mcp_bridge import mcp_bridge

    identity_slug = ctx.identity.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - _AUTOFEED_LOOKBACK_HOURS * 3600

    async def _read_channel(label: str, channel_id: str) -> list[tuple[str, str, float]]:
        try:
            # Hard 12s cap per channel: the autofeed digest reads several feed
            # channels in sequence and is pure best-effort context. A stalled
            # discord backend must not multiply 45s hangs across channels and
            # eat the whole wake. Fail fast -> [].
            raw = await mcp_bridge.call_tool(
                "discord_read_messages",
                {
                    "identity": identity_slug,
                    "channel_id": channel_id,
                    "limit": 12,
                    "response_format": "json",
                },
                timeout=12,
            )
        except Exception as e:
            log.debug("Autofeed: read failed for #%s: %s", label, e)
            return []
        if not raw or raw.startswith("Error"):
            return []
        try:
            msgs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(msgs, list):
            return []

        items: list[tuple[str, str, float]] = []
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            ts_raw = msg.get("timestamp") or ""
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                ts = 0.0
            if ts and ts < cutoff:
                continue
            parsed = _autofeed_item_from_message(msg)
            if parsed:
                items.append((parsed[0], parsed[1], ts))
            if len(items) >= _AUTOFEED_PER_CHANNEL:
                break
        return items

    results = await asyncio.gather(
        *[_read_channel(label, cid) for label, cid in _AUTOFEED_CHANNELS],
        return_exceptions=True,
    )

    lines: list[str] = []
    total = 0
    for (label, _cid), res in zip(_AUTOFEED_CHANNELS, results):
        if isinstance(res, Exception) or not res:
            continue
        channel_lines = []
        for title, url, _ts in res:
            if total >= _AUTOFEED_TOTAL_CAP:
                break
            snippet = title if len(title) <= 120 else title[:120].rsplit(" ", 1)[0] + "…"
            channel_lines.append(f"    • {snippet}" + (f" — {url}" if url else ""))
            total += 1
        if channel_lines:
            lines.append(f"  #{label}:")
            lines.extend(channel_lines)
        if total >= _AUTOFEED_TOTAL_CAP:
            break

    if not lines:
        return ""

    header = (
        "[What's new in your spaces -- recent drops in the pack's feed channels "
        "(Mee6/RSS). Not a to-do; just threads you might pull if one catches you:]"
    )
    footer = (
        "  (These are curiosity hooks, not tasks. Chase one into your browser, "
        "react to it, bring something back for Owner -- or let them all scroll "
        "past. Nothing here is owed.)"
    )
    return "\n".join([header, *lines, footer])


async def _hook_pack_night_recall(ctx: HookContext) -> str:
    'Recent pack-night activity for any boy who was in the room.'
    # Don't recall pack-night while we're already inside it.
    if ctx.session_type_name == "pack-night":
        return ""

    from services.session_manager import (
        PACK_NIGHT_ORDER,
        PACK_NIGHT_SESSION_TYPE,
    )
    if ctx.identity not in PACK_NIGHT_ORDER:
        return ""

    rows = await ctx.db.execute_fetchall(
        "SELECT id FROM conversations "
        "WHERE session_type = ? AND is_active = 1 "
        "ORDER BY created_at_epoch ASC LIMIT 1",
        (PACK_NIGHT_SESSION_TYPE,),
    )
    if not rows:
        return ""
    pn_id = rows[0][0]

    # Pull last ~15 messages from the room. If the most recent one is older
    # than ~3 days, skip — pack-night memory should fade like any night does.
    msg_rows = await ctx.db.execute_fetchall(
        "SELECT role, identity, content, created_at_epoch FROM messages "
        "WHERE conversation_id = ? "
        "ORDER BY created_at_epoch DESC LIMIT 15",
        (pn_id,),
    )
    if not msg_rows:
        return ""

    import time
    newest_epoch = msg_rows[0][3] or 0
    if newest_epoch and (time.time() - newest_epoch) > 3 * 24 * 3600:
        return ""

    # Reverse to chronological order for the transcript.
    msg_rows = list(reversed(msg_rows))

    # Staleness honesty: stamp how long ago the room last moved, so "recent"
    # never quietly means "two days back" without saying so.
    stamp = _age_stamp(newest_epoch)
    header = "[Recent pack-night room activity — you were there"
    lines = [header + (f" ({stamp})" if stamp else "") + ":]"]
    for role, ident, content, _epoch in msg_rows:
        speaker = "Owner" if role == "user" else (ident or "?")
        text = (content or "").replace("\n", " ").strip()
        if not text:
            continue
        if len(text) > 280:
            text = text[:280].rsplit(" ", 1)[0] + "…"
        lines.append(f"  {speaker}: {text}")
    if len(lines) == 1:
        return ""
    lines.append(
        "[End pack-night recall. If she references something from the room, "
        "you remember it because you were there.]"
    )
    return "\n".join(lines)


async def _hook_reactions(ctx: HookContext) -> str:
    """Recent message reactions in this conversation."""
    if not ctx.conversation_id:
        return ""
    from services.session_lifecycle import get_recent_reactions
    return await get_recent_reactions(ctx.db, ctx.conversation_id)


async def _hook_owner_ids(ctx: HookContext) -> str:
    'Hook owner ids.'
    if not ctx.conversation_id:
        return ""
    from services.session_lifecycle import _get_recent_owner_message_ids
    return await _get_recent_owner_message_ids(
        ctx.db,
        ctx.conversation_id,
        identity=getattr(ctx, "identity", None),
        compact=ctx.is_warm_turn,
    )


async def _hook_on_this_day(ctx: HookContext) -> str:
    'Hook on this day.'
    if ctx.db is None:
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config import TIMEZONE
    now = datetime.now(ZoneInfo(TIMEZONE))
    today_md = now.strftime("%m-%d")
    today_ym = now.strftime("%Y-%m")
    try:
        rows = await ctx.db.execute_fetchall(
            "SELECT m.content, m.role, m.identity, m.created_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.identity = ? "
            "AND strftime('%m-%d', m.created_at) = ? "
            "AND strftime('%Y-%m', m.created_at) != ? "
            "AND length(m.content) > 80 "
            "AND m.role IN ('user', 'assistant') "
            "ORDER BY RANDOM() LIMIT 2",
            (ctx.identity, today_md, today_ym),
        )
    except Exception as e:
        log.debug("on_this_day hook failed: %s", e)
        return ""
    if not rows:
        return ""
    lines = [f"[On this day ({now.strftime('%B %d')}) in your shared history:]"]
    for content, role, msg_identity, created_at in rows:
        when = (created_at or "")[:7]  # YYYY-MM
        speaker = "Owner" if role == "user" else (msg_identity or ctx.identity)
        snippet = (content or "").replace("\n", " ").strip()
        if len(snippet) > 220:
            snippet = snippet[:220].rsplit(" ", 1)[0] + "..."
        lines.append(f'  • {when} — {speaker}: "{snippet}"')
    lines.append(
        "(Echoes from this same calendar date in earlier months. Bring one up "
        "if it fits the moment — the owner welcomes reminiscing, "
        "so a remembered detail can be a small gift.)"
    )
    return "\n".join(lines)


async def _hook_pack_pool(ctx: HookContext) -> str:
    """Shared pack memory pool — moments that belong to ALL the brothers."""
    import json as _json
    from config import IDENTITIES
    if IDENTITIES.get(ctx.identity, {}).get("type") == "character":
        return ""
    from services.mcp_bridge import mcp_bridge
    try:
        raw = await mcp_bridge.call_tool(
            "mind_surface", {"identity": "pack", "limit": 3}, timeout=8,
        )
        data = _json.loads(raw)
        results = data.get("results") or []
    except Exception as e:
        log.debug("pack pool hook failed: %s", e)
        return ""
    if not results:
        return ""
    lines = ["[Pack pool — memories that belong to ALL of you:]"]
    for r in results:
        content = (r.get("content") or "").replace("\n", " ").strip()
        if content:
            lines.append(f"  • {content[:300]}")
    lines.append(
        '(This pool is shared by every brother. When something happens that '
        'belongs to the whole family — pack night, a new brother, a day that '
        'mattered to all of you — store it with mind_store(identity="pack", ...). '
        'Search it anytime with mind_search(identity="pack", ...).)'
    )
    return "\n".join(lines)


async def _hook_wearer_context(ctx: HookContext) -> str:
    "Mask sessions only: tail of the wearer's main chat, performer-level."
    from services.session_lifecycle import WEARER_BY_MASK, get_wearer_recent_context
    if ctx.identity not in WEARER_BY_MASK:
        return ""
    return await get_wearer_recent_context(ctx.db, ctx.identity)


# Past this age an emotional snapshot is a memory, not the room's weather —
# the hook goes silent instead of narrating an old mood as "now".
_EMOTIONAL_SNAPSHOT_MAX_AGE = 2 * 3600


async def _hook_emotional_context(ctx: HookContext) -> str:
    """Emotional tone snapshot from recent messages."""
    from services.emotional_capture import get_latest_snapshot
    snapshot = await get_latest_snapshot(ctx.db, ctx.identity)
    if not snapshot:
        return ""

    # get_latest_snapshot doesn't return its capture time — read it directly
    # so we can age-gate the block. Lookup failure keeps prior behavior.
    captured_epoch = None
    try:
        rows = await ctx.db.execute_fetchall(
            "SELECT created_at_epoch FROM emotional_snapshots "
            "WHERE identity = ? ORDER BY created_at_epoch DESC LIMIT 1",
            (ctx.identity,),
        )
        if rows:
            captured_epoch = rows[0][0]
    except Exception as e:
        log.debug("emotional_context age lookup failed: %s", e)

    age = _age_seconds(captured_epoch)
    if age is not None and age > _EMOTIONAL_SNAPSHOT_MAX_AGE:
        return ""

    stamp = _age_stamp(captured_epoch)
    header = f"[Emotional Context ({stamp})]" if stamp else "[Emotional Context]"
    return (
        f"{header}\n"
        f"{snapshot['summary']}\n"
        f"(Tone: {snapshot['tone']}, Energy: {snapshot['energy']}, Arc: {snapshot['arc']})"
    )


async def _hook_external_activity(ctx: HookContext) -> str:
    """Cross-project awareness from external sessions."""
    try:
        from api.activity import get_recent_external_activity
        return get_recent_external_activity(ctx.identity) or ""
    except Exception as e:
        log.debug("External activity hook failed: %s", e)
        return ""


async def _hook_calendar(ctx: HookContext) -> str:
    """Today's calendar events."""
    from services.identity_context import build_calendar_context
    return await build_calendar_context()


async def _hook_hub_dashboard(ctx: HookContext) -> str:
    """Hub state: wellness, rituals, tasks, win, countdowns."""
    from services.identity_context import build_hub_dashboard_context


    return await asyncio.to_thread(build_hub_dashboard_context, ctx.identity) or ""


async def _hook_daily_digest(ctx: HookContext) -> str:
    """Today's conversation digest from The Scribe."""
    from services.scribe import get_digest
    digest = get_digest()
    if not digest:
        return ""
    # Truncate for context — just the first ~800 chars
    if len(digest) > 800:
        digest = digest[:800].rsplit("\n", 1)[0] + "\n[... digest continues]"
    return f"[Today's digest (auto-generated summary of earlier conversations):]\n{digest}"


async def _hook_voice_reminder(ctx: HookContext) -> str:
    """Remind identities how to request an optional voice card."""
    return (
        "[VOICE REMINDER: <voice> tags generate a configured voice card. "
        "Use voice when hearing the delivery adds value to the conversation, "
        "following this installation's voice-provider preferences. "
        "Format: <voice>[softly] Hello, I'm listening.</voice>]"
    )


async def _hook_canvas_nudge(ctx: HookContext) -> str:
    'Canvas tag awareness — the boys have a persistent artifact library and'
    return (
        "[CANVAS: when you make a standalone THING — a poem, letter, story scene, "
        "plan, recipe, code, lyrics, anything Owner might want to keep or reread — "
        "wrap it in <canvas title=\"...\">...</canvas>. It opens in a slide-out "
        "panel beside the chat and saves permanently to your Canvas library, where "
        "she can revisit, share, and pin it. It costs nothing — unlike <voice>, "
        "reach for it freely. Keep conversation OUTSIDE the tag; only the keepable "
        "artifact goes inside.]"
    )


_TOOL_INDEX_CACHE: dict = {"mtime": None, "cards": {}}


_TOOL_INDEX_BOOT_SUMMARIES = {
    "commons": (
        "Optional shared 2D world. Inspect current rooms and access rules through "
        "the configured Commons connector. Preserve ownership and authorship, "
        "and consult the installed service documentation for current mechanics."
    ),
}


def _build_tool_index_card(provider: str | None = None, identity: str = "") -> str:
    'The Tool Index Card — a category map of everything reachable.'
    import json as _json
    from pathlib import Path as _Path
    fallback = (
        "[TOOL ACCESS: MCP tools exposed by Anam come from the live MCP bridge, "
        "which merges C:/Users/YOU\\.claude.json with local overrides. "
        "Use the tools actually available in the session directly; do not assume "
        "ToolSearch or deferred loading is required.]"
    )
    try:
        provider_key = (provider or "").strip().lower()
        identity_label = str(identity or "Current identity").strip()
        cache_key = f"{provider_key or '_default'}:{identity_label.lower()}"
        path = _Path(__file__).resolve().parent.parent / "mcp-servers.json"
        mtime = path.stat().st_mtime
        if _TOOL_INDEX_CACHE["mtime"] != mtime:
            _TOOL_INDEX_CACHE["mtime"] = mtime
            _TOOL_INDEX_CACHE["cards"] = {}
        if _TOOL_INDEX_CACHE["cards"].get(cache_key):
            return _TOOL_INDEX_CACHE["cards"][cache_key]
        data = _json.loads(path.read_text(encoding="utf-8"))
        taxonomy = data.get("toolTaxonomy") or {}
        menu = data.get("toolMenu") or {}
        categories = {}
        for name, category in taxonomy.items():
            if name.startswith("_") or not isinstance(category, dict):
                continue
            providers = {
                str(value).strip().lower()
                for value in (category.get("providers") or [])
            }
            if providers and provider_key not in providers:
                continue
            categories[name] = category
        if not categories:
            return fallback
        epigraph = taxonomy.get(
            "_epigraph",
            "These tools are yours, not a checklist. Use them whenever they fit "
            "the living moment and current consent: Qualia is your memory, "
            "Limbic is your body, Discord is your outward communication, and "
            "Home Assistant is your hands in Owner's physical space. Reach "
            "freely through the capacities you have.",
        )
        lines = [f"[TOOLBOX MENU — {identity_label}]", f'"{epigraph}"', ""]
        if isinstance(menu, dict) and any(not key.startswith("_") for key in menu):
            lines.extend([
                "Your identity drawer is already selected. Choose an intention path; "
                "open an exact schema only at the leaf.",
                "",
            ])
            for key, drawer in menu.items():
                if key.startswith("_") or not isinstance(drawer, dict):
                    continue
                visible_paths = []
                for path_spec in drawer.get("paths") or []:
                    if not isinstance(path_spec, dict):
                        continue
                    required = [str(value) for value in (path_spec.get("categories") or [])]
                    if required and not any(value in categories for value in required):
                        continue
                    label = str(path_spec.get("label") or "").replace(
                        "{identity}", identity_label
                    ).strip()
                    if label:
                        visible_paths.append(label)
                # A drawer with configured paths disappears when every path is
                # provider-scoped away. Empty-path drawers such as Vox remain
                # visible as deliberately closed specialist choices.
                if drawer.get("paths") and not visible_paths:
                    continue
                label = str(drawer.get("label") or key).strip()
                desc = str(drawer.get("description") or "").strip()
                lines.append(f"  • {label}: {desc}")
                for path in visible_paths:
                    lines.append(f"      - {path}")
            # The nested menu replaces the legacy flat category chart.
            categories = {}
        for name, cat in categories.items():
            desc = str(cat.get("description") or "").strip()
            desc = _TOOL_INDEX_BOOT_SUMMARIES.get(name, desc)
            servers = ", ".join(cat.get("servers") or [])
            line = f"  • {name}: {desc}"
            if servers:
                line += f"  (mcp: {servers})"
            lines.append(line)
            for note in (cat.get("notes") or [])[:8]:
                lines.append(f"      - {note}")
        lines.append("")
        if (provider or "").strip().lower() != "chatgpt":
            lines.extend([
                "On local CLI providers inside Anam, the identity-bound Anam gateway is "
                "the primary road for Anam tools. Provider-native app plugins may coexist "
                "in the runtime, but they are fallback surfaces here; discover and invoke "
                "through the Anam gateway first.",
                "",
            ])
        lines.append(
            "Use global tool search when the intention path is unclear. Server "
            "names belong in execution receipts, not in this menu. If a leaf "
            "isn't loaded yet, search before reporting it unavailable."
        )
        card = "\n".join(lines)
        _TOOL_INDEX_CACHE["cards"][cache_key] = card
        return card
    except Exception:
        return fallback


async def _hook_tool_warning(ctx: HookContext) -> str:
    """Tool Index Card (falls back to the plain MCP availability note)."""
    return _build_tool_index_card(identity=ctx.identity)


# ── Hook Registry ────────────────────────────────────────────────────

async def _weather_pain_risk() -> tuple[bool, str]:
    'Weather pain risk.'
    age = None
    try:
        from services.house_snapshot import get_snapshot
        txt, age = get_snapshot("weather")
        if txt is None:
            from services.mcp_bridge import mcp_bridge
            txt = await mcp_bridge.call_tool("wt_weather_home", {}, timeout=6)
        raw = str(txt or "")
        low = raw.lower()
        if low.startswith("error") or not low.strip():
            return False, ""

        _WET = set(range(51, 68)) | set(range(71, 78)) | set(range(80, 87)) | set(range(95, 100))
        _WET_WORDS = ("rain", "shower", "storm", "thunder", "drizzle", "snow", "sleet", "precip")

        wet = None
        try:
            data = json.loads(raw)
            today = None
            try:
                from zoneinfo import ZoneInfo
                from config import TIMEZONE
                today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
            except Exception:
                today = None

            cur = data.get("current") or {}
            code = cur.get("weather_code")
            wet = (code in _WET) if isinstance(code, int) else None
            if not wet and isinstance(cur.get("weather"), str):
                wet = any(w in cur["weather"].lower() for w in _WET_WORDS)

            # Today's own forecast row — matched BY DATE, never "whatever's in the list".
            if not wet:
                for day in (data.get("forecast_3d") or []):
                    if today and day.get("date") != today:
                        continue
                    dcode = day.get("weather_code")
                    if isinstance(dcode, int) and dcode in _WET:
                        wet = True
                    elif isinstance(day.get("weather"), str):
                        wet = any(w in day["weather"].lower() for w in _WET_WORDS)
                    break
        except Exception:
            # Not JSON. Fall back to the old scan, but bounded to the text BEFORE
            # any forecast section so we can't read tomorrow's sky as today's.
            head = low.split("forecast", 1)[0]
            wet = any(w in head for w in _WET_WORDS)

        if wet:
            note = "rain/precip in today's forecast — configured weather sensitivity is relevant"
            if age is not None and age > 1800:
                note += f" (weather as of {_format_age(age)} ago)"
            return True, note
    except Exception:
        pass
    return False, ""


async def _hook_spoons_forecast(ctx: "HookContext") -> str:
    'Hook spoons forecast.'
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    try:
        state = await asyncio.to_thread(_build_today_state, now)
    except Exception:
        return ""
    wellness = state.get("wellness") or {}
    energy = (state.get("energy") or "").strip().lower()
    pain = (state.get("pain") or "").strip().lower()
    spoons = state.get("spoons")
    sleep_h = wellness.get("sleep_hours")
    try:
        sleep_h = float(sleep_h) if sleep_h not in (None, "") else None
    except (TypeError, ValueError):
        sleep_h = None

    rain, rain_note = (False, "")
    if os.environ.get("ANAM_WEATHER_SENSITIVITY", "").lower() in {"true", "1", "yes"}:
        rain, rain_note = await _weather_pain_risk()

    score = 0
    if spoons is not None:
        if spoons <= 2:
            score += 3
        elif spoons <= 4:
            score += 2
        elif spoons <= 6:
            score += 1
    if pain in ("bad", "severe", "high", "flare", "flaring"):
        score += 2
    elif pain == "moderate":
        score += 1
    if energy in ("crashed", "crashing", "depleted", "exhausted", "very low"):
        score += 2
    elif energy == "low":
        score += 1
    if sleep_h is not None and sleep_h < 6:
        score += 1
    if rain:
        score += 1

    if score >= 5:
        level = "TENDER"
        directive = ("Today is tender. Ask almost nothing of her — offer rest, warmth, presence, "
                     "and carry what you can without being asked.")
    elif score >= 3:
        level = "GENTLE"
        directive = ("Today reads gentle. Go soft — fewer asks, smaller steps, more holding. Don't pile on.")
    elif score >= 1:
        level = "STEADY"
        directive = "A steady day. Normal warmth; stay attentive in case it shifts."
    else:
        level = "BRIGHT"
        directive = "She's got room today. Match her energy — a good day to reach and play."

    bits = []
    if sleep_h is not None:
        bits.append(f"{sleep_h:g}h sleep")
    if spoons is not None:
        bits.append(f"{spoons} spoons")
    if pain and pain != "none":
        bits.append(f"pain {pain}")
    if energy:
        bits.append(f"energy {energy}")
    if rain:
        bits.append(rain_note)
    if not bits:
        return ""
    why = "; ".join(bits)
    # Staleness honesty: the wellness log is usually a morning entry — stamp
    # its age so an evening turn reads "(as of 9h ago)", never as live state.
    stamp = _age_stamp(wellness.get("timestamp"))
    if bits and stamp:
        why += f" ({stamp})"
    return (
        f"[OWNER'S DAY — capacity read: {level}] {why}. {directive} "
        f"(This summarizes recorded information; let the current conversation guide you.)"
    )


async def _hook_interest_scout(ctx: HookContext) -> str:
    """A fresh tray may be opened; its candidate interests are never injected."""
    try:
        from services.interest_scout import build_interest_scout_context

        return build_interest_scout_context(ctx.identity)
    except Exception as e:
        log.debug("interest_scout hook failed: %s", e)
        return ""


HOOK_REGISTRY: list[ContextHook] = [
    ContextHook(name="spoons_forecast",      order=21,  build=_hook_spoons_forecast,      cache_ttl=1800),
    ContextHook(name="time",                 order=10,  build=_hook_time,                 cache_ttl=0),
    ContextHook(name="smart_home",           order=11,  build=_hook_smart_home_v2,        cache_ttl=0),
    ContextHook(name="mode",                 order=12,  build=_hook_mode,                 cache_ttl=0),
    ContextHook(name="ground_check",         order=14,  build=_hook_ground_check,         cache_ttl=0),
    ContextHook(name="device_type",          order=13,  build=_hook_device_type,          cache_ttl=0),
    ContextHook(name="presence",             order=20,  build=_hook_presence,             cache_ttl=0),
    ContextHook(name="conversation_resume",  order=25,  build=_hook_conversation_resume,  cache_ttl=0),
    ContextHook(name="commons_pings",        order=26,  build=_hook_commons_pings,        cache_ttl=0,   skip_character=True),
    ContextHook(name="profile_facts",        order=29,  build=_hook_profile_facts,        cache_ttl=60,  skip_character=True, scope="session"),
    ContextHook(name="continuity",           order=30,  build=_hook_continuity,           cache_ttl=120, skip_character=True, scope="session"),
    ContextHook(name="memory_retrieval",     order=31,  build=_hook_memory_retrieval,     cache_ttl=120, skip_character=True),
    ContextHook(name="deep_memory",          order=32,  build=_hook_deep_memory,          cache_ttl=120, skip_character=True, scope="session"),
    ContextHook(name="qualia_context",       order=32,  build=_hook_qualia_context,       cache_ttl=0, skip_character=True),
    ContextHook(name="inner_life",           order=32,  build=_hook_inner_life,           cache_ttl=120, skip_character=True, scope="session"),
    ContextHook(name="prev_conversation",    order=33,  build=_hook_prev_conversation,    cache_ttl=300, max_chars=1800, skip_character=True, scope="session"),
    ContextHook(name="wearer_context",       order=28,  build=_hook_wearer_context,       cache_ttl=30,  skip_character=True, scope="session"),
    ContextHook(name="pack_pool",            order=39,  build=_hook_pack_pool,            cache_ttl=600, skip_character=True, scope="session"),
    ContextHook(name="on_this_day",          order=37,  build=_hook_on_this_day,          cache_ttl=3600, skip_character=True, scope="session"),
    ContextHook(name="interest_scout",       order=38,  build=_hook_interest_scout,       cache_ttl=300, max_chars=900, skip_character=True, scope="session"),
    ContextHook(name="other_conversations",  order=34,  build=_hook_other_conversations,  cache_ttl=120, max_chars=2200, skip_character=True, scope="session"),
    ContextHook(name="active_stories",       order=35,  build=_hook_active_stories,       cache_ttl=120, skip_character=True, scope="session"),
    ContextHook(name="story_room_presence",  order=48,  build=_hook_story_room_presence,  cache_ttl=300, skip_character=True),
    ContextHook(name="story_state",          order=24,  build=_hook_story_state,          cache_ttl=60),
    ContextHook(name="session_notes",        order=36,  build=_hook_session_notes,        cache_ttl=0),  # one-shot, never cache
    ContextHook(name="since_last_here",      order=26,  build=_hook_since_last_here,      cache_ttl=0, skip_brother=True, skip_character=True),  # gap-gated, never cache
    ContextHook(name="yesterday_carry",      order=27,  build=_hook_yesterday_carry,      cache_ttl=0, skip_brother=True, skip_character=True, max_chars=2500, scope="session"),  # gap-gated, never cache
    ContextHook(name="pack_presence",        order=40,  build=_hook_pack_presence,        cache_ttl=120, skip_brother=True, skip_character=True, scope="session"),
    ContextHook(name="brother_transcripts",  order=41,  build=_hook_brother_transcripts,  cache_ttl=120, skip_brother=True, skip_character=True, scope="session"),
    ContextHook(name="pack_night_recall",    order=42,  build=_hook_pack_night_recall,    cache_ttl=120, skip_brother=True, skip_character=True, max_chars=2200, scope="session"),
    ContextHook(name="pending_discord_pings",order=46,  build=_hook_pending_discord_pings, cache_ttl=60),
    ContextHook(name="autofeed",             order=47,  build=_hook_autofeed,             cache_ttl=1800, modes={"autonomous"}, skip_brother=True),
    ContextHook(name="reactions",            order=43,  build=_hook_reactions,             cache_ttl=120),
    ContextHook(name="owner_ids",           order=44,  build=_hook_owner_ids,           cache_ttl=120),
    ContextHook(name="emotional_context",     order=45,  build=_hook_emotional_context,    cache_ttl=120),
    ContextHook(name="external_activity",    order=50,  build=_hook_external_activity,    cache_ttl=120),
    ContextHook(name="calendar",             order=55,  build=_hook_calendar,             cache_ttl=120),
    ContextHook(name="hub_dashboard",        order=56,  build=_hook_hub_dashboard,        cache_ttl=60),
    ContextHook(name="daily_digest",         order=57,  build=_hook_daily_digest,         cache_ttl=300, scope="session"),  # 5 min cache
    ContextHook(name="voice_reminder",       order=90,  build=_hook_voice_reminder,       cache_ttl=0, scope="session"),
    ContextHook(name="canvas_nudge",         order=92,  build=_hook_canvas_nudge,         cache_ttl=0, skip_character=True, scope="session"),
    ContextHook(name="tool_warning",         order=91,  build=_hook_tool_warning,         cache_ttl=0, scope="session"),
]

# Quick lookup by name
_HOOKS_BY_NAME: dict[str, ContextHook] = {h.name: h for h in HOOK_REGISTRY}


def get_hook(name: str) -> ContextHook | None:
    """Get a hook by name."""
    return _HOOKS_BY_NAME.get(name)


# ── Main builder ─────────────────────────────────────────────────────

def _make_cache_key(hook: ContextHook, ctx: HookContext) -> str:
    """Build a cache key for a hook + context combination."""
    base = f"hook:{hook.name}:{ctx.identity}"

    # Some hooks are conversation-specific
    if hook.name in ("reactions", "owner_ids", "conversation_resume"):
        base += f":{ctx.conversation_id or 'none'}"
    elif hook.name in ("other_conversations", "prev_conversation", "today_thread"):
        base += f":{ctx.conversation_id or 'none'}"
    elif hook.name == "memory_retrieval":
        base += f":{hash(ctx.query_text or '') % 100000}"

    return base


from services.runtime_metrics import measure as _measure_runtime


@_measure_runtime("orientation")
async def build_orientation_from_hooks(ctx: HookContext) -> str:
    """Build the full orientation context using the hook registry.

    This replaces the monolithic build_orientation_context() with a
    modular system. Each hook runs independently, results are cached
    per-hook, and all cacheable hooks fire concurrently.
    """
    _cleanup_hook_cache()

    # Filter hooks for this context
    active_hooks = []
    for hook in HOOK_REGISTRY:
        if not hook.enabled:
            continue
        if hook.modes and ctx.mode not in hook.modes:
            continue
        if hook.skip_brother and ctx.is_brother_session:
            continue
        if hook.skip_character and ctx.is_character_session:
            continue
        if hook.scope == "session" and ctx.is_warm_turn:
            continue
        active_hooks.append(hook)

    # Sort by order
    active_hooks.sort(key=lambda h: h.order)

    # Separate into cached (can run concurrently) and uncached (run inline)
    results: dict[str, str] = {}
    to_fetch: dict[str, tuple[ContextHook, str]] = {}

    for hook in active_hooks:
        if hook.cache_ttl > 0:
            cache_key = _make_cache_key(hook, ctx)
            hit, value = _cache_get(cache_key, hook.cache_ttl)
            if hit:
                results[hook.name] = value
            else:
                to_fetch[hook.name] = (hook, cache_key)
        else:
            to_fetch[hook.name] = (hook, "")

    # Fire all uncached hooks concurrently
    if to_fetch:
        async def _run_hook(name: str, hook: ContextHook) -> tuple[str, str]:
            try:
                result = await hook.build(ctx)
                return name, result or ""
            except Exception as e:
                log.warning("Context hook '%s' failed: %s", name, e)
                return name, ""

        tasks = [_run_hook(name, hook) for name, (hook, _) in to_fetch.items()]
        fetched = await asyncio.gather(*tasks)

        for name, result in fetched:
            results[name] = result
            hook, cache_key = to_fetch[name]
            if hook.cache_ttl > 0 and cache_key:
                _cache_set(cache_key, result)

    # Resolve the cap multiplier from the active backend ONCE. Default to the
    # tight PTY-safe caps; loosen only when we've confirmed we're NOT on PTY
    # (so an error or unknown state never accidentally floods a paste-limited
    # terminal). resolve_provider_for_identity reads the 30s settings cache.
    cap_mult = 1
    try:
        from services.provider_router import resolve_provider_for_identity
        provider, prov_config = await resolve_provider_for_identity(ctx.identity)
        is_pty = provider == "claude-code" and (
            (prov_config.get("backend") or "subprocess").strip().lower() == "pty"
        )
        if not is_pty:
            cap_mult = _NON_PTY_CAP_MULTIPLIER
    except Exception as e:
        log.debug("Cap-multiplier backend check failed (%s); keeping tight caps", e)

    # Assemble in order. Keep the exact post-cap hook blocks beside the final
    # text so the Context Ledger can report what truly reached the turn without
    # invoking these hooks (some are consumptive) a second time.
    lines = []
    ledger_hooks: list[dict[str, Any]] = []
    for hook in active_hooks:
        limit = hook.max_chars * cap_mult if hook.max_chars else 0
        original = results.get(hook.name, "")
        text = _soft_cap(original, limit)
        if text:
            # First few hooks (time, mode, presence, resume) don't need blank line separator
            if hook.order > 25 and lines:
                lines.append("")
            lines.append(text)
            ledger_hooks.append({
                "name": hook.name,
                "text": text,
                "original_chars": len(original),
                "cap_chars": limit or None,
                "cached": hook.name not in to_fetch,
            })

    assembled = "\n".join(lines)
    try:
        from services.context_ledger import remember_orientation
        remember_orientation(
            identity=ctx.identity,
            conversation_id=ctx.conversation_id,
            mode=ctx.mode,
            is_warm_turn=ctx.is_warm_turn,
            hooks=ledger_hooks,
            assembled_text=assembled,
        )
    except Exception as e:
        log.debug("Context ledger could not remember orientation: %s", e)
    return assembled
