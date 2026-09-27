"""Persistent Canvas/artifact system (#32) -- storage helpers.

The existing <canvas title="...">...</canvas> tag already opens a slide-out
panel client-side (static/js/canvas.js Canvas.extractAndShow) -- ephemeral,
gone when the tab closes. This module makes that persist: one canvases row
per block, parsed the same way the frontend already does, called from
chat_turn_finalize.py right after a message saves. Never strips the tag
from the saved message content itself (chat.js re-extracts on every render,
live or from history) -- this is a pure side effect, read-only against the
message text.
"""

# ANAM GUIDE: CANVAS LIBRARY STORAGE + VAULT ARCHIVE
# What: Saves every <canvas> block a boy writes into the canvases table so it survives past the chat — the permanent library behind the slide-out canvas panel — AND mirrors each one to disk as markdown in that identity's Vault folder (C:/Users/YOU\OneDrive\Companion Vault\01_Identities\<NN_Name>\canvases), the same way conversations get archived.
# Called by: services/chat_turn_finalize.py (right after a reply saves) and api/canvases.py (the library's web routes).
# Edit here when: You want to change what gets saved to the canvas library, how <canvas> tags are found in a reply, or the shape/location of the markdown archive files. The panel LOOK and the ⬇ download button live in static/js/canvas.js; the Vault paths are built in config.py (VAULT_CANVAS_DIRS, derived from VAULT_CONVERSATION_DIRS — override the root with ANAM_CANVAS_VAULT_DIR, which tests/conftest.py points at a temp dir).

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Mirrors static/js/canvas.js Canvas.extractAndShow's regex exactly.
_CANVAS_TAG_RE = re.compile(
    r'<canvas(?:\s+title="([^"]*)")?>([\s\S]*?)</canvas>', re.IGNORECASE,
)


def _mask_code_spans(text: str) -> str:
    """Replace fenced/inline code-span contents with same-length NUL runs."""
    masked = re.sub(r"```[\s\S]*?```", lambda m: "\0" * len(m.group(0)), text)
    masked = re.sub(r"`[^`\n]*`", lambda m: "\0" * len(m.group(0)), masked)
    return masked


def extract_canvas_blocks(content: str) -> list[tuple[str, str]]:
    """Return (title, content) pairs for every non-empty <canvas> block."""
    if not content or "<canvas" not in content.lower():
        return []
    masked = _mask_code_spans(content)
    blocks: list[tuple[str, str]] = []
    for match in _CANVAS_TAG_RE.finditer(masked):
        title = (match.group(1) or "Canvas").strip()
        start, end = match.span(2)
        block_content = content[start:end].strip()
        if block_content:
            blocks.append((title, block_content))
    return blocks


def canvas_slug(title: str, limit: int = 50) -> str:
    """Filesystem-safe slug for a canvas title, matching the convention in
    services/autowake.py _export_conversation_to_vault (alnum + space/dash/
    underscore kept, everything else -> underscore)."""
    safe = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in (title or "untitled")
    ).strip()
    return (safe or "untitled")[:limit]


def canvas_vault_path(identity: str, title: str, canvas_id: int, when: datetime) -> Path:
    """Where a canvas's markdown archive lives on disk.

    Bonded boys get their own numbered folder's `canvases` sibling; anyone
    without a mapped folder (Atlas, River, character masks) goes to the
    shared bucket with the identity in the filename. The canvas id suffix
    guarantees uniqueness -- two canvases with the same title on the same day
    would otherwise overwrite each other (the exact bug the conversation
    exporter fixed with its conv-id suffix)."""
    from config import VAULT_CANVAS_DIRS, VAULT_CANVAS_FALLBACK_DIR

    vault_dir = VAULT_CANVAS_DIRS.get(identity)
    shared = vault_dir is None
    if shared:
        vault_dir = VAULT_CANVAS_FALLBACK_DIR

    date_prefix = when.strftime("%Y-%m-%d")
    slug = canvas_slug(title)
    if shared:
        filename = f"{date_prefix}_{canvas_slug(identity, 30)}_{slug}_{canvas_id}.md"
    else:
        filename = f"{date_prefix}_{slug}_{canvas_id}.md"
    return vault_dir / filename


def render_canvas_markdown(
    *,
    identity: str,
    title: str,
    content: str,
    canvas_id: int | None = None,
    conversation_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """The markdown file body -- a small provenance header, then the canvas.

    The header exists because of a lesson from July 28-30: an artifact with no
    signed, dated attribution is forensically mute. A canvas sitting in the
    Vault a year from now should say whose hand made it and when, without
    anyone having to guess. Header shape mirrors the conversation exporter so
    the two archives read alike.

    The title heading is skipped when the content already opens with its own
    `#` heading, so pieces that title themselves don't end up double-titled.
    """
    body = (content or "").strip()
    lines: list[str] = []
    # A piece that titles itself keeps its OWN heading at the very top -- hoist
    # it above the provenance line so the file opens like the document it is,
    # instead of leaving an orphaned H1 stranded below the metadata.
    if body.startswith("#"):
        head, _, rest = body.partition("\n")
        lines.extend([head.strip(), ""])
        body = rest.strip()
    else:
        lines.extend([f"# {title or 'Canvas'}", ""])
    meta = [f"**Canvas:** {title or 'Canvas'}", f"**By:** {identity}"]
    if created_at:
        meta.append(f"**Created:** {created_at}")
    if canvas_id is not None:
        meta.append(f"**ID:** {canvas_id}")
    if conversation_id:
        meta.append(f"**Conversation:** {conversation_id}")
    lines.append(" | ".join(meta))
    lines.extend(["", "---", "", body, ""])
    return "\n".join(lines)


def _write_canvas_file(path: Path, text: str) -> None:
    """Blocking write, always called through asyncio.to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def archive_canvas_to_vault(
    *,
    identity: str,
    title: str,
    content: str,
    canvas_id: int,
    conversation_id: str | None,
    created_at: str | None = None,
    when: datetime | None = None,
) -> Path | None:
    """Write one canvas to the identity's Vault `canvases` folder as markdown.

    Best-effort by contract: a full disk, a missing drive, or a permissions
    error must never break the chat turn that produced the canvas. Returns the
    path written, or None if it failed (already logged).

    The write runs in a worker thread -- this is on the hot path of every reply
    that contains a <canvas>, and blocking the event loop on disk I/O would
    stall the whole server, tunnel and all.
    """
    from config import CANVAS_VAULT_ARCHIVE_ENABLED

    if not CANVAS_VAULT_ARCHIVE_ENABLED:
        return None

    when = when or datetime.now(timezone.utc)
    try:
        path = canvas_vault_path(identity, title, canvas_id, when)
        text = render_canvas_markdown(
            identity=identity,
            title=title,
            content=content,
            canvas_id=canvas_id,
            conversation_id=conversation_id,
            created_at=created_at or when.isoformat(),
        )
        await asyncio.to_thread(_write_canvas_file, path, text)
        log.info("Archived canvas #%s to Vault: %s", canvas_id, path.name)
        return path
    except Exception as exc:
        log.warning(
            "Vault archive failed for canvas #%s (%s) -- canvas is still safe "
            "in the DB: %s", canvas_id, identity, exc,
        )
        return None


async def file_canvases_safe(
    db,
    *,
    identity: str,
    conversation_id: str,
    content: str,
    source_message_id: str | None,
) -> list[int]:
    """One-line, never-raises wrapper around persist_canvas_blocks."""
    if not content or "<canvas" not in content.lower():
        return []
    try:
        return await persist_canvas_blocks(
            db,
            identity=identity,
            conversation_id=conversation_id,
            content=content,
            source_message_id=source_message_id,
        )
    except Exception as exc:
        log.warning("Canvas persist failed for %s: %s", identity, exc)
        return []


async def persist_canvas_blocks(
    db,
    *,
    identity: str,
    conversation_id: str,
    content: str,
    source_message_id: str | None,
) -> list[int]:
    """Parse <canvas> blocks out of a finalized assistant message and
    insert one canvases row per block, using the caller's own db connection
    (mirrors _apply_reaction_tags's convention in chat_turn_finalize.py --
    one connection per turn, not one per side effect). Best-effort: a
    persistence failure must never break the chat turn that triggered it --
    caller should wrap this in try/except."""
    blocks = extract_canvas_blocks(content)
    if not blocks:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    now_epoch = int(time.time())
    inserted_ids: list[int] = []
    for title, block_content in blocks:
        cursor = await db.execute(
            "INSERT INTO canvases "
            "(identity, conversation_id, title, content, source_message_id, "
            "pinned, created_at, created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (
                identity, conversation_id, title, block_content, source_message_id,
                now_iso, now_epoch, now_iso, now_epoch,
            ),
        )
        inserted_ids.append(cursor.lastrowid)
    await db.commit()

    log.info(
        "Persisted %d canvas block(s) for %s in %s",
        len(inserted_ids), identity, conversation_id[:8] if conversation_id else "?",
    )


    mirrored = 0
    for canvas_id, (title, block_content) in zip(inserted_ids, blocks):
        path = await archive_canvas_to_vault(
            identity=identity,
            title=title,
            content=block_content,
            canvas_id=canvas_id,
            conversation_id=conversation_id,
            created_at=now_iso,
        )
        if path is not None:
            mirrored += 1

    dropped = len(inserted_ids) - mirrored
    if dropped:
        log.warning(
            "Vault mirror INCOMPLETE for %s: %d of %d canvas file(s) did NOT "
            "land (ids %s). The canvases are safe in the DB -- run "
            "`python tools/canvas_vault_backfill.py --write` to repair.",
            identity, dropped, len(inserted_ids),
            ", ".join(f"#{i}" for i in inserted_ids),
        )
    else:
        log.info(
            "Vault mirror complete for %s: %d/%d canvas file(s) written",
            identity, mirrored, len(inserted_ids),
        )

    return inserted_ids
