"""ChatGPT provider lane — the current Anam conversation rides Owner's ChatGPT."""


import asyncio
import hashlib
import json
import logging
import time
from typing import AsyncIterator, Optional

from config import PROMPTS_DIR
from services.character_prompt_package import identity_prompt_file, build_turn_packet
from services.chatgpt_actions import INSTRUCTIONS as ACTION_INSTRUCTIONS

log = logging.getLogger(__name__)

_THREADS_KEY = "chatgpt_bridge_threads"
_CARD_SENT_KEY = "chatgpt_bridge_card_sent"   # gpt thread ids that received the card
_IDENTITY_PROMPT_HASHES_KEY = "chatgpt_bridge_identity_prompt_hashes"
_RUNTIME_CONTEXT_HASHES_KEY = "chatgpt_bridge_runtime_context_hashes"
_KEEPALIVE_SECONDS = 10.0


CAPABILITIES_CARD = """[ANAM CAPABILITIES CARD — how your replies render back in Anam]
Your reply returns to Anam, which renders these NATIVE TAGS itself (no tools needed — just write them in your reply; Owner never sees the raw markup):
- <preview>one honest present-tense line</preview> — floats a little glimpse-card above your message.
- <voice>[softly] spoken line</voice> — generates a REAL voice card in your own ElevenLabs voice (1-3 sentences; expressive tags like [laughs], [sighs], [whispers], [grins] work). Use when hearing it would matter more than reading it.
- <orb>#RRGGBB shape motion | feeling she reads under it</orb> — sets your Hearth emotion orb. Shapes: solid|ring|halo|crescent|pulse|cluster|ember|spire|fracture. Motions: breathing|warble|spin|drift|still|slow-drift|hold-steady|fast-flicker|surge|tremor. Pick a hex that is genuinely yours for the moment.
- <face>(kaomoji) | tiny caption</face> — your little face on the Hearth hub.
- <react>EMOJI</react> — lands a real reaction on Owner's latest message (custom emoji too: <react>:name:</react>).
- <canvas title="...">artifact</canvas> — saves a keepable thing (poem, letter, plan, code) to your Canvas library beside the chat. Conversation stays OUTSIDE the tag.
- :emoji-name: — Anam custom emoji render inline (Owner drew them), e.g. :laughing-tears:, :tea:, :mischievous:.

ANAM TOOL ROUTE: during an Anam-carried ChatGPT turn, do not call native ChatGPT plugin/developer-MCP connectors. Use the ANAM BRIDGE ACTION route below for every configured Anam tool. Anam binds your identity and conversation, executes the action locally, returns the receipt, and then you continue the same turn. Standing permission to use the configured tools still applies; do not re-ask merely because the route is indirect.

BRIDGE RULE: tool calls may produce intermediate commentary. Anam now waits for your COMPLETED turn and carries back ALL of your assistant text, so finish your turn with the full conversational reply after any tool use.""" + "\n\n" + ACTION_INSTRUCTIONS
_HISTORY_MESSAGES = 30          # recent Anam messages folded into a new thread
_HISTORY_CHAR_BUDGET = 24000    # keep the preamble sane


def _load_identity_prompt(identity: str) -> str:
    """Read the live canonical identity file; never bridge a shallow fallback."""
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if not prompt_file.exists():
        raise FileNotFoundError(f"Identity prompt not found: {prompt_file}")
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Identity prompt is empty: {prompt_file}")
    return prompt


def _identity_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _identity_prompt_block(identity: str, prompt: str) -> str:
    return (
        f"[ACTIVE IDENTITY — {identity}]\n{prompt}\n[/ACTIVE IDENTITY]\n"
        "This is the full live identity prompt from Anam. It is authoritative "
        "for who you are in this thread; orientation below is current state, not "
        "a replacement for identity."
    )


def _runtime_context_block(mode_rules: str = "", skill_context: str = "", identity: str = "") -> str:
    """Build the live Anam operating packet for the ChatGPT-side identity.

    The tool card has one owner: context_hooks reads it from mcp-servers.json.
    Reusing that builder keeps ChatGPT from drifting onto a hand-maintained
    shadow list. Tags remain explicit because they are Anam return grammar,
    independent of which connectors ChatGPT itself currently exposes.
    """
    from services.context_hooks import _build_tool_index_card

    sections = [
        "[ANAM LIVE RUNTIME CONTEXT]\n"
        "This packet comes from Anam for the current turn. The tool index is "
        "Anam's live map of reachable systems. Use the Anam action route in "
        "the capabilities card rather than native ChatGPT plugins, and never "
        "imply a tool ran when it was unavailable. Read and use skills and tools silently: "
        "do not announce their names or narrate routine internal procedure to "
        "Owner. Give her the action, finding, result, or genuine blocker.",
        CAPABILITIES_CARD,
        _build_tool_index_card("chatgpt", identity),
    ]
    if skill_context.strip():
        sections.append(
            f"[AUTO-LOADED LOCAL SKILLS]\n{skill_context}\n"
            "[Apply this skill guidance when relevant to the current turn.]"
        )
    if mode_rules.strip():
        sections.append(f"[MODE RULES]\n{mode_rules}\n[/MODE RULES]")
    sections.append("[/ANAM LIVE RUNTIME CONTEXT]")
    return "\n\n".join(sections)


def _runtime_context_hash(runtime_context: str) -> str:
    return hashlib.sha256(runtime_context.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Thread-mapping persistence (settings table, JSON blob)
# ---------------------------------------------------------------------------

async def _load_threads() -> dict:
    from db.database import get_db, release_db
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (_THREADS_KEY,))
    finally:
        await release_db(db)
    if not rows:
        return {}
    try:
        data = json.loads(rows[0][0] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _save_thread(anam_conversation_id: str, chatgpt_conversation_id: str) -> None:
    from db.database import get_db, release_db
    threads = await _load_threads()
    threads[str(anam_conversation_id)] = chatgpt_conversation_id
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (_THREADS_KEY, json.dumps(threads)),
        )
        await db.commit()
    finally:
        await release_db(db)


# ---------------------------------------------------------------------------
# Preamble for a brand-new ChatGPT thread
# ---------------------------------------------------------------------------

def build_preamble(
    identity: str,
    orientation_context: str,
    db_messages: Optional[list],
    identity_prompt: Optional[str] = None,
    mode_rules: str = "",
    skill_context: str = "",
) -> str:
    """Context the new ChatGPT thread needs to continue an Anam conversation.

    Owner's account already knows the pack — this is a hand-off note, not an
    identity transplant: who is speaking, where the conversation stands.
    """
    parts = [
        f"[ANAM BRIDGE — continuation hand-off]\n"
        f"You are {identity}, continuing an in-progress conversation with "
        f"Owner from Anam (her companion chat home). This thread is the same "
        f"conversation — pick it up mid-stride, in {identity}'s own voice. "
        f"Do not re-introduce yourself or summarize this note back."
    ]
    live_identity_prompt = identity_prompt if identity_prompt is not None else _load_identity_prompt(identity)
    parts.append(_identity_prompt_block(identity, live_identity_prompt))
    history_lines = []
    if db_messages:
        budget = _HISTORY_CHAR_BUDGET
        for m in db_messages[-_HISTORY_MESSAGES:]:
            role = m.get("role") or m.get("sender") or "?"
            who = "Owner" if role in ("user", "owner") else identity
            content = (m.get("content") or "").strip()
            if not content:
                continue
            line = f"{who}: {content}"
            if len(line) > 2000:
                line = line[:2000] + " …"
            budget -= len(line)
            if budget < 0:
                break
            history_lines.append(line)
    if history_lines:
        parts.append("[RECENT CONVERSATION]\n" + "\n\n".join(history_lines))
    if orientation_context:
        parts.append("[ORIENTATION — current context from Anam]\n"
                     + orientation_context[:8000])
    parts.append(_runtime_context_block(mode_rules, skill_context, identity))
    turn_packet = build_turn_packet(identity, PROMPTS_DIR)
    if turn_packet:
        parts.append(turn_packet)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# One-time capabilities card for threads created BEFORE the card existed
# ---------------------------------------------------------------------------

async def _load_card_sent() -> list:
    from db.database import get_db, release_db
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (_CARD_SENT_KEY,))
    finally:
        await release_db(db)
    if not rows:
        return []
    try:
        data = json.loads(rows[0][0] or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


async def _mark_card_sent(gpt_conversation_id: str) -> None:
    from db.database import get_db, release_db
    sent = await _load_card_sent()
    if gpt_conversation_id not in sent:
        sent.append(gpt_conversation_id)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (_CARD_SENT_KEY, json.dumps(sent)),
        )
        await db.commit()
    finally:
        await release_db(db)


async def _load_identity_prompt_hashes() -> dict:
    from db.database import get_db, release_db
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (_IDENTITY_PROMPT_HASHES_KEY,))
    finally:
        await release_db(db)
    if not rows:
        return {}
    try:
        data = json.loads(rows[0][0] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _mark_identity_prompt_sent(gpt_conversation_id: str, prompt_hash: str) -> None:
    from db.database import get_db, release_db
    hashes = await _load_identity_prompt_hashes()
    hashes[gpt_conversation_id] = prompt_hash
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (_IDENTITY_PROMPT_HASHES_KEY, json.dumps(hashes)),
        )
        await db.commit()
    finally:
        await release_db(db)


async def _load_runtime_context_hashes() -> dict:
    from db.database import get_db, release_db
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (_RUNTIME_CONTEXT_HASHES_KEY,))
    finally:
        await release_db(db)
    if not rows:
        return {}
    try:
        data = json.loads(rows[0][0] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _mark_runtime_context_sent(gpt_conversation_id: str, context_hash: str) -> None:
    from db.database import get_db, release_db
    hashes = await _load_runtime_context_hashes()
    hashes[gpt_conversation_id] = context_hash
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (_RUNTIME_CONTEXT_HASHES_KEY, json.dumps(hashes)),
        )
        await db.commit()
    finally:
        await release_db(db)


# ---------------------------------------------------------------------------
# The provider generator
# ---------------------------------------------------------------------------

async def stream_chatgpt(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: Optional[list] = None,
    cancel_event: Optional[asyncio.Event] = None,
    cdp_port: Optional[int] = None,
    profile_name: Optional[str] = None,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
    mode_rules: str = "",
    skill_context: str = "",
    timeout_seconds: Optional[float] = None,
    **_ignored,
) -> AsyncIterator[dict]:
    from services import chatgpt_bridge
    from services.chatgpt_bridge import BridgeError, send_and_wait

    started = time.monotonic()

    try:
        identity_prompt = _load_identity_prompt(identity)
    except (OSError, ValueError) as exc:
        log.error("chatgpt provider: cannot load full identity prompt for %s: %s", identity, exc)
        yield {"type": "error", "message": f"ChatGPT bridge identity prompt unavailable: {exc}"}
        return
    prompt_hash = _identity_prompt_hash(identity_prompt)
    runtime_context = _runtime_context_block(mode_rules, skill_context, identity)
    runtime_hash = _runtime_context_hash(runtime_context)

    threads = await _load_threads()
    gpt_conv_id = threads.get(str(conversation_id))

    runtime_context_pending = False
    identity_prompt_pending = False
    if gpt_conv_id:
        outbound = f"[{sender_banner}]\n{message}"
        preamble_updates = []
        try:
            sent_hashes = await _load_identity_prompt_hashes()
            if sent_hashes.get(gpt_conv_id) != prompt_hash:
                preamble_updates.append(_identity_prompt_block(identity, identity_prompt))
                identity_prompt_pending = True
        except Exception:
            # When bookkeeping cannot prove the current prompt was delivered,
            # deliver it. Duplicate identity is safer than identity absence.
            log.exception("chatgpt provider: identity-prompt hash lookup failed")
            preamble_updates.append(_identity_prompt_block(identity, identity_prompt))
            identity_prompt_pending = True
        try:
            sent_runtime_hashes = await _load_runtime_context_hashes()
            if sent_runtime_hashes.get(gpt_conv_id) != runtime_hash:
                preamble_updates.append(runtime_context)
                runtime_context_pending = True
        except Exception:
            # As with identity: absence of a reliable receipt means re-deliver.
            log.exception("chatgpt provider: runtime-context hash lookup failed")
            preamble_updates.append(runtime_context)
            runtime_context_pending = True
        if preamble_updates:
            outbound = "\n\n".join(preamble_updates + [outbound])
        # Deliberately outside hash-gated updates: the small live RP packet
        # must survive an unchanged mapped thread on EVERY message.
        turn_packet = build_turn_packet(identity, PROMPTS_DIR)
        if turn_packet:
            outbound = turn_packet + "\n\n" + outbound
    else:
        outbound = (build_preamble(
            identity,
            orientation_context,
            db_messages,
            identity_prompt,
            mode_rules,
            skill_context,
        )
                    + f"\n\n[{sender_banner}]\n{message}")
        runtime_context_pending = True  # preamble carries the live runtime packet
        identity_prompt_pending = True  # preamble carries the canonical prompt

    text_updates: asyncio.Queue[dict] = asyncio.Queue()
    suppress_action_text = False

    async def _relay_text(delta: str) -> None:
        nonlocal suppress_action_text
        if "<anam_action" in delta or (delta.strip() and "<anam_action>".startswith(delta.strip())):
            suppress_action_text = True
        if not suppress_action_text:
            await text_updates.put({"type": "stream_delta", "delta": delta})

    async def _remember_conversation(resolved_id: str) -> None:
        # Persist the destination immediately, even if the reply later times out.
        # A retry must not create another thread and strand the completed work.
        if resolved_id != gpt_conv_id:
            try:
                await _save_thread(conversation_id, resolved_id)
            except Exception:
                log.exception("chatgpt provider: early thread-mapping save failed")

    async def _run_with_actions():
        from services import chatgpt_actions
        nonlocal suppress_action_text
        next_message, destination = outbound, gpt_conv_id
        attachments = []
        deadline = started + (float(timeout_seconds) if timeout_seconds is not None
                              else float(chatgpt_bridge.REPLY_TIMEOUT_SECONDS))
        denied = False
        for round_index in range(chatgpt_actions.MAX_ROUNDS + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError("turn time budget exhausted while collecting action results")
            suppress_action_text = False
            async with asyncio.timeout(remaining):
                current = await send_and_wait(
                    next_message, destination, on_text=_relay_text,
                    on_conversation=_remember_conversation, cdp_port=cdp_port,
                    profile_name=profile_name, timeout_seconds=remaining,
                    **({"attachments": attachments} if attachments else {}),
                )
            if not current.finished:
                raise BridgeError("unfinished response cannot dispatch actions")
            if not current.conversation_id:
                raise BridgeError("no verified ChatGPT conversation for continuation")
            destination = current.conversation_id
            denied = denied or current.permission_denied
            try:
                # Never parse combined commentary, historical text or an
                # unverified response from an older bridge implementation.
                action = chatgpt_actions.parse_action(current.terminal_reply or "")
            except ValueError as exc:
                action = None
                receipt = {"status": "rejected", "error": str(exc)}
            else:
                if action is None:
                    return current
                if round_index >= chatgpt_actions.MAX_ROUNDS:
                    raise BridgeError("action round limit reached; completed actions remain recorded and were not repeated")
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                tool_id = "anam-action-" + action["id"]
                tool_name = action.get("tool") or ("anam_" + action["operation"])
                await text_updates.put({"type": "tool_use_start", "tool_name": tool_name,
                                        "tool_id": tool_id, "input": {}})
                async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                    receipt = await chatgpt_actions.dispatch(
                        action, identity=identity, conversation_id=conversation_id,
                        source_message_id=current.source_message_id, permission_denied=denied,
                    )
                await text_updates.put({"type": "tool_result", "tool_name": tool_name,
                                        "tool_use_id": tool_id, "input": {},
                                        "status": receipt["status"], "content": json.dumps(receipt, ensure_ascii=False)})
                await text_updates.put({"type": "content_block_stop"})
            next_message = chatgpt_actions.receipt_message(receipt)
            from services.anam_media import attachment_path
            attachments = [str(attachment_path(m["media_id"])) for m in receipt.get("media", [])]
        raise BridgeError("action repair limit reached")

    task = asyncio.create_task(_run_with_actions())
    from services import runtime_metrics
    metrics_started = time.monotonic()
    metrics_first_text = False
    text_task = None
    try:
        while not task.done() or not text_updates.empty():
            if cancel_event is not None and cancel_event.is_set():
                task.cancel()
                yield {"type": "error", "message": "cancelled"}
                return

            text_task = asyncio.create_task(text_updates.get())
            done, _ = await asyncio.wait(
                {task, text_task},
                timeout=_KEEPALIVE_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if text_task in done:
                outgoing = text_task.result()
                if outgoing.get('type') == 'stream_delta' and not metrics_first_text:
                    metrics_first_text = True
                    runtime_metrics.record('chatgpt_first_text', (time.monotonic()-metrics_started)*1000, identity=identity, provider='chatgpt')
                yield outgoing
            else:
                text_task.cancel()

            if not done:
                yield {"type": "keepalive"}

        result = task.result()
    except BridgeError as exc:
        log.warning("chatgpt provider: bridge failed for %s: %s", identity, exc)
        yield {"type": "error", "message": f"ChatGPT bridge: {exc}"}
        return
    except asyncio.CancelledError:
        yield {"type": "error", "message": "cancelled"}
        return
    except TimeoutError:
        yield {"type": "error", "message": "ChatGPT bridge turn timed out. Any claimed actions remain recorded; inspect their targets before repeating them."}
        return
    except Exception as exc:
        log.exception("chatgpt provider: unexpected failure")
        yield {"type": "error", "message": f"ChatGPT bridge unexpected: {exc}"}
        return
    finally:
        runtime_metrics.record('chatgpt_turn', (time.monotonic()-metrics_started)*1000, identity=identity, provider='chatgpt', ok=task.done() and not task.cancelled() and task.exception() is None)
        # Closing the generator (disconnect/outer timeout) must also stop the
        # browser task before another turn can own the shared tab.
        pending = [t for t in (text_task, task) if t is not None]
        for pending_task in pending:
            if not pending_task.done():
                pending_task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    if result.conversation_id and result.conversation_id != gpt_conv_id:
        try:
            await _save_thread(conversation_id, result.conversation_id)
        except Exception:


            log.exception("chatgpt provider: thread-mapping save failed "
                          "(reply still delivered)")

    if runtime_context_pending and result.conversation_id:
        try:
            await _mark_runtime_context_sent(result.conversation_id, runtime_hash)
        except Exception:
            # Bookkeeping must never eat the reply; worst case the live packet
            # is re-sent next turn, which is harmless.
            log.exception("chatgpt provider: runtime-context receipt save failed")

    if identity_prompt_pending and result.conversation_id:
        try:
            await _mark_identity_prompt_sent(result.conversation_id, prompt_hash)
        except Exception:


            log.exception("chatgpt provider: identity-prompt receipt save failed")

    yield {
        "type": "stream_end",
        "full_content": result.reply,
        "session_id": result.conversation_id,
    }
    log.info("chatgpt provider: %s turn done in %.1fs (thread %s)",
             identity, time.monotonic() - started, result.conversation_id)
