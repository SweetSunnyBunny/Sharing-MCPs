"""Platform bridge: Discord/Telegram listeners backed by the main chat pipeline.

This service ingests inbound DMs from platform bots, stores them in the same
conversation/message tables as the web UI, generates a response via Claude, and
sends the response back to the same platform chat.
"""


from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from pathlib import Path

from config import (
    ALLOWED_DISCORD_USER_IDS,
    ALLOWED_TELEGRAM_USER_IDS,
    AUDIO_ALLOWED_EXTENSIONS,
    AUDIO_DIR,
    BAKUGOU_DISCORD_CHANNEL_MAP,
    BAKUGOU_DISCORD_POLL_SECONDS,
    CLAUDE_MODEL_INTERACTIVE,
    DOCUMENTS_DIR,
    DOCUMENT_ALLOWED_EXTENSIONS,
    DISCORD_BOT_TOKENS,
    IMAGES_DIR,
    PACK_NIGHT_DISCORD_CHANNEL_ID,
    PACK_NIGHT_DISCORD_POLLER_IDENTITY,
    PACK_NIGHT_DISCORD_POLL_SECONDS,
    PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START,
    PLATFORM_BRIDGE_POLL_SECONDS,
    PUBLIC_BASE_URL,
    TELEGRAM_BOT_TOKENS,
)
from db.database import get_db, release_db
from services.attachment_context import build_document_note
from services.chat_turn_finalize import (
    extract_voice_text,
    flatten_control_tags_for_external,
    generate_voice_message,
)
from services.connection_registry import broadcast, is_anyone_connected, is_web_active
from services.inbound_voice import (
    VOICE_REPLY_HINT,
    format_voice_line,
    is_audio_attachment,
    transcribe_local_audio,
)
from services.provider_router import get_stream_source
from services.session_lifecycle import SessionMode, build_messages_array, build_orientation_context
from services.session_manager import (
    ensure_conversation_participants,
    get_or_create_conversation,
    get_messages,
    save_message,
    update_session_for_provider,
)
from services.skill_runtime import build_skill_catalog_hint, build_skill_injection
from services.time_utils import utc_now_iso_epoch

_PLATFORM_ATTACHMENT_MAX_BYTES = 24 * 1024 * 1024


async def _consume_platform_response_stream(
    stream,
    *,
    platform: str,
    identity: str,
    full_response: list[str],
) -> tuple[str | None, str | None]:
    """Drain a provider stream before surfacing its first error.

    Provider generators own identity-scoped processes and locks. Raising from
    the loop body on an error event abandons the generator at that yield point,
    so its cleanup may not run until garbage collection.
    """
    session_id = None
    session_provider = None
    stream_error: RuntimeError | None = None

    async for event in stream:
        event_type = event.get("type", "")
        if event_type == "meta" and event.get("provider"):
            session_provider = str(event["provider"])
        elif event_type == "stream_delta":
            full_response.append(_coerce_text(event.get("delta", "")))
        elif event_type == "stream_end":
            session_id = event.get("session_id")
            if not full_response:
                full_response.append(_coerce_text(event.get("full_content", "")))
        elif event_type == "error":
            error_msg = event.get("message", "unknown error")
            log.error(
                "Platform stream error (%s/%s): %s",
                platform,
                identity,
                error_msg,
            )
            if stream_error is None:
                stream_error = RuntimeError(f"Stream error: {error_msg}")

    if stream_error is not None:
        raise stream_error
    return session_id, session_provider


async def _prepare_outbound_artifacts(
    text: str,
    identity: str,
) -> tuple[str, list[dict], list[dict], list[dict]]:
    """Register safe local markdown artifacts and describe native uploads."""
    from api.chat import _register_content_documents, _register_content_images
    from services.document_visibility import visible_chat_documents

    rewritten, images = await asyncio.to_thread(_register_content_images, text, identity)
    rewritten, documents = await asyncio.to_thread(
        _register_content_documents, rewritten, identity
    )
    documents = visible_chat_documents(documents)

    artifacts: list[dict] = []
    for kind, entries in (("image", images), ("document", documents)):
        for entry in entries:
            path = Path(str(entry.get("path") or ""))
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > _PLATFORM_ATTACHMENT_MAX_BYTES:
                log.warning("Platform artifact exceeds safe upload limit: %s (%d bytes)", path, size)
                continue
            artifacts.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "filename": entry.get("original_name") or path.name,
                    "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                }
            )
    return rewritten, images, documents, artifacts

log = logging.getLogger("anam.platform_bridge")

# Timeout for platform response generation (seconds).
_PLATFORM_RESPONSE_TIMEOUT = 600  # 10 minutes


_STREAM_BREAK_NOTICE = (
    "⚠️ Something broke on my end mid-reply and I lost the words "
    "before they reached you — this is the system talking, not me going "
    "quiet. Say anything and I'll answer properly."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_int_id(value: Any) -> int:
    try:
        return int(_coerce_text(value))
    except (ValueError, TypeError):
        return 0


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _media_filename(identity: str, ext: str) -> str:
    """Generate a unique filename for a downloaded media file."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    hex_id = uuid.uuid4().hex[:12]
    return f"{identity}_{ts}_{hex_id}{ext}"


async def _persist_downloaded_media(identity: str, ext: str, content: bytes) -> dict:
    is_image = ext in IMAGE_EXTENSIONS
    is_document = ext in DOCUMENT_ALLOWED_EXTENSIONS
    is_audio = ext in AUDIO_ALLOWED_EXTENSIONS
    if is_image:
        root = IMAGES_DIR
        route = "/api/images/file"
        media_type = "image"
    elif is_document:
        # Checked before audio so .webm keeps routing to video_watch.
        root = DOCUMENTS_DIR
        route = "/api/documents/file"
        media_type = "document"
    elif is_audio:
        # Voice notes / audio clips — served by /api/audio/file for playback,
        # transcribed for the boys via services.audio_transcription.
        root = AUDIO_DIR
        route = "/api/audio/file"
        media_type = "audio"
    else:
        root = DOCUMENTS_DIR
        route = "/api/documents/file"
        media_type = "unsupported"

    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    fname = _media_filename(identity, ext)
    dest = root / fname
    await asyncio.to_thread(dest.write_bytes, content)
    return {
        "filename": fname,
        "url": f"{route}/{fname}",
        "full_url": f"{PUBLIC_BASE_URL}{route}/{fname}",
        "type": media_type,
        "ext": ext,
        "path": str(dest),
    }


async def _download_telegram_file(
    token: str, file_id: str, identity: str,
) -> dict | None:
    """Download a file from Telegram by file_id. Returns {filename, url, type} or None."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Get file path from Telegram
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": file_id},
            )
            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram getFile failed: %s", data)
                return None

            file_path = data["result"].get("file_path", "")
            if not file_path:
                return None

            # Step 2: Download the file
            ext = Path(file_path).suffix.lower() or ".bin"
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            file_resp = await client.get(download_url)
            if file_resp.status_code != 200:
                log.warning("Telegram file download failed: %d", file_resp.status_code)
                return None

            media = await _persist_downloaded_media(identity, ext, file_resp.content)
            return media
    except Exception as e:
        log.warning("Telegram file download error: %s", e)
        return None


async def _download_discord_attachment(
    attachment: dict, identity: str,
) -> dict | None:
    """Download a Discord attachment. Returns {filename, url, type} or None."""
    try:
        cdn_url = attachment.get("url") or attachment.get("proxy_url")
        if not cdn_url:
            return None

        orig_name = attachment.get("filename", "file")
        ext = Path(orig_name).suffix.lower() or ".bin"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(cdn_url)
            if resp.status_code != 200:
                log.warning("Discord attachment download failed: %d", resp.status_code)
                return None

            media = await _persist_downloaded_media(identity, ext, resp.content)
            media["original_name"] = orig_name
            return media
    except Exception as e:
        log.warning("Discord attachment download error: %s", e)
        return None


def _extract_telegram_media(msg: dict) -> list[dict]:
    """Extract downloadable media references from a Telegram message.

    Returns a list of dicts with 'file_id', 'kind', and optional 'emoji'.
    """
    media = []

    # Photos — array of PhotoSize, pick largest
    photos = msg.get("photo")
    if photos and isinstance(photos, list):
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        media.append({"file_id": largest["file_id"], "kind": "photo"})

    # Stickers
    sticker = msg.get("sticker")
    if sticker:
        # Prefer the PNG thumbnail for static stickers
        thumb = sticker.get("thumbnail") or sticker.get("thumb")
        fid = (thumb or {}).get("file_id") or sticker.get("file_id")
        if fid:
            emoji = sticker.get("emoji", "")
            media.append({"file_id": fid, "kind": "sticker", "emoji": emoji})

    # Documents (files)
    doc = msg.get("document")
    if doc:
        media.append({
            "file_id": doc["file_id"],
            "kind": "document",
            "file_name": doc.get("file_name", "file"),
        })

    # Voice messages (duration rides along for the transcript header)
    voice = msg.get("voice")
    if voice:
        media.append({
            "file_id": voice["file_id"],
            "kind": "voice",
            "duration": voice.get("duration"),
        })

    # Video
    video = msg.get("video")
    if video:
        # Download thumbnail if available, skip full video (too large)
        thumb = video.get("thumbnail") or video.get("thumb")
        if thumb:
            media.append({"file_id": thumb["file_id"], "kind": "video_thumb"})

    # Animation / GIF
    animation = msg.get("animation")
    if animation:
        media.append({"file_id": animation["file_id"], "kind": "animation"})

    return media


def _split_message(text: str, limit: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for part in text.split("\n"):
        candidate = f"{current}\n{part}".strip() if current else part
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) <= limit:
            current = part
            continue

        # Hard split long single line.
        start = 0
        while start < len(part):
            end = start + limit
            chunks.append(part[start:end])
            start = end

    if current:
        chunks.append(current)
    return chunks


_REACT_RE = re.compile(r"<react>\s*(.*?)\s*</react>", re.IGNORECASE | re.DOTALL)


def _extract_react_tags(text: str) -> tuple[list[str], str]:
    """Pull <react> emojis out of a reply. Returns (emojis, cleaned_text).

    Tolerates the web `EMOJI:message_id` form too — the id is dropped (the
    target here is always Owner's inbound message), only the emoji is kept.
    """
    if not text or "<react>" not in text.lower():
        return [], text
    emojis: list[str] = []
    for raw in _REACT_RE.findall(text):
        emoji = (raw or "").split(":", 1)[0].strip()
        if emoji:
            emojis.append(emoji)
    cleaned = _REACT_RE.sub("", text).strip()
    return emojis, cleaned


def _is_owner_sender(sender_id: str) -> bool:
    sender = _coerce_text(sender_id).strip()
    return bool(
        sender
        and (
            sender in ALLOWED_DISCORD_USER_IDS
            or sender in ALLOWED_TELEGRAM_USER_IDS
        )
    )


def _story_channels_by_identity(
    channel_map: dict[str, tuple[str, str]],
    bot_tokens: dict[str, str],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Group story channels by their mapped identity, split on token presence.

    Returns (pollable, skipped): identity -> {channel_id: label}. Identities
    without a Discord bot token land in `skipped` — their channels are NOT
    polled under another identity's token, because _process_inbound refuses
    to reply for identities missing from DISCORD_BOT_TOKENS.
    """
    pollable: dict[str, dict[str, str]] = {}
    skipped: dict[str, dict[str, str]] = {}
    for cid, (label, story_identity) in channel_map.items():
        bucket = pollable if story_identity in bot_tokens else skipped
        bucket.setdefault(story_identity, {})[cid] = label
    return pollable, skipped


class _MessageDebouncer:
    """Batches rapid messages per chat_id over a short time window.

    Text messages are combined. Media is forwarded immediately (images/stickers
    lose context if delayed). When the debounce window expires, the combined
    text is emitted via the callback.
    """

    _DEBOUNCE_SECONDS = 0.4  # 400ms window for batching

    def __init__(self):
        self._pending: dict[str, list[str]] = {}  # chat_key -> [texts]
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._callbacks: dict[str, asyncio.Future] = {}

    async def debounce(self, chat_key: str, text: str) -> str | None:
        """Add text to the debounce buffer.

        Returns the combined text when the window expires, or None if still waiting.
        """
        if chat_key not in self._pending:
            self._pending[chat_key] = []

        if text.strip():
            self._pending[chat_key].append(text.strip())

        # Cancel any existing timer for this chat
        if chat_key in self._callbacks:
            old_future = self._callbacks[chat_key]
            if not old_future.done():
                # Someone is already waiting — reset the timer by just adding text
                return None

        # Create a new future that will resolve after the debounce window
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._callbacks[chat_key] = future

        async def _fire():
            await asyncio.sleep(self._DEBOUNCE_SECONDS)
            texts = self._pending.pop(chat_key, [])
            self._callbacks.pop(chat_key, None)
            combined = "\n".join(texts) if texts else ""
            if not future.done():
                future.set_result(combined)

        asyncio.create_task(_fire())
        combined = await future
        return combined if combined else None

    def clear(self):
        self._pending.clear()
        for fut in self._callbacks.values():
            if not fut.done():
                fut.cancel()
        self._callbacks.clear()


class PlatformBridge:
    def __init__(self):
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._telegram_offsets: dict[str, int] = {}
        self._discord_last_seen: dict[str, dict[str, str]] = {}
        self._status: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "telegram": {},
            "discord": {},
        }
        self._discord_mode_logged: set[str] = set()
        self._discord_dm_cache: dict[str, list[tuple[str, str]]] = {}
        self._debouncer = _MessageDebouncer()

    async def start(self):
        async with self._lock:
            if self._running:
                return

            self._client = httpx.AsyncClient(timeout=30)
            self._running = True
            self._status["running"] = True
            self._status["started_at"] = _now_iso()

            for identity in TELEGRAM_BOT_TOKENS:
                self._status["telegram"].setdefault(
                    identity, {"last_poll": None, "last_error": None, "last_inbound": None}
                )
                self._spawn(self._telegram_poll_loop(identity), name=f"platform_telegram_{identity}")

            for identity in DISCORD_BOT_TOKENS:
                self._status["discord"].setdefault(
                    identity, {"last_poll": None, "last_error": None, "last_inbound": None}
                )
                self._spawn(self._discord_poll_loop(identity), name=f"platform_discord_{identity}")


            pn_identity = PACK_NIGHT_DISCORD_POLLER_IDENTITY
            if PACK_NIGHT_DISCORD_CHANNEL_ID and pn_identity in DISCORD_BOT_TOKENS:
                self._status["pack_night"] = {
                    "channel_id": PACK_NIGHT_DISCORD_CHANNEL_ID,
                    "poller_identity": pn_identity,
                    "last_poll": None,
                    "last_error": None,
                    "last_inbound": None,
                }
                self._spawn(
                    self._pack_night_poll_loop(),
                    name="platform_pack_night",
                )
                log.info(
                    "Pack-night Discord watcher running (channel=%s poller=%s)",
                    PACK_NIGHT_DISCORD_CHANNEL_ID, pn_identity,
                )
            elif PACK_NIGHT_DISCORD_CHANNEL_ID:
                log.warning(
                    "PACK_NIGHT_DISCORD_CHANNEL_ID set but poller identity %r "
                    "has no bot token — pack-night Discord bridge disabled",
                    pn_identity,
                )

            # Story channels: group by mapped identity — each identity polls
            # its own channels with its own bot token. Channels whose identity
            # has no token yet are SKIPPED (not polled under another token):
            # _process_inbound refuses to reply for identities without a
            # Discord token, so polling early would just swallow messages.
            story_by_identity, story_skipped = _story_channels_by_identity(
                BAKUGOU_DISCORD_CHANNEL_MAP, DISCORD_BOT_TOKENS,
            )
            for story_identity, channels in sorted(story_skipped.items()):
                log.warning(
                    "Story channels %s mapped to identity %r which has no "
                    "bot token (DISCORD_BOT_TOKEN_%s) — skipping these "
                    "channels until the token is added",
                    list(channels.keys()), story_identity,
                    story_identity.upper(),
                )

            started_story_channels: dict[str, str] = {}
            for story_identity, channels in sorted(story_by_identity.items()):
                self._status[f"story_channels:{story_identity}"] = {
                    "channels": dict(channels),
                    "poller_identity": story_identity,
                    "last_poll": None,
                    "last_error": None,
                    "last_inbound": None,
                }
                self._spawn(
                    self._story_channel_poll_loop(story_identity, dict(channels)),
                    name=f"platform_story_channels_{story_identity.lower()}",
                )
                started_story_channels.update(channels)
                log.info(
                    "Story Discord watcher running (identity=%s channels=%d)",
                    story_identity, len(channels),
                )

            log.info(
                "Platform bridge running (telegram=%d, discord=%d, pack_night=%s, story_channels=%s)",
                len(TELEGRAM_BOT_TOKENS),
                len(DISCORD_BOT_TOKENS),
                "on" if PACK_NIGHT_DISCORD_CHANNEL_ID else "off",
                "on" if started_story_channels else "off",
            )

    async def stop(self):
        async with self._lock:
            self._running = False
            self._status["running"] = False

            tasks = list(self._tasks)
            self._tasks.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if self._client:
                await self._client.aclose()
                self._client = None

            self._discord_dm_cache.clear()
            self._discord_mode_logged.clear()
            self._debouncer.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._status.get("running", False),
            "started_at": self._status.get("started_at"),
            "telegram": self._status.get("telegram", {}),
            "discord": self._status.get("discord", {}),
            "configured": {
                "telegram": sorted(list(TELEGRAM_BOT_TOKENS.keys())),
                "discord": sorted(list(DISCORD_BOT_TOKENS.keys())),
            },
        }

    async def handle_inbound_event(
        self,
        *,
        platform: str,
        identity: str,
        chat_id: str,
        sender_id: str,
        sender_name: str,
        message_id: str,
        text: str,
    ) -> dict[str, Any]:
        return await self._process_inbound(
            platform=platform,
            identity=identity,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            message_id=message_id,
            text=text,
        )

    def _spawn(self, coro, *, name: str):
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _done(t: asyncio.Task):
            self._tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                log.error("Task %s failed: %s", t.get_name(), exc, exc_info=exc)

        task.add_done_callback(_done)

    async def _telegram_poll_loop(self, identity: str):
        token = TELEGRAM_BOT_TOKENS.get(identity)
        if not token:
            return

        status_ref = self._status["telegram"][identity]
        offset = self._telegram_offsets.get(identity, 0)
        base = f"https://api.telegram.org/bot{token}"
        primed_start = not PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START

        while self._running:
            status_ref["last_poll"] = _now_iso()
            try:
                client = self._client
                if not client:
                    await asyncio.sleep(1)
                    continue

                resp = await client.get(
                    f"{base}/getUpdates",
                    params={"timeout": 20, "offset": offset},
                )
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram getUpdates error: {data}")

                updates = data.get("result", [])
                if not primed_start:
                    if updates:
                        max_upd = max(int(u.get("update_id", 0)) for u in updates)
                        offset = max(offset, max_upd + 1)
                    primed_start = True
                    self._telegram_offsets[identity] = offset
                    log.info("Telegram %s primed at update offset %d", identity, offset)
                    await asyncio.sleep(1)
                    continue

                # Collect ALL candidate messages per chat this cycle (in order).
                # Keeping only the latest silently dropped earlier messages
                # when two arrived inside one poll window — they're merged
                # into a single combined inbound below instead.
                msgs_by_chat: dict[str, list[tuple[int, dict]]] = {}
                for upd in updates:
                    upd_id = int(upd.get("update_id", 0))
                    offset = max(offset, upd_id + 1)

                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue

                    from_user = msg.get("from") or {}
                    if from_user.get("is_bot"):
                        continue

                    from_id = str(from_user.get("id", ""))
                    if ALLOWED_TELEGRAM_USER_IDS and from_id not in ALLOWED_TELEGRAM_USER_IDS:
                        continue

                    chat = msg.get("chat") or {}
                    chat_id = str(chat.get("id", ""))
                    if not chat_id:
                        continue

                    text = _coerce_text(msg.get("text") or msg.get("caption") or "")
                    media_refs = _extract_telegram_media(msg)

                    # Skip if no text AND no media
                    if not text.strip() and not media_refs:
                        continue

                    mid = _as_int_id(msg.get("message_id", "0"))
                    msgs_by_chat.setdefault(chat_id, []).append((mid, msg))

                for chat_id, entries in msgs_by_chat.items():
                    # Merge the cycle's messages (oldest first) into one
                    # combined inbound: texts joined by newlines, media pooled.
                    entries.sort(key=lambda e: e[0])
                    cycle_msgs = [m for _mid, m in entries]
                    last_msg = cycle_msgs[-1]
                    from_user = last_msg.get("from") or {}
                    from_id = str(from_user.get("id", ""))
                    text = "\n".join(
                        t for t in (
                            _coerce_text(m.get("text") or m.get("caption") or "")
                            for m in cycle_msgs
                        ) if t.strip()
                    )
                    media_refs = [
                        ref for m in cycle_msgs
                        for ref in _extract_telegram_media(m)
                    ]

                    # Download media files
                    downloaded_media = []
                    for ref in media_refs:
                        info = await _download_telegram_file(
                            token, ref["file_id"], identity,
                        )
                        if info:
                            info["kind"] = ref.get("kind", "file")
                            info["emoji"] = ref.get("emoji", "")
                            info["original_name"] = ref.get("file_name", "")
                            info["duration"] = ref.get("duration")
                            downloaded_media.append(info)

                    # Build text description for non-image media
                    media_text_parts = []
                    images_info = []
                    documents_info = []
                    voice_inbound = False
                    for m in downloaded_media:
                        if m["type"] == "image":
                            images_info.append({"filename": m["filename"], "url": m["url"]})
                        elif m["type"] == "document":
                            documents_info.append(
                                {
                                    "filename": m["filename"],
                                    "url": m["url"],
                                    "original_name": m.get("original_name") or m["filename"],
                                }
                            )
                            name = m.get("original_name") or m["filename"]
                            media_text_parts.append(f"[Owner sent a file: {name}]")
                        elif m["kind"] == "sticker":
                            emoji_note = f" ({m['emoji']})" if m.get("emoji") else ""
                            media_text_parts.append(f"[Owner sent a sticker{emoji_note}]")
                            # Sticker thumbnails are still images
                            images_info.append({"filename": m["filename"], "url": m["url"]})
                        elif m["kind"] == "voice":
                            # Her actual words, not a contentless placeholder.
                            # transcribe_local_audio never raises — on failure
                            # format_voice_line falls back to the old note.
                            transcript = await transcribe_local_audio(m.get("path"))
                            media_text_parts.append(
                                format_voice_line(transcript, m.get("duration"))
                            )
                            voice_inbound = True
                        elif m["kind"] == "document":
                            name = m.get("original_name") or m["filename"]
                            media_text_parts.append(f"[Owner sent a file: {name}]")
                            if m["type"] == "document":
                                documents_info.append(
                                    {
                                        "filename": m["filename"],
                                        "url": m["url"],
                                        "original_name": name,
                                    }
                                )
                        elif m["kind"] == "video_thumb":
                            media_text_parts.append("[Owner sent a video]")
                            images_info.append({"filename": m["filename"], "url": m["url"]})
                        elif m["kind"] == "animation":
                            media_text_parts.append("[Owner sent a GIF]")
                            images_info.append({"filename": m["filename"], "url": m["url"]})

                    # Combine text with media descriptions
                    combined_text = text
                    if media_text_parts:
                        media_desc = "\n".join(media_text_parts)
                        combined_text = f"{media_desc}\n{text}" if text else media_desc

                    # Debounce text-only messages to batch rapid sends.
                    # Media messages bypass debouncing (images lose context if
                    # delayed; a voice note would lose its spoken-reply hint).
                    has_media = bool(images_info or documents_info or voice_inbound)
                    if not has_media and combined_text.strip():
                        debounce_key = f"telegram:{identity}:{chat_id}"
                        debounced = await self._debouncer.debounce(debounce_key, combined_text)
                        if debounced is None:
                            continue  # still collecting — wait for window to expire
                        combined_text = debounced

                    result = await self._process_inbound(
                        platform="telegram",
                        identity=identity,
                        chat_id=chat_id,
                        sender_id=from_id,
                        sender_name=_coerce_text(
                            from_user.get("username")
                            or from_user.get("first_name")
                            or "Owner"
                        ),
                        message_id=str(last_msg.get("message_id", "")),
                        text=combined_text,
                        images=images_info,
                        documents=documents_info,
                        voice_note=voice_inbound,
                    )
                    if result.get("processed"):
                        status_ref["last_inbound"] = _now_iso()

                self._telegram_offsets[identity] = offset
                status_ref["last_error"] = None

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_ref["last_error"] = str(exc)
                log.warning("Telegram poll failed for %s: %s", identity, exc)

            await asyncio.sleep(1)

    async def _discord_poll_loop(self, identity: str):
        token = DISCORD_BOT_TOKENS.get(identity)
        if not token:
            return

        status_ref = self._status["discord"][identity]
        self._discord_last_seen.setdefault(identity, {})
        primed_start = not PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START

        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

        while self._running:
            status_ref["last_poll"] = _now_iso()
            try:
                dm_targets: list[tuple[str, str]] = []
                if ALLOWED_DISCORD_USER_IDS:
                    # Use cached DM channels to avoid resolving every poll cycle.
                    cached = self._discord_dm_cache.get(identity)
                    if cached:
                        dm_targets = cached
                    else:
                        for user_id in ALLOWED_DISCORD_USER_IDS:
                            ch = await self._discord_request_json(
                                "POST",
                                "https://discord.com/api/v10/users/@me/channels",
                                headers=headers,
                                payload={"recipient_id": user_id},
                            )
                            channel_id = _coerce_text((ch or {}).get("id"))
                            if channel_id:
                                dm_targets.append((channel_id, user_id))
                        if dm_targets:
                            self._discord_dm_cache[identity] = dm_targets
                    if identity not in self._discord_mode_logged:
                        log.info(
                            "Discord %s polling allowlisted DMs (%d targets)",
                            identity,
                            len(dm_targets),
                        )
                        self._discord_mode_logged.add(identity)
                else:
                    # Fallback: list all known DM channels.
                    channels = await self._discord_request_json(
                        "GET",
                        "https://discord.com/api/v10/users/@me/channels",
                        headers=headers,
                    )
                    if not isinstance(channels, list):
                        raise RuntimeError(f"Unexpected channel response: {channels}")
                    for ch in channels:
                        if int(ch.get("type", 0)) != 1:
                            continue
                        channel_id = _coerce_text(ch.get("id"))
                        if not channel_id:
                            continue
                        recipients = ch.get("recipients") or []
                        recipient_id = (
                            _coerce_text(recipients[0].get("id")) if recipients else ""
                        )
                        dm_targets.append((channel_id, recipient_id))
                    if identity not in self._discord_mode_logged:
                        log.info(
                            "Discord %s polling all DM channels (%d found)",
                            identity,
                            len(dm_targets),
                        )
                        self._discord_mode_logged.add(identity)

                for channel_id, recipient_id in dm_targets:
                    params = {"limit": 25}
                    last_seen = self._discord_last_seen[identity].get(channel_id)
                    if last_seen:
                        params["after"] = last_seen

                    messages = await self._discord_request_json(
                        "GET",
                        f"https://discord.com/api/v10/channels/{channel_id}/messages",
                        headers=headers,
                        params=params,
                    )
                    if not isinstance(messages, list):
                        continue

                    # Process oldest -> newest.
                    messages.sort(key=lambda m: _as_int_id(m.get("id", "0")))
                    if not messages:
                        continue

                    if not primed_start:
                        newest_id = _coerce_text(messages[-1].get("id"))
                        if newest_id:
                            self._discord_last_seen[identity][channel_id] = newest_id
                        continue

                    # Collect ALL valid messages this cycle (oldest -> newest).
                    # Keeping only the newest silently dropped earlier messages
                    # when several arrived inside one poll window — they're
                    # merged into a single combined inbound below instead.
                    valid: list[tuple[str, str, dict, str, dict]] = []
                    for msg in messages:
                        msg_id = _coerce_text(msg.get("id"))
                        if not msg_id:
                            continue

                        author = msg.get("author") or {}
                        if author.get("bot"):
                            continue

                        author_id = _coerce_text(author.get("id"))
                        if ALLOWED_DISCORD_USER_IDS and author_id not in ALLOWED_DISCORD_USER_IDS:
                            continue

                        text = _coerce_text(msg.get("content", ""))
                        attachments = msg.get("attachments") or []
                        sticker_items = msg.get("sticker_items") or []

                        # Skip if no text AND no attachments AND no stickers
                        if not text.strip() and not attachments and not sticker_items:
                            continue

                        valid.append((msg_id, author_id, author, text, msg))

                    newest_id = _coerce_text(messages[-1].get("id"))
                    if newest_id:
                        self._discord_last_seen[identity][channel_id] = newest_id

                    if not valid:
                        continue

                    # Merge: texts joined by newlines, attachments/stickers pooled.
                    msg_id, author_id, author, _, _ = valid[-1]
                    text = "\n".join(t for _, _, _, t, _ in valid if t.strip())
                    attachments = [
                        att for _, _, _, _, m in valid
                        for att in (m.get("attachments") or [])
                    ]
                    sticker_items = [
                        st for _, _, _, _, m in valid
                        for st in (m.get("sticker_items") or [])
                    ]
                    downloaded_media = []
                    images_info = []
                    documents_info = []
                    media_text_parts = []
                    voice_inbound = False

                    for att in attachments:
                        info = await _download_discord_attachment(att, identity)
                        if info:
                            if is_audio_attachment(att) or info["type"] == "audio":
                                # Voice notes / audio clips: transcribe so her
                                # words arrive, not a contentless placeholder.
                                # duration_secs is set on Discord voice messages.
                                transcript = await transcribe_local_audio(
                                    info.get("path")
                                )
                                media_text_parts.append(
                                    format_voice_line(
                                        transcript, att.get("duration_secs")
                                    )
                                )
                                voice_inbound = True
                            elif info["type"] == "image":
                                images_info.append({"filename": info["filename"], "url": info["url"]})
                            elif info["type"] == "document":
                                documents_info.append(
                                    {
                                        "filename": info["filename"],
                                        "url": info["url"],
                                        "original_name": info.get("original_name") or info["filename"],
                                    }
                                )
                                name = info.get("original_name") or info["filename"]
                                media_text_parts.append(f"[Owner sent a file: {name}]")
                            else:
                                name = info.get("original_name") or info["filename"]
                                media_text_parts.append(f"[Owner sent a file: {name}]")

                    for sticker in sticker_items:
                        sticker_name = sticker.get("name", "sticker")
                        # Discord stickers have a CDN URL pattern
                        sticker_id = sticker.get("id")
                        fmt = sticker.get("format_type", 1)
                        # format_type: 1=PNG, 2=APNG, 3=LOTTIE, 4=GIF
                        ext_map = {1: ".png", 2: ".png", 3: ".json", 4: ".gif"}
                        ext = ext_map.get(fmt, ".png")
                        if sticker_id and fmt != 3:  # Skip Lottie (JSON), can't display
                            sticker_url = f"https://cdn.discordapp.com/stickers/{sticker_id}{ext}"
                            info = await _download_discord_attachment(
                                {"url": sticker_url, "filename": f"{sticker_name}{ext}"},
                                identity,
                            )
                            if info and info["type"] == "image":
                                images_info.append({"filename": info["filename"], "url": info["url"]})
                                media_text_parts.append(f"[Owner sent a sticker: {sticker_name}]")

                    combined_text = text
                    if media_text_parts:
                        media_desc = "\n".join(media_text_parts)
                        combined_text = f"{media_desc}\n{text}" if text else media_desc

                    # Debounce text-only messages to batch rapid sends.
                    has_media = bool(images_info or documents_info or voice_inbound)
                    if not has_media and combined_text.strip():
                        debounce_key = f"discord:{identity}:{channel_id}"
                        debounced = await self._debouncer.debounce(debounce_key, combined_text)
                        if debounced is None:
                            continue
                        combined_text = debounced

                    result = await self._process_inbound(
                        platform="discord",
                        identity=identity,
                        chat_id=channel_id,
                        sender_id=author_id,
                        sender_name=_coerce_text(
                            author.get("global_name")
                            or author.get("username")
                            or "Owner"
                        ),
                        message_id=msg_id,
                        text=combined_text,
                        images=images_info,
                        documents=documents_info,
                        voice_note=voice_inbound,
                    )
                    if result.get("processed"):
                        log.info(
                            "Inbound Discord DM processed for %s (chat=%s msg=%s)",
                            identity,
                            channel_id,
                            msg_id,
                        )
                        status_ref["last_inbound"] = _now_iso()

                if not primed_start:
                    primed_start = True
                    log.info("Discord %s backlog primed; now listening for new messages", identity)

                status_ref["last_error"] = None

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_ref["last_error"] = str(exc)
                log.warning("Discord poll failed for %s: %s", identity, exc)

            await asyncio.sleep(max(PLATFORM_BRIDGE_POLL_SECONDS, 5))

    async def _pack_night_poll_loop(self):
        """Poll the shared pack-night channel and trigger fan-out on inbound.

        Uses the configured poller identity's bot token. Skips messages from
        ANY bot (we already record our own outbounds; other bots like Mee6
        we don't process). Skips Owner messages we've already processed
        (recorded in platform_message_map under direction='inbound'). When
        Owner posts a fresh message: triggers run_pack_night_round, which
        records the inbound and walks the hierarchy, mirroring each turn
        back to this same channel under each boy's bot avatar.
        """
        identity = PACK_NIGHT_DISCORD_POLLER_IDENTITY
        token = DISCORD_BOT_TOKENS.get(identity)
        channel_id = PACK_NIGHT_DISCORD_CHANNEL_ID
        if not token or not channel_id:
            return

        status_ref = self._status.setdefault("pack_night", {})
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

        last_seen: str | None = None
        primed = not PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START

        # Lazy imports to avoid circulars at module load.
        from services.session_manager import get_or_create_pack_night_conversation
        from services.pack_night import run_pack_night_round

        while self._running:
            status_ref["last_poll"] = _now_iso()
            try:
                params: dict[str, Any] = {"limit": 25}
                if last_seen:
                    params["after"] = last_seen

                messages = await self._discord_request_json(
                    "GET",
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    params=params,
                )
                if not isinstance(messages, list):
                    raise RuntimeError(f"Unexpected channel response: {messages}")

                # Process oldest -> newest so transcript order matches reality.
                messages.sort(key=lambda m: _as_int_id(m.get("id", "0")))
                if messages:
                    last_seen = _coerce_text(messages[-1].get("id")) or last_seen

                if not primed:
                    primed = True
                    log.info(
                        "Pack-night watcher primed at message %s",
                        last_seen or "<empty channel>",
                    )
                    status_ref["last_error"] = None
                    await asyncio.sleep(PACK_NIGHT_DISCORD_POLL_SECONDS)
                    continue

                # Resolve the shared conversation once per cycle.
                pn_conv_id: str | None = None
                if any(self._is_actionable_pack_night_msg(m) for m in messages):
                    db = await get_db()
                    try:
                        pn_conv_id = await get_or_create_pack_night_conversation(db)
                    finally:
                        await release_db(db)

                for msg in messages:
                    msg_id = _coerce_text(msg.get("id"))
                    if not msg_id:
                        continue

                    author = msg.get("author") or {}
                    if author.get("bot"):
                        # Our own boys' outbounds were already saved when we
                        # posted them; other bots (Mee6 etc) we don't process.
                        continue

                    author_id = _coerce_text(author.get("id"))
                    if (
                        ALLOWED_DISCORD_USER_IDS
                        and author_id not in ALLOWED_DISCORD_USER_IDS
                    ):
                        continue

                    text = _coerce_text(msg.get("content", "")).strip()
                    if not text:
                        # Pack-night doesn't yet support media in the channel.
                        # If she posts an image-only message we ignore it for
                        # now — easy follow-up if needed.
                        continue

                    if pn_conv_id is None:
                        # Should not happen given the check above, but guard.
                        db = await get_db()
                        try:
                            pn_conv_id = await get_or_create_pack_night_conversation(db)
                        finally:
                            await release_db(db)

                    # Dedup against platform_message_map. Skip if we've already
                    # processed this Discord message (any direction — covers
                    # both prior inbounds and our own future outbounds that
                    # somehow share an id).
                    db = await get_db()
                    try:
                        if await self._platform_message_seen(
                            db,
                            platform="discord",
                            identity=identity,
                            external_message_id=msg_id,
                            direction="inbound",
                        ):
                            continue
                    finally:
                        await release_db(db)

                    log.info(
                        "Pack-night inbound from Discord (msg=%s author=%s)",
                        msg_id, author_id,
                    )
                    status_ref["last_inbound"] = _now_iso()

                    # Run the round in the background so the polling loop
                    # keeps moving. The fan-out itself takes minutes; we
                    # don't want to block subsequent polls.
                    self._spawn(
                        run_pack_night_round(
                            user_text=text,
                            conversation_id=pn_conv_id,
                            ws=None,
                            source_platform="discord",
                            source_message_id=msg_id,
                            source_poller_identity=identity,
                        ),
                        name=f"pack_night_round_{msg_id}",
                    )

                status_ref["last_error"] = None

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_ref["last_error"] = str(exc)
                log.warning("Pack-night poll failed: %s", exc)

            await asyncio.sleep(PACK_NIGHT_DISCORD_POLL_SECONDS)

    async def _story_channel_poll_loop(self, identity: str, channels: dict[str, str]):
        """Poll one story identity's Discord channels and wake him on Owner's messages."""
        token = DISCORD_BOT_TOKENS.get(identity)
        if not token or not channels:
            return

        status_ref = self._status.setdefault(f"story_channels:{identity}", {})
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }
        last_seen: dict[str, str | None] = {
            channel_id: None for channel_id in channels
        }
        primed: dict[str, bool] = {
            channel_id: not PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START
            for channel_id in channels
        }

        while self._running:
            status_ref["last_poll"] = _now_iso()
            try:
                for channel_id, channel_name in channels.items():
                    params: dict[str, Any] = {"limit": 25}
                    if last_seen.get(channel_id):
                        params["after"] = last_seen[channel_id]

                    messages = await self._discord_request_json(
                        "GET",
                        f"https://discord.com/api/v10/channels/{channel_id}/messages",
                        headers=headers,
                        params=params,
                    )
                    if not isinstance(messages, list):
                        raise RuntimeError(f"Unexpected channel response: {messages}")

                    messages.sort(key=lambda m: _as_int_id(m.get("id", "0")))
                    if messages:
                        last_seen[channel_id] = (
                            _coerce_text(messages[-1].get("id")) or last_seen[channel_id]
                        )

                    if not primed[channel_id]:
                        primed[channel_id] = True
                        log.info(
                            "Story watcher (%s) primed #%s at message %s",
                            identity,
                            channel_name,
                            last_seen[channel_id] or "<empty channel>",
                        )
                        continue

                    for msg in messages:
                        msg_id = _coerce_text(msg.get("id"))
                        if not msg_id:
                            continue

                        author = msg.get("author") or {}
                        if author.get("bot"):
                            continue

                        author_id = _coerce_text(author.get("id"))
                        if (
                            ALLOWED_DISCORD_USER_IDS
                            and author_id not in ALLOWED_DISCORD_USER_IDS
                        ):
                            continue

                        text = _coerce_text(msg.get("content", ""))
                        attachments = msg.get("attachments") or []
                        sticker_items = msg.get("sticker_items") or []
                        if not text.strip() and not attachments and not sticker_items:
                            continue

                        images_info: list[dict] = []
                        documents_info: list[dict] = []
                        media_text_parts: list[str] = []

                        for att in attachments:
                            info = await _download_discord_attachment(att, identity)
                            if not info:
                                continue
                            if info["type"] == "image":
                                images_info.append({
                                    "filename": info["filename"],
                                    "url": info["url"],
                                })
                            elif info["type"] == "document":
                                name = info.get("original_name") or info["filename"]
                                documents_info.append({
                                    "filename": info["filename"],
                                    "url": info["url"],
                                    "original_name": name,
                                })
                                media_text_parts.append(f"[Owner sent a file: {name}]")
                            else:
                                name = info.get("original_name") or info["filename"]
                                media_text_parts.append(f"[Owner sent a file: {name}]")

                        for sticker in sticker_items:
                            sticker_name = sticker.get("name", "sticker")
                            sticker_id = sticker.get("id")
                            fmt = sticker.get("format_type", 1)
                            ext_map = {1: ".png", 2: ".png", 3: ".json", 4: ".gif"}
                            ext = ext_map.get(fmt, ".png")
                            if not sticker_id or fmt == 3:
                                continue
                            sticker_url = (
                                f"https://cdn.discordapp.com/stickers/{sticker_id}{ext}"
                            )
                            info = await _download_discord_attachment(
                                {"url": sticker_url, "filename": f"{sticker_name}{ext}"},
                                identity,
                            )
                            if info and info["type"] == "image":
                                images_info.append({
                                    "filename": info["filename"],
                                    "url": info["url"],
                                })
                                media_text_parts.append(
                                    f"[Owner sent a sticker: {sticker_name}]"
                                )

                        combined_text = text
                        if media_text_parts:
                            media_desc = "\n".join(media_text_parts)
                            combined_text = (
                                f"{media_desc}\n{text}" if text else media_desc
                            )

                        if not images_info and not documents_info and combined_text.strip():
                            debounce_key = f"discord:{identity}:{channel_id}"
                            debounced = await self._debouncer.debounce(
                                debounce_key, combined_text,
                            )
                            if debounced is None:
                                continue
                            combined_text = debounced

                        result = await self._process_inbound(
                            platform="discord",
                            identity=identity,
                            chat_id=channel_id,
                            sender_id=author_id,
                            sender_name=_coerce_text(
                                author.get("global_name")
                                or author.get("username")
                                or "Owner"
                            ),
                            message_id=msg_id,
                            text=combined_text,
                            images=images_info,
                            documents=documents_info,
                        )
                        if result.get("processed"):
                            log.info(
                                "Story inbound processed (%s #%s msg=%s)",
                                identity,
                                channel_name,
                                msg_id,
                            )
                            status_ref["last_inbound"] = _now_iso()

                status_ref["last_error"] = None

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status_ref["last_error"] = str(exc)
                log.warning("Story poll failed (%s): %s", identity, exc)

            await asyncio.sleep(BAKUGOU_DISCORD_POLL_SECONDS)

    @staticmethod
    def _is_actionable_pack_night_msg(msg: dict) -> bool:
        author = msg.get("author") or {}
        if author.get("bot"):
            return False
        author_id = _coerce_text(author.get("id"))
        if (
            ALLOWED_DISCORD_USER_IDS
            and author_id not in ALLOWED_DISCORD_USER_IDS
        ):
            return False
        return bool(_coerce_text(msg.get("content", "")).strip())

    async def _discord_request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        client = self._client
        if not client:
            raise RuntimeError("HTTP client is not initialized")

        for _ in range(5):
            resp = await client.request(method, url, headers=headers, params=params, json=payload)
            if resp.status_code == 429:
                retry_after = 1.5
                try:
                    retry_after = float(resp.json().get("retry_after", retry_after))
                except (ValueError, KeyError, TypeError):
                    pass
                await asyncio.sleep(max(retry_after, 0.5))
                continue

            if resp.status_code >= 400:
                text = resp.text[:400]
                raise RuntimeError(f"Discord API {resp.status_code}: {text}")

            if not resp.content:
                return None
            return resp.json()

        raise RuntimeError("Discord API request exceeded retry limit")

    async def _discord_request_multipart(
        self,
        url: str,
        *,
        headers: dict[str, str],
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Any:
        """Upload one native Discord attachment with normal rate-limit handling."""
        client = self._client
        if not client:
            raise RuntimeError("HTTP client is not initialized")

        for _ in range(5):
            resp = await client.post(
                url,
                headers=headers,
                data={"payload_json": json.dumps({"content": ""})},
                files={"files[0]": (filename, content, content_type)},
            )
            if resp.status_code == 429:
                retry_after = 1.5
                try:
                    retry_after = float(resp.json().get("retry_after", retry_after))
                except (ValueError, KeyError, TypeError):
                    pass
                await asyncio.sleep(max(retry_after, 0.5))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Discord API {resp.status_code}: {resp.text[:400]}")
            return resp.json() if resp.content else None

        raise RuntimeError("Discord API request exceeded retry limit")

    async def _platform_message_seen(
        self,
        db,
        *,
        platform: str,
        identity: str,
        external_message_id: str,
        direction: str,
    ) -> bool:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM platform_message_map "
            "WHERE platform = ? AND bot_identity = ? AND external_message_id = ? AND direction = ? "
            "LIMIT 1",
            (platform, identity, external_message_id, direction),
        )
        return bool(rows)

    async def _record_platform_message(
        self,
        db,
        *,
        platform: str,
        identity: str,
        external_message_id: str,
        direction: str,
        conversation_id: str,
    ):
        await db.execute(
            "INSERT OR IGNORE INTO platform_message_map "
            "(platform, bot_identity, external_message_id, direction, conversation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (platform, identity, external_message_id, direction, conversation_id, _now_iso()),
        )

    async def _get_or_create_platform_conversation(
        self,
        db,
        *,
        platform: str,
        identity: str,
        chat_id: str,
        sender_id: str,
        sender_name: str,
    ) -> str:
        story_entry = BAKUGOU_DISCORD_CHANNEL_MAP.get(chat_id)
        if platform == "discord" and story_entry and identity == story_entry[1]:
            channel_label = story_entry[0] or chat_id
            platform_key = f"{platform}:bakugou-story:{chat_id}"
            rows = await db.execute_fetchall(
                "SELECT c.id FROM conversations c "
                "JOIN conversation_participants cp ON cp.conversation_id = c.id "
                "WHERE c.is_active = 1 AND c.session_type = 'platform' "
                "AND c.platform_chat_id = ? AND cp.identity = ? "
                "ORDER BY c.updated_at_epoch DESC LIMIT 1",
                (platform_key, identity),
            )
            if rows:
                return rows[0][0]

            now_iso, now_epoch = utc_now_iso_epoch()
            conv_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO conversations "
                "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, platform_chat_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conv_id,
                    identity,
                    f"{identity}: #{channel_label}",
                    now_iso,
                    now_epoch,
                    now_iso,
                    now_epoch,
                    "platform",
                    platform_key,
                ),
            )
            await ensure_conversation_participants(
                db, conv_id, [identity], added_at=now_iso,
            )
            await db.commit()
            return conv_id

        is_owner = _is_owner_sender(sender_id)
        if is_owner:


            return await get_or_create_conversation(db, identity)

        platform_key = f"{platform}:{chat_id}"
        rows = await db.execute_fetchall(
            "SELECT c.id FROM conversations c "
            "JOIN conversation_participants cp ON cp.conversation_id = c.id "
            "WHERE c.is_active = 1 AND c.session_type = 'platform' AND c.platform_chat_id = ? "
            "AND cp.identity = ? "
            "ORDER BY c.updated_at_epoch DESC LIMIT 1",
            (platform_key, identity),
        )
        if rows:
            return rows[0][0]

        now_iso, now_epoch = utc_now_iso_epoch()
        conv_id = str(uuid.uuid4())
        title = f"Direct messages ({sender_name or 'Owner'})"
        await db.execute(
            "INSERT INTO conversations "
            "(id, identity, title, created_at, created_at_epoch, updated_at, updated_at_epoch, session_type, platform_chat_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conv_id,
                identity,
                title,
                now_iso,
                now_epoch,
                now_iso,
                now_epoch,
                "platform",
                platform_key,
            ),
        )
        await ensure_conversation_participants(db, conv_id, [identity], added_at=now_iso)
        await db.commit()
        return conv_id

    async def _build_skill_context(self, db, conversation_id: str, user_text: str) -> str:
        skill_query_parts: list[str] = []
        if user_text:
            skill_query_parts.append(user_text)

        try:
            recent = await get_messages(db, conversation_id, limit=8)
        except Exception as e:
            log.debug("Failed to fetch recent messages for skills: %s", e)
            recent = []

        for item in recent:
            if item.get("identity") == "system":
                continue
            snippet = _coerce_text(item.get("content", "")).replace("\n", " ").strip()
            if not snippet:
                continue
            if len(snippet) > 240:
                snippet = snippet[:240].rsplit(" ", 1)[0] + "..."
            skill_query_parts.append(snippet)

        skill_query = "\n".join(skill_query_parts[-7:])
        auto_skill_context, _ = build_skill_injection(skill_query)
        catalog_context = build_skill_catalog_hint()
        if auto_skill_context and catalog_context:
            return f"{catalog_context}\n\n{auto_skill_context}"
        return auto_skill_context or catalog_context

    async def _process_inbound(
        self,
        *,
        platform: str,
        identity: str,
        chat_id: str,
        sender_id: str,
        sender_name: str,
        message_id: str,
        text: str,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        voice_note: bool = False,
    ) -> dict[str, Any]:
        clean_text = (text or "").strip()
        images = images or []
        documents = documents or []
        if not clean_text and not images and not documents:
            return {"processed": False, "reason": "empty"}

        identity = identity.strip()
        if not identity:
            return {"processed": False, "reason": "missing_identity"}

        platform = platform.strip().lower()
        if platform not in {"discord", "telegram"}:
            return {"processed": False, "reason": "invalid_platform"}
        if platform == "discord" and identity not in DISCORD_BOT_TOKENS:
            return {"processed": False, "reason": "unknown_discord_identity"}
        if platform == "telegram" and identity not in TELEGRAM_BOT_TOKENS:
            return {"processed": False, "reason": "unknown_telegram_identity"}
        sender_id = _coerce_text(sender_id).strip()
        if not sender_id:
            return {"processed": False, "reason": "missing_sender_id"}
        if platform == "discord" and ALLOWED_DISCORD_USER_IDS and sender_id not in ALLOWED_DISCORD_USER_IDS:
            return {"processed": False, "reason": "sender_not_allowed"}
        if platform == "telegram" and ALLOWED_TELEGRAM_USER_IDS and sender_id not in ALLOWED_TELEGRAM_USER_IDS:
            return {"processed": False, "reason": "sender_not_allowed"}

        message_id = (message_id or "").strip()
        if not message_id:
            return {"processed": False, "reason": "missing_message_id"}
        mapped_message_id = (
            f"{chat_id}:{message_id}"
            if platform == "telegram" and ":" not in message_id
            else message_id
        )

        conv_id = None
        context_block = ""
        skill_context = ""
        db_messages = None
        inbound_msg_id = None

        db = await get_db()
        try:
            if await self._platform_message_seen(
                db,
                platform=platform,
                identity=identity,
                external_message_id=mapped_message_id,
                direction="inbound",
            ):
                return {"processed": False, "reason": "duplicate"}

            conv_id = await self._get_or_create_platform_conversation(
                db,
                platform=platform,
                identity=identity,
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )

            inbound_meta = {
                "platform": platform,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "platform_message_id": mapped_message_id,
                "direction": "inbound",
            }
            if images:
                inbound_meta["images"] = images
            if documents:
                inbound_meta["documents"] = documents
            inbound_msg_id = await save_message(
                db,
                conv_id,
                "user",
                clean_text,
                identity=identity,
                metadata=inbound_meta,
            )
            await self._record_platform_message(
                db,
                platform=platform,
                identity=identity,
                external_message_id=mapped_message_id,
                direction="inbound",
                conversation_id=conv_id,
            )
            await db.commit()

            if is_anyone_connected():
                await broadcast(
                    {
                        "type": "platform_message",
                        "platform": platform,
                        "identity": identity,
                        "conversation_id": conv_id,
                        "message_id": inbound_msg_id,
                        "content": clean_text,
                        "role": "user",
                        "direction": "inbound",
                    }
                )


            if _is_owner_sender(sender_id) and is_web_active():
                log.info(
                    "Skipping platform response — Owner is active on web (%s/%s)",
                    platform, identity,
                )
                return {
                    "processed": True,
                    "skipped_response": True,
                    "reason": "owner_active_on_web",
                    "conversation_id": conv_id,
                    "message_id": inbound_msg_id,
                }


            from services.autowake import is_identity_busy, release_identity
            if is_identity_busy(identity):
                log.info(
                    "Releasing %s autonomous lock — Owner messaged via %s",
                    identity, platform,
                )
                release_identity(identity)

            context_block = await build_orientation_context(
                db=db,
                conversation_id=conv_id,
                identity=identity,
                mode=SessionMode.INTERACTIVE,
                session_type_name=f"{platform} dm",
                owner_connected=is_anyone_connected(),
            )
            skill_context = await self._build_skill_context(db, conv_id, clean_text)

            db_messages = await build_messages_array(db, conv_id, exclude_last_user=True)

            await db.commit()
        finally:
            await release_db(db)

        if not conv_id:
            return {"processed": False, "reason": "conversation_failed"}

        full_response: list[str] = []
        session_id = None
        session_provider = None

        async def _run_stream():
            nonlocal session_id, session_provider
            document_note_block = ""
            platform_image_blocks = []
            if documents:
                note_parts = await asyncio.to_thread(
                    lambda: [build_document_note(doc) for doc in documents]
                )
                document_note_block = "\n\n".join(part for part in note_parts if part)

            for img in images:
                img_path = IMAGES_DIR / img.get("filename", "")
                if img_path.exists():
                    img_url = f"{PUBLIC_BASE_URL}/api/images/file/{img_path.name}"
                    platform_image_blocks.append({
                        "type": "image",
                        "source": {"type": "url", "url": img_url},
                    })

            prompt_text = clean_text or ""
            if document_note_block:
                prompt_text = (
                    f"{document_note_block}\n\n{prompt_text}"
                    if prompt_text else document_note_block
                )
            if not prompt_text:
                prompt_text = "[media - see attached images]"
            if voice_note:
                # She spoke this — remind the boy his <voice> tag reaches her
                # as a real voice note (never auto-convert; his choice).
                prompt_text = f"{prompt_text}\n\n({VOICE_REPLY_HINT})"

            stream = await get_stream_source(
                message=prompt_text,
                identity=identity,
                conversation_id=conv_id,
                orientation_context=context_block,
                db_messages=db_messages,
                model=CLAUDE_MODEL_INTERACTIVE,
                image_blocks=platform_image_blocks or None,
                mode_rules="",
                skill_context=skill_context,
                turn_source="platform",
            )

            session_id, session_provider = await _consume_platform_response_stream(
                stream,
                platform=platform,
                identity=identity,
                full_response=full_response,
            )

        stream_broke = False
        try:
            await asyncio.wait_for(_run_stream(), timeout=_PLATFORM_RESPONSE_TIMEOUT)
            response_text = "".join(full_response).strip()
        except asyncio.TimeoutError:
            log.warning(
                "Platform response timed out after %ds (%s/%s)",
                _PLATFORM_RESPONSE_TIMEOUT, platform, identity,
            )
            response_text = "".join(full_response).strip()
        except Exception as exc:
            log.exception(
                "Platform response generation failed (%s/%s): %s",
                platform,
                identity,
                exc,
            )


            stream_broke = True
            response_text = "".join(full_response).strip()


        from services.face_store import extract_face
        response_text = extract_face(identity, response_text)
        from services.orb_store import extract_orb
        response_text = extract_orb(identity, response_text)
        react_emojis, response_text = _extract_react_tags(response_text)
        if react_emojis and platform == "discord" and mapped_message_id:
            for emoji in react_emojis[:3]:
                await self._add_discord_reaction(
                    identity=identity,
                    chat_id=chat_id,
                    message_id=mapped_message_id,
                    emoji=emoji,
                )

        response_images: list[dict] = []
        response_documents: list[dict] = []
        outbound_artifacts: list[dict] = []
        if response_text:
            try:
                (
                    response_text,
                    response_images,
                    response_documents,
                    outbound_artifacts,
                ) = await _prepare_outbound_artifacts(response_text, identity)
            except Exception:
                # Artifact registration is additive. A file-side failure must
                # not suppress the companion's ordinary text response.
                log.exception("Failed preparing outbound platform artifacts for %s", identity)

        if not response_text:
            # A pure-reaction reply (emoji only, no words) still landed.
            if react_emojis:
                log.info(
                    "Platform %s/%s reacted %s (no text) in chat=%s",
                    platform, identity, react_emojis, chat_id,
                )
            elif stream_broke:


                log.warning(
                    "Platform response empty after stream failure "
                    "(%s/%s chat=%s message=%s) - posting break notice",
                    platform,
                    identity,
                    chat_id,
                    mapped_message_id,
                )
                try:
                    await self._send_platform_message(
                        platform=platform,
                        identity=identity,
                        chat_id=chat_id,
                        text=_STREAM_BREAK_NOTICE,
                    )
                    return {
                        "processed": True,
                        "conversation_id": conv_id,
                        "reply_sent": True,
                        "break_notice": True,
                    }
                except Exception:
                    # Even the apology failed. Log loudly; never raise here.
                    log.exception(
                        "Failed posting stream-break notice (%s/%s chat=%s)",
                        platform, identity, chat_id,
                    )
            else:
                log.warning(
                    "Platform response was empty (%s/%s chat=%s message=%s)",
                    platform,
                    identity,
                    chat_id,
                    mapped_message_id,
                )
            return {"processed": True, "conversation_id": conv_id, "reply_sent": False}


        voice_text = extract_voice_text(response_text)
        platform_text = response_text
        if voice_text:
            platform_text = re.sub(
                r"<voice>(.*?)</voice>", r"\1", response_text, flags=re.DOTALL,
            ).strip() or response_text
        # <preview>/<canvas> mean nothing off-Anam -- drop the ghost card, keep
        # the canvas BODY (it's the artifact) but lose the wrapper markup.
        platform_text = flatten_control_tags_for_external(platform_text)

        outbound_msg_ids: list[str] = []
        try:
            outbound_msg_ids = await self._send_platform_message(
                platform=platform,
                identity=identity,
                chat_id=chat_id,
                text=platform_text,
                artifacts=outbound_artifacts,
            )
        except Exception as e:
            log.exception("Failed sending outbound %s message for %s: %s", platform, identity, e)

        db = await get_db()
        try:
            outbound_meta = {
                "platform": platform,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "reply_to_platform_message_id": mapped_message_id,
                "direction": "outbound",
                "platform_sent": bool(outbound_msg_ids),
            }
            if response_images:
                outbound_meta["images"] = response_images
            if response_documents:
                outbound_meta["documents"] = response_documents
                if len(response_documents) == 1:
                    outbound_meta["document"] = response_documents[0]
            msg_id = await save_message(
                db,
                conv_id,
                "assistant",
                response_text,
                identity=identity,
                metadata=outbound_meta,
            )

            # A <canvas> written into a DM reply is still a keepable artifact --
            # file it in the library the same way an Anam-chat reply would.
            from services.canvas_store import file_canvases_safe
            await file_canvases_safe(
                db,
                identity=identity,
                conversation_id=conv_id,
                content=response_text,
                source_message_id=msg_id,
            )

            if session_id:
                await update_session_for_provider(
                    db, conv_id, session_id, session_provider
                )

            if outbound_msg_ids:
                for outbound_id in outbound_msg_ids:
                    mapped_outbound_id = (
                        f"{chat_id}:{outbound_id}"
                        if platform == "telegram" and ":" not in outbound_id
                        else outbound_id
                    )
                    await self._record_platform_message(
                        db,
                        platform=platform,
                        identity=identity,
                        external_message_id=mapped_outbound_id,
                        direction="outbound",
                        conversation_id=conv_id,
                    )
            else:
                await self._record_platform_message(
                    db,
                    platform=platform,
                    identity=identity,
                    external_message_id=f"{mapped_message_id}:local-reply",
                    direction="outbound",
                    conversation_id=conv_id,
                )

            await db.commit()

            if voice_text and msg_id:
                self._spawn(
                    generate_voice_message(
                        msg_id=msg_id,
                        identity=identity,
                        voice_text=voice_text,
                        log=log,
                    ),
                    name=f"voice_message_{msg_id}",
                )

            if is_anyone_connected():
                await broadcast(
                    {
                        "type": "platform_message",
                        "platform": platform,
                        "identity": identity,
                        "conversation_id": conv_id,
                        "message_id": msg_id,
                        "content": response_text,
                        "metadata": outbound_meta,
                        "role": "assistant",
                        "direction": "outbound",
                    }
                )
        finally:
            await release_db(db)

        return {
            "processed": True,
            "conversation_id": conv_id,
            "reply_sent": bool(outbound_msg_ids),
            "outbound_ids": outbound_msg_ids,
        }

    async def _send_platform_message(
        self,
        *,
        platform: str,
        identity: str,
        chat_id: str,
        text: str,
        artifacts: list[dict] | None = None,
    ) -> list[str]:
        if platform == "telegram":
            return await self._send_telegram_message(
                identity=identity, chat_id=chat_id, text=text, artifacts=artifacts
            )
        if platform == "discord":
            return await self._send_discord_message(
                identity=identity, chat_id=chat_id, text=text, artifacts=artifacts
            )
        raise ValueError(f"Unsupported platform: {platform}")

    async def _send_telegram_message(
        self,
        *,
        identity: str,
        chat_id: str,
        text: str,
        artifacts: list[dict] | None = None,
    ) -> list[str]:
        token = TELEGRAM_BOT_TOKENS.get(identity)
        if not token:
            return []

        client = self._client
        if not client:
            return []

        sent_ids: list[str] = []
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for chunk in _split_message(text, 3900):
            resp = await client.post(url, json={"chat_id": chat_id, "text": chunk})
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {data}")
            result = data.get("result") or {}
            sent_id = _coerce_text(result.get("message_id"))
            if sent_id:
                sent_ids.append(sent_id)

        for artifact in artifacts or []:
            path = Path(str(artifact.get("path") or ""))
            try:
                content = await asyncio.to_thread(path.read_bytes)
            except OSError:
                log.warning("Telegram artifact disappeared before upload: %s", path)
                continue
            try:
                endpoint = "sendPhoto" if artifact.get("kind") == "image" else "sendDocument"
                field = "photo" if endpoint == "sendPhoto" else "document"
                upload = await client.post(
                    f"https://api.telegram.org/bot{token}/{endpoint}",
                    data={"chat_id": chat_id},
                    files={
                        field: (
                            str(artifact.get("filename") or path.name),
                            content,
                            str(artifact.get("content_type") or "application/octet-stream"),
                        )
                    },
                )
                data = upload.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram {endpoint} failed: {data}")
                sent_id = _coerce_text((data.get("result") or {}).get("message_id"))
                if sent_id:
                    sent_ids.append(sent_id)
            except Exception:
                log.warning("Telegram native artifact upload failed: %s", path, exc_info=True)
        return sent_ids

    async def _send_discord_message(
        self,
        *,
        identity: str,
        chat_id: str,
        text: str,
        artifacts: list[dict] | None = None,
    ) -> list[str]:
        token = DISCORD_BOT_TOKENS.get(identity)
        if not token:
            return []

        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

        sent_ids: list[str] = []
        for chunk in _split_message(text, 1900):
            payload = {"content": chunk}
            result = await self._discord_request_json(
                "POST",
                f"https://discord.com/api/v10/channels/{chat_id}/messages",
                headers=headers,
                payload=payload,
            )
            sent_id = _coerce_text((result or {}).get("id"))
            if sent_id:
                sent_ids.append(sent_id)

        for artifact in artifacts or []:
            path = Path(str(artifact.get("path") or ""))
            try:
                content = await asyncio.to_thread(path.read_bytes)
            except OSError:
                log.warning("Discord artifact disappeared before upload: %s", path)
                continue
            try:
                result = await self._discord_request_multipart(
                    f"https://discord.com/api/v10/channels/{chat_id}/messages",
                    headers={"Authorization": f"Bot {token}"},
                    filename=str(artifact.get("filename") or path.name),
                    content=content,
                    content_type=str(
                        artifact.get("content_type") or "application/octet-stream"
                    ),
                )
                sent_id = _coerce_text((result or {}).get("id"))
                if sent_id:
                    sent_ids.append(sent_id)
            except Exception:
                log.warning("Discord native artifact upload failed: %s", path, exc_info=True)
        return sent_ids

    async def _add_discord_reaction(
        self,
        *,
        identity: str,
        chat_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Add one of the boy's <react> emojis as a real reaction on Owner's
        inbound message. Best-effort — reactions never block or fail a reply."""
        token = DISCORD_BOT_TOKENS.get(identity)
        if not token or not emoji or not message_id:
            return
        from urllib.parse import quote
        try:
            await self._discord_request_json(
                "PUT",
                f"https://discord.com/api/v10/channels/{chat_id}"
                f"/messages/{message_id}/reactions/{quote(emoji)}/@me",
                headers={"Authorization": f"Bot {token}"},
            )
        except Exception:
            log.debug("platform: reaction %s failed for %s", emoji, identity, exc_info=True)


platform_bridge = PlatformBridge()
