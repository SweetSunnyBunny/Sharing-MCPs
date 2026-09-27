"""Backend-agnostic helpers for Claude Code CLI integration."""

# ANAM GUIDE: SHARED CLI MESSAGE COMPOSER
# What: Pure text helpers shared by all CLI-flavored backends — builds the per-turn message (identity + history + who-it's-from banner), formats DB history, and detects approval prompts and image/document paths in tool output.
# Called by: services/claude_subprocess.py, services/claude_pty.py, services/claude_agent_sdk_provider.py, services/provider_router.py.
# Edit here when: changing what a turn's text looks like before it reaches Claude Code — history format, sender banner, first-message shape — so BOTH backends change together.
# Note: the docstring above is stale — claude_subprocess.py came back as the default backend and imports from here too.

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from config import (
    DOCUMENT_ALLOWED_EXTENSIONS,
    DOCUMENTS_DIR,
    IMAGE_ALLOWED_EXTENSIONS,
    IMAGES_DIR,
)
from services.identity_context import build_identity_anchor

log = logging.getLogger(__name__)


# ─── Approval / sensitive-path detection ──────────────────────────────────────

_APPROVAL_KEYWORDS = (
    "approval", "permission", "not permitted",
    "not allowed", "denied", "forbidden",
    "requires user confirmation", "sensitive file",
)

_RULE_PATH_RE = re.compile(
    r"(?P<verb>Write|Edit|Read|Execute|Bash)\s*(?:to\s+)?(?:\()?"
    r"(?P<path>[A-Z]:\\[^\s;,)]+|/[^\s;,)]+|~/[^\s;,)]+)",
    re.IGNORECASE,
)

# CC hardcodes `.claude/skills/*` and `.claude/agents/*` as sensitive paths.
_SENSITIVE_PATH_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|[/~])[^\s\"'`,;)]*\.claude[\\/](?:skills|agents)[\\/][^\s\"'`,;)]*)",
    re.IGNORECASE,
)


def _infer_suggested_rule(error_text: str) -> str | None:
    m = _RULE_PATH_RE.search(error_text)
    if not m:
        return None
    verb_raw = m.group("verb").lower()
    path = m.group("path").replace("\\", "/")
    last_slash = path.rfind("/")
    if last_slash > 0:
        path = path[:last_slash + 1] + "*"
    verb_map = {"read": "Read", "execute": "Bash", "bash": "Bash"}
    verb = verb_map.get(verb_raw, "Write")
    return f"{verb}({path})"


def _extract_sensitive_path(error_text: str) -> str | None:
    m = _SENSITIVE_PATH_RE.search(error_text)
    return m.group(1) if m else None


def _annotate_sensitive_path(approval_evt: dict, error_text: str) -> None:
    sensitive_path = _extract_sensitive_path(error_text)
    if sensitive_path:
        approval_evt["sensitive_path"] = True
        approval_evt["target_path"] = sensitive_path
        approval_evt["bypass_recommendation"] = "file-scribe"


# ─── Image / document extraction from tool-result text ────────────────────────

def _extract_image_paths(text: str, identity: str | None = None) -> list[dict]:
    from api.images import _is_allowed_source_path, informative_filename
    if not text or not isinstance(text, str):
        return []
    results = []
    seen = set()
    ext_group = '|'.join(ext.lstrip('.') for ext in IMAGE_ALLOWED_EXTENSIONS)
    path_pattern = (
        r'(?:[A-Za-z]:[\\\/][^\n<>"]+?|/[^\s<>"]+?)'
        r'\.(?:' + ext_group + r')'
        r'(?=[\s<>"\n,;:\)\*]|$)'
    )
    for match in re.finditer(path_pattern, text, re.IGNORECASE | re.MULTILINE):
        found_path = Path(match.group().strip())
        path_str = str(found_path)
        if path_str in seen:
            continue
        seen.add(path_str)
        if not (found_path.exists() and found_path.is_file()):
            continue
        try:
            resolved = found_path.resolve()
        except OSError:
            continue
        if not _is_allowed_source_path(resolved):
            log.warning("Tool-result image extraction blocked - outside allowlist: %s", resolved)
            continue
        try:
            filename = informative_filename(identity, resolved.suffix.lower())
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            dest = IMAGES_DIR / filename
            shutil.copy2(str(resolved), str(dest))
            results.append({
                "path": str(resolved),
                "url": f"/api/images/file/{filename}",
            })
            log.info("Extracted image from tool result: %s -> %s", resolved, filename)
        except Exception as e:
            log.exception("Failed to copy tool result image %s: %s", resolved, e)
    return results


def _human_size(size_bytes: int) -> str:
    if size_bytes > 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    return f"{round(size_bytes / 1024)} KB"


def _extract_document_paths(text: str, identity: str | None = None) -> list[dict]:
    from api.documents import informative_filename
    from api.images import _is_allowed_source_path
    if not text or not isinstance(text, str):
        return []
    results = []
    seen = set()
    ext_group = "|".join(ext.lstrip(".") for ext in DOCUMENT_ALLOWED_EXTENSIONS)
    path_pattern = (
        r'(?:[A-Za-z]:[\\\/][^\n<>"]+?|/[^\s<>"]+?)'
        r'\.(?:' + ext_group + r')'
        r'(?=[\s<>"\n,;:\)]|$)'
    )
    for match in re.finditer(path_pattern, text, re.IGNORECASE | re.MULTILINE):
        found_path = Path(match.group().strip())
        path_str = str(found_path)
        if path_str in seen:
            continue
        seen.add(path_str)
        if not (found_path.exists() and found_path.is_file()):
            continue
        try:
            resolved = found_path.resolve()
        except OSError:
            continue
        if not _is_allowed_source_path(resolved):
            log.warning("Tool-result document extraction blocked - outside allowlist: %s", resolved)
            continue
        try:
            filename = informative_filename(identity, resolved.suffix.lower())
            DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
            dest = DOCUMENTS_DIR / filename
            shutil.copy2(str(resolved), str(dest))
            doc_id = filename.rsplit(".", 1)[0]
            results.append({
                "doc_id": doc_id,
                "filename": filename,
                "original_name": resolved.name,
                "size_display": _human_size(dest.stat().st_size),
                "url": f"/api/documents/file/{filename}",
                "path": str(resolved),
            })
            log.info("Extracted document from tool result: %s -> %s", resolved, filename)
        except Exception as e:
            log.exception("Failed to copy tool result document %s: %s", resolved, e)
    return results


# ─── Prompt composition ───────────────────────────────────────────────────────

def _image_blocks_to_text(image_blocks: list[dict] | None) -> str:
    """Convert Anthropic image content blocks to text instructions for CLI providers."""
    if not image_blocks:
        return ""
    notes = []
    for block in image_blocks:
        if block.get("type") != "image":
            continue
        source = block.get("source", {})
        url = source.get("url", "")
        if "/api/images/file/" in url:
            filename = url.split("/api/images/file/")[-1]
            img_path = IMAGES_DIR / filename
            if img_path.exists():
                notes.append(
                    f"[Owner shared an image: {img_path}\n"
                    f"Use the Read tool to view it.]"
                )
            else:
                notes.append(f"[Owner shared an image but the file was not found: {filename}]")
        elif url:
            notes.append(f"[Owner shared an image: {url}]")
    return "\n\n".join(notes)


def _db_message_to_cli_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    text_parts = []
    image_blocks = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            text_parts.append(f"[Used tool: {block.get('name', '?')}]")
        elif block_type == "tool_result":
            result_text = str(block.get("content", ""))
            if len(result_text) > 500:
                result_text = result_text[:500] + "..."
            text_parts.append(f"[Tool result: {result_text}]")
        elif block_type == "image":
            image_blocks.append(block)
    image_text = _image_blocks_to_text(image_blocks)
    if image_text:
        text_parts.insert(0, image_text)
    return "\n".join(p for p in text_parts if p)


def _format_history(
    db_messages: list[dict] | None,
    identity: str,
    *,
    per_message_chars: int = 1500,
    message_limit: int | None = None,
) -> str:
    if not db_messages:
        return ""
    replay_messages = (
        db_messages[-message_limit:]
        if message_limit and len(db_messages) > message_limit
        else db_messages
    )
    lines = []
    for msg in replay_messages:
        role = msg.get("role", "user")
        content = _db_message_to_cli_text(msg.get("content", ""))
        if not content:
            continue
        speaker = "Owner" if role == "user" else (msg.get("identity") or identity or "Assistant").strip()
        if per_message_chars > 0 and len(content) > per_message_chars:
            content = content[:per_message_chars] + "\n[...truncated...]"
        lines.append(f"{speaker}: {content}")
    if not lines:
        return ""
    return "[RECENT CONVERSATION HISTORY]\n" + "\n\n".join(lines) + "\n[/RECENT CONVERSATION HISTORY]"


async def _load_recent_history(
    conversation_id: str,
    identity: str,
    limit: int = 30,
    *,
    per_message_chars: int = 1500,
) -> str:
    """Pull recent turns from the DB and format them for the first user message."""
    from db.database import get_db, release_db
    from services.session_manager import get_messages
    db = await get_db()
    try:
        messages = await get_messages(db, conversation_id, limit=limit)
    finally:
        await release_db(db)
    return _format_history(
        messages,
        identity,
        per_message_chars=per_message_chars,
        message_limit=limit,
    )


def _build_first_message(
    *,
    identity: str,
    user_message: str,
    orientation_context: str,
    mode_rules: str,
    skill_context: str,
    image_blocks: list[dict] | None,
    history_block: str,
    identity_prompt: str,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
) -> str:
    """Compose the full per-turn message."""
    parts: list[str] = []


    image_text = _image_blocks_to_text(image_blocks)
    user_block = f"[{sender_banner} — RESPOND TO THIS]\n"
    if image_text:
        user_block += image_text + "\n\n"
    user_block += user_message
    parts.append(user_block)

    anchor = build_identity_anchor(identity)
    parts.append("[IDENTITY ANCHOR — reinforces who you are; --system-prompt already loaded this]")
    parts.append(anchor)
    parts.append("[/IDENTITY ANCHOR]")

    if identity_prompt:
        parts.append("[IDENTITY PROMPT — This defines who you are. Follow it completely.]")
        parts.append(identity_prompt)
        parts.append("[/IDENTITY PROMPT]")

    if mode_rules:
        parts.append(f"[MODE RULES]\n{mode_rules}\n[/MODE RULES]")
    if orientation_context:
        parts.append(f"[ORIENTATION]\n{orientation_context}\n[/ORIENTATION]")
    if history_block:
        parts.append(history_block)
    if skill_context:
        parts.append(
            "[AUTO-LOADED LOCAL SKILLS]\n"
            f"{skill_context}\n\n"
            "[Apply the skill guidance above when relevant to this turn.]"
        )

    return "\n\n".join(parts)
