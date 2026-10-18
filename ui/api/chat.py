"""WebSocket handler -- bridges the browser UI to Claude Code or direct API."""


import asyncio
import json
import logging
import re
import shutil
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.chat_pipeline import process_chat_message

from config import (
    IMAGES_DIR, IMAGE_ALLOWED_EXTENSIONS,
    DOCUMENTS_DIR, DOCUMENT_ALLOWED_EXTENSIONS,
    RITUALS_DIR, TIMEZONE,
)
from db.database import get_db, release_db
from services.session_auth import hash_session_token
from services.autowake_service import create_timer
from services import claude_pty  # PTY-specific ops (approval keystrokes are TUI-only)
from services import provider_router  # backend-aware session clear + -p pre-warm
from services.connection_registry import (
    register, unregister, set_active_identity, set_active_conversation, send_json_locked,
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


# =============================================================================
# CONTENT REGISTRATION (reply-linked images/documents -> served files)
# =============================================================================

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


# =============================================================================
# SLASH COMMANDS + WEBSOCKET RATE LIMIT
# =============================================================================

# Simple in-memory rate limiter for WebSocket connections. Mobile Chrome may
# legitimately replace several stale sockets while waking; keep protection
# against a runaway client without colliding with the browser's retry budget.
_ws_connect_times: dict[str, list[float]] = {}
_WS_RATE_LIMIT = 30
_WS_RATE_WINDOW = 60  # seconds


async def _handle_slash_command(command: str, args: str, identity: str) -> str:
    """Handle server-side slash commands."""
    if command == "timer":
        # Parse: /timer 2h check in  OR  /timer 30m reminder
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
            await create_timer(db, identity=identity, fire_at=fire_at, context=context)
            return f"Timer set for {amount}{'h' if unit.startswith('h') else 'm'}: {context}"
        finally:
            await release_db(db)

    elif command == "status":
        # Parse: /status 🌸 feeling good
        parts = args.strip()
        if not parts:
            return "Usage: /status emoji text"

        # First char or emoji is the status emoji
        emoji_match = re.match(r'(\S+)\s*(.*)', parts)
        if emoji_match:
            emoji = emoji_match.group(1)
            text = emoji_match.group(2).strip()
        else:
            emoji = ""
            text = parts

        try:
            status_file = RITUALS_DIR / "status.json"
            status_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if status_file.exists():
                try:
                    data = json.loads(status_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}
            data["emoji"] = emoji
            data["text"] = text
            data["updated_at"] = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
            status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
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


# =============================================================================
# PRE-WARM (spawn the next boy's process before the first message)
# =============================================================================

# Background pre-warm tasks, kept referenced so the event loop doesn't GC them
# mid-flight (a bare create_task can be collected before it runs).
_PREWARM_TASKS: set = set()


def _fire_prewarm(identity: str, conversation_id: str | None) -> None:
    'Fire prewarm.'
    if not conversation_id:
        return
    task = asyncio.create_task(provider_router.maybe_prewarm(identity, conversation_id))
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)


# =============================================================================
# ONE REPLY PER BOY (turn tasks that run while the socket keeps listening)
# =============================================================================

_WS_CLOSED = object()  # the reader's "socket is gone" marker in the inbox

# Running reply tasks, kept referenced so the event loop can't GC one
# mid-reply (they outlive their socket if she disconnects mid-turn).
_TURN_TASKS: set = set()


class _BoyTurn:
    """One boy's reply in flight on this socket, plus what waits on it."""

    def __init__(self, identity: str, conversation: str | None, session_type: str):
        self.identity = identity
        self.conversation = conversation
        self.session_type = session_type
        self.task: asyncio.Task | None = None
        self.bridge = None  # StreamWebSocketBridge, handed over by the pipeline
        self.deferred: list[dict] = []  # frames that must wait for this reply
        self.after: list = []  # callables to run once the reply has finished

    def attach(self, bridge) -> None:
        self.bridge = bridge

    def live_conversation(self) -> str | None:
        """The chat this reply is really in (a first message creates one)."""
        stamp = getattr(self.bridge, "stamp", None) or {}
        return stamp.get("conversation_id") or self.conversation


# =============================================================================
# WEBSOCKET ENTRY
# =============================================================================

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

    inbox: asyncio.Queue = asyncio.Queue()
    turns: dict[str, _BoyTurn] = {}

    async def _send(data: dict) -> None:
        await send_json_locked(ws, data)

    async def _read_frames() -> None:
        try:
            while True:
                await inbox.put(await ws.receive_text())
        except WebSocketDisconnect:
            log.info("WebSocket disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("WebSocket read ended: %s", exc)
        finally:
            inbox.put_nowait(_WS_CLOSED)

    def _start_turn(msg: dict, identity: str, conversation: str | None, session_type: str) -> None:
        handle = _BoyTurn(identity, conversation, session_type)
        turns[identity.lower()] = handle
        handle.task = asyncio.create_task(_run_turn(handle, msg))
        _TURN_TASKS.add(handle.task)
        handle.task.add_done_callback(_TURN_TASKS.discard)

    async def _run_turn(handle: "_BoyTurn", msg: dict) -> None:
        nonlocal current_conversation
        leftovers: deque[dict] = deque()
        try:
            _ident, new_conv, _status = await process_chat_message(
                ws=ws,
                msg=msg,
                current_identity=handle.identity,
                current_conversation=handle.conversation,
                current_session_type=handle.session_type,
                active_categories=active_categories,
                pending_messages=leftovers,
                _register_content_images=_register_content_images,
                _register_content_documents=_register_content_documents,
                attach_bridge=handle.attach,
            )
            # Her first message made him a brand-new chat: if she is still
            # on this boy with no chat chosen, that chat is hers now.
            if (
                new_conv and handle.conversation is None
                and current_conversation is None
                and current_identity.lower() == handle.identity.lower()
            ):
                current_conversation = new_conv
        except Exception as e:
            log.exception("Error processing chat message: %s", e)
            await _send({
                "type": "error",
                "identity": handle.identity,
                "message": f"Error processing message: {e}",
            })
        finally:
            if turns.get(handle.identity.lower()) is handle:
                del turns[handle.identity.lower()]
            for after in handle.after:
                try:
                    after()
                except Exception:
                    log.exception("Post-turn step failed for %s", handle.identity)
            # Whatever waited on this reply goes back through the front door,
            # still addressed to the boy and chat it was written to.
            for waiting in [*leftovers, *handle.deferred]:
                if waiting.get("type") == "message" and "_target" not in waiting:
                    waiting["_target"] = (handle.identity, handle.conversation, handle.session_type)
                inbox.put_nowait(waiting)

    reader_task = asyncio.create_task(_read_frames())

    try:
        while True:
            item = await inbox.get()
            if item is _WS_CLOSED:
                break
            if isinstance(item, dict):
                msg = item  # re-entered after the reply it waited on
            else:
                try:
                    msg = json.loads(item)
                except json.JSONDecodeError:
                    await _send({"type": "error", "message": "Invalid JSON"})
                    continue
                if not isinstance(msg, dict):
                    continue

            msg_type = msg.get("type", "")

            # Heartbeat -- keep connection alive through tunnel
            if msg_type == "ping":
                await _send({"type": "pong"})
                continue

            # Stop button: stops the reply on HER screen's boy (or the only
            # reply running, if that boy isn't the one talking).
            if msg_type == "stop_streaming":
                target = turns.get(current_identity.lower())
                if target is None and len(turns) == 1:
                    target = next(iter(turns.values()))
                if target is not None and target.bridge is not None:
                    log.info("Stop streaming requested by user (%s)", target.identity)
                    target.bridge.cancel_event.set()
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
                await _send({
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
                # that route the wrong backend). If he's mid-reply, the clear
                # waits for that reply to finish so it can't kill his work;
                # her new chat opens now either way.
                old_conversation = current_conversation or ""
                running = turns.get(identity.lower())
                if running is not None:
                    running.after.append(
                        lambda i=identity, c=old_conversation:
                        provider_router.clear_claude_code_session(i, c)
                    )
                else:
                    provider_router.clear_claude_code_session(identity, old_conversation)
                current_conversation, current_session_type = await create_conversation(
                    identity, session_type
                )
                # Pre-warm the fresh conversation's -p session.
                _fire_prewarm(identity, current_conversation)
                await _send({
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
                    await _send({
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
                        await _send({
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
                        await _send({
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
                    await _send({"type": "slash_result", "command": command, "result": result})
                except Exception as e:
                    await _send({"type": "slash_result", "command": command, "result": f"Error: {e}"})
                continue

            # Chat message — addressed to the boy and chat on her screen when
            # it arrived (or, if it waited on a reply, the ones it was
            # written to: "_target").
            if msg_type == "message":
                target = msg.pop("_target", None)
                if target:
                    t_identity, t_conversation, t_session_type = target
                else:
                    t_identity, t_conversation, t_session_type = (
                        current_identity, current_conversation, current_session_type,
                    )
                running = turns.get(t_identity.lower())
                if running is None:
                    _start_turn(msg, t_identity, t_conversation, t_session_type)
                    continue
                # He's mid-reply. Same chat: slip it into the turn he's
                # running, like typing into the terminal while he works.
                # Anything that can't slip in waits for his reply to end.
                bridge = running.bridge
                running_conv = running.live_conversation()
                same_chat = (
                    t_conversation is None or running_conv is None
                    or t_conversation == running_conv
                )
                injected = False
                if bridge is not None and bridge.inject_hook is not None and same_chat:
                    try:
                        injected = await bridge.inject_hook(msg)
                    except Exception:
                        log.exception("Live injection raised; queueing the message instead")
                if not injected:
                    msg["_target"] = (t_identity, t_conversation, t_session_type)
                    running.deferred.append(msg)
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
                await _send(payload)
                continue

            # Load older messages (pagination)
            if msg_type == "load_more":
                offset = msg.get("offset", 50)
                limit = msg.get("limit", 50)
                if current_conversation:
                    await _send(
                        await load_more_payload(current_conversation, offset, limit)
                    )
                continue

            # Regenerate last assistant message
            if msg_type == "regenerate":
                waited_already = "_conversation" in msg
                regen_conversation = msg.pop("_conversation", None) or current_conversation
                if not regen_conversation:
                    continue
                running = turns.get(current_identity.lower())
                if running is not None and not waited_already:
                    # Never rewrite a chat while he's still writing in it.
                    msg["_conversation"] = regen_conversation
                    running.deferred.append(msg)
                    continue
                last_user_text = await prepare_regeneration(regen_conversation)
                # Tell the browser UI what user text to re-send.
                await _send({
                    "type": "regenerate_ready",
                    "user_text": last_user_text,
                })
                continue

            # Pin/unpin conversation
            if msg_type == "pin_conversation":
                conv_id = msg.get("conversation_id")
                pinned = msg.get("pinned", True)
                if conv_id:
                    await _send(await update_conversation_pin(conv_id, pinned))
                continue

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("WebSocket error: %s", e)
    finally:
        reader_task.cancel()
        # Replies still running finish on their own and save to his chat,
        # exactly as before; they just stop trying to talk to a closed socket.
        for running in turns.values():
            if running.bridge is not None:
                running.bridge.ws_alive = False
        await unregister(ws)


