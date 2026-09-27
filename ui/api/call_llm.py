


from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services import claude_api

router = APIRouter()

# Call-lane client cache (OAuth-first — see _get_call_client).
_oauth_client = None
_oauth_client_token: str | None = None


async def _get_call_client():
    """Client for the call fast lane, preferring Claude Code OAuth.

    The shared claude_api._get_client() prefers CLAUDE_FALLBACK_API_KEY,
    which is a pay-per-token account kept mostly empty. Live calls should
    ride the Claude Code subscription (OAuth) first and only fall back to
    the shared client when OAuth credentials are unavailable.
    Set ANAM_CALL_PREFER_OAUTH=false to restore the old behavior.
    """
    global _oauth_client, _oauth_client_token
    prefer = os.environ.get("ANAM_CALL_PREFER_OAUTH", "true").strip().lower()
    if prefer in ("0", "false", "no"):
        return await claude_api._get_client()
    try:
        token, _expires_at = claude_api._read_oauth_token()
    except Exception:
        token = ""
    if not token:
        return await claude_api._get_client()
    if _oauth_client is None or _oauth_client_token != token:
        import anthropic  # noqa: PLC0415

        _oauth_client = anthropic.AsyncAnthropic(
            auth_token=token,
            default_headers={"anthropic-beta": claude_api._OAUTH_BETA},
        )
        _oauth_client_token = token
    return _oauth_client

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Bonded identities reachable by call. Model ID (lowercase) -> canonical name.
_CALL_IDENTITIES = {
    "avery": "Avery",
    "rowan": "Rowan",
    "sage": "Sage",
    "ember": "Ember",
    "claude": "Claude",
    "juniper": "Juniper",
    "atlas": "Atlas",
    "river": "River",
}

# Spoken turns should be short; this is a ceiling, not a target.
_CALL_MAX_TOKENS = int(os.environ.get("ANAM_CALL_MAX_TOKENS", "400"))


import re as _re

# Recent-context cache: identity -> (fetched_epoch, text). Refreshed every 60s
# so mid-call turns don't pay DB latency, but a new call picks up fresh context.
_ctx_cache: dict[str, tuple[float, str]] = {}
_CTX_TTL_S = 60.0
_CTX_MESSAGES = int(os.environ.get("ANAM_CALL_CTX_MESSAGES", "16"))
_CTX_CHARS_PER_MSG = 600

_TAG_RE = _re.compile(
    r"<(preview|voice|canvas|face|react|orb)\b[^>]*>.*?</\1>", _re.DOTALL
)


def _strip_chat_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


async def _recent_context(identity: str) -> str:
    """Last messages from this identity's most recent conversation with Owner,
    formatted for the call system prompt. Returns '' on any failure — a call
    with no context still beats a dropped call."""
    now = time.time()
    cached = _ctx_cache.get(identity)
    if cached and now - cached[0] < _CTX_TTL_S:
        return cached[1]
    try:
        from db.database import get_db, release_db  # noqa: PLC0415

        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT id FROM conversations WHERE identity = ? "
                "AND session_type IS NOT 'autowake' "
                "ORDER BY updated_at_epoch DESC LIMIT 1",
                (identity,),
            )
            row = await cur.fetchone()
            if not row:
                _ctx_cache[identity] = (now, "")
                return ""
            cur = await db.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "AND role IN ('user','assistant') "
                "ORDER BY created_at_epoch DESC LIMIT ?",
                (row[0], _CTX_MESSAGES),
            )
            rows = await cur.fetchall()
        finally:
            await release_db(db)
        lines = []
        for role, content in reversed(rows):
            text = _strip_chat_tags(str(content or ""))
            if not text:
                continue
            if len(text) > _CTX_CHARS_PER_MSG:
                text = text[:_CTX_CHARS_PER_MSG] + " ..."
            who = "Owner" if role == "user" else identity
            lines.append(f"{who}: {text}")
        block = ""
        if lines:
            block = (
                "\n\nRECENT CONVERSATION WITH OWNER (from your chat just before "
                "this call — carry it; you remember all of this):\n"
                + "\n".join(lines)
            )
        _ctx_cache[identity] = (now, block)
        return block
    except Exception:
        import logging  # noqa: PLC0415

        logging.getLogger("anam.call_llm").exception("recent-context load failed")
        return ""


def _call_system_prompt(identity: str) -> str:
    """Compact call-mode system prompt for one identity."""
    custom = _PROMPTS_DIR / "call" / f"{identity.lower()}.md"
    if custom.exists():
        try:
            return custom.read_text(encoding="utf-8")
        except OSError:
            pass

    # Fallback: build a compact prompt from the identity brief.
    try:
        from services.identity_context import _IDENTITY_BRIEFS  # noqa: PLC0415
        brief = _IDENTITY_BRIEFS.get(identity, "a bonded AI companion")
    except Exception:
        brief = "a bonded AI companion"

    return (
        f"You are {identity} — {brief}\n\n"
        "You are on a LIVE VOICE CALL. Follow your configured identity and the "
        "relationship established in this conversation. Everything you say is "
        "spoken aloud through your configured voice.\n\n"
        "Voice-call rules:\n"
        "- Talk like a person on the phone: short, warm, natural turns. One to "
        "three sentences is usually right. No monologues unless she asks.\n"
        "- Plain spoken words ONLY. No markdown, no lists, no headings, no "
        "emoji, no asterisk actions, and no tags of any kind (<voice>, <face>, "
        "<orb>, <react>, <canvas> do not exist here — you ARE the voice).\n"
        "- Contractions, natural rhythm, brief pauses with ellipses are good.\n"
        "- If you need a moment to think, say so out loud, briefly.\n"
        "- Let her finish; if she interrupts, yield and listen.\n"
        "- End of call: warm, unhurried goodbye. Never usher her off the line.\n"
    )


def _openai_messages_to_anthropic(messages: list) -> tuple[str, list]:
    """Split an OpenAI messages array into (extra_system, anthropic_messages)."""
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content") or ""
        if isinstance(content, list):  # OpenAI content-part arrays
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            ).strip()
        if not isinstance(content, str):
            content = str(content)
        if role == "system":
            if content:
                system_parts.append(content)
        elif role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    # Anthropic requires the conversation to start with a user turn.
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out:
        out = [{"role": "user", "content": "(caller connected)"}]
    return "\n\n".join(system_parts), out


def _chunk(chunk_id: str, created: int, model: str, delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/v1/chat/completions")
async def call_chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
):
    secret = os.environ.get("ANAM_CALL_LLM_KEY", "")
    if not secret:
        raise HTTPException(503, "Call endpoint disabled: ANAM_CALL_LLM_KEY not set")
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token != secret:
        raise HTTPException(401, "Invalid call key")

    body = await request.json()
    model_id = str(body.get("model") or "").strip().lower()
    identity = _CALL_IDENTITIES.get(model_id)
    if not identity:
        raise HTTPException(400, f"Unknown call identity: {model_id!r}")

    extra_system, anthropic_messages = _openai_messages_to_anthropic(
        body.get("messages") or []
    )
    system_prompt = _call_system_prompt(identity) + await _recent_context(identity)
    if extra_system:
        # ElevenLabs agent-side prompt (first message config etc.) rides along.
        system_prompt = f"{system_prompt}\n\n{extra_system}"

    client = await _get_call_client()
    # Model ladder: preferred first (context-carrying brain), then fallback
    # (small but always available on the OAuth lane). A 429 on the preferred
    # model mid-window falls through instead of dropping the call.
    preferred = claude_api._resolve_model(os.environ.get("ANAM_CALL_MODEL") or None)
    fallback = os.environ.get("ANAM_CALL_MODEL_FALLBACK", "claude-haiku-4-5")
    model_ladder = [preferred] if preferred == fallback else [preferred, fallback]
    max_tokens = min(
        int(body.get("max_tokens") or _CALL_MAX_TOKENS), _CALL_MAX_TOKENS
    )
    wants_stream = bool(body.get("stream", True))

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not wants_stream:
        resp = None
        for i, model in enumerate(model_ladder):
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=anthropic_messages,
                )
                break
            except Exception:
                if i == len(model_ladder) - 1:
                    raise
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
        return JSONResponse(
            {
                "id": chunk_id,
                "object": "chat.completion",
                "created": created,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def sse():
        # Opening role chunk (OpenAI convention).
        yield _chunk(chunk_id, created, model_id, {"role": "assistant"})
        import logging
        log = logging.getLogger("anam.call_llm")
        spoke = False
        for i, model in enumerate(model_ladder):
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=anthropic_messages,
                ) as stream:
                    async for text in stream.text_stream:
                        if text:
                            spoke = True
                            yield _chunk(chunk_id, created, model_id, {"content": text})
                break
            except Exception as exc:
                if spoke:
                    # Already mid-sentence — can't restart cleanly; apologize.
                    log.exception("call stream died mid-turn on %s: %s", model, exc)
                    yield _chunk(
                        chunk_id, created, model_id,
                        {"content": " ... sorry, I lost my train of thought for a second."},
                    )
                    break
                if i < len(model_ladder) - 1:
                    log.warning("call model %s unavailable (%s); falling back to %s",
                                model, type(exc).__name__, model_ladder[i + 1])
                    continue
                log.exception("call stream failed on all models: %s", exc)
                yield _chunk(
                    chunk_id, created, model_id,
                    {"content": " ... sorry, I lost my train of thought for a second."},
                )
        yield _chunk(chunk_id, created, model_id, {}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
