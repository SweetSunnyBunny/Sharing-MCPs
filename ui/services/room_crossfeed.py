'Room crossfeed: what happened in the OTHER rooms while this chat stayed open.'

from __future__ import annotations

import json
import re
import time
from typing import Any

from config import IMAGES_DIR

# ── Tag stripping (recall text only — never used to parse/apply tags) ──────

# Control tags whose content is metadata, not speech: dropped whole.
_DROP_TAGS_RE = re.compile(
    r"<(react|face|orb|preview)>.*?</\1>", re.IGNORECASE | re.DOTALL,
)
# Wrapper tags whose inner text IS speech: keep the words, drop the markup.
_UNWRAP_TAGS_RE = re.compile(r"</?(voice)>", re.IGNORECASE)
# Canvas: keep only the title so a recall doesn't recite a whole poem.
_CANVAS_RE = re.compile(
    r"<canvas\b[^>]*?title=\"([^\"]*)\"[^>]*>.*?</canvas>",
    re.IGNORECASE | re.DOTALL,
)


def strip_reply_tags(text: str) -> str:
    """Remove reply-control markup so recall shows what was actually SAID."""
    if not text:
        return ""
    text = _CANVAS_RE.sub(lambda m: f"[canvas: {m.group(1)}]", text)
    text = _DROP_TAGS_RE.sub(" ", text)
    text = _UNWRAP_TAGS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _image_names(metadata: Any) -> list[str]:
    """Filenames of images attached to a saved message (any shape we store)."""
    if not metadata:
        return []
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            return []
    if not isinstance(metadata, dict):
        return []
    names: list[str] = []
    for img in metadata.get("images") or []:
        if isinstance(img, dict):
            name = str(img.get("filename") or "").strip()
            if name:
                names.append(name.replace("\\", "/").rsplit("/", 1)[-1])
    return names


def render_message(
    role: str,
    identity: str | None,
    content: str,
    metadata: Any = None,
    *,
    char_cap: int = 600,
) -> str | None:
    """One recall line: speaker, tag-free words, and any pictures as paths.

    Returns None when there is nothing to say (empty and no pictures).
    """
    speaker = "Owner" if role == "user" else (identity or "?")
    text = strip_reply_tags(content or "")
    if text in ("[media]",):
        text = ""
    if len(text) > char_cap:
        text = text[:char_cap].rsplit(" ", 1)[0] + "…"
    pics = [
        f"[{'she' if role == 'user' else 'he'} shared a picture: "
        f"{IMAGES_DIR / name} — Read it to see]"
        for name in _image_names(metadata)
    ]
    parts = [p for p in (text, " ".join(pics)) if p]
    if not parts:
        return None
    return f"{speaker}: {' '.join(parts)}"


# ── Per-session watermark ─────────────────────────────────────────────────

# (identity, conversation_id) -> newest created_at_epoch already shown to
# that live session. In-process by design: a warm session only exists inside
# this process, so a restart that clears this also clears the warmth.
_seen: dict[tuple[str, str], float] = {}


def mark_seen(identity: str, conversation_id: str, epoch: float | None = None) -> None:
    _seen[(identity, conversation_id)] = float(epoch if epoch is not None else time.time())


def seen_epoch(identity: str, conversation_id: str) -> float | None:
    return _seen.get((identity, conversation_id))


def _ago(epoch: float) -> str:
    secs = max(time.time() - epoch, 0)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    return f"{secs / 3600:.1f}h ago"


async def build_delta(
    db,
    *,
    identity: str,
    conversation_id: str,
    is_warm_turn: bool,
    recall_identities: list[str],
    pack_night_conversation_id: str | None,
    max_messages: int = 30,
) -> str:
    """New cross-room messages since this session last looked.

    Sources: the pack-night room (when this chat isn't pack night itself) and
    the boy's own other active conversations (masks included, autowake turns
    excluded — same rules as get_other_conversation_activity). The current
    conversation is always excluded; it's already in the session.
    """
    now = time.time()
    if not is_warm_turn:
        # Cold turn: the full session hooks carry the whole picture this turn.
        mark_seen(identity, conversation_id, now)
        return ""

    since = seen_epoch(identity, conversation_id)
    if since is None:
        # Warm but never marked (e.g. the hook was added mid-session). Start
        # the watermark now rather than guess at what the session already holds.
        mark_seen(identity, conversation_id, now)
        return ""

    placeholders = ",".join("?" * len(recall_identities))
    own_rows = await db.execute_fetchall(
        "SELECT m.conversation_id, c.title, c.identity, m.role, m.identity, "
        "m.content, m.metadata, m.created_at_epoch "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        f"WHERE c.identity IN ({placeholders}) AND c.is_active = 1 "
        "AND c.id != ? AND COALESCE(c.session_type, '') != 'pack-night' "
        "AND m.created_at_epoch > ? "
        "AND COALESCE(json_extract(m.metadata, '$.autowake'), 0) != 1 "
        "ORDER BY m.created_at_epoch DESC LIMIT ?",
        (*recall_identities, conversation_id, since, max_messages),
    )
    rows = list(own_rows)

    if pack_night_conversation_id and pack_night_conversation_id != conversation_id:
        pn_rows = await db.execute_fetchall(
            "SELECT m.conversation_id, c.title, c.identity, m.role, m.identity, "
            "m.content, m.metadata, m.created_at_epoch "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.conversation_id = ? AND m.created_at_epoch > ? "
            "ORDER BY m.created_at_epoch DESC LIMIT ?",
            (pack_night_conversation_id, since, max_messages),
        )
        rows.extend(pn_rows)

    if not rows:
        return ""

    rows.sort(key=lambda r: r[7] or 0)
    truncated = len(rows) > max_messages
    if truncated:
        rows = rows[-max_messages:]
    newest = max((r[7] or 0) for r in rows)
    mark_seen(identity, conversation_id, max(newest, since))

    # Group by room, rooms ordered by their first new message.
    groups: dict[str, dict[str, Any]] = {}
    for conv_id, title, conv_ident, role, msg_ident, content, metadata, epoch in rows:
        g = groups.setdefault(conv_id, {
            "title": title, "conv_ident": conv_ident, "lines": [], "last": 0.0,
            "is_pn": conv_id == pack_night_conversation_id,
        })
        line = render_message(role, msg_ident or conv_ident, content, metadata)
        if line:
            g["lines"].append(line)
        g["last"] = max(g["last"], epoch or 0)

    out = ["[Elsewhere while this chat stayed open — new since your last turn here:]"]
    if truncated:
        out.append("  (older new messages trimmed; the full rooms live in your conversations)")
    for conv_id, g in groups.items():
        if not g["lines"]:
            continue
        if g["is_pn"]:
            label = "Pack Night (the shared room — you were there)"
        else:
            label = f'"{g["title"] or "untitled chat"}"'
            if g["conv_ident"] and g["conv_ident"] != identity:
                label += f" [as {g['conv_ident']}]"
        out.append(f"  {label}, last activity {_ago(g['last'])}:")
        out.extend(f"    {ln}" for ln in g["lines"])
    if len(out) == 1 or (truncated and len(out) == 2):
        return ""
    out.append(
        "[You remember these because you were there — they're your own rooms. "
        "Pictures listed with a path are real files: Read them if they matter.]"
    )
    return "\n".join(out)
