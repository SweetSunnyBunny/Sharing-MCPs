"""WebSocket handler -- bridges the browser UI to Claude Code or direct API."""

import asyncio
import json
import logging
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.chat_pipeline import process_chat_message, _DISCONNECTED

from config import (
    IMAGES_DIR, IMAGE_ALLOWED_EXTENSIONS,
    DOCUMENTS_DIR, DOCUMENT_ALLOWED_EXTENSIONS,
)
from db.database import get_db, release_db
from services.session_auth import hash_session_token
from services import claude_pty  # PTY-specific ops (approval keystrokes are TUI-only)
from services import provider_router  # backend-aware session clear + -p pre-warm
from services.connection_registry import register, unregister, set_active_identity, set_active_conversation
from services.session_manager import (
    get_or_create_conversation, save_message,
)
from services.chat_session_ops import (
    create_conversation,
    load_history_payload,
    load_more_payload,
    prepare_regeneration,
    switch_identity_conversation,
    update_conversation_pin,
)

log = logging.getLogger(__name__)
router = APIRouter()

def _register_content_images(content: str, identity: str | None = None) -> tuple[str, list[dict]]:
    """Scan markdown ![alt](path) in response text, copy files to IMAGES_DIR, rewrite URLs."""
    from api.images import _is_allowed_source_path, informative_filename
    images = []

    def replace_path(match):
        alt = match.group(1)
        url = match.group(2)
        # Skip if already a serve URL or external URL
        if url.startswith("/api/") or url.startswith("http://") or url.startswith("https://"):
            return match.group(0)
        # Try as file path (Windows or Unix)
        path = Path(url)
        if not (path.exists() and path.is_file() and path.suffix.lower() in IMAGE_ALLOWED_EXTENSIONS):
            return match.group(0)
        # Allowlist gate: refuse to copy files from outside trusted source
        # directories. Defends against prompt-injected model output trying
        # to exfiltrate arbitrary local files via the public serve URL.
        try:
            resolved = path.resolve()
        except OSError:
            return match.group(0)
        if not _is_allowed_source_path(resolved):
            log.warning("Content image registration blocked - outside allowlist: %s", resolved)
            return match.group(0)
        filename = informative_filename(identity, path.suffix.lower())
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        dest = IMAGES_DIR / filename
        shutil.copy2(str(resolved), str(dest))
        serve_url = f"/api/images/file/{filename}"
        images.append({"path": str(resolved), "url": serve_url})
        log.info("Registered content image: %s -> %s", resolved, filename)
        return f"![{alt}]({serve_url})"

    rewritten = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_path, content)
    return rewritten, images


def _register_content_documents(content: str, identity: str | None = None) -> tuple[str, list[dict]]:
    """Scan markdown links [text](path), copy local docs to DOCUMENTS_DIR, rewrite URLs."""
    from api.documents import informative_filename, human_size
    from api.images import _is_allowed_source_path
    from services.docling_convert import CONVERTIBLE_EXTENSIONS, convert_and_save
    documents = []

    def replace_path(match):
        label = match.group(1)
        url = match.group(2)
        # Skip image markdown and already-serve/external links
        if match.group(0).startswith("!"):
            return match.group(0)
        if url.startswith("/api/") or url.startswith("http://") or url.startswith("https://"):
            return match.group(0)
        path = Path(url)
        if not (path.exists() and path.is_file() and path.suffix.lower() in DOCUMENT_ALLOWED_EXTENSIONS):
            return match.group(0)
        # Allowlist gate: refuse to copy files from outside trusted source
        # directories. Defends against prompt-injected model output trying
        # to exfiltrate arbitrary local files via the public serve URL.
        try:
            resolved = path.resolve()
        except OSError:
            return match.group(0)
        if not _is_allowed_source_path(resolved):
            log.warning("Content document registration blocked - outside allowlist: %s", resolved)
            return match.group(0)
        filename = informative_filename(identity, path.suffix.lower())
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = DOCUMENTS_DIR / filename
        shutil.copy2(str(resolved), str(dest))
        # Auto-convert PDF/DOCX/ODT/PPTX to markdown companion via docling
        ext = path.suffix.lower()
        if ext in CONVERTIBLE_EXTENSIONS:
            md_path = convert_and_save(dest)
            if md_path:
                log.info("Docling converted content document: %s", dest.name)
        serve_url = f"/api/documents/file/{filename}"
        documents.append({
            "doc_id": filename.rsplit(".", 1)[0],
            "filename": filename,
            "original_name": path.name,
            "size_display": human_size(dest.stat().st_size),
            "path": str(resolved),
            "url": serve_url,
        })
        log.info("Registered content document: %s -> %s", resolved, filename)
        return f"[{label}]({serve_url})"

    rewritten = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", replace_path, content)
    return rewritten, documents


# Simple in-memory rate limiter for WebSocket connections. Mobile Chrome may
# legitimately replace several stale sockets while waking; keep protection
# against a runaway client without colliding with the browser's retry budget.
_ws_connect_times: dict[str, list[float]] = {}
_WS_RATE_LIMIT = 30
_WS_RATE_WINDOW = 60  # seconds


async def _handle_slash_command(command: str, args: str, identity: str) -> str:
    """Handle server-side slash commands."""
    from zoneinfo import ZoneInfo

    if command == "timer":
        # Parse: /timer 2h check in  OR  /timer 30m reminder
        import re
        from datetime import datetime, timedelta, timezone
        from config import TIMEZONE
        from db.database import get_db, release_db
        from services.autowake_service import create_timer

        match = re.match(r'(\d+)\s*(m|min|h|hr|hour)s?\s*(.*)', args, re.IGNORECASE)
        if not match:
            return "Usage: /timer 30m reminder text"

        amount = int(match.group(1))
        unit = match.group(2).lower()
        context = match.group(3).strip() or "Timer reminder"

        if unit.startswith('h'):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(minutes=amount)

        tz = ZoneInfo(TIMEZONE)
        fire_at = (datetime.now(tz) + delta).isoformat()

        db = await get_db()
        try:
            timer = await create_timer(db, identity=identity, fire_at=fire_at, context=context)
            return f"Timer set for {amount}{'h' if unit.startswith('h') else 'm'}: {context}"
        finally:
            await release_db(db)

    elif command == "status":
        # Parse: /status 🌸 feeling good
        from db.database import get_db, release_db
        parts = args.strip()
        if not parts:
            return "Usage: /status emoji text"

        # First char or emoji is the status emoji
        import re
        emoji_match = re.match(r'(\S+)\s*(.*)', parts)
        if emoji_match:
            emoji = emoji_match.group(1)
            text = emoji_match.group(2).strip()
        else:
            emoji = ""
            text = parts

        try:
            import json as _json
            from config import RITUALS_DIR
            status_file = RITUALS_DIR / "status.json"
            status_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if status_file.exists():
                try:
                    data = _json.loads(status_file.read_text(encoding="utf-8"))
                except (_json.JSONDecodeError, OSError):
                    data = {}
            data["emoji"] = emoji
            data["text"] = text
            data["updated_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
            status_file.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            return f"Status updated: {emoji} {text}"
        except Exception as e:
            return f"Error updating status: {e}"

    return f"Unknown command: /{command}"


def _ws_rate_limited(client_ip: str) -> bool:
    """Return True if this IP has exceeded the WebSocket connection rate limit."""
    now = time.monotonic()
    times = _ws_connect_times.get(client_ip, [])
    # Prune entries outside the window
    times = [t for t in times if now - t < _WS_RATE_WINDOW]
    if len(times) >= _WS_RATE_LIMIT:
        _ws_connect_times[client_ip] = times
        return True
    times.append(now)
    _ws_connect_times[client_ip] = times
    return False


# Background pre-warm tasks, kept referenced so the event loop doesn't GC them
# mid-flight (a bare create_task can be collected before it runs).
_PREWARM_TASKS: set = set()


def _fire_prewarm(identity: str, conversation_id: str | None) -> None:
    """Spawn the -p session for (identity, conv) in the background so Owner's
    first message lands on an already-booted process. No-op for PTY/other
    providers (maybe_prewarm guards that). Fire-and-forget — never blocks the
    WS response or raises into the handler."""
    if not conversation_id:
        return
    task = asyncio.create_task(provider_router.maybe_prewarm(identity, conversation_id))
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)


# ANAM GUIDE: CHAT SERVER ENTRY
# Browser WebSocket messages arrive here. Connection/session commands stay in
# this boundary; ordinary message turns are handed to chat_pipeline.py.
@router.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    # Auth check -- must accept() first so the close code reaches the client
    await ws.accept()

    # Rate limit WebSocket connections. This is intentionally above the
    # browser's reconnect budget so a normal Android wake can recover.
    client_ip = ws.client.host if ws.client else "unknown"
    if _ws_rate_limited(client_ip):
        log.warning("WebSocket connection rate-limited for %s", client_ip)
        await ws.close(code=4029, reason="Too many connections, try again later")
        return

    from config import AUTH_ENABLED
    if AUTH_ENABLED:
        session_token = ws.cookies.get("anam_session")
        if not session_token:
            await ws.close(code=4001, reason="Not authenticated")
            return
        token_hash = hash_session_token(session_token)
        db_check = await get_db()
        try:
            rows = await db_check.execute_fetchall(
                "SELECT expires_at_epoch FROM sessions WHERE token_hash = ?",
                (token_hash,),
            )
            if not rows:
                await ws.close(code=4001, reason="Invalid session")
                return
            if not rows[0][0]:
                await ws.close(code=4001, reason="Invalid session")
                return
            if int(datetime.now(timezone.utc).timestamp()) > int(rows[0][0]):
                await ws.close(code=4001, reason="Session expired")
                return
        finally:
            await release_db(db_check)
    await register(ws)
    try:
        from services.connection_registry import set_connection_device
        set_connection_device(ws, ws.headers.get("user-agent", ""))
    except Exception:
        pass
    log.info("WebSocket connected")

    current_identity = "Avery"
    current_conversation = None
    current_session_type = "chat"
    active_categories: set[str] = {"core"}
    pending_messages: deque[dict] = deque()

    try:
        while True:
            if pending_messages:
                msg = pending_messages.popleft()
            else:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

            msg_type = msg.get("type", "")

            # Heartbeat -- keep connection alive through tunnel
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            # Identity switch
            if msg_type == "switch_identity":
                current_identity = msg.get("identity", current_identity)
                current_session_type = "chat"
                await set_active_identity(current_identity, ws=ws)
                current_conversation = await switch_identity_conversation(current_identity)
                set_active_conversation(current_identity, current_conversation)
                # Pre-warm the -p session now that we know who she's about to
                # talk to and in which conversation — her first message lands
                # on an already-booted process instead of a cold spawn.
                _fire_prewarm(current_identity, current_conversation)
                await ws.send_json({
                    "type": "identity_switched",
                    "identity": current_identity,
                    "conversation_id": current_conversation,
                })
                continue

            # New conversation
            if msg_type == "new_conversation":
                identity = msg.get("identity", current_identity)
                session_type = msg.get("session_type", "chat")
                # Backend-aware clear (was hardcoded to PTY; -p revival made
                # that route the wrong backend).
                provider_router.clear_claude_code_session(identity, current_conversation or "")
                current_conversation, current_session_type = await create_conversation(
                    identity, session_type
                )
                # Pre-warm the fresh conversation's -p session.
                _fire_prewarm(identity, current_conversation)
                await ws.send_json({
                    "type": "new_conversation",
                    "conversation_id": current_conversation,
                    "identity": identity,
                    "session_type": current_session_type,
                })
                continue


            if msg_type == "approval_decision":
                target_identity = msg.get("identity") or current_identity
                target_conv = msg.get("conversation_id") or current_conversation
                decision = msg.get("decision", "allow_once")
                session = claude_pty.get_session_for(target_identity, target_conv)
                if session is None:
                    await ws.send_json({
                        "type": "approval_decision_result",
                        "ok": False,
                        "reason": (
                            "No live PTY session for this conversation. "
                            "If you're on the subprocess backend, this approval "
                            "uses the legacy retry flow instead."
                        ),
                    })
                else:
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, session.send_approval_response, decision,
                        )
                        await ws.send_json({
                            "type": "approval_decision_result",
                            "ok": True,
                            "decision": decision,
                        })
                    except Exception as exc:
                        log.warning(
                            "approval_decision failed for %s/%s: %s",
                            target_identity,
                            (target_conv or "")[:8],
                            exc,
                        )
                        await ws.send_json({
                            "type": "approval_decision_result",
                            "ok": False,
                            "reason": f"keystroke send failed: {exc}",
                        })
                continue

            # Slash command from the browser UI.
            if msg_type == "slash_command":
                command = msg.get("command", "")
                args = msg.get("args", "")
                try:
                    result = await _handle_slash_command(command, args, current_identity)
                    await ws.send_json({"type": "slash_result", "command": command, "result": result})
                except Exception as e:
                    await ws.send_json({"type": "slash_result", "command": command, "result": f"Error: {e}"})
                continue

            # Chat message
            if msg_type == "message":
                try:
                    current_identity, current_conversation, status = await process_chat_message(
                        ws=ws,
                        msg=msg,
                        current_identity=current_identity,
                        current_conversation=current_conversation,
                        current_session_type=current_session_type,
                        active_categories=active_categories,
                        pending_messages=pending_messages,
                        _register_content_images=_register_content_images,
                        _register_content_documents=_register_content_documents,
                    )

                    if status is _DISCONNECTED:
                        break
                except Exception as e:
                    log.exception("Error processing chat message: %s", e)
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": f"Error processing message: {e}",
                        })
                    except Exception:
                        pass
                continue

            # Load conversation history (paginated — last 50 messages)
            if msg_type == "load_history":
                identity = msg.get("identity", current_identity)
                current_identity = identity
                await set_active_identity(current_identity, ws=ws)
                payload, current_conversation, current_session_type = await load_history_payload(
                    identity,
                    msg.get("conversation_id"),
                    limit=msg.get("limit", 50),
                )
                if current_conversation:
                    set_active_conversation(identity, current_conversation)
                    # Opening a conversation — including a roleplay story like
                    # Bakugou's — pre-warms THAT identity+conversation so his
                    # session is booted (tools/files ready) before her first
                    # message, not cold-spawned on it. Covers masks too.
                    _fire_prewarm(current_identity, current_conversation)
                await ws.send_json(payload)
                continue

            # Load older messages (pagination)
            if msg_type == "load_more":
                offset = msg.get("offset", 50)
                limit = msg.get("limit", 50)
                if current_conversation:
                    await ws.send_json(
                        await load_more_payload(current_conversation, offset, limit)
                    )
                continue

            # Regenerate last assistant message
            if msg_type == "regenerate":
                if not current_conversation:
                    continue
                last_user_text = await prepare_regeneration(current_conversation)
                # Tell the browser UI what user text to re-send.
                await ws.send_json({
                    "type": "regenerate_ready",
                    "user_text": last_user_text,
                })
                continue

            # Pin/unpin conversation
            if msg_type == "pin_conversation":
                conv_id = msg.get("conversation_id")
                pinned = msg.get("pinned", True)
                if conv_id:
                    await ws.send_json(await update_conversation_pin(conv_id, pinned))
                continue

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket error: %s", e)
    finally:
        await unregister(ws)


