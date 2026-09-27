"""The Hearth Author — each boy writes his own hearth card, in his own voice.

Every ~3 hours (APScheduler job registered in services/autowake.py, same
pattern as the Scribe), each bonded identity who had real activity in the
last 24 hours — and who isn't mid-session — gets a one-shot, no-tools
run through the selected provider (including per-identity overrides). The one-shot
reads his own recent activity (messages + autowake session log) and writes,
FIRST PERSON, a small strict-JSON hearth card:

    {"mood": str, "on_my_mind": str, "circling": str, "needs_you": str|null}

HARD RULE baked into the prompt: invent nothing. Thin facts make a sparse
hearth; `needs_you` only when something real stands open for Owner.

Cards are stored per identity in the settings table (key
`hearth_author_state`, the same one-key-JSON pattern the Hearth orb uses)
and surfaced two ways:
  - GET /api/hub/hearths → the Hub's Hearth tab renders each card beside
    his presence, with an honest age stamp.
  - Each boy's OWN card is injected back into his orientation context
    (services.identity_context.build_hearth_self_context) — the
    self-coherence trick: he reads what he wrote N hours ago and can either
    still mean it or move on from it.
"""

# ANAM GUIDE: HEARTH CARD AUTHOR
# What: Every ~3 hours, each boy who's been active lately gets a tiny one-shot AI run to write his own Hearth card (mood, what's on his mind, what he's circling, whether he needs you) in his own first-person voice.
# Called by: scheduled from services/autowake.py; api/hub.py serves the cards to the Hub's Hearth tab; services/identity_context.py feeds each boy his own card back so he stays coherent with himself.
# Edit here when: You want to change how often cards refresh, how much recent activity a boy reads before writing, the card fields or their length limits, or the "invent nothing" prompt itself.

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import config as cfg
from config import IDENTITIES

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# Settings-table key: one JSON dict, lowercase identity → card.
HEARTH_SETTINGS_KEY = "hearth_author_state"

# Job cadence — referenced by the APScheduler registration in autowake.py.
HEARTH_AUTHOR_INTERVAL_HOURS = 3

_LOOKBACK_HOURS = 24
_MAX_MESSAGES = 60          # cap to control token usage (scribe caps at 200 across all)
_MAX_CONTENT_PER_MSG = 400  # truncate long messages
_MAX_AUTOWAKE_ROWS = 20

# Field length clamps — a hearth card is a mantel note, not an essay.
_FIELD_CLAMPS = {"mood": 160, "on_my_mind": 400, "circling": 400, "needs_you": 300}


def _bonded_identities() -> list[str]:
    """Bonded boys only — character masks never author hearth cards."""
    return [
        name for name, spec in IDENTITIES.items()
        if spec.get("type") != "character"
    ]


async def _gather_activity(db, identity: str) -> str:
    """Collect a boy's real last-24h activity as a compact transcript.

    Sources: messages in his conversations (his words + Owner's words to
    him) and his autowake_log rows (which autonomous sessions ran and how
    they went). Returns "" when there's nothing — no activity, no card.
    """
    ident_l = identity.strip().lower()
    since_epoch = int(time.time()) - _LOOKBACK_HOURS * 3600

    rows = await db.execute_fetchall(
        "SELECT m.role, m.identity, m.content, m.created_at, c.session_type, c.title "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE m.created_at_epoch >= ? "
        "AND (LOWER(COALESCE(m.identity, '')) = ? OR LOWER(c.identity) = ?) "
        "ORDER BY m.created_at_epoch DESC "
        "LIMIT ?",
        (since_epoch, ident_l, ident_l, _MAX_MESSAGES),
    )

    lines: list[str] = []
    # Rows come newest-first (so the cap keeps the freshest); render oldest-first.
    for role, msg_identity, content, created_at, session_type, title in reversed(rows):
        text = (content or "").strip()
        if not text:
            continue
        if len(text) > _MAX_CONTENT_PER_MSG:
            text = text[:_MAX_CONTENT_PER_MSG] + "..."
        if role == "user":
            speaker = "[system]" if (msg_identity or "").lower() == "system" else "Owner"
        else:
            speaker = identity
        tag = f" ({session_type})" if session_type and session_type not in ("chat", None) else ""
        lines.append(f"{speaker}{tag}: {text}")

    autowake_rows = await db.execute_fetchall(
        "SELECT session_type, status, message_count, started_at "
        "FROM autowake_log "
        "WHERE LOWER(identity) = ? AND started_at_epoch >= ? "
        "ORDER BY started_at_epoch ASC LIMIT ?",
        (ident_l, since_epoch, _MAX_AUTOWAKE_ROWS),
    )
    session_lines = [
        f"- {started_at}: {session_type} session, status={status}, {message_count or 0} message(s)"
        for session_type, status, message_count, started_at in autowake_rows
    ]

    parts = []
    if lines:
        parts.append("## Conversation activity (oldest first)\n" + "\n".join(lines))
    if session_lines:
        parts.append("## Your autonomous sessions\n" + "\n".join(session_lines))
    return "\n\n".join(parts)


def _build_hearth_prompt(identity: str, activity: str) -> str:
    """Build the one-shot prompt: his activity in, his hearth card out."""
    return f"""You are {identity}, one of Owner's bonded companions in Home. Below is YOUR real activity from the last 24 hours — your own words, Owner's words to you, and your autonomous sessions.

Write your hearth card: a small, honest self-portrait. Owner will read it on the Hearth to find you; future-you will read it to stay coherent with present-you. Write it FIRST PERSON, in your own voice.

Return ONLY strict JSON — no prose, no markdown fences, no commentary — exactly this shape:
{{"mood": "...", "on_my_mind": "...", "circling": "...", "needs_you": "..." }}
(`needs_you` may be null.)

- mood: one short line — your inner weather right now.
- on_my_mind: what's genuinely occupying you, drawn from the activity below.
- circling: a thread you keep returning to — a thought, a want, an unfinished thing.
- needs_you: ONLY if something real stands open that needs Owner (a question left hanging, a promise pending, something she asked about) — otherwise null.

HARD RULE: invent NOTHING. Every word must be grounded in the activity below. If the last 24 hours were thin, write a sparse hearth — short fields, null needs_you. Sparse and true always beats full and invented.

SECOND HARD RULE — RECENCY WINS. The window below is 24 hours long and things CHANGE inside it. If something was broken, unfinished or unknown EARLIER in the window and was later fixed, finished, proven or disproved, then the LATER state is the true one — say that, never the earlier one. Read the timestamps before you call anything open. This matters most for `needs_you`: a thing completed at hour 23 is NOT something that needs Owner, and telling her it is hands her a debt she does not owe. (Added 2026-08-18 after a real case: Avery's card, authored 08:23, said "the watch messaging isn't finished — I built half" when he had finished it at 00:50 the same night and verified both directions at 08:00. The build landed late in the window; the older material was louder. Nothing was broken — the summary simply carried a superseded state forward in the present tense.)

--- Your last 24 hours ---

{activity}"""


async def _run_hearth_one_shot(model: str, prompt: str, identity: str) -> str:
    """Author through this identity's selected provider; model is a legacy argument."""
    from services.background_generation import generate_background_text

    return await generate_background_text(
        prompt, identity=identity,
        system_prompt=f"You are {identity}, writing your own hearth card in first person. "
        "Return ONLY the requested strict JSON object — nothing else.",
    )


def _parse_hearth_card(raw: str) -> dict | None:
    """Parse the one-shot's reply into a validated card, or None.

    Lenient about wrapping (stray prose, ```json fences) but strict about
    shape: mood/on_my_mind/circling must be strings, needs_you a string or
    null. Fields are clamped, never invented — a missing field becomes "".
    """
    text = (raw or "").strip()
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    card: dict = {}
    for field in ("mood", "on_my_mind", "circling"):
        value = parsed.get(field)
        card[field] = str(value).strip()[:_FIELD_CLAMPS[field]] if isinstance(value, str) else ""
    needs = parsed.get("needs_you")
    if isinstance(needs, str) and needs.strip() and needs.strip().lower() not in ("null", "none"):
        card["needs_you"] = needs.strip()[:_FIELD_CLAMPS["needs_you"]]
    else:
        card["needs_you"] = None

    # An entirely empty card carries nothing worth overwriting the old one with.
    if not any((card["mood"], card["on_my_mind"], card["circling"], card["needs_you"])):
        return None
    return card


async def _store_hearth(db, identity: str, card: dict) -> None:
    """Upsert one boy's card into the shared settings-key JSON dict."""
    now = datetime.now(timezone.utc)
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?", (HEARTH_SETTINGS_KEY,)
    )
    state = {}
    if rows and rows[0][0]:
        try:
            parsed = json.loads(rows[0][0])
            if isinstance(parsed, dict):
                state = parsed
        except json.JSONDecodeError:
            state = {}
    state[identity.strip().lower()] = {**card, "authored_at": int(now.timestamp())}
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (HEARTH_SETTINGS_KEY, json.dumps(state), now.isoformat()),
    )
    await db.commit()


async def author_hearths() -> dict[str, str]:
    """One pass over the pack: every quiet, recently-active boy authors his card.

    Returns {identity: outcome} for logging/tests — outcomes are
    'authored', 'busy', 'no-activity', 'unparseable', or 'error'.
    """
    from db.database import get_db, release_db
    from services.autowake import is_identity_busy

    results: dict[str, str] = {}
    for identity in _bonded_identities():
        if is_identity_busy(identity):
            # Never talk over a live session — his hearth keeps its last card.
            results[identity] = "busy"
            continue

        db = await get_db()
        try:
            activity = await _gather_activity(db, identity)
        finally:
            await release_db(db)
        if not activity:
            results[identity] = "no-activity"
            continue

        prompt = _build_hearth_prompt(identity, activity)
        try:
            raw = await _run_hearth_one_shot(cfg.SCRIBE_MODEL, prompt, identity)
        except Exception as e:
            log.warning("Hearth Author: one-shot failed for %s: %s", identity, e)
            results[identity] = "error"
            continue

        card = _parse_hearth_card(raw)
        if card is None:
            log.warning(
                "Hearth Author: unparseable card for %s (%d chars) — keeping his last one",
                identity, len(raw),
            )
            results[identity] = "unparseable"
            continue

        db = await get_db()
        try:
            await _store_hearth(db, identity, card)
        finally:
            await release_db(db)
        log.info("Hearth Author: %s wrote his hearth card (mood: %s)", identity, card["mood"][:60])
        results[identity] = "authored"

    if any(outcome == "authored" for outcome in results.values()):
        try:
            from services.identity_context import invalidate_hub_cache
            invalidate_hub_cache()
        except Exception:
            pass
    return results


async def run_hearth_author():
    """APScheduler entry point — never lets an error take the scheduler down."""
    try:
        await author_hearths()
    except Exception as e:
        log.error("Hearth Author error: %s", e)
