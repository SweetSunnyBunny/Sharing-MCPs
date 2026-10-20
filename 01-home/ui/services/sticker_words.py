
from __future__ import annotations

import re
from datetime import datetime, timedelta

ECHO_OVERLAP = 0.8
ECHO_DAYS = 30

_WORD = re.compile(r"[a-z0-9']+")

_VERDICT = {
    "proud", "good", "job", "well", "done", "amazing", "awesome", "great",
    "self-care", "selfcare", "productive", "earned", "deserve", "deserved",
    "deserves", "so", "of", "you", "for", "today", "really", "very", "such",
    "a", "the", "and", "your", "were", "are", "so", "much", "!", "wonderful",
    "brave", "strong", "incredible", "prioritising", "prioritizing", "care",
    "self", "yourself", "i'm", "im", "i", "am", "being", "did", "it",
}

_CHORE_RE = re.compile(
    r"\b(dishes|laundry|vacuum\w*|chores?|clean(?:ed|ing)? (?:the|your)|"
    r"finally|productive|productivity|got it done|got everything done|"
    r"even though|despite|on time|to-?do|tasks? (?:list|done)|grade|score)\b",
    re.IGNORECASE,
)

# Signs a line carries a seen thing: a clock time, a quote, a number, or a
# concrete word that isn't praise.
_SEEN_RE = re.compile(r"\d|[\"\u201c\u201d']\w|\bat (?:noon|night|dawn|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|one)\b", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _overlap(a: str, b: str) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _is_verdict_only(text: str) -> bool:
    words = _words(text)
    if not words:
        return False
    if _SEEN_RE.search(text):
        return False
    concrete = [w for w in words if w not in _VERDICT and len(w) > 2]
    return len(concrete) <= 1


def _recent_book_lines() -> list[str]:
    try:
        from services.sticker_book import read_records
    except Exception:
        return []
    cutoff = (datetime.now() - timedelta(days=ECHO_DAYS)).date().isoformat()
    return [
        str(r.get("why", ""))
        for r in read_records()
        if r.get("kind") == "sticker" and str(r.get("for_day", "")) >= cutoff
    ]


def proof(reason: str, recent_reasons: list[str] | None = None) -> list[str]:
    """Return advice notes for one reason line. Never raises on odd input.

    recent_reasons: lines to check for echoes. When None, reads her book.
    Because sticker_book calls this AFTER writing, one exact copy of the line
    itself is skipped so a sticker never echoes itself.
    """
    text = re.sub(r"\s+", " ", str(reason or "")).strip()
    if not text:
        return []
    notes: list[str] = []

    if _is_verdict_only(text):
        notes.append(
            "verdict_only: this is praise without the thing you saw. Name one "
            "concrete thing from her day (a time, an object, her words). That's "
            "what she keeps."
        )
    if _CHORE_RE.search(text):
        notes.append(
            "chore_shaped: this reads like a chore or a grade, something she "
            "could fail at. Stickers are for surviving the day and being kind "
            "to herself."
        )

    pool = list(_recent_book_lines() if recent_reasons is None else recent_reasons)
    skipped_self = False
    for old in reversed(pool):
        if not skipped_self and old.strip().lower() == text.lower():
            skipped_self = True
            continue
        if _overlap(text, old) >= ECHO_OVERLAP:
            notes.append(
                f"echo: very close to a line already in her book (\"{old[:80]}\"). "
                "Same truth is fine; say what was different this time."
            )
            break
    return notes
