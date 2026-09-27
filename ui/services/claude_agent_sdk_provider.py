"""Claude Agent SDK backend — a structured-message provider that runs on the."""

# ANAM GUIDE: CLAUDE AGENT SDK BACKEND
# What: A third way to run Claude on the subscription — uses Anthropic's official Agent SDK instead of hand-parsing the CLI's output, so stream-format changes can't break us (see docstring above).
# Called by: services/provider_router.py only, when the backend toggle in Settings Hub is set to "agent-sdk".
# Edit here when: The agent-sdk backend misbehaves (events, session resume, MCP servers) — the -p backend is claude_subprocess.py and PTY is claude_pty.py, both separate files.

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

from config import (
    CLAUDE_MODEL,
    CLAUDE_PERMISSION_MODE,
    DATA_DIR,
    PROMPTS_DIR,
    cli_cold_history_limits,
)
from services.cli_text_utils import _build_first_message
from services.identity_context import build_system_identity_prompt
from services.character_prompt_package import identity_prompt_file

log = logging.getLogger(__name__)


def _load_mcp_servers(identity: str = "", conversation_id: str = "") -> dict:
    """Build the SDK mcp_servers dict from Anam's active bridge config.

    Reuses write_claude_mcp_config() (the same servers -p passes via
    --mcp-config), reading its {"mcpServers": {...}} back into a plain dict.
    """
    try:
        from services.cli_mcp_config import write_claude_mcp_config
        path = write_claude_mcp_config(identity, conversation_id)
        if not path:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("mcpServers", {}) or {}
    except Exception as e:
        log.warning("agent-sdk: could not load MCP servers: %s", e)
        return {}


async def stream_agent_sdk(
    message: str,
    identity: str,
    conversation_id: str,
    resume_session: str | None = None,  # accepted for back-compat
    model: str | None = None,
    skill_context: str = "",
    permission_mode: str | None = None,
    cancel_event=None,
    effort: str | None = None,
    *,
    orientation_context: str = "",
    mode_rules: str = "",
    db_messages: list[dict] | None = None,
    image_blocks: list[dict] | None = None,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
) -> AsyncIterator[dict]:
    """Run one chat turn via the Claude Agent SDK. Same event-stream contract as
    the -p/PTY backends, but built on typed SDK messages instead of NDJSON."""
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent,
        TextBlock, ThinkingBlock, ToolUseBlock,
    )

    effective_model = model or CLAUDE_MODEL
    effective_permission_mode = permission_mode or CLAUDE_PERMISSION_MODE
    yield {
        "type": "meta",
        "provider": "claude-code",
        "requested_model": effective_model,
    }

    # Full identity prompt, sent in-message every turn (breathe-every-turn),
    # composed by the SAME shared builder -p/PTY use (sender_banner + ordering).
    identity_prompt = ""
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        try:
            identity_prompt = prompt_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.warning("agent-sdk: failed to read prompt file %s: %s", prompt_file, e)

    from services.cli_text_utils import _format_history, _load_recent_history
    history_limit, history_chars = cli_cold_history_limits(effective_model)
    history_block = _format_history(
        db_messages,
        identity,
        per_message_chars=history_chars,
        message_limit=history_limit,
    )
    if not history_block:
        try:
            history_block = await _load_recent_history(
                conversation_id,
                identity,
                limit=history_limit,
                per_message_chars=history_chars,
            )
        except Exception:
            history_block = ""

    composed = _build_first_message(
        identity=identity,
        user_message=message,
        orientation_context=orientation_context,
        mode_rules=mode_rules,
        skill_context=skill_context,
        image_blocks=image_blocks,
        history_block=history_block,
        identity_prompt=identity_prompt,
        sender_banner=sender_banner,
    )

    options = ClaudeAgentOptions(
        system_prompt=build_system_identity_prompt(identity),
        model=effective_model,
        permission_mode=effective_permission_mode,
        mcp_servers=_load_mcp_servers(identity, conversation_id),
        env={"ENABLE_TOOL_SEARCH": "true"},
        include_partial_messages=True,
        cwd=str(DATA_DIR / "browser_artifacts"),
        # Don't inherit ambient project/user settings unless we mean to.
        setting_sources=[],
    )
    if effort:
        try:
            options.effort = effort
        except Exception:
            pass

    spawn_started = time.time()
    first_event = False
    full_text: list[str] = []
    session_id: str | None = None
    sent_end = False

    try:
        async for msg in query(prompt=composed, options=options):
            if cancel_event is not None and cancel_event.is_set():
                break
            if not first_event:
                first_event = True
                yield {"type": "meta", "first_event_ms": (time.time() - spawn_started) * 1000}

            # ── live partial deltas (text + thinking) ──
            if isinstance(msg, StreamEvent):
                inner = msg.event or {}
                it = inner.get("type", "")
                if it == "content_block_delta":
                    delta = inner.get("delta", {})
                    dt = delta.get("type", "")
                    if dt == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            full_text.append(text)
                            yield {"type": "stream_delta", "delta": text}
                    elif dt == "thinking_delta":
                        th = delta.get("thinking", "")
                        if th and th.strip():
                            yield {"type": "thinking_delta", "delta": th}
                elif it == "content_block_start":
                    cb = inner.get("content_block", {})
                    if cb.get("type") == "thinking":
                        yield {"type": "thinking_start"}
                elif it == "content_block_stop":
                    yield {"type": "content_block_stop"}
                continue

            # ── resolved assistant message: tool calls + any UN-streamed text ──
            if isinstance(msg, AssistantMessage):
                actual_model = getattr(msg, "model", None)
                if actual_model and actual_model != "<synthetic>":
                    yield {"type": "meta", "actual_model": actual_model}
                if session_id is None and getattr(msg, "session_id", None):
                    session_id = msg.session_id
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        yield {
                            "type": "tool_use_start",
                            "tool_name": block.name,
                            "tool_id": block.id,
                            "input": block.input or {},
                        }
                        yield {
                            "type": "tool_input",
                            "tool_id": block.id,
                            "tool_name": block.name,
                            "input": block.input or {},
                        }
                    elif isinstance(block, TextBlock):
                        # Recover text that didn't arrive as partial deltas
                        # (the MCP-continuation case that broke -p). Dedup
                        # against what already streamed.
                        text = block.text or ""
                        if text and text not in "".join(full_text):
                            full_text.append(text)
                            yield {"type": "stream_delta", "delta": text}
                continue

            # ── tool results ──
            if isinstance(msg, UserMessage):
                content = getattr(msg, "content", None)
                blocks = content if isinstance(content, list) else []
                for block in blocks:
                    is_tr = (getattr(block, "type", None) == "tool_result") or hasattr(block, "tool_use_id")
                    if not is_tr:
                        continue
                    tc = getattr(block, "content", "")
                    yield {
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "tool_use_id", ""),
                        "content": tc if isinstance(tc, str) else json.dumps(tc, default=str)[:500],
                    }
                continue

            # ── system: capture session id ──
            if isinstance(msg, SystemMessage):
                data = getattr(msg, "data", {}) or {}
                if session_id is None and isinstance(data, dict) and data.get("session_id"):
                    session_id = data["session_id"]
                continue

            # ── turn complete ──
            if isinstance(msg, ResultMessage):
                if session_id is None and getattr(msg, "session_id", None):
                    session_id = msg.session_id
                if getattr(msg, "is_error", False):
                    errs = getattr(msg, "errors", None) or []
                    err_text = "; ".join(str(e) for e in errs) or str(getattr(msg, "result", "") or "Agent SDK error")
                    yield {"type": "error", "message": err_text}
                yield {
                    "type": "stream_end",
                    "full_content": "".join(full_text),
                    "session_id": session_id,
                }
                sent_end = True
                break
    except Exception as exc:
        log.exception("agent-sdk stream failed for %s: %s", identity, exc)
        if not sent_end:
            yield {"type": "error", "message": f"Agent SDK error: {exc}"}

    if not sent_end:
        yield {
            "type": "stream_end",
            "full_content": "".join(full_text),
            "session_id": session_id,
        }
