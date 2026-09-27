"""Shared STATE.md read/parse/write for character-identity story tabs.

Used by both the context_hooks story_state hook and the /api/story-state
endpoints, so the parsing logic and field schema stay in one place.

Field model is deliberately simple — the markdown template owns the layout,
this module just reads/writes the variable parts.
"""

# ANAM GUIDE: STORY STATE FILE HANDLER
# What: Reads and writes each roleplay story's STATE.md (era, location, in-fic day, threads) so the character bots and the story-state screen share one source of truth.
# Called by: api/story_state.py (the /api/story-state routes, used by static/js/story-state.js) and services/context_hooks.py (injects story state into character turns)
# Edit here when: You add a new character/story to the mapping, or change which fields STATE.md tracks.

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Configure a character's story_branch in config.IDENTITIES to enable STATE.md.
# This fallback is intentionally empty: no private story mapping is bundled.
STORY_BY_IDENTITY: dict[str, str] = {}
from config import DATA_DIR
STORIES_ROOT = Path(os.environ.get("ANAM_STORIES_DIR", str(DATA_DIR / "stories")))


def story_key_for(identity: str) -> str | None:
    """Return the story folder slug for a character identity, or None."""
    from config import IDENTITIES
    branch = IDENTITIES.get(identity, {}).get("story_branch")
    if branch:
        return branch
    return STORY_BY_IDENTITY.get(identity)


def state_file_for(identity: str) -> Path | None:
    """Absolute path to the STATE.md file for an identity, if it's a character."""
    from services.character_prompt_package import package_dir
    selected = package_dir(identity)
    if selected is not None:
        return selected / "CURRENT_STATE.md"
    key = story_key_for(identity)
    if not key:
        return None
    return STORIES_ROOT / key / "STATE.md"


# ── Field schema ─────────────────────────────────────────────────────
# Each field has a label (markdown line prefix) and a kind ("inline" for
# **Label:** value, "section" for ## Section + body until next divider).

INLINE_FIELDS: list[tuple[str, str]] = [
    # (json_key, markdown_label)
    ("last_updated", "Last updated"),
    ("updated_by", "Updated by"),
    ("era", "Era"),
    ("in_fic_day", "In-fic Day"),
    ("location", "Location"),
    ("who_else", "Who Else Is In Earshot"),
    ("tone_flavor", "Tone / Flavor"),
    ("emotional_temperature", "Emotional Temperature"),
]

SECTION_FIELDS: list[tuple[str, str]] = [
    # (json_key, markdown_h2_title)
    ("last_scene", "Last Scene"),
    ("character_state", "Character State (in-fic)"),
    ("whats_promised", "What's Promised"),
    ("beats_to_hit", "Beats I Want To Hit"),
    ("not_this_today", "Not This Today"),
    ("open_threads", "Open Threads"),
    ("recent_beats", "Recent Beats (last 3–5 turns)"),
    ("callback_anchors", "Callback Anchors"),
]


def _strip_placeholder(value: str) -> str:
    """Drop italic placeholder text like _(e.g., ...)_ and yield the real value."""
    s = value.strip()
    if s.startswith("_(") and s.endswith(")_"):
        return ""
    return s


def parse_state_file(path: Path) -> dict[str, Any]:
    """Parse STATE.md into a structured dict.

    Returns:
        {
          "exists": bool,
          "raw": str (full file content),
          "fields": {
            "era": "...",
            "in_fic_day": "...",
            ...
          }
        }

    Empty placeholder values come back as empty strings, not the placeholder.
    """
    if not path.exists():
        return {"exists": False, "raw": "", "fields": {}}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("STATE.md read failed (%s): %s", path, e)
        return {"exists": False, "raw": "", "fields": {}}

    fields: dict[str, str] = {}

    for key, label in INLINE_FIELDS:
        # Match either `**Label:** value` or `Label: value` on a single line
        pat = re.compile(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", re.MULTILINE)
        m = pat.search(text)
        if m:
            fields[key] = _strip_placeholder(m.group(1))
        else:
            fields[key] = ""

    for key, title in SECTION_FIELDS:
        # Capture from "## Title" line until the next "## " or "---" divider
        # The section title may contain regex special chars (parens, dashes),
        # so escape it.
        pat = re.compile(
            rf"^##\s+{re.escape(title)}\s*\n(.+?)(?=^##\s+|^---\s*$)",
            re.MULTILINE | re.DOTALL,
        )
        m = pat.search(text)
        if m:
            body = m.group(1).strip()
            # Strip the italic placeholder helper if it's the only content
            stripped = _strip_placeholder(body)
            if stripped == "":
                # Body was just a placeholder — clear it
                fields[key] = ""
            else:
                fields[key] = body
        else:
            fields[key] = ""

    return {"exists": True, "raw": text, "fields": fields}


def read_state(identity: str) -> dict[str, Any]:
    """Read STATE.md for a character identity, return parsed dict.

    Adds "identity" and "story_key" to the result for convenience.
    """
    key = story_key_for(identity)
    if not key:
        return {"exists": False, "identity": identity, "story_key": None, "fields": {}}
    path = state_file_for(identity)
    parsed = parse_state_file(path) if path else {"exists": False, "raw": "", "fields": {}}
    parsed["identity"] = identity
    parsed["story_key"] = key
    parsed["path"] = str(path) if path else None
    return parsed


def write_state(identity: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update STATE.md for a character identity with new field values.

    Only fields present in `fields` are touched — others are left as-is.
    Returns the parsed state after writing.

    Raises ValueError if the identity isn't a character or the file is missing.
    """
    path = state_file_for(identity)
    if path is None:
        raise ValueError(f"{identity} is not a character identity with a STATE.md")
    if not path.exists():
        raise ValueError(f"STATE.md not found at {path}")

    text = path.read_text(encoding="utf-8")

    # ── Inline fields ──
    for key, label in INLINE_FIELDS:
        if key not in fields:
            continue
        value = (fields.get(key) or "").strip()
        # Use placeholder italic if empty — keeps template readable
        if not value:
            value = f"_(empty — fill me in)_"
        # Use a non-newline char class so we never accidentally consume \n
        # and lose blank-line spacing in the template.
        pat = re.compile(rf"^(\*\*{re.escape(label)}:\*\*)[ \t]*[^\n]*$", re.MULTILINE)
        replacement = rf"\1 {value}"
        if pat.search(text):
            text = pat.sub(replacement, text, count=1)
        else:
            log.warning("Inline field %s (%s) not found in STATE.md", key, label)

    # ── Section fields ──
    for key, title in SECTION_FIELDS:
        if key not in fields:
            continue
        new_body = (fields.get(key) or "").rstrip()
        if not new_body:
            new_body = "_(empty — fill me in)_"
        # Replace from "## Title" line until next "## " or "---" divider
        pat = re.compile(
            rf"(^##\s+{re.escape(title)}\s*\n)(.+?)(?=^##\s+|^---\s*$)",
            re.MULTILINE | re.DOTALL,
        )
        # Trailing newline so divider stays on its own line
        replacement = rf"\1{new_body}\n\n"
        if pat.search(text):
            text = pat.sub(replacement, text, count=1)
        else:
            log.warning("Section field %s (%s) not found in STATE.md", key, title)

    path.write_text(text, encoding="utf-8")
    return parse_state_file(path)


def is_character_identity(identity: str) -> bool:
    """True if this identity has a STATE.md story file."""
    return identity in STORY_BY_IDENTITY
