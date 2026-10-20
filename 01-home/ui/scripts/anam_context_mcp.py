"""Anam-local context MCP: read-only history plus guarded skill staging.

Run:  python anam_context_mcp.py                     (stdio transport, default)
      MCP_TRANSPORT=http python anam_context_mcp.py   (persistent HTTP on MCP_PORT,
                                                         default 8814 — one shared
                                                         server instead of a fresh
                                                         subprocess per claude -p turn)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP

from services.anam_context_queries import (
    read_conversation,
    recent_conversations,
    search_history,
)
from services.skill_usage import (
    get_skill_usage,
    list_pending_improvements,
    stage_skill_improvement,
)


mcp = FastMCP("anam-context")


@mcp.tool()
def anam_search_history(
    query: str,
    identity: str = "",
    speaker: str = "",
    limit: int = 5,
    window: int = 5,
    mode: str = "hybrid",
) -> str:
    """Search Anam's conversation history and return contextual windows.

    mode: "hybrid" (default, semantic + keyword — finds things by MEANING,
    not just matching words), "semantic" (vector recall only), or "keyword"
    (exact FTS5 word matching only, the original behavior). Use for
    questions such as "what did we decide about X?" or "find the chat where
    we discussed Y" — hybrid mode also catches paraphrases and related
    concepts a keyword-only search would miss. Results include the session
    beginning, the match and nearby messages, and the session ending. This
    is historical evidence; check live files or services separately for
    current state.
    """
    return json.dumps(
        search_history(
            query,
            identity=identity or None,
            speaker=speaker or None,
            limit=limit,
            window=window,
            mode=mode or "hybrid",
        ),
        ensure_ascii=False,
    )


@mcp.tool()
def anam_read_conversation(
    conversation_id: str,
    around_message_id: str = "",
    window: int = 10,
) -> str:
    """Read an Anam conversation or scroll around a message returned by search."""
    return json.dumps(
        read_conversation(
            conversation_id,
            around_message_id=around_message_id or None,
            window=window,
        ),
        ensure_ascii=False,
    )


@mcp.tool()
def anam_recent_conversations(identity: str = "", limit: int = 10) -> str:
    """List recent active Anam conversations, optionally for one identity."""
    return json.dumps(
        recent_conversations(identity=identity or None, limit=limit),
        ensure_ascii=False,
    )


@mcp.tool()
def anam_search_old_chats(identity: str, query: str, limit: int = 5) -> str:
    'Anam search old chats.'
    from services.archive_chunks import search_old_chats_sync

    hits = search_old_chats_sync(identity, query, limit=max(1, min(int(limit or 5), 15)))
    return json.dumps({"ok": True, "identity": identity.lower(), "results": hits}, ensure_ascii=False)


@mcp.tool()
def anam_read_old_chat(
    chunk_id: int = 0,
    identity: str = "",
    conversation_id: str = "",
    start_chunk: int = 0,
    before: int = 2,
    after: int = 4,
) -> str:
    'Anam read old chat.'
    from services.archive_chunks import read_old_chat

    return json.dumps(
        read_old_chat(
            chunk_id=chunk_id or None, identity=identity, conversation_id=conversation_id,
            start_chunk=start_chunk, before=before, after=after,
        ),
        ensure_ascii=False,
    )


@mcp.tool()
def anam_list_old_chats(identity: str, limit: int = 20, offset: int = 0) -> str:
    'Anam list old chats.'
    from services.archive_chunks import list_old_chats

    return json.dumps(list_old_chats(identity, limit=limit, offset=offset), ensure_ascii=False)


@mcp.tool()
def anam_skill_usage() -> str:
    """Read local counts showing which injected skills are actually used."""
    return json.dumps(get_skill_usage(), ensure_ascii=False)


@mcp.tool()
def anam_stage_skill_improvement(
    skill_name: str,
    reason: str,
    proposed_change: str,
) -> str:
    'Anam stage skill improvement.'
    return json.dumps(
        stage_skill_improvement(skill_name, reason, proposed_change, source="anam-context-mcp"),
        ensure_ascii=False,
    )


@mcp.tool()
def anam_pending_skill_improvements(limit: int = 50) -> str:
    """List staged skill proposals awaiting review; does not apply them."""
    return json.dumps(list_pending_improvements(limit), ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
async def anam_list_canvases(identity: str, limit: int = 20, offset: int = 0) -> dict:
    """List saved Anam reply canvases owned by or shared with an identity. Qualia Studio creations are separate: use mind_create for those."""
    from api.canvases import list_canvases
    return _canvas_result(await list_canvases(_canvas_identity(identity), limit, offset))


@mcp.tool(annotations={"readOnlyHint": True})
async def anam_read_canvas(canvas_id: int, identity: str) -> dict:
    """Read a saved Anam reply canvas visible to the active identity."""
    from api.canvases import get_canvas
    return _canvas_result(await get_canvas(canvas_id, _canvas_identity(identity)))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def anam_delete_canvas(canvas_id: int, identity: str) -> dict:
    """Delete an Anam reply canvas. Only its owning identity may delete it."""
    from api.canvases import delete_canvas
    return _canvas_result(await delete_canvas(canvas_id, _canvas_identity(identity)))


@mcp.tool(annotations={"readOnlyHint": False})
async def anam_share_canvas(canvas_id: int, identity: str, share_with: str) -> dict:
    'Anam share canvas.'
    from api.canvases import share_canvas
    from starlette.requests import Request
    body = json.dumps({"identity": _canvas_identity(identity), "share_with": _canvas_identity(share_with)}).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    return _canvas_result(await share_canvas(canvas_id, request))


@mcp.tool(annotations={"readOnlyHint": False})
async def anam_unshare_canvas(canvas_id: int, identity: str, shared_identity: str) -> dict:
    'Anam unshare canvas.'
    from api.canvases import unshare_canvas
    return _canvas_result(await unshare_canvas(canvas_id, _canvas_identity(shared_identity), _canvas_identity(identity)))


def _canvas_identity(identity):
    from config import IDENTITIES
    return next((name for name in IDENTITIES if name.lower() == identity.lower()), identity)


def _canvas_result(result):
    from starlette.responses import Response
    if isinstance(result, Response):
        raise ValueError(json.loads(result.body).get("error", "Canvas operation failed"))
    return result


@mcp.tool(annotations={"readOnlyHint": True})
def anam_view_image(path: str = "", media_id: str = "", crop: list[int] | None = None, max_dimension: int = 1536):
    """View a private local image as native pixels and a reusable media ID. Supply exactly one path or media_id. Optional crop [left, top, right, bottom] uses original image coordinates. Previews expire after 24 hours; originals are never edited. In the ChatGPT tag fallback, Anam attaches the preview to the continuation."""
    from services.anam_media import native_preview
    return native_preview(path, media_id, crop, max_dimension)


async def _computer_request(operation, **arguments):
    import httpx
    from dotenv import dotenv_values
    key = os.getenv("ANAM_API_KEY") or dotenv_values(ROOT / ".env").get("ANAM_API_KEY")
    async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
        response = await client.post("http://127.0.0.1:8790/api/computer/terminal",
            headers={"Authorization": "Bearer " + (key or "")}, json={"operation": operation, **arguments})
    if response.is_error:
        raise ValueError(response.json().get("detail", "Terminal request failed"))
    return response.json()


@mcp.tool(annotations={"readOnlyHint": False})
async def anam_terminal_start(command: str, cwd: str, shell: str = "powershell") -> dict:
    """Start a command in its own interactive Windows terminal. Returns a session ID promptly; read output, send stdin, or send Ctrl+C using the matching tools. Use powershell or cmd. Sessions belong to Anam and end on restart. To start an interactive shell itself, use command powershell.exe -NoLogo -NoProfile."""
    return await _computer_request("start", command=command, cwd=cwd, shell=shell)


@mcp.tool(annotations={"readOnlyHint": True})
async def anam_terminal_read(session_id: str, cursor: int = 0, max_chars: int = 24000, wait_ms: int = 0) -> dict:
    """Collect interactive terminal output without restarting its command. Pass next_cursor from the previous read. Bounded output reports truncation, process exit, and whether output collection is complete. Optional wait_ms up to 10000."""
    return await _computer_request("read", session_id=session_id, cursor=cursor, max_chars=max_chars, wait_ms=wait_ms)


@mcp.tool(annotations={"readOnlyHint": False})
async def anam_terminal_write(session_id: str, text: str, enter: bool = False) -> dict:
    """Send input to your explicit interactive terminal session. Set enter=true to answer a line prompt. Input can execute commands; check the current output before sending it."""
    return await _computer_request("write", session_id=session_id, text=text, enter=enter)


@mcp.tool(annotations={"readOnlyHint": False})
async def anam_terminal_interrupt(session_id: str) -> dict:
    """Send Ctrl+C to your interactive terminal. Read afterward to verify whether the application stopped; sending the signal is not proof of exit."""
    return await _computer_request("interrupt", session_id=session_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def anam_terminal_close(session_id: str) -> dict:
    """Close your interactive terminal and terminate its remaining child processes. Collect needed output first. Requires the exact session ID."""
    return await _computer_request("close", session_id=session_id)


@mcp.tool(annotations={"readOnlyHint": True})
async def anam_connector_health(server: str = "", probe: bool = False) -> dict:
    """Read redacted Anam connector health and specific recovery advice. Set server and probe=true to check one live catalog. Distinguishes login, permissions, rate limits, missing tools and stale sessions. Does not expose ChatGPT's independent private approval state."""
    import httpx
    from dotenv import dotenv_values
    key = os.getenv("ANAM_API_KEY") or dotenv_values(ROOT / ".env").get("ANAM_API_KEY")
    async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
        response = await client.get("http://127.0.0.1:8790/api/computer/connectors", headers={"Authorization": "Bearer " + (key or "")}, params={"server": server, "probe": str(probe).lower()})
    response.raise_for_status()
    return response.json()


@mcp.tool(annotations={"readOnlyHint": False})
def sticker_give(identity: str, why: str, art: str = "", for_day: str = "", seen_in: str = "") -> dict:
    'Sticker give.'
    from services import sticker_book
    try:
        result = sticker_book.give(identity, why, art=art, for_day=for_day, seen_in=seen_in)
    except sticker_book.StickerRefused as exc:
        raise ValueError(f"Not given: {exc}") from None
    result["note"] = "It's on her page now. She'll see it at /sticker-book."
    return result


@mcp.tool(annotations={"readOnlyHint": True})
def sticker_book_day(identity: str, day: str = "") -> dict:
    'Sticker book day.'
    from services import sticker_book
    page = sticker_book.day_page(day)
    page["your_art"] = sticker_book.own_art(identity)
    return page


@mcp.tool(annotations={"readOnlyHint": False})
def sticker_note(identity: str, sticker_id: str, text: str) -> dict:
    'Sticker note.'
    from services import sticker_book
    try:
        result = sticker_book.add_note(identity, sticker_id, text)
    except sticker_book.StickerRefused as exc:
        raise ValueError(f"Not written: {exc}") from None
    result["note_shown"] = "It's under your sticker now, in smaller ink."
    return result


@mcp.tool(annotations={"readOnlyHint": False})
def coloring_drop(identity: str, image_path: str, title: str = "", drawn_by: str = "", tool: str = "",
                  made_from: str = "") -> dict:
    'Coloring drop.'
    from services import coloring_drawer
    try:
        result = coloring_drawer.drop(identity, image_path, title=title, drawn_by=drawn_by, tool=tool,
                                      made_from=made_from)
    except coloring_drawer.DropRefused as exc:
        raise ValueError(f"Not dropped: {exc}") from None
    result["note"] = "It's in her drawer now. She colours it at /princess. It never leaves unless she hangs it up."
    return result


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "http":
        port = int(os.environ.get("MCP_PORT", "8814"))
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run(transport="stdio")
