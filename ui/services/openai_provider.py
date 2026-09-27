"""OpenAI-compatible streaming provider -- works with OpenAI API and Ollama.

Yields the same event dict interface as claude_api.stream_api() so callers
need no changes: meta, stream_delta, thinking_start, thinking_delta,
tool_use_start, tool_input, tool_result, content_block_stop, stream_end,
keepalive, error.

Full tool support: converts Anthropic tool schemas to OpenAI function calling
format and runs the same agentic tool loop.
"""

# ANAM GUIDE: OPENAI / OLLAMA / LM STUDIO PROVIDER
# What: The chat brain used when the provider is set to openai, lmstudio, or ollama — streams replies and runs tools the same way the Claude providers do.
# Called by: services/provider_router.py (which picks the provider), plus services/vision_sidecar.py and services/cli_text_utils.py.
# Edit here when: changing how OpenAI-compatible models are called — their tool handling, thinking display, or streaming behavior.

import asyncio
import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import AsyncIterator

from config import OPENAI_MAX_TOOL_ROUNDS, PROMPTS_DIR
from services.identity_context import build_identity_anchor
from services.character_prompt_package import identity_prompt_file
from services.mcp_bridge import mcp_bridge
from services.tool_loop_guard import ToolLoopGuard, append_guard_warning
from services.tool_search import BRIDGE_NAMES, ToolSearchCatalog

log = logging.getLogger(__name__)
_client_cache: dict[tuple[str, str], object] = {}


@lru_cache(maxsize=16)
def _load_identity_prompt(identity: str) -> str:
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    log.warning("Identity prompt not found: %s", prompt_file)
    return f"You are {identity}."


def _get_openai_client(api_key: str, base_url: str):
    from openai import AsyncOpenAI

    cache_key = (api_key, base_url)
    client = _client_cache.get(cache_key)
    if client is not None:
        return client

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncOpenAI(**client_kwargs)
    _client_cache[cache_key] = client
    return client


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schemas to OpenAI function calling format."""
    oai_tools = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        # Remove cache_control -- OpenAI doesn't support it
        schema = {k: v for k, v in schema.items() if k != "cache_control"}
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": schema,
            },
        })
    return oai_tools


def _convert_db_messages(db_messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format db_messages to OpenAI message format.

    Handles text, images, tool_use, tool_result, and thinking blocks.
    """
    oai_messages = []

    for msg in db_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            oai_messages.append({"role": role, "content": str(content)})
            continue

        # Anthropic list-of-blocks format
        # Separate into: text parts, tool_use blocks, tool_result blocks
        text_parts = []
        tool_calls = []
        tool_results = []

        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                continue

            btype = block.get("type", "")

            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                # Skip thinking blocks -- they're internal
                pass
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{len(tool_calls)}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif btype == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": str(block.get("content", "")),
                })
            elif btype == "image":
                # Convert image to description (can't send Anthropic images as-is)
                text_parts.append("[image]")

        if role == "assistant" and tool_calls:
            # Assistant message with tool calls
            msg_out = {
                "role": "assistant",
                "content": "\n".join(t for t in text_parts if t) or None,
                "tool_calls": tool_calls,
            }
            oai_messages.append(msg_out)
        elif tool_results:
            # Tool results go as separate tool messages
            for tr in tool_results:
                oai_messages.append(tr)
        else:
            # Regular text message
            combined = "\n".join(t for t in text_parts if t)
            if combined:
                oai_messages.append({"role": role, "content": combined})

    return oai_messages


def _reasoning_detail_dict(detail) -> dict | None:
    if isinstance(detail, dict):
        return {key: value for key, value in detail.items() if value is not None}
    if hasattr(detail, "model_dump"):
        return detail.model_dump(exclude_none=True)
    return None


def _merge_reasoning_details(target: list[dict], incoming) -> list[str]:
    """Reconstruct OpenRouter streaming reasoning blocks for tool continuity."""
    visible: list[str] = []
    for raw in incoming or []:
        detail = _reasoning_detail_dict(raw)
        if not detail:
            continue
        key = (detail.get("id"), detail.get("index"), detail.get("type"))
        existing = next(
            (
                item for item in target
                if (item.get("id"), item.get("index"), item.get("type")) == key
            ),
            None,
        )
        if existing is None:
            existing = dict(detail)
            target.append(existing)
        else:
            for field, value in detail.items():
                if field in {"text", "summary", "data"} and isinstance(value, str):
                    existing[field] = str(existing.get(field) or "") + value
                elif value is not None:
                    existing[field] = value
        for field in ("text", "summary"):
            value = detail.get(field)
            if isinstance(value, str) and value:
                visible.append(value)
    return visible


async def stream_openai_compatible(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: list[dict] | None = None,
    model: str = "gpt-4o",
    image_blocks: list[dict] | None = None,
    mode_rules: str = "",
    skill_context: str = "",
    active_categories: set[str] | None = None,
    cancel_event: asyncio.Event | None = None,
    *,
    api_key: str = "",
    base_url: str = "",
    max_tokens: int = 4096,
    context_budget: int = 0,
    vision_model: str = "",
    vision_api_key: str = "",
    vision_base_url: str = "",
) -> AsyncIterator[dict]:
    """Stream a response from an OpenAI-compatible API with full tool support.

    Works with both OpenAI and Ollama (via base_url).
    Converts Anthropic tool schemas to OpenAI function calling format.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        yield {"type": "error", "message": "openai package not installed. Run: pip install openai"}
        return

    # Local servers (LM Studio, Ollama) don't need an API key but openai SDK requires one
    if not api_key and base_url:
        api_key = "lm-studio"

    client = _get_openai_client(api_key, base_url)
    yield {
        "type": "meta",
        "provider": "openai-compatible",
        "requested_model": model,
    }

    # Build system prompt -- for local models with limited context,
    # prioritize orientation > mode rules > identity (truncated)
    identity_anchor = build_identity_anchor(identity)
    identity_prompt = _load_identity_prompt(identity)

    if context_budget > 0:
        original_identity_prompt_len = len(identity_prompt)
        # Rough estimate: 1 token ~ 4 chars. Reserve space for response + user message.
        char_budget = (context_budget - max_tokens - 500) * 4
        if char_budget < 2000:
            char_budget = 2000

        # Priority order: orientation (must have), mode rules, skill context, identity (truncatable)
        reserved = 0
        if orientation_context:
            reserved += len(orientation_context) + 10
        if mode_rules:
            reserved += len(mode_rules) + 10
        if skill_context:
            reserved += len(skill_context) + 10

        identity_budget = char_budget - reserved
        if identity_budget < len(identity_prompt) and identity_budget > 500:
            # Truncate identity prompt, keeping the beginning (core identity) and noting truncation
            identity_prompt = identity_prompt[:identity_budget] + "\n\n[...identity prompt truncated to fit context window...]"
            log.info(
                "Truncated identity prompt for %s: %d -> %d chars (context_budget=%d)",
                identity, original_identity_prompt_len, len(identity_prompt), context_budget,
            )
        elif identity_budget <= 500:
            # Context is extremely small -- use a minimal identity
            identity_prompt = identity_anchor
            log.warning(
                "Context budget too small for %s (%d tokens) -- using minimal identity prompt",
                identity, context_budget,
            )

    system_parts = [identity_anchor]
    if identity_prompt != identity_anchor:
        system_parts.append(identity_prompt)
    if mode_rules:
        system_parts.append(mode_rules)
    if orientation_context:
        system_parts.append(orientation_context)
    if skill_context:
        system_parts.append(skill_context)
    system_text = "\n\n---\n\n".join(system_parts)

    # Vision sidecar: if the chat model can't see images, route them to a
    # vision model (default Gemini Flash via OpenRouter) and inject the
    # resulting descriptions as text. Lets text-only models like DeepSeek
    # "see" both the current image and any images earlier in the history.
    from services.vision_sidecar import (
        describe_images,
        enrich_history_images,
        model_can_see,
    )

    use_sidecar = bool(vision_model) and not model_can_see(model)
    v_key = vision_api_key or api_key
    v_url = vision_base_url or base_url

    # Build messages
    oai_messages = [{"role": "system", "content": system_text}]

    # Convert db_messages (Anthropic format) to OpenAI format. When the
    # sidecar is active, first swap history image blocks for descriptions so
    # they don't collapse to the "[image]" placeholder.
    effective_db_messages = db_messages or []
    if use_sidecar and effective_db_messages:
        effective_db_messages = await enrich_history_images(
            effective_db_messages, api_key=v_key, base_url=v_url, model=vision_model,
        )
    oai_messages.extend(_convert_db_messages(effective_db_messages))

    # Add current user message
    if image_blocks and use_sidecar:
        # Text-only model: describe the image(s) and fold the description
        # into the user text. No image_url parts are sent.
        vision_desc = await describe_images(
            image_blocks, api_key=v_key, base_url=v_url, model=vision_model, hint=message,
        )
        if vision_desc:
            log.info(
                "Vision sidecar described %d image(s) for %s (chat=%s, vision=%s)",
                len(image_blocks), identity, model, vision_model,
            )
            message = f"{vision_desc}\n\n{message}" if message else vision_desc
        oai_messages.append({"role": "user", "content": message})
    elif image_blocks:
        # Vision-capable model: send images natively as image_url parts.
        user_content = []
        for img_block in image_blocks:
            if img_block.get("type") == "image":
                source = img_block.get("source", {})
                if source.get("type") == "url":
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": source.get("url", "")},
                    })
                elif source.get("type") == "base64":
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    })
        if message:
            user_content.append({"type": "text", "text": message})
        oai_messages.append({"role": "user", "content": user_content})
    else:
        oai_messages.append({"role": "user", "content": message})

    # Build tools: local + MCP (same as Anthropic path)
    from services.claude_api import _LOCAL_TOOLS, _LOCAL_TOOL_NAMES, _execute_local_tool

    mcp_tools = mcp_bridge.get_tools_for_categories(active_categories)
    # Weaker OpenAI-compatible models follow concrete schemas much more
    # reliably than a generic search/describe/call meta-tool. Keep native MCP
    # schemas visible by default; the deferred catalog remains opt-in.
    use_deferred_tools = os.environ.get(
        "ANAM_OPENAI_MCP_TOOL_SEARCH", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    tool_catalog = ToolSearchCatalog.maybe_build(mcp_tools) if use_deferred_tools else None
    exposed_mcp_tools = tool_catalog.bridge_schemas() if tool_catalog else mcp_tools
    all_tools = [dict(t) for t in _LOCAL_TOOLS] + [
        dict(t) for t in exposed_mcp_tools
        if t["name"] not in _LOCAL_TOOL_NAMES
    ]
    # Remove cache_control from all tools
    for t in all_tools:
        t.pop("cache_control", None)

    oai_tools = _anthropic_tools_to_openai(all_tools) if all_tools else None
    tool_name_set = {t["name"] for t in all_tools} if all_tools else set()

    log.info(
        "OpenAI-compatible API call for %s (model=%s, base_url=%s, history=%d msgs, tools=%d)",
        identity, model, base_url or "default",
        len(oai_messages) - 2,
        len(oai_tools) if oai_tools else 0,
    )

    start_time = time.monotonic()
    first_token_logged = False
    full_text = []
    max_rounds = OPENAI_MAX_TOOL_ROUNDS
    tool_guard = ToolLoopGuard(hard_stop=True)
    round_num = 0
    had_tool_rounds = False
    force_final = False

    try:
        while True:
            tool_calls_accumulated: dict[int, dict] = {}
            round_reasoning_details: list[dict] = []
            finish_reason = None

            create_kwargs = {
                "model": model,
                "messages": oai_messages,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if oai_tools and not force_final:
                create_kwargs["tools"] = oai_tools

            stream = await client.chat.completions.create(**create_kwargs)

            reported_models: set[str] = set()
            async for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    log.info("Stream cancelled by user for %s", identity)
                    break

                reported_model = getattr(chunk, "model", None)
                if reported_model and reported_model not in reported_models:
                    reported_models.add(reported_model)
                    yield {"type": "meta", "actual_model": reported_model}

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Track finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                reasoning_details = getattr(delta, "reasoning_details", None)
                if reasoning_details is None and getattr(delta, "model_extra", None):
                    reasoning_details = delta.model_extra.get("reasoning_details")
                visible_reasoning = _merge_reasoning_details(
                    round_reasoning_details, reasoning_details
                )

                # First event timing
                if not first_token_logged and (
                    delta.content or delta.tool_calls or visible_reasoning
                ):
                    first_token_logged = True
                    first_ms = (time.monotonic() - start_time) * 1000
                    log.info("OpenAI first token for %s after %.0fms", identity, first_ms)
                    yield {"type": "meta", "first_event_ms": first_ms}

                # Text content
                if delta.content:
                    full_text.append(delta.content)
                    yield {"type": "stream_delta", "delta": delta.content}

                # Reasoning/thinking content (o1, o3 models)
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    yield {"type": "thinking_delta", "delta": delta.reasoning_content}
                for reasoning_text in visible_reasoning:
                    yield {"type": "thinking_delta", "delta": reasoning_text}

                # Tool calls (streamed incrementally)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_accumulated:
                            tool_calls_accumulated[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "arguments": "",
                            }
                            if tc.id:
                                tool_calls_accumulated[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_accumulated[idx]["name"] = tc.function.name
                                yield {
                                    "type": "tool_use_start",
                                    "tool_name": tc.function.name,
                                    "tool_id": tool_calls_accumulated[idx]["id"],
                                    "input": {},
                                }
                            if tc.function.arguments:
                                tool_calls_accumulated[idx]["arguments"] += tc.function.arguments

            # Check cancel after stream completes
            if cancel_event and cancel_event.is_set():
                break

            # Some OpenRouter backends emit tool calls with a non-standard
            # finish reason. The calls themselves are the source of truth.
            if not tool_calls_accumulated:
                break

            had_tool_rounds = True
            # Narration emitted before a tool call ("Let me check...") is not
            # the final answer. Keep it visible during the live turn but reset
            # persisted response accumulation before the next model round.
            yield {"type": "stream_reset"}

            # -- Agentic tool loop --
            # Build assistant message with tool_calls for history
            assistant_tool_calls = []
            for idx in sorted(tool_calls_accumulated.keys()):
                tc = tool_calls_accumulated[idx]
                assistant_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                })

            assistant_message = {
                "role": "assistant",
                "content": "".join(full_text) if full_text else None,
                "tool_calls": assistant_tool_calls,
            }
            if round_reasoning_details:
                assistant_message["reasoning_details"] = round_reasoning_details
            oai_messages.append(assistant_message)

            # Execute each tool call
            halt_after_batch = False
            for idx in sorted(tool_calls_accumulated.keys()):
                tc = tool_calls_accumulated[idx]
                tool_name = tc["name"]
                tool_id = tc["id"]

                if cancel_event and cancel_event.is_set():
                    break

                yield {"type": "keepalive"}

                # Parse arguments
                try:
                    arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    argument_error = None
                except json.JSONDecodeError as exc:
                    arguments = {}
                    argument_error = f"Malformed JSON tool arguments: {exc.msg}"

                log.info(
                    "Executing tool %s (round %d) for %s",
                    tool_name, round_num + 1, identity,
                )

                # Send resolved input
                yield {
                    "type": "tool_input",
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "input": arguments,
                }

                # Execute tool
                guard_name = tool_name
                guard_args = arguments
                if tool_catalog and tool_name in BRIDGE_NAMES:
                    guard_name, guard_args = tool_catalog.target(tool_name, arguments)
                before = tool_guard.before_call(guard_name, guard_args)

                if argument_error:
                    result_text = f"Error: {argument_error}. Reissue one valid tool call."
                elif before.blocks:
                    result_text = f"Error: {before.message}"
                elif tool_catalog and tool_name in BRIDGE_NAMES:
                    result_text = await tool_catalog.execute(tool_name, arguments, mcp_bridge)
                elif tool_name in _LOCAL_TOOL_NAMES:
                    result_text = await _execute_local_tool(
                        tool_name, arguments, tool_id=tool_id, identity=identity
                    )
                elif tool_name in tool_name_set:
                    result_text = await mcp_bridge.call_tool(
                        tool_name, arguments,
                        session_key=f"{identity}:{conversation_id}",
                    )
                else:
                    result_text = f"Error: unknown tool '{tool_name}'"

                if not before.blocks and not argument_error:
                    decision = tool_guard.after_call(guard_name, guard_args, result_text)
                    result_text = append_guard_warning(result_text, decision)
                    if decision.action == "halt":
                        halt_after_batch = True


                result_event = {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "tool_name": tool_name,
                    "status": "completed",
                    "input": arguments,
                }
                yield result_event

                # Append tool result for next round
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": result_text,
                })

            if cancel_event and cancel_event.is_set():
                break

            # Clear full_text for next round (tool loop continues)
            full_text = []

            round_num += 1
            reached_configured_limit = max_rounds > 0 and round_num >= max_rounds
            if halt_after_batch or reached_configured_limit:
                force_final = True
                reason = (
                    "Repeated tool failures indicate no progress."
                    if halt_after_batch
                    else f"The optional configured tool-round limit ({max_rounds}) was reached."
                )
                oai_messages.append({
                    "role": "system",
                    "content": (
                        f"{reason} Tools are disabled for this final response. "
                        "Explain what succeeded, what remains, and the exact blocker. "
                        "Do not claim you will make another tool call."
                    ),
                })

        # Stream complete
        total_ms = (time.monotonic() - start_time) * 1000
        log.info(
            "OpenAI stream complete for %s in %.0fms (%d chars)",
            identity, total_ms, sum(len(t) for t in full_text),
        )

        yield {
            "type": "stream_end",
            "full_content": "".join(full_text),
            "session_id": None,
            "discard_intermediate": bool(had_tool_rounds and not full_text),
        }

    except Exception as e:
        log.exception("OpenAI-compatible API error for %s", identity)
        content = "".join(full_text)
        if content:
            yield {
                "type": "stream_end",
                "full_content": content,
                "session_id": None,
            }
        message = str(e)
        lmstudio_ctx = re.search(r"n_keep:\s*(\d+)\s*>?=\s*n_ctx:\s*(\d+)", message)
        if lmstudio_ctx:
            prompt_tokens, model_context = lmstudio_ctx.groups()
            friendly = (
                "LM Studio context window is too small for this full Anam prompt "
                f"({prompt_tokens} tokens requested, model loaded with {model_context}). "
                "If you don't want to trim context, load the model with a larger context "
                "window or choose a larger-context model in LM Studio."
            )
            yield {"type": "error", "message": friendly}
            return
        yield {"type": "error", "message": f"API error: {e}"}
