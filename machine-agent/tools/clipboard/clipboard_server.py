"""Clipboard MCP server — read, write, and search clipboard history."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pyperclip
from fastmcp import FastMCP

mcp = FastMCP("clipboard")

HISTORY_FILE = Path(__file__).parent / "clipboard_history.json"
MAX_HISTORY = 100
log = logging.getLogger(__name__)
_history_lock = threading.Lock()


def _load_history() -> list[dict]:
    """Load history. utf-8-sig so a BOM-prefixed file (e.g. written by
    PowerShell) still parses. A genuinely corrupt file is MOVED ASIDE, never
    silently treated as empty — otherwise the next save would overwrite it."""
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        aside = HISTORY_FILE.with_name(f"{HISTORY_FILE.name}.corrupt-{stamp}")
        try:
            HISTORY_FILE.rename(aside)
            log.warning("clipboard history unreadable (%s); moved to %s and starting fresh", e, aside)
        except OSError as move_err:
            log.error("clipboard history unreadable (%s) and could not be moved aside (%s)", e, move_err)
            raise RuntimeError(f"clipboard history at {HISTORY_FILE} is corrupt and could not be moved aside; refusing to overwrite it") from e
        return []
    return data if isinstance(data, list) else []


def _save_history(history: list[dict]) -> None:
    # Write a sibling temp file and swap it in, so a crash mid-write cannot
    # leave a half-written history behind.
    temp = HISTORY_FILE.with_name(f"{HISTORY_FILE.name}.tmp")
    temp.write_text(
        json.dumps(history[-MAX_HISTORY:], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp, HISTORY_FILE)


@mcp.tool()
def clipboard_read() -> str:
    """Read the current clipboard contents."""
    try:
        content = pyperclip.paste()
        return content if content else "(clipboard is empty)"
    except Exception as e:
        return f"Error reading clipboard: {e}"


@mcp.tool()
def clipboard_write(text: str) -> str:
    """Write text to the clipboard and save to history.

    Args:
        text: The text to copy to the clipboard.
    """
    try:
        pyperclip.copy(text)
        # The bridge serves requests on separate threads; without the lock two
        # writes could both load the same history and one entry would be lost.
        with _history_lock:
            history = _load_history()
            history.append({
                "text": text[:500],
                "timestamp": datetime.now().isoformat(),
                "source": "ai",
            })
            _save_history(history)
        return f"Copied to clipboard ({len(text)} chars)"
    except Exception as e:
        return f"Error writing clipboard: {e}"


@mcp.tool()
def clipboard_history(limit: int = 20) -> str:
    """Show recent clipboard history.

    Args:
        limit: Number of recent entries to show (default 20).
    """
    history = _load_history()
    if not history:
        return "No clipboard history yet."
    recent = history[-limit:]
    lines = []
    for i, entry in enumerate(reversed(recent), 1):
        ts = entry.get("timestamp", "unknown")
        text = entry.get("text", "")
        preview = text[:80].replace("\n", " ")
        if len(text) > 80:
            preview += "..."
        source = entry.get("source", "unknown")
        lines.append(f"{i}. [{ts}] ({source}) {preview}")
    return "\n".join(lines)


@mcp.tool()
def clipboard_search(query: str) -> str:
    """Search clipboard history for text matching a query.

    Args:
        query: Search string to find in clipboard history.
    """
    history = _load_history()
    query_lower = query.lower()
    matches = [
        entry for entry in history
        if query_lower in entry.get("text", "").lower()
    ]
    if not matches:
        return f"No clipboard entries matching '{query}'"
    lines = []
    for entry in matches[-10:]:
        ts = entry.get("timestamp", "unknown")
        text = entry.get("text", "")
        preview = text[:120].replace("\n", " ")
        lines.append(f"[{ts}] {preview}")
    return f"Found {len(matches)} matches (showing last 10):\n" + "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
