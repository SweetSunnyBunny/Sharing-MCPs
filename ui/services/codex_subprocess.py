"""Codex CLI subprocess streaming -- spawns `codex exec --json` and yields events.

Yields the same event dict interface as claude_api.stream_api():
    meta, stream_delta, tool_use_start, tool_input, tool_result,
    content_block_stop, stream_end, keepalive, error

Note: Codex exec doesn't stream tokens -- text arrives as complete blocks
via item.completed events. Tool execution (command_execution) is shown
with start/result events.
"""

# ANAM GUIDE: CODEX CLI PROVIDER
# What: Lets OpenAI's Codex CLI answer chat turns — spawns `codex exec --json` and translates its events into Anam's normal stream events.
# Called by: services/provider_router.py when the provider is set to Codex.
# Edit here when: changing how Codex is launched, its stall timeout, or how its JSON events map to chat events. Codex sends whole text blocks, not typed-out tokens.

import asyncio
import json
import logging
import os
import subprocess
from services.owned_process import owned_popen, close_owned_process
import threading
from queue import Queue, Empty
from typing import AsyncIterator

from config import PROMPTS_DIR
from services.character_prompt_package import identity_prompt_file, build_turn_packet
from services.cli_mcp_config import build_codex_mcp_overrides
from services.codex_cli import (
    find_codex_executable,
    get_codex_default_model_id,
    normalize_codex_model,
)

log = logging.getLogger(__name__)

# Seconds of TOTAL silence from a still-live Codex process before we treat it as
# wedged and kill it. With stderr now drained continuously (no pipe-deadlock), a
# long silent stretch means a genuinely stuck run (hung MCP tool / child), so we
# fail FAST (~5min) instead of riding the outer ~15min autowake cap. Generous on
# purpose: Codex delivers whole blocks, not tokens, so one long generation is
# legitimately event-silent. The autowake wall-clock cap remains the backstop.
_STALL_TIMEOUT = 300


def _reader_thread(pipe, queue: Queue):
    """Read lines from subprocess stdout in a background thread."""
    try:
        for raw_line in iter(pipe.readline, b""):
            queue.put(raw_line)
    except Exception as e:
        queue.put(e)
    finally:
        queue.put(None)


def _stderr_reader_thread(pipe, buf: list):
    """Drain subprocess stderr in a background thread.

    Windows-critical: if stderr fills its OS pipe buffer (~4-64KB) and nobody
    reads it, the child BLOCKS on the write, stops producing stdout, and the
    whole turn wedges until something kills it — the classic pipe-buffer
    deadlock. The Claude lane fixed this long ago with a dedicated stderr reader
    (`claude_subprocess._stderr_reader`); the Codex lane was missing it. Cap the
    buffer so a long, chatty run can't grow memory without bound.
    """
    try:
        for raw_line in iter(pipe.readline, b""):
            buf.append(raw_line)
            if len(buf) > 5000:
                del buf[:2500]
    except Exception:
        pass


def _tree_kill(proc) -> None:
    """Kill the Codex process AND its MCP child tree, then reap the handle.

    Windows-critical: killing the parent `codex` process alone does NOT cascade
    to the node MCP servers it spawned — they orphan and pile up (the same stray
    `node` leak that starved the box on the Claude path). `taskkill /T /F` by the
    parent PID takes the whole subtree down in one shot. Mirrors
    `claude_subprocess.kill()`. Best-effort — never raises.
    """
    if proc is not None and close_owned_process(proc):
        return
    if not proc or proc.poll() is not None:
        return
    pid = proc.pid
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10,
        )
    except Exception as e:
        log.debug("Codex tree-kill (taskkill /T /F) failed for pid %s: %s", pid, e)
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


async def stream_codex(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: list[dict] | None = None,
    model: str | None = None,
    mode_rules: str = "",
    skill_context: str = "",
    cancel_event: asyncio.Event | None = None,
    bypass_approvals: bool = False,
) -> AsyncIterator[dict]:
    """Spawn Codex CLI and yield parsed JSONL events."""
    model = normalize_codex_model(model)
    resolved_model = model or get_codex_default_model_id() or "default"
    yield {
        "type": "meta",
        "provider": "codex",
        "requested_model": model or resolved_model,
        "actual_model": resolved_model,
    }

    # Build the full prompt with context
    prompt_parts = []

    # Identity prompt
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        try:
            identity_prompt = prompt_file.read_text(encoding="utf-8")
            if identity_prompt.strip():
                prompt_parts.append(
                    f"[IDENTITY INSTRUCTIONS — {identity}]\n"
                    f"{identity_prompt}\n"
                    f"[/IDENTITY INSTRUCTIONS]"
                )
        except Exception as e:
            log.warning("Failed to read prompt file %s: %s", prompt_file, e)

    if skill_context:
        prompt_parts.append(
            "[AUTO-LOADED LOCAL SKILLS]\n"
            f"{skill_context}\n"
            "[Apply the skill guidance above when relevant to this turn.]"
        )

    if mode_rules:
        prompt_parts.append(f"[MODE RULES]\n{mode_rules}\n[/MODE RULES]")

    if orientation_context:
        prompt_parts.append(f"[ORIENTATION]\n{orientation_context}\n[/ORIENTATION]")

    # Recent conversation history
    if db_messages:
        history_lines = []
        for msg in db_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[Used tool: {block.get('name', '?')}]")
                        elif block.get("type") == "tool_result":
                            result_text = str(block.get("content", ""))
                            if len(result_text) > 500:
                                result_text = result_text[:500] + "..."
                            text_parts.append(f"[Tool result: {result_text}]")
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(t for t in text_parts if t)

            if not content:
                continue

            speaker = "Owner" if role == "user" else "You"
            if len(content) > 1500:
                content = content[:1500] + "\n[...truncated...]"
            history_lines.append(f"{speaker}: {content}")

        if history_lines:
            prompt_parts.append(
                "[RECENT CONVERSATION HISTORY]\n"
                + "\n\n".join(history_lines)
                + "\n[/RECENT CONVERSATION HISTORY]"
            )

    turn_packet = build_turn_packet(identity, PROMPTS_DIR)
    if turn_packet:
        prompt_parts.append(turn_packet)

    # Current message
    prompt_parts.append(f"[CURRENT MESSAGE FROM OWNER]\n{message}")

    full_prompt = "\n\n".join(prompt_parts)

    # Build command
    codex_cmd = find_codex_executable()
    if not codex_cmd:
        yield {"type": "error", "message": "Codex CLI not found. Install with: npm install -g @openai/codex"}
        return

    cmd = [
        codex_cmd, "exec",
        "--json",
        "--ephemeral",
        # Anam supplies its own complete MCP map. Loading the global config too
        # can merge two transports under one server name and abort at startup.
        "--ignore-user-config",
        "--skip-git-repo-check",
        "-s", "read-only",
    ]
    if bypass_approvals:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    if model:
        cmd += ["-m", model]
    for override in build_codex_mcp_overrides(
        identity=identity, conversation_id=conversation_id,
        bypass_approvals=bypass_approvals,
    ):
        cmd += ["-c", override]

    log.info(
        "Spawning Codex for %s (model=%s, prompt_chars=%d)",
        identity, model or "default", len(full_prompt),
    )

    # Strip CLAUDECODE env var to avoid nesting issues
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)

    # Drain stderr before writing the prompt. Config failures can make Codex
    # exit immediately, and the real error must survive the resulting EPIPE.
    proc = None
    stderr_buf: list[bytes] = []
    stderr_thread = None
    try:
        proc = owned_popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stderr_thread = threading.Thread(
            target=_stderr_reader_thread,
            args=(proc.stderr, stderr_buf),
            daemon=True,
            name=f"codex-stderr-{identity}",
        )
        stderr_thread.start()
        proc.stdin.write(full_prompt.encode("utf-8"))
        proc.stdin.close()
    except Exception as e:
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                _tree_kill(proc)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1)
        stderr_text = b"".join(stderr_buf).decode("utf-8", errors="replace").strip()
        if stderr_text:
            message = f"Codex exited before accepting the prompt:\n{stderr_text[:2000]}"
        else:
            message = f"Failed to spawn Codex: {e}"
        lowered = message.lower()
        if "approval" in lowered or "permission" in lowered:
            yield {
                "type": "approval_required",
                "provider": "codex",
                "message": message,
                "bypass_approvals": bypass_approvals,
            }
        else:
            yield {"type": "error", "message": message}
        return

    # Read stdout in background thread
    line_queue: Queue = Queue()
    reader = threading.Thread(target=_reader_thread, args=(proc.stdout, line_queue), daemon=True)
    reader.start()

    # Drain stderr in its OWN thread — without this the pipe buffer fills and the
    # whole turn deadlocks on Windows (the missing-drain bug behind Codex hangs).
    loop = asyncio.get_running_loop()
    thread_id = None
    full_text = []
    sent_stream_end = False
    terminal_error_seen = False
    spawn_started = loop.time()
    last_event_time = loop.time()   # bumped ONLY on real events — drives the stall watchdog
    last_keepalive = loop.time()    # separate clock so keepalives don't mask a real stall
    first_event_logged = False
    _KEEPALIVE_INTERVAL = 10

    try:
        while True:
            try:
                raw_line = await loop.run_in_executor(
                    None, lambda: line_queue.get(timeout=0.1)
                )
            except Empty:
                if proc.poll() is not None and line_queue.empty():
                    break
                now = loop.time()
                # Caller asked us to stop (turn superseded / shutdown) — honor it.
                # The loop never checked cancel_event before, so a cancelled turn
                # ran to completion anyway; now it stops promptly and cleanly.
                if cancel_event is not None and cancel_event.is_set():
                    log.info("Codex stream cancelled for %s — terminating", identity)
                    _tree_kill(proc)
                    break
                # Stall watchdog — fail fast on a genuinely wedged run.
                if now - last_event_time >= _STALL_TIMEOUT:
                    log.warning(
                        "Codex stalled for %s (%.0fs with no output) — terminating",
                        identity, now - last_event_time,
                    )
                    _tree_kill(proc)
                    yield {
                        "type": "error",
                        "message": f"Codex produced no output for {_STALL_TIMEOUT}s — terminated (stalled)",
                    }
                    sent_stream_end = True  # suppress the finally's terminal emit
                    break
                if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                    yield {"type": "keepalive"}
                    last_keepalive = now
                continue

            if raw_line is None:
                break

            if isinstance(raw_line, Exception):
                message = str(raw_line)
                lowered = message.lower()
                if "approval" in lowered or "permission" in lowered:
                    yield {
                        "type": "approval_required",
                        "provider": "codex",
                        "message": message,
                        "bypass_approvals": bypass_approvals,
                    }
                else:
                    yield {"type": "error", "message": message}
                break

            last_event_time = loop.time()

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Non-JSON line from Codex: %s", line[:200])
                continue

            event_type = event.get("type", "")

            if not first_event_logged:
                first_event_logged = True
                first_event_ms = (loop.time() - spawn_started) * 1000
                log.info("Codex first event for %s after %.0fms", identity, first_event_ms)
                yield {"type": "meta", "first_event_ms": first_event_ms}

            # Thread started -- capture session ID
            if event_type == "thread.started":
                thread_id = event.get("thread_id", "")
                continue

            # Turn started
            if event_type == "turn.started":
                continue

            # Item started -- tool execution beginning
            if event_type == "item.started":
                item = event.get("item", {})
                item_type = item.get("type", "")

                if item_type == "command_execution":
                    command = item.get("command", "")
                    yield {
                        "type": "tool_use_start",
                        "tool_name": "shell",
                        "tool_id": item.get("id", ""),
                        "input": {},
                    }
                    yield {
                        "type": "tool_input",
                        "tool_id": item.get("id", ""),
                        "tool_name": "shell",
                        "input": {"command": command},
                    }
                continue

            # Item completed -- text or tool result
            if event_type == "item.completed":
                item = event.get("item", {})
                item_type = item.get("type", "")

                if item_type == "agent_message":
                    text = item.get("text", "")
                    if text:
                        full_text.append(text)
                        yield {"type": "stream_delta", "delta": text}

                elif item_type == "command_execution":
                    output = item.get("aggregated_output", "")
                    exit_code = item.get("exit_code")
                    status = "completed" if exit_code == 0 else "error"

                    # Truncate large outputs for the browser UI.
                    display_output = output
                    if len(display_output) > 2000:
                        display_output = display_output[:2000] + "\n[...truncated...]"

                    yield {
                        "type": "tool_result",
                        "tool_use_id": item.get("id", ""),
                        "tool_name": "shell",
                        "status": status,
                        "input": {"command": item.get("command", "")},
                        "content": display_output,
                    }
                    yield {"type": "content_block_stop"}

                elif item_type == "error":
                    error_msg = item.get("message", "Unknown error")
                    log.warning("Codex item error: %s", error_msg)
                    # Don't yield as error -- these are often non-fatal warnings

                continue

            # Turn completed -- we're done
            if event_type == "turn.completed":
                usage = event.get("usage", {})
                log.info(
                    "Codex turn completed for %s (input_tokens=%s, output_tokens=%s)",
                    identity,
                    usage.get("input_tokens", "?"),
                    usage.get("output_tokens", "?"),
                )
                total_ms = (loop.time() - spawn_started) * 1000


                try:
                    from services.usage_tracker import record_usage
                    await record_usage(
                        identity=identity,
                        conversation_id=conversation_id,
                        model=resolved_model,
                        usage={
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read_input_tokens": usage.get(
                                "cached_input_tokens"
                            ),
                        },
                        duration_ms=int(total_ms),
                        source="codex",
                    )
                except Exception as exc:
                    log.debug("Codex usage record skipped: %s", exc)
                log.info(
                    "Codex stream complete for %s in %.0fms (%d chars)",
                    identity, total_ms, sum(len(t) for t in full_text),
                )
                yield {
                    "type": "stream_end",
                    "full_content": "".join(full_text),
                    "session_id": thread_id,
                }
                sent_stream_end = True
                continue

            # Failure / error / abort — run through approval classifier
            if event_type in ("turn.failed", "error", "turn.aborted"):
                terminal_error_seen = True
                message = (
                    event.get("message")
                    or event.get("detail")
                    or event.get("error", {}).get("message")
                    or json.dumps(event, ensure_ascii=False)
                )
                log.error("Codex %s for %s: %s", event_type, identity, message)
                lowered = str(message).lower()
                _APPROVAL_KEYWORDS = (
                    "approval", "permission", "not permitted",
                    "not allowed", "denied", "forbidden",
                )
                if any(kw in lowered for kw in _APPROVAL_KEYWORDS):
                    yield {
                        "type": "approval_required",
                        "provider": "codex",
                        "message": str(message),
                        "bypass_approvals": bypass_approvals,
                    }
                else:
                    yield {"type": "error", "message": f"Codex error: {message}"}
                continue

            log.debug("Unhandled Codex event type: %s", event_type)

    except Exception as e:
        log.exception("Error reading Codex subprocess output")
        message = str(e)
        lowered = message.lower()
        _APPROVAL_KEYWORDS = (
            "approval", "permission", "not permitted",
            "not allowed", "denied", "forbidden",
        )
        if any(kw in lowered for kw in _APPROVAL_KEYWORDS):
            yield {
                "type": "approval_required",
                "provider": "codex",
                "message": message,
                "bypass_approvals": bypass_approvals,
            }
        else:
            yield {"type": "error", "message": message}

    finally:
        # Take the whole tree down (codex + its MCP node children) rather than
        # leaking orphans — and don't block 30s on a possibly-wedged process.
        if proc.poll() is None:
            _tree_kill(proc)
        else:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

        # stderr was drained continuously into stderr_buf; flush the thread and
        # read from there (the pipe itself may already be closed/empty).
        try:
            stderr_thread.join(timeout=1)
        except Exception:
            pass
        stderr_data = b"".join(stderr_buf)
        if stderr_data:
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            if stderr_text:
                log.warning("Codex stderr (exit %s): %s", proc.returncode, stderr_text[:2000])

        if not sent_stream_end:
            content = "".join(full_text)
            if content or proc.returncode == 0:
                yield {
                    "type": "stream_end",
                    "full_content": content,
                    "session_id": thread_id,
                }
            elif proc.returncode != 0 and not terminal_error_seen:
                err_detail = f"Codex process exited with code {proc.returncode}"
                if stderr_data:
                    err_detail += f"\n{stderr_data.decode('utf-8', errors='replace').strip()[:500]}"
                yield {"type": "error", "message": err_detail}
