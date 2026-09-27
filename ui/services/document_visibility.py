"""Helpers for deciding which document artifacts belong in chat UI."""

# ANAM GUIDE: HIDE INTERNAL FILES FROM CHAT
# What: One yes/no check that decides whether a file attached to a message is a real document worth showing in chat, or just the boys' behind-the-scenes scratch files (tool outputs, .claude temp files) that should stay hidden.
# Called by: services/chat_turn_finalize.py, services/chat_session_ops.py, and services/platform_bridge.py before they show attachments.
# Edit here when: A junk file keeps appearing as a chat attachment (add its pattern here), or a real document is wrongly being hidden.

from __future__ import annotations

import re
from pathlib import PureWindowsPath


_INTERNAL_PATH_MARKERS = (
    "\\.claude\\",
    "/.claude/",
    "\\.codex\\",
    "/.codex/",
    "\\tool-results\\",
    "/tool-results/",
)


def is_internal_document_artifact(doc: dict) -> bool:
    """Return True for tool scratch/output files that should not render as chat attachments."""
    if not isinstance(doc, dict):
        return False

    path = str(doc.get("path") or "")
    original_name = str(doc.get("original_name") or "")
    filename = str(doc.get("filename") or "")
    lowered_path = path.replace("/", "\\").lower()

    if any(marker.replace("/", "\\") in lowered_path for marker in _INTERNAL_PATH_MARKERS):
        return True

    if path:
        try:
            parsed = PureWindowsPath(path)


            if len(parsed.parts) >= 3 and parsed.parts[0].lower().startswith("c:") and parsed.parts[1].lower() == "users":
                return True
        except Exception:
            pass

    name = original_name or filename
    if name.startswith("toolu_"):
        return True
    if re.fullmatch(r"b[a-z0-9]{8,}\.(txt|json)", name, flags=re.IGNORECASE):
        return True

    return False


def visible_chat_documents(documents: list[dict]) -> list[dict]:
    return [
        doc for doc in documents
        if isinstance(doc, dict) and not is_internal_document_artifact(doc)
    ]
