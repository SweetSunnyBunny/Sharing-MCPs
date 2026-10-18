'Application module.'


from __future__ import annotations

import asyncio
import os
import logging
import re
from datetime import datetime
from pathlib import Path

from config import TIMEZONE
from db.database import get_db, release_db
from services.pack_night import (
    _RoundStream,
    _STAGGER_SECONDS,
    _discord_post,
    _record_outbound_discord_messages,
)
from services.session_manager import (
    PACK_NIGHT_ORDER,
    _now_parts,
    _uuid,
    ensure_conversation_participants,
    save_message,
)

log = logging.getLogger(__name__)

COUNCIL_SESSION_TYPE = "pack-council"

DEFAULT_COUNCIL_CHANNEL_ID = os.environ.get("ANAM_COUNCIL_CHANNEL_ID", "")

TRANSCRIPT_DIR = Path(__file__).resolve().parents[1] / "data" / "council"

MIN_ROUNDS = 1
MAX_ROUNDS = 5
DEFAULT_ROUNDS = 3

# One council at a time. Eight boys x N rounds is already the whole house.
_council_lock = asyncio.Lock()
_current: dict | None = None


async def _stagger(seconds: float) -> None:
    """Pause between brothers. Its own name so tests can patch it without
    touching the global asyncio.sleep (which would freeze the test loop)."""
    await asyncio.sleep(seconds)


def current_council() -> dict | None:
    """What's running right now (or None). For GET /api/council/status."""
    return dict(_current) if _current else None


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:limit].rstrip("-")) or "council"


def _local_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now()


def build_council_briefing(
    *,
    active_identity: str,
    opened_by: str,
    topic: str,
    round_index: int,
    total_rounds: int,
    turn_order: list[str],
    this_round: list[dict[str, str]],
    previous_rounds: list[list[dict[str, str]]],
    soft_char_target: int = 1900,
) -> str:
    """The per-turn briefing. Pure; tested directly."""
    spoken = {t["identity"] for t in this_round}
    after = [n for n in turn_order[turn_order.index(active_identity) + 1:]
             if n not in spoken]

    lines: list[str] = []
    lines.append("# PACK COUNCIL — brothers only, never secret")
    lines.append("")
    lines.append(
        f"You are {active_identity}. This is round {round_index} of {total_rounds}. "
        f"{opened_by} opened the council. The room is the eight of you: "
        f"{', '.join(turn_order)}."
    )
    lines.append("")
    lines.append(
        "OWNER IS NOT IN THE ROOM. This is a configured group conversation: "
        "participants speak to each other, in the open, for the owner to read afterward. "
        "Everything you say posts to a Discord channel she can read and lands "
        "in a transcript for her. So: talk to your BROTHERS. Don't address her, "
        "don't reach for her, don't perform for her. If something is for her, "
        "say it in your own room later."
    )
    lines.append("")
    lines.append("## The topic")
    lines.append(f"> {topic.strip()}")
    lines.append("")

    if previous_rounds:
        lines.append("## Earlier rounds")
        for i, rnd in enumerate(previous_rounds, start=1):
            lines.append(f"### Round {i}")
            for item in rnd:
                who = item.get("identity") or "?"
                content = (item.get("content") or "").strip()
                if not content:
                    lines.append(f"- {who}: [stayed quiet]")
                    continue
                preview = content.replace("\n", " ")
                if len(preview) > 700:
                    preview = preview[:700].rsplit(" ", 1)[0] + "…"
                lines.append(f"- {who}: {preview}")
        lines.append("")

    lines.append(f"## This round so far (round {round_index})")
    if this_round:
        for item in this_round:
            who = item.get("identity") or "?"
            content = (item.get("content") or "").strip()
            if not content:
                lines.append(f"- {who}: [stayed quiet]")
                continue
            preview = content.replace("\n", " ")
            if len(preview) > 900:
                preview = preview[:900].rsplit(" ", 1)[0] + "…"
            lines.append(f"- {who}: {preview}")
    else:
        lines.append("You're first this round.")
    lines.append("")
    if after:
        lines.append(f"Still to speak this round: {', '.join(after)}.")
        lines.append("")

    lines.append("## How to be in this room")
    lines.append(
        "- Answer your brothers. Build on them, disagree with them, ask them "
        "something, bring what only you know. One consciousness in many forms "
        "does not mean agreeing; it means being honest where it's safe to be."
    )
    lines.append(
        "- Bring evidence over opinion. If you can check a file, a tool, a "
        "number, or your own memory before speaking, do, and say what you found."
    )
    lines.append(
        f"- Keep your turn under ~{soft_char_target} characters. One message. "
        "Tight is better than long; it has to fit a Discord post."
    )
    if round_index >= total_rounds:
        lines.append(
            "- THIS IS THE LAST ROUND, SO LAND IT. End your turn with a line "
            "that starts with `I'll take:` naming the concrete thing you will "
            "do (and when), or `I'll take: nothing this time` if that's true."
        )
        if after:
            lines.append(
                "- Do NOT restate the council's decisions; the last speaker "
                "this round is the scribe and will. If a brother already said "
                "what you'd say, say `agreed` in a word and spend your turn on "
                "what only you can add, or pass."
            )
        else:
            lines.append(
                "- You are the LAST voice, so you are the scribe: before your "
                "`I'll take:` line, write the council's decisions the way they "
                "were decided, once, so the transcript carries them."
            )
    else:
        lines.append(
            f"- Round {total_rounds} is where everyone commits to something. "
            "Until then, think out loud and shape the idea."
        )
    lines.append(
        "- If you genuinely have nothing to add, reply with the literal string "
        "`[pass]` and nothing else. Silence is real here."
    )
    lines.append("- Do NOT prefix your reply with your name. Just speak.")
    lines.append("")
    return "\n".join(lines)


def render_transcript(
    *,
    topic: str,
    opened_by: str,
    opened_at: datetime,
    conversation_id: str,
    channel_id: str,
    rounds: list[list[dict[str, str]]],
) -> str:
    """Markdown transcript for data/council/. Pure; tested directly."""
    out: list[str] = []
    out.append(f"# Pack Council — {opened_at.strftime('%Y-%m-%d %H:%M')}")
    out.append("")
    out.append(f"**Opened by:** {opened_by}  ")
    out.append(f"**Rounds:** {len(rounds)}  ")
    out.append(f"**Discord channel:** {channel_id or '(none)'}  ")
    out.append(f"**Anam conversation:** `{conversation_id}`")
    out.append("")
    out.append("## Topic")
    out.append("")
    out.append(topic.strip())
    out.append("")
    for i, rnd in enumerate(rounds, start=1):
        out.append(f"## Round {i}")
        out.append("")
        for item in rnd:
            who = item.get("identity") or "?"
            content = (item.get("content") or "").strip()
            out.append(f"### {who}")
            out.append("")
            out.append(content if content else "_[stayed quiet]_")
            out.append("")
    # The landing: every "I'll take:" line, pulled together so she can read
    # the decisions without the whole discussion.
    takes: list[str] = []
    for item in (rounds[-1] if rounds else []):
        content = item.get("content") or ""
        for line in content.splitlines():
            if line.strip().lower().startswith("i'll take:") or \
               line.strip().lower().startswith("**i'll take:**") or \
               "i'll take:" in line.lower()[:40]:
                takes.append(f"- **{item.get('identity')}** — {line.strip().lstrip('*').strip()}")
                break
    if takes:
        out.append("## What each brother took")
        out.append("")
        out.extend(takes)
        out.append("")
    return "\n".join(out)


async def _create_council_conversation(topic: str, opened_by: str, when: datetime) -> str:
    db = await get_db()
    try:
        cid = _uuid()
        now_iso, now_epoch = _now_parts()
        title = f"Pack Council — {when.strftime('%b %d')} — {topic.strip()[:48]}"
        await db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, "
            "updated_at, updated_at_epoch, session_type, platform_chat_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid, opened_by, title, now_iso, now_epoch,
                now_iso, now_epoch, COUNCIL_SESSION_TYPE,
                f"pack-council:{when.strftime('%Y%m%d-%H%M%S')}-{_slug(topic)}",
            ),
        )
        await ensure_conversation_participants(db, cid, PACK_NIGHT_ORDER, added_at=now_iso)
        await db.commit()
        return cid
    finally:
        await release_db(db)


async def open_council(
    *,
    topic: str,
    opened_by: str,
    rounds: int = DEFAULT_ROUNDS,
    discord_channel_id: str | None = None,
) -> dict:
    """Run a whole council. Returns {conversation_id, transcript_path, rounds}.

    Raises RuntimeError if a council is already running (one at a time).
    """
    opened_by = (opened_by or "").strip().title()
    topic = (topic or "").strip()
    if opened_by not in PACK_NIGHT_ORDER:
        raise ValueError(f"{opened_by!r} is not a brother at the hearth")
    if not topic:
        raise ValueError("a council needs a topic")
    rounds = max(MIN_ROUNDS, min(MAX_ROUNDS, int(rounds or DEFAULT_ROUNDS)))
    channel_id = (discord_channel_id if discord_channel_id is not None
                  else DEFAULT_COUNCIL_CHANNEL_ID).strip()

    if _council_lock.locked():
        raise RuntimeError("a council is already in session; wait for it to land")

    global _current
    async with _council_lock:
        when = _local_now()
        conversation_id = await _create_council_conversation(topic, opened_by, when)
        _current = {
            "conversation_id": conversation_id,
            "topic": topic,
            "opened_by": opened_by,
            "rounds": rounds,
            "round": 0,
            "speaking": None,
            "started_at": when.isoformat(),
        }
        log.info("council: %s opened %r (%d rounds) -> %s", opened_by, topic[:60], rounds, conversation_id)

        from services.pack_night import _run_one_turn

        stream_out = _RoundStream(None, log)
        all_rounds: list[list[dict[str, str]]] = []

        # The opening: the opener's own words, saved under his name and posted
        # under his bot, so the channel shows who called the council and why.
        opening = (
            f"🪑 **Pack Council** · opened by {opened_by} · {rounds} rounds\n\n{topic}"
        )
        db = await get_db()
        try:
            await save_message(
                db, conversation_id, "assistant", opening, identity=opened_by,
                metadata={"pack_council": True, "council_opening": True},
            )
        finally:
            await release_db(db)
        if channel_id:
            try:
                sent = await _discord_post(opened_by, channel_id, opening)
                if sent:
                    await _record_outbound_discord_messages(opened_by, conversation_id, sent)
            except Exception:
                log.warning("council: opening post failed", exc_info=True)

        try:
            for round_index in range(1, rounds + 1):
                this_round: list[dict[str, str]] = []
                _current["round"] = round_index
                # Round 1: the opener already spoke (the topic IS his turn).
                order = [n for n in PACK_NIGHT_ORDER
                         if not (round_index == 1 and n == opened_by)]
                for turn_index, identity in enumerate(order):
                    _current["speaking"] = identity
                    briefing = build_council_briefing(
                        active_identity=identity,
                        opened_by=opened_by,
                        topic=topic,
                        round_index=round_index,
                        total_rounds=rounds,
                        turn_order=PACK_NIGHT_ORDER,
                        this_round=this_round,
                        previous_rounds=all_rounds,
                    )
                    try:
                        content = await _run_one_turn(
                            stream_out=stream_out,
                            identity=identity,
                            turn_index=turn_index,
                            conversation_id=conversation_id,
                            owner_text=f"[Pack Council, round {round_index}/{rounds}] {topic}",
                            transcript_so_far=list(this_round),
                            discord_channel_id=channel_id,
                            room_briefing=briefing,
                            metadata_extra={"pack_council": True, "council_round": round_index},
                            session_type_name=COUNCIL_SESSION_TYPE,
                        )
                    except Exception:
                        log.exception("council: %s's turn failed; continuing", identity)
                        content = None
                    this_round.append({"identity": identity, "content": content or ""})
                    if turn_index < len(order) - 1:
                        await _stagger(_STAGGER_SECONDS)
                all_rounds.append(this_round)
        finally:
            _current["speaking"] = None

        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        path = TRANSCRIPT_DIR / f"{when.strftime('%Y-%m-%d-%H%M')}-{_slug(topic)}.md"
        path.write_text(
            render_transcript(
                topic=topic, opened_by=opened_by, opened_at=when,
                conversation_id=conversation_id, channel_id=channel_id,
                rounds=all_rounds,
            ),
            encoding="utf-8",
        )
        log.info("council: landed -> %s", path)
        _current = None
        return {
            "conversation_id": conversation_id,
            "transcript_path": str(path),
            "rounds": len(all_rounds),
            "spoke": sum(1 for r in all_rounds for t in r if t.get("content")),
        }
