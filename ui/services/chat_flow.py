"""Helpers for websocket chat message normalization and stream coordination."""

# ANAM GUIDE: CHAT STREAM PLUMBING HELPERS
# What: The shared toolbox for a live reply in flight — tidies incoming messages (text/images/documents/audio), collects the streaming reply piece by piece (StreamAccumulator), batches deltas for the browser, and strips rare glitch words.
# Called by: services/chat_pipeline.py, services/autowake.py, api/chat_http.py, api/echo_relay.py, services/pack_night.py, services/claude_pty.py — basically every path that streams a reply.
# Edit here when: A reply streams to the screen oddly (chunking, glued-together text), attachments aren't being picked up from a message, or message metadata needs a new field.

import asyncio
import json
import logging
import re
from collections import deque
from typing import Any


_LEADING_GLITCH_NO_SPACE = re.compile(r"^\s*[a-z]{3,12}(?=[A-Z\[*])")
_LEADING_GLITCH_SPACE_CAP = re.compile(r"^\s*[a-z]{3,12} (?=[A-Z])")


def strip_leading_glitch_token(text: str) -> str:
    """Remove a stray leading glitch word the model layer sometimes emits.

    Returns ``text`` unchanged when no glitch shape matches (the common case).
    """
    if not text:
        return text
    m = _LEADING_GLITCH_NO_SPACE.match(text) or _LEADING_GLITCH_SPACE_CAP.match(text)
    if m:
        return text[m.end():].lstrip()
    return text


def normalize_incoming_message(
    msg: dict[str, Any],
) -> tuple[str, list[dict], list[dict], list[dict]]:
    """Normalize text, image, document, and audio payloads from a websocket message.

    Returns (text, images_info, documents_info, audio_info). Older callers that
    expect a 3-tuple should unpack the first three and ignore audio; new code
    should consume all four.
    """
    text = (msg.get("content") or "").strip()

    images_info = msg.get("images") or []
    legacy_image = msg.get("image")
    if legacy_image and not images_info:
        images_info = [legacy_image]

    documents_info = msg.get("documents") or []
    legacy_document = msg.get("document")
    if legacy_document and not documents_info:
        documents_info = [legacy_document]
    documents_info = [
        doc for doc in documents_info
        if isinstance(doc, dict) and doc.get("filename")
    ]

    audio_info = msg.get("audio") or []
    if isinstance(audio_info, dict):
        audio_info = [audio_info]
    audio_info = [
        clip for clip in audio_info
        if isinstance(clip, dict) and clip.get("filename")
    ]

    return text, images_info, documents_info, audio_info


def build_user_message_metadata(
    images_info: list[dict],
    documents_info: list[dict],
    audio_info: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Build persisted metadata for a user message."""
    metadata: dict[str, Any] = {}
    if images_info:
        metadata["images"] = images_info
    if documents_info:
        metadata["documents"] = documents_info
        if len(documents_info) == 1:
            metadata["document"] = documents_info[0]
    if audio_info:
        metadata["audio"] = audio_info
    return metadata or None


def build_assistant_message_metadata(
    response_images: list[dict],
    response_documents: list[dict],
    thinking_blocks: list[str],
    tool_events: list[dict],
    tool_results_map: dict[str, dict[str, Any]],
    context_notice: dict[str, Any] | None = None,
    model_provenance: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build persisted metadata for an assistant message."""
    metadata: dict[str, Any] = {}

    if response_images:
        metadata["images"] = response_images
    if response_documents:
        metadata["documents"] = response_documents
        if len(response_documents) == 1:
            metadata["document"] = response_documents[0]
    if thinking_blocks:
        metadata["thinking"] = thinking_blocks
    if tool_events:
        tools_data = []
        for tool_event in tool_events:
            tool_id = tool_event.get("tool_id", "")
            result = tool_results_map.get(tool_id, {})
            tool_entry = {
                "tool_name": tool_event.get("tool_name", "unknown"),
                "tool_id": tool_id,
                "status": result.get("status", "completed"),
            }
            tool_input = result.get("input", {})
            if tool_input:
                tool_entry["input"] = tool_input
            tool_content = result.get("content")
            if tool_content not in (None, ""):
                tool_entry["content"] = tool_content
            tools_data.append(tool_entry)
        metadata["tools"] = tools_data
    if context_notice:
        metadata["context_notice"] = context_notice
    if model_provenance:
        metadata["model_provenance"] = model_provenance

    return metadata or None


def merge_tool_result_entry(
    tool_results_map: dict[str, dict[str, Any]],
    *,
    tool_id: str,
    tool_name: str | None = None,
    status: str | None = None,
    tool_input: dict[str, Any] | None = None,
    content: Any = None,
) -> None:
    """Accumulate tool details so history replay can rebuild richer cards."""
    if not tool_id:
        return

    existing = dict(tool_results_map.get(tool_id, {}))
    if tool_name:
        existing["tool_name"] = tool_name
    if status:
        existing["status"] = status
    if tool_input:
        existing["input"] = tool_input
    if content not in (None, ""):
        existing["content"] = content
    tool_results_map[tool_id] = existing


class StreamAccumulator:
    """Collects assistant-turn state from a provider event stream.

    The same accumulation logic (streamed text, tool events, thinking blocks,
    tool-result metadata, response images/documents) was previously copy-pasted
    into four stream loops — chat_pipeline, chat_http, echo_relay, autowake —
    and had already drifted (e.g. the resume-retry path used the wrong tool
    keys). This is the single source of truth: each loop calls ``observe(event)``
    per event, then reads the collected fields at ``stream_end`` to finalize.

    ``observe`` only mutates internal state; the caller still owns transport
    (websocket send vs SSE queue vs broadcast) and finalization, because those
    differ meaningfully per entry point.
    """

    def __init__(self) -> None:
        self.full_response: list[str] = []
        self.tool_events: list[dict] = []
        self.thinking_blocks: list[str] = []
        self.current_thinking: list[str] = []
        self.tool_results_map: dict[str, dict[str, Any]] = {}
        self.response_images: list[dict] = []
        self.response_documents: list[dict] = []
        self.first_event_ms: float | None = None
        self.provider: str | None = None
        self.requested_model: str | None = None
        self.models_used: list[str] = []

    def observe(self, event: dict[str, Any]) -> str:
        """Update internal state from one stream event; return its type.

        Note: ``stream_end`` is intentionally not special-cased here — the
        caller calls ``flush_thinking()`` and then finalizes, so any trailing
        thinking that did not get a ``content_block_stop`` is still captured.
        """
        event_type = event.get("type", "")

        if event_type == "meta":
            if event.get("first_event_ms") is not None:
                self.first_event_ms = float(event["first_event_ms"])
            if event.get("provider"):
                self.provider = str(event["provider"])
            if event.get("requested_model"):
                self.requested_model = str(event["requested_model"])
            actual = event.get("actual_model")
            if actual and actual != "<synthetic>" and actual not in self.models_used:
                self.models_used.append(str(actual))
        elif event_type == "stream_delta":
            self.full_response.append(event.get("delta", ""))
        elif event_type == "stream_reset":
            self.full_response = []
        elif event_type == "tool_use_start":
            self.tool_events.append({
                "tool_name": event.get("tool_name", "unknown"),
                "tool_id": event.get("tool_id", ""),
            })
        elif event_type == "tool_input":
            # Resolved input arrives after the stream, before execution.
            merge_tool_result_entry(
                self.tool_results_map,
                tool_id=event.get("tool_id", ""),
                tool_name=event.get("tool_name"),
                tool_input=event.get("input") or {},
            )
        elif event_type == "tool_result":
            result_images = event.get("images") or []
            if result_images:
                self.response_images.extend(result_images)
            result_documents = event.get("documents") or []
            if result_documents:
                self.response_documents.extend(result_documents)
            merge_tool_result_entry(
                self.tool_results_map,
                tool_id=event.get("tool_use_id", ""),
                tool_name=event.get("tool_name"),
                status=event.get("status", "completed"),
                tool_input=event.get("input") or {},
                content=event.get("content"),
            )
        elif event_type == "thinking_start":
            self.current_thinking = []
        elif event_type == "thinking_delta":
            self.current_thinking.append(event.get("delta", ""))
        elif event_type == "content_block_stop":
            self.flush_thinking()

        return event_type

    def flush_thinking(self) -> None:
        """Move any buffered thinking deltas into a completed block."""
        if self.current_thinking:
            self.thinking_blocks.append("".join(self.current_thinking))
            self.current_thinking = []

    def streamed_text(self) -> str:
        return strip_leading_glitch_token("".join(self.full_response))

    def model_provenance(self) -> dict[str, Any] | None:
        if not (self.provider or self.requested_model or self.models_used):
            return None
        result: dict[str, Any] = {}
        if self.provider:
            result["provider"] = self.provider
        if self.requested_model:
            result["requested_model"] = self.requested_model
        if self.models_used:
            result["models_used"] = list(self.models_used)
            result["actual_model"] = self.models_used[-1]
            result["switched"] = len(self.models_used) > 1
        return result


class DeltaCoalescer:
    """Merges bursts of per-token delta events into batched frames.

    Providers emit one ``stream_delta`` / ``thinking_delta`` event per token,
    and every forwarded event costs a per-socket lock acquire plus a
    ``ws.send_json`` (or a full ``broadcast()``) — hundreds to thousands of
    websocket frames per reply. This buffers consecutive delta text and emits
    ONE merged event (identical shape, concatenated ``delta`` field) when any
    of these happens:

      * ~48ms elapsed since the buffer opened (trailing timer flush — keeps
        streaming visually identical while cutting frame count 10-20x),
      * the buffered text exceeds ``max_chars``,
      * an event of any other type (or a delta whose non-``delta`` fields
        differ from the buffered ones) arrives — the buffer flushes FIRST,
        then the new event is forwarded, so wire ordering exactly matches the
        provider stream,
      * the owner awaits ``flush()`` (stream end / teardown).

    Only the ``delta`` string is concatenated; two delta events merge only
    when every other field matches exactly, so per-chunk metadata is never
    mixed across a merge. All sends happen under an internal lock, which keeps
    the timer-driven flush strictly ordered against the feed path. ``send`` is
    any async callable taking the event dict (e.g. ``StreamWebSocketBridge.send``
    or ``connection_registry.broadcast``).
    """

    _DELTA_TYPES = frozenset({"stream_delta", "thinking_delta", "autowake_delta"})

    def __init__(
        self,
        send,
        *,
        max_hold: float = 0.048,
        max_chars: int = 512,
    ) -> None:
        self._send = send
        self._max_hold = max_hold
        self._max_chars = max_chars
        self._lock = asyncio.Lock()
        self._meta: dict[str, Any] = {}
        self._parts: list[str] = []
        self._chars = 0
        self._timer: asyncio.Task | None = None

    async def feed(self, event: dict[str, Any]) -> None:
        """Buffer a delta event, or flush-then-forward any other event."""
        async with self._lock:
            if (
                event.get("type") in self._DELTA_TYPES
                and isinstance(event.get("delta"), str)
            ):
                meta = {k: v for k, v in event.items() if k != "delta"}
                if self._parts and meta != self._meta:
                    # Different type or metadata — never merge across it.
                    await self._flush_locked()
                if not self._parts:
                    self._meta = meta
                self._parts.append(event["delta"])
                self._chars += len(event["delta"])
                if self._chars >= self._max_chars:
                    await self._flush_locked()
                elif self._timer is None:
                    self._timer = asyncio.create_task(self._timer_flush())
                return
            # Any other event: flush buffered deltas first, then forward it,
            # so event ordering on the wire matches the provider stream.
            await self._flush_locked()
            await self._send(event)

    async def flush(self) -> None:
        """Flush any buffered delta text immediately (stream end / teardown)."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        if not self._parts:
            return
        merged = dict(self._meta)
        merged["delta"] = "".join(self._parts)
        self._parts = []
        self._chars = 0
        self._meta = {}
        await self._send(merged)

    async def _timer_flush(self) -> None:
        try:
            await asyncio.sleep(self._max_hold)
        except asyncio.CancelledError:
            return
        async with self._lock:
            # Only flush if this timer is still the live one — a size or
            # ordering flush may have already emptied the buffer (and a new
            # burst may own a newer timer by now).
            if self._timer is asyncio.current_task():
                await self._flush_locked()


class StreamWebSocketBridge:
    """Owns in-stream websocket sending, cancellation, and queued messages."""

    def __init__(self, ws, log: logging.Logger):
        self.ws = ws
        self.log = log
        self.ws_alive = True
        self.cancel_event = asyncio.Event()
        self.queued_messages: deque[dict] = deque()
        # Set by chat_pipeline once identity+conversation are known. Async
        # callable(incoming_msg) -> bool: True means the message was slipped
        # into the RUNNING turn and must not be queued as a separate one.
        self.inject_hook = None

    async def send(self, data: dict[str, Any]) -> None:
        """Send to the websocket if it is still alive.

        Routed through connection_registry.send_json_locked so a concurrent
        broadcast() can never interleave a frame on the same socket mid-stream.
        """
        if not self.ws_alive:
            return
        from services.connection_registry import send_json_locked
        ok = await send_json_locked(self.ws, data)
        if not ok:
            self.ws_alive = False
            self.log.info("WebSocket died mid-stream -- continuing to save message")

    async def listen_during_stream(self) -> None:
        """Read websocket messages during streaming; handle stop and queue others."""
        try:
            while not self.cancel_event.is_set():
                try:
                    raw_msg = await asyncio.wait_for(self.ws.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    self.ws_alive = False
                    return

                try:
                    incoming = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                msg_type = incoming.get("type")
                if msg_type == "ping":
                    await self.send({"type": "pong"})
                elif msg_type == "stop_streaming":
                    self.log.info("Stop streaming requested by user")
                    self.cancel_event.set()
                    return
                else:


                    if self.inject_hook is not None and msg_type == "message":
                        try:
                            if await self.inject_hook(incoming):
                                continue
                        except Exception:
                            self.log.exception(
                                "Live injection raised; queueing the message instead"
                            )
                    self.queued_messages.append(incoming)
        except asyncio.CancelledError:
            pass

    async def stop_listener(self, listener_task: asyncio.Task) -> bool:
        """Stop the listener task and return whether the user cancelled."""
        was_cancelled = self.cancel_event.is_set()
        if not was_cancelled:
            self.cancel_event.set()
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        return was_cancelled
