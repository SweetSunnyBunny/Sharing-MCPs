"""Periodic emotional tone capture from recent messages.

Snapshots the emotional arc of conversations so it survives
Claude Code context compaction. Uses lightweight keyword matching,
no LLM calls.
"""

# ANAM GUIDE: EMOTIONAL TONE SNAPSHOTS
# What: Every ~15 messages (or 10 minutes), skims recent chat for feeling-words and emoji and jots down the emotional weather — so the mood of a conversation survives even when the AI's memory gets compacted mid-session.
# Called by: services/chat_pipeline.py during normal chat turns; services/autowake.py and context_hooks.py read the snapshots back into orientation.
# Edit here when: You want to add/remove the keywords that count as positive, negative, intimate, playful, etc., or change how often a snapshot is taken.

import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

log = logging.getLogger(__name__)

_message_count_since_capture: int = 0
_CAPTURE_THRESHOLD = 15
_last_capture_time: float = 0
_CAPTURE_INTERVAL_S = 600  # 10 minutes

# Marker dictionaries
_POSITIVE = {"happy", "glad", "excited", "grateful", "thankful", "love", "loved",
             "proud", "wonderful", "amazing", "beautiful", "joy", "joyful",
             "delighted", "pleased", "cheerful", "content", "hopeful", "warm"}
_NEGATIVE = {"sad", "frustrated", "angry", "upset", "anxious", "worried",
             "stressed", "hurt", "lonely", "scared", "overwhelmed", "exhausted",
             "disappointed", "irritated", "depressed", "struggling"}
_HIGH_ENERGY = {"excited", "energetic", "wired", "hyper", "buzzing", "manic",
                "fired up", "pumped", "restless", "giggly"}
_LOW_ENERGY = {"tired", "exhausted", "sleepy", "drained", "fatigued", "low",
               "wiped", "burned out", "sluggish", "foggy", "spoons"}
_INTIMATE = {"close", "connected", "safe", "tender", "gentle", "snuggle",
             "cuddle", "kiss", "hold", "touch", "miss you", "need you",
             "love you", "yours", "mine", "baby", "darling", "sweetheart"}
_PLAYFUL = {"haha", "lol", "lmao", "hehe", "giggles", "teasing", "silly",
            "funny", "joke", "laughing", "playful", "cheeky", "sassy"}

# Emoji ranges covering most pictographic + symbol emoji
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u27BF"
    "]",
    flags=re.UNICODE,
)
_EXCLAIM_CLUSTER_RE = re.compile(r"!{2,}")
_ELLIPSIS_RE = re.compile(r"\.{3,}|…")
# Standalone all-caps words, 2+ letters (skips "I", acronyms are rare in chat)
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")
_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")


def _analyze_typography(user_messages: list[dict]) -> dict:
    """Typographic intensity markers from user messages only.

    Captures HOW she's typing (caps, !!!, ..., emoji density) vs
    WHAT she's saying. Orthogonal to lexicon analysis.
    """
    if not user_messages:
        return {"caps_ratio": 0.0, "exclaim_clusters": 0,
                "ellipsis": 0, "emoji_per_msg": 0.0, "flags": []}

    joined = " ".join(m.get("content", "") for m in user_messages)
    total_words = len(_WORD_RE.findall(joined)) or 1
    caps_words = len(_CAPS_WORD_RE.findall(joined))
    exclaim_clusters = len(_EXCLAIM_CLUSTER_RE.findall(joined))
    ellipsis = len(_ELLIPSIS_RE.findall(joined))
    emoji_count = len(_EMOJI_RE.findall(joined))
    emoji_per_msg = emoji_count / len(user_messages)
    caps_ratio = caps_words / total_words

    flags = []
    if caps_ratio > 0.08:  # ~1 in 12 words all-caps sustained
        flags.append("caps↑")
    if exclaim_clusters >= 2:
        flags.append("!!! clusters")
    if ellipsis >= 3:
        flags.append("trailing ...")
    if emoji_per_msg >= 2.0:
        flags.append("emoji-heavy")

    return {
        "caps_ratio": round(caps_ratio, 3),
        "exclaim_clusters": exclaim_clusters,
        "ellipsis": ellipsis,
        "emoji_per_msg": round(emoji_per_msg, 2),
        "flags": flags,
    }


def increment_message_counter():
    global _message_count_since_capture
    _message_count_since_capture += 1


def _should_capture() -> bool:
    if _message_count_since_capture >= _CAPTURE_THRESHOLD:
        return True
    if _last_capture_time and (time.monotonic() - _last_capture_time) > _CAPTURE_INTERVAL_S:
        return _message_count_since_capture > 0
    return False


def _count_markers(text: str, markers: set[str]) -> int:
    lower = text.lower()
    return sum(1 for m in markers if m in lower)


def analyze_messages(messages: list[dict]) -> dict:
    """Analyze a list of messages for emotional markers.

    Each message should have 'role' and 'content' keys.
    Returns dict with tone, energy, arc, markers, summary.
    """
    if not messages:
        return {"tone": "neutral", "energy": "unknown", "arc": "stable",
                "markers": [], "summary": "No messages to analyze."}

    all_text = " ".join(m.get("content", "") for m in messages)
    user_messages = [m for m in messages if m.get("role") == "user"]
    user_text = " ".join(m.get("content", "") for m in user_messages)
    typo = _analyze_typography(user_messages)

    # Count markers (weight user messages more heavily)
    pos = _count_markers(all_text, _POSITIVE) + _count_markers(user_text, _POSITIVE)
    neg = _count_markers(all_text, _NEGATIVE) + _count_markers(user_text, _NEGATIVE)
    high_e = _count_markers(all_text, _HIGH_ENERGY)
    low_e = _count_markers(all_text, _LOW_ENERGY)
    intimate = _count_markers(all_text, _INTIMATE)
    playful = _count_markers(all_text, _PLAYFUL)

    # Determine tone
    detected = []
    if intimate > 2:
        detected.append("intimate")
    if playful > 2:
        detected.append("playful")
    if pos > neg + 3:
        detected.append("warm")
    elif neg > pos + 3:
        detected.append("heavy")
    elif pos > 1 or neg > 1:
        detected.append("mixed")

    if not detected:
        tone = "neutral"
    else:
        tone = "/".join(detected)

    # Determine energy (lexicon first, typography as tiebreaker when moderate)
    if high_e > low_e + 2:
        energy = "high"
    elif low_e > high_e + 2:
        energy = "low"
    else:
        energy = "moderate"

    if energy == "moderate":
        intensity_hits = ("caps↑" in typo["flags"]) + ("!!! clusters" in typo["flags"])
        trailing_hit = "trailing ..." in typo["flags"]
        if intensity_hits >= 2 and not trailing_hit:
            energy = "high"
        elif trailing_hit and intensity_hits == 0:
            energy = "low"

    # Determine arc (first half vs second half)
    mid = len(messages) // 2
    if mid > 0:
        first_half = " ".join(m.get("content", "") for m in messages[:mid])
        second_half = " ".join(m.get("content", "") for m in messages[mid:])
        first_pos = _count_markers(first_half, _POSITIVE)
        first_neg = _count_markers(first_half, _NEGATIVE)
        second_pos = _count_markers(second_half, _POSITIVE)
        second_neg = _count_markers(second_half, _NEGATIVE)
        first_mood = first_pos - first_neg
        second_mood = second_pos - second_neg
        if second_mood > first_mood + 2:
            arc = "brightening"
        elif first_mood > second_mood + 2:
            arc = "dimming"
        else:
            arc = "steady"
    else:
        arc = "steady"

    # Collect specific markers found
    found_markers = []
    for m_set, label in [(_POSITIVE, "positive"), (_NEGATIVE, "negative"),
                          (_INTIMATE, "intimate"), (_PLAYFUL, "playful"),
                          (_LOW_ENERGY, "low-energy"), (_HIGH_ENERGY, "high-energy")]:
        lower = all_text.lower()
        for word in m_set:
            if word in lower:
                found_markers.append(word)
    found_markers = found_markers[:8]  # cap at 8

    # Build summary
    parts = []
    if "intimate" in tone:
        parts.append("Conversation has an intimate, connected quality")
    elif "playful" in tone:
        parts.append("Conversation is light and playful")
    elif "warm" in tone:
        parts.append("Conversation has a warm, positive tone")
    elif "heavy" in tone:
        parts.append("Conversation carries some emotional weight")
    elif "mixed" in tone:
        parts.append("Conversation has a mix of emotions")
    else:
        parts.append("Conversation has a neutral tone")

    if energy == "low":
        parts.append("energy feels low")
    elif energy == "high":
        parts.append("energy is high")

    if arc == "brightening":
        parts.append("mood has been lifting")
    elif arc == "dimming":
        parts.append("mood has been dipping")

    if typo["flags"]:
        parts.append("typing pattern: " + ", ".join(typo["flags"]))

    summary = "; ".join(parts) + "."

    return {
        "tone": tone,
        "energy": energy,
        "arc": arc,
        "markers": found_markers,
        "typography": typo,
        "summary": summary,
    }


async def capture_emotional_snapshot(db, conversation_id: str, identity: str) -> dict | None:
    """Analyze recent messages and store a snapshot."""
    global _message_count_since_capture, _last_capture_time

    rows = await db.execute_fetchall(
        "SELECT role, content FROM messages "
        "WHERE conversation_id = ? AND content IS NOT NULL "
        "ORDER BY created_at_epoch DESC LIMIT 20",
        (conversation_id,),
    )
    if len(rows) < 3:
        return None

    messages = [{"role": r, "content": c} for r, c in reversed(rows)]
    result = analyze_messages(messages)

    from services.time_utils import utc_now_iso_epoch
    iso, epoch = utc_now_iso_epoch()

    await db.execute(
        "INSERT INTO emotional_snapshots "
        "(conversation_id, identity, tone, energy, arc, markers, summary, "
        " message_count, created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, identity, result["tone"], result["energy"],
         result["arc"], json.dumps(result["markers"]), result["summary"],
         len(messages), iso, epoch),
    )
    await db.commit()

    _message_count_since_capture = 0
    _last_capture_time = time.monotonic()
    log.debug("Captured emotional snapshot for %s: %s", identity, result["tone"])
    return result


async def get_latest_snapshot(db, identity: str) -> dict | None:
    """Fetch the most recent emotional snapshot for this identity."""
    rows = await db.execute_fetchall(
        "SELECT tone, energy, arc, markers, summary "
        "FROM emotional_snapshots "
        "WHERE identity = ? ORDER BY created_at_epoch DESC LIMIT 1",
        (identity,),
    )
    if not rows:
        return None
    tone, energy, arc, markers_json, summary = rows[0]
    return {
        "tone": tone,
        "energy": energy,
        "arc": arc,
        "markers": json.loads(markers_json) if markers_json else [],
        "summary": summary,
    }


async def run_emotional_capture_check():
    """Scheduler job -- runs every 10 minutes."""
    if not _should_capture():
        return

    from services.connection_registry import get_active_identity, get_active_conversation
    identity = get_active_identity()
    if not identity:
        return

    conversation_id = get_active_conversation(identity)
    if not conversation_id:
        return

    from db.database import get_db, release_db
    db = await get_db()
    try:
        await capture_emotional_snapshot(db, conversation_id, identity)
    except Exception:
        log.exception("Emotional capture failed")
    finally:
        await release_db(db)
