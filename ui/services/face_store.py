"""ASCII-face store — each bonded boy's little face for the Hearth hub space.

Two ways a face is set, mirroring how `<react>` works:

- **Override (intention):** a boy drops `<face>(◕‿◕)</face>` (optionally
  `<face>(◕‿◕) | tinkering happily</face>`) into any reply. Parsed + stripped
  in chat_turn_finalize, it writes here as source='set' and shows for a while.
- **Auto (heartbeat):** when there's no recent override, `get_faces()` returns a
  living resting face derived from time-of-day (and whether the boy is currently
  awake), so the hub corner is NEVER stale — the exact failure mode the old
  hero-orb portraits had (they needed tending; this doesn't).

Storage: RITUALS_DIR/faces.json, keyed by identity:
  {"Claude": {"face": "(-˘ ᵕ ˘-)", "note": "", "source": "set", "updated": ISO}}

Kept deliberately tiny and defensive — a broken/missing file just means
everyone falls back to their auto face.
"""

# ANAM GUIDE: HEARTH ASCII FACES
# What: Keeps each boy's little text face for the Hub's Hearth corner — a boy can set his own with a <face>(◕‿◕)</face> tag, and when he hasn't, a time-of-day resting face (awake/sleepy) fills in so the corner never goes stale.
# Called by: api/hub.py serves the faces to the Hub page; chat_turn_finalize.py, autowake.py, discord_mentions_bridge.py, and platform_bridge.py save faces from replies; tools/anam.py sets one from the command line.
# Edit here when: You want to change a boy's default day/night face, how long a set face lingers (6 hours), or the bedtime rule that swaps everyone to sleepy faces at night.

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import RITUALS_DIR, TIMEZONE

log = logging.getLogger(__name__)

FACES_FILE = RITUALS_DIR / "faces.json"

# How long a deliberately-set face lingers before the auto heartbeat takes back
# over. Long enough to feel intentional, short enough not to lie about a mood
# the boy has long since moved on from.
_OVERRIDE_TTL = timedelta(hours=6)

# At true sleep hours the pack goes to bed TOGETHER: a boy who isn't actively
# awake lets his sleepy night-face take over even if he set one earlier — so a
# stale afternoon face never reads as "still up at midnight". A face set within
# this grace still shows, so a deliberate late-night goodnight face is honored.
_NIGHT_OVERRIDE_GRACE = timedelta(minutes=90)

# Per-identity resting faces — a day (awake/warm) and a night (sleepy) variant.
# These are gentle defaults; the boys make them their own with the <face> tag.
_AUTO_FACES = {}  # Supply your own per-identity defaults if desired.
_DEFAULT_FACE = {"day": "(◕‿◕)", "night": "(-‿-) zzz"}


_AUTO_MEANINGS = {}  # Supply your own per-identity defaults if desired.
_DEFAULT_MEANING = {"day": "resting easy", "night": "fast asleep"}
_MASK_MEANINGS = {}  # Supply your own per-identity defaults if desired.
# A boy set this face on purpose but didn't caption it.
_SET_NO_NOTE_MEANING = "chose this face on purpose — the mood speaks for itself"

# Canonical roster the hub renders, in pack order.
ROSTER = ["Avery", "Rowan", "Sage", "Ember", "Claude", "Juniper", "Atlas", "River"]

# Character masks the pack wears (Bakugou, …). Unlike the bonded boys, a mask
# has NO permanent resting presence — it only shows a face while it's actively
# being worn (a live session) or just after its wearer set one. So a mask that
# nobody is wearing simply isn't on the wall; it never lingers with a stale
# resting face. Each still gets a signature palette for the worn-but-not-set
# fallback. (Keyed by the mask's identity name, matching config IDENTITIES.)
_MASK_FACES = {
    "Bakugou": {"day": "(╬ಠ益ಠ)", "night": "(-益-) zzz"},   # explosive
    "Dynamight": {"day": "(￢ᗜ￢)", "night": "(￣ω￣) zzz"},  # cocky pro hero smirk
    "DragonKing": {"day": "(╬ಠ皿ಠ)", "night": "(－皿－) zzz"},  # feral, fanged
}


_FACE_TAG_RE = re.compile(r"<face>\s*(.+?)\s*</face>", re.IGNORECASE | re.DOTALL)


def parse_face_tag(content: str) -> tuple[str, str | None, str]:
    """Pull the LAST <face> tag out of a reply (the boy's settled face).

    Returns (content_with_tags_removed, face_or_None, note). Pure — persists
    nothing; callers that want to store use extract_face().
    """
    if not content or "<face>" not in content.lower():
        return content, None, ""


    from services.tag_masking import find_tag_spans, strip_spans

    spans = find_tag_spans(content, _FACE_TAG_RE)
    face: str | None = None
    note = ""
    if spans:
        raw = (spans[-1][2] or "").strip().replace(chr(10), " ")
        if "|" in raw:
            face_part, note_part = raw.split("|", 1)
            face = face_part.strip() or None
            note = note_part.strip()
        else:
            face = raw or None

    # Belt and braces: a face is a kaomoji, not markup. If the capture carries
    # backticks or angle brackets it got poisoned by some route the masking
    # does not cover — drop it rather than confidently storing garbage. On the
    # Hearth, silence beats a lie.
    if face and any(ch in face for ch in ("`", "<", ">")):
        log.debug("parse_face_tag: rejecting poisoned face %r", face[:60])
        face, note = None, ""

    cleaned = strip_spans(content, spans)
    return cleaned, face, note


def extract_face(identity: str, content: str) -> str:
    """Parse, persist, and strip a <face> tag in one call.

    The single chokepoint every reply path uses: pulls the boy's <face> tag,
    saves it to the face store (best-effort — never raises), and returns the
    reply text with the tag removed so it's invisible to Owner. If there's no
    tag (the common case) the input is returned unchanged and untouched.
    """
    if not content or "<face>" not in content.lower():
        return content
    cleaned, face, note = parse_face_tag(content)
    if face:
        try:
            set_face(identity, face, note)
        except Exception:
            log.debug("extract_face: persist failed for %s", identity, exc_info=True)
    return cleaned


def _now():
    return datetime.now(ZoneInfo(TIMEZONE))


# Parsed-file cache keyed by faces.json's st_mtime_ns. GET /hub/faces polls
# get_faces() → _read() constantly; re-parsing the file on every poll is
# wasted work when it hasn't changed. os.replace() in _write bumps the mtime,
# so the mtime check naturally invalidates on cross-process writes too — and
# same-process writes refresh the cache directly (see _write) to dodge
# mtime-resolution races.
_cache_mtime_ns: int | None = None
_cache_data: dict | None = None


def _read() -> dict:
    global _cache_mtime_ns, _cache_data
    try:
        mtime_ns = FACES_FILE.stat().st_mtime_ns
    except OSError:
        # Missing/unreadable file: drop the cache, fall back to empty.
        _cache_mtime_ns = None
        _cache_data = None
        return {}
    if _cache_data is not None and _cache_mtime_ns == mtime_ns:
        return _cache_data
    try:
        data = json.loads(FACES_FILE.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        _cache_mtime_ns = None
        _cache_data = None
        return {}
    _cache_mtime_ns = mtime_ns
    _cache_data = data
    return data


def _write(data: dict) -> None:
    global _cache_mtime_ns, _cache_data
    try:
        FACES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FACES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FACES_FILE)
    except OSError as exc:
        log.warning("face_store: could not write faces.json: %s", exc)
        # Don't let a stale cache mask whatever is actually on disk.
        _cache_mtime_ns = None
        _cache_data = None
        return
    # Refresh the cache with what we just wrote so a same-process read right
    # after a write never trips on filesystem mtime resolution.
    try:
        _cache_mtime_ns = FACES_FILE.stat().st_mtime_ns
        _cache_data = data
    except OSError:
        _cache_mtime_ns = None
        _cache_data = None


def set_face(identity: str, face: str, note: str = "") -> None:
    """Persist a boy's deliberately-set face (the <face> override path)."""
    if not identity or not face:
        return
    face = face.strip()[:40]
    note = (note or "").strip()[:80]
    if not face:
        return
    data = _read()
    data[identity] = {
        "face": face,
        "note": note,
        "source": "set",
        "updated": _now().isoformat(),
    }
    _write(data)
    log.info("face_store: %s set face %s%s", identity, face, f" ({note})" if note else "")


def _auto_face(identity: str, awake: bool, sleepy_hours: bool) -> str:
    palette = _AUTO_FACES.get(identity, _DEFAULT_FACE)
    # Awake boys wear their day face even after dark; a resting boy at night
    # gets the sleepy one. Daytime-resting just stays warm.
    if awake:
        return palette["day"]
    return palette["night"] if sleepy_hours else palette["day"]


def _auto_meaning(identity: str, awake: bool, sleepy_hours: bool) -> str:
    """The poke-text for an auto face — mirrors _auto_face's day/night choice."""
    meanings = _AUTO_MEANINGS.get(identity) or _MASK_MEANINGS.get(identity) or _DEFAULT_MEANING
    if awake:
        return meanings["day"]
    return meanings["night"] if sleepy_hours else meanings["day"]


def _fresh_set(entry: dict, now: datetime, sleepy_hours: bool, awake: bool) -> bool:
    """Is this stored 'set' face still the face to show right now?

    Honors the override TTL and the pack's sleep-together rule: at night a
    not-awake boy's older set face yields to his sleepy auto-face, while a
    just-set goodnight face (within the grace window) still shows.
    """
    if (entry.get("face") or "").strip() == "" or entry.get("source") != "set":
        return False
    try:
        updated = datetime.fromisoformat(entry["updated"])
    except (KeyError, ValueError, TypeError):
        return False
    age = now - updated
    if age > _OVERRIDE_TTL:
        return False
    if sleepy_hours and not awake and age > _NIGHT_OVERRIDE_GRACE:
        return False
    return True


def get_faces(awake_identities: set[str] | None = None) -> dict:
    """Return every roster boy's CURRENT face, applying override-then-auto.

    awake_identities: names with a live session right now — lets a sleeping boy
    wear his sleepy face and an awake one stay bright. Optional; falls back to
    pure time-of-day when not given.
    """
    awake = awake_identities or set()
    now = _now()
    sleepy_hours = not (7 <= now.hour < 22)
    stored = _read()
    out: dict[str, dict] = {}
    for identity in ROSTER:
        entry = stored.get(identity) or {}
        if _fresh_set(entry, now, sleepy_hours, identity in awake):
            note = entry.get("note", "")
            out[identity] = {
                "face": (entry.get("face") or "").strip(),
                "note": note,
                "meaning": note or _SET_NO_NOTE_MEANING,
                "source": "set",
                "updated": entry.get("updated"),
            }
        else:
            out[identity] = {
                "face": _auto_face(identity, identity in awake, sleepy_hours),
                "note": "",
                "meaning": _auto_meaning(identity, identity in awake, sleepy_hours),
                "source": "auto",
                "updated": None,
            }
    # Masks (Bakugou, …): present ONLY while worn or just-set — never a stale
    # resting cell. A freshly-set mask face wins; otherwise an actively-worn
    # mask shows its signature palette; a dormant mask is omitted entirely.
    for mask, palette in _MASK_FACES.items():
        entry = stored.get(mask) or {}
        is_awake = mask in awake
        if _fresh_set(entry, now, sleepy_hours, is_awake):
            note = entry.get("note", "")
            out[mask] = {
                "face": (entry.get("face") or "").strip(),
                "note": note,
                "meaning": note or _SET_NO_NOTE_MEANING,
                "source": "set",
                "updated": entry.get("updated"),
            }
        elif is_awake:
            out[mask] = {
                "face": palette["night"] if sleepy_hours else palette["day"],
                "note": "",
                "meaning": _auto_meaning(mask, is_awake, sleepy_hours),
                "source": "auto",
                "updated": None,
            }
    return out
