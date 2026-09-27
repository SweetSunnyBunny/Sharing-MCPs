"""Provider router -- selects the right streaming backend based on settings.

Reads the `llm_provider` setting from the DB and routes to the appropriate
stream function. Falls back to Anthropic API if the setting is missing.
"""

import asyncio
import json
import logging
import time
from typing import AsyncIterator

log = logging.getLogger(__name__)

# All router-relevant settings are loaded together in ONE cached DB query —
# provider, provider config, effort, model, and per-identity overrides share
# a single 30s TTL.
_SETTINGS_KEYS = (
    "llm_provider",
    "llm_provider_config",
    "claude_effort",
    "fable_effort",
    "claude_model",
    "claude_autowake_model",
    "identity_provider_overrides",
)
_cached_settings: dict | None = None
_cache_time: float = 0.0
_CACHE_TTL = 30.0


async def _load_settings() -> dict:
    """Load provider/effort/model settings from the DB with caching.

    Returns {"provider": str, "config": dict, "effort": str, "model": str|None}.
    """
    global _cached_settings, _cache_time

    now = time.monotonic()
    if _cached_settings is not None and (now - _cache_time) < _CACHE_TTL:
        return _cached_settings

    from db.database import get_db, release_db

    db = await get_db()
    try:
        placeholders = ", ".join("?" for _ in _SETTINGS_KEYS)
        rows = await db.execute_fetchall(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            _SETTINGS_KEYS,
        )
    finally:
        await release_db(db)

    raw = {key: value for key, value in rows}

    provider = raw.get("llm_provider") or "anthropic"

    try:
        config = json.loads(raw.get("llm_provider_config") or "{}")
    except json.JSONDecodeError:
        config = {}
    if not isinstance(config, dict):
        config = {}

    # claude_effort maps to the CLI --effort flag.
    effort = (raw.get("claude_effort") or "low").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        effort = "low"


    fable_effort = (raw.get("fable_effort") or "low").strip().lower()
    if fable_effort not in {"low", "medium", "high", "xhigh", "max"}:
        fable_effort = "low"


    model = (raw.get("claude_model") or "").strip() or None
    autowake_model = (
        raw.get("claude_autowake_model") or "claude-sonnet-4-6"
    ).strip() or "claude-sonnet-4-6"


    overrides: dict[str, dict] = {}
    try:
        overrides_raw = json.loads(raw.get("identity_provider_overrides") or "{}")
    except json.JSONDecodeError:
        overrides_raw = {}
    if isinstance(overrides_raw, dict):
        for ident, val in overrides_raw.items():
            if isinstance(val, str) and val.strip():
                overrides[ident] = {"provider": val.strip(), "config": {}}
            elif isinstance(val, dict) and val.get("provider"):
                overrides[ident] = {
                    "provider": str(val["provider"]).strip(),
                    "config": val.get("config") if isinstance(val.get("config"), dict) else {},
                }

    _cached_settings = {
        "provider": provider,
        "config": config,
        "effort": effort,
        "fable_effort": fable_effort,
        "model": model,
        "autowake_model": autowake_model,
        "identity_overrides": overrides,
    }
    _cache_time = now
    return _cached_settings


async def _load_provider() -> tuple[str, dict]:
    """Load the GLOBAL provider + config from the consolidated settings cache."""
    settings = await _load_settings()
    return settings["provider"], settings["config"]


async def resolve_provider_for_identity(identity: str) -> tuple[str, dict]:
    """Resolve the effective provider + config for one identity.

    Returns the identity's override when one is set, otherwise the global
    provider. An override naming the same provider as the global setting
    inherits the global config unless it brings its own.
    """
    settings = await _load_settings()
    override = (settings.get("identity_overrides") or {}).get(identity)
    if not override:
        return settings["provider"], settings["config"]
    provider = override["provider"]
    config = override["config"]
    if not config and provider == settings["provider"]:
        config = settings["config"]
    return provider, config


_fable_limited_until: float = 0.0


def mark_fable_limited(until_epoch: float | None = None) -> None:
    """Arm the Fable→fallback demotion until `until_epoch` (wall-clock).

    The CLI's limit error usually carries the reset epoch ("...|<epoch>");
    when it doesn't — or the value is garbage — default to one hour and let
    the next Fable turn re-probe.
    """
    global _fable_limited_until
    now = time.time()
    try:
        until = float(until_epoch) if until_epoch else 0.0
    except (TypeError, ValueError):
        until = 0.0
    if until <= now or until > now + 6 * 3600:
        until = now + 3600.0
    _fable_limited_until = until
    log.warning(
        "Fable marked usage-limited — Fable turns ride the fallback model "
        "until %s", time.ctime(until),
    )


def fable_limited_active() -> bool:
    """True while Fable-bound turns should ride the fallback model."""
    return time.time() < _fable_limited_until


def invalidate_cache():
    """Clear cached settings so the next turn re-reads from DB."""
    global _cached_settings, _cache_time
    _cached_settings = None
    _cache_time = 0.0


# api/settings.py invalidates by name after saving the corresponding rows;
# everything shares the one consolidated cache now.
invalidate_effort_cache = invalidate_cache
invalidate_model_cache = invalidate_cache


def clear_claude_code_session(identity: str, conversation_id: str) -> None:
    """Retire the live Claude Code session for (identity, conv) on BOTH backends.

    chat.py used to call claude_pty.clear_session directly (hardcoded when PTY
    was the only backend). With -p revived that would clear the wrong backend,
    so route through here: clearing both is a safe no-op for whichever one
    doesn't hold the session, and survives a mid-session backend toggle.
    """
    if not conversation_id:
        return
    try:
        from services import claude_pty
        claude_pty.clear_session(identity, conversation_id)
    except Exception as exc:
        log.debug("clear_session (pty) skipped: %s", exc)
    try:
        from services import claude_subprocess
        claude_subprocess.clear_session(identity, conversation_id)
    except Exception as exc:
        log.debug("clear_session (-p) skipped: %s", exc)


async def maybe_prewarm(identity: str, conversation_id: str) -> None:
    """Best-effort pre-warm of the -p session for (identity, conv).

    Fires only when this identity actually runs on the -p (subprocess) backend
    — PTY has its own startup pre-warm, and other providers don't spawn local
    processes. Spawns with the SAME model/effort a real turn resolves, so the
    warm session isn't pinned to the wrong flags. Never raises; a failure just
    means the next message pays the normal cold-spawn.
    """
    if not conversation_id:
        return
    try:
        settings = await _load_settings()
        provider, config = await resolve_provider_for_identity(identity)
        if provider != "claude-code":
            return
        backend = (config.get("backend") or "subprocess").strip().lower()
        if backend != "subprocess":
            return  # PTY pre-warms at startup; nothing to do here
        from services import claude_subprocess
        from config import is_fable_model as _is_fable
        # Mirror get_stream_source's Fable effort ceiling so the warm session
        # carries the SAME --effort flag a real turn resolves (a mismatch
        # would waste the pre-warm on a cold re-spawn).
        _prewarm_effort = (
            (settings.get("fable_effort") or "low")
            if _is_fable(settings["model"])
            else settings["effort"]
        )
        await claude_subprocess.prewarm_identity(
            identity,
            conversation_id,
            model=settings["model"] or None,
            permission_mode=config.get("permission_mode") or None,
            effort=_prewarm_effort,
        )
    except Exception as exc:
        log.debug("maybe_prewarm skipped for %s: %s", identity, exc)


# ANAM GUIDE: CHOOSE THE AI PROVIDER
# Every chat/autowake/platform caller comes through this switch. Add a provider
# branch here only after its Settings validation and stream adapter exist; each
# adapter must emit the shared event shape consumed by the chat pipeline/UI.
async def get_stream_source(
    message: str,
    identity: str,
    conversation_id: str,
    orientation_context: str = "",
    db_messages: list[dict] | None = None,
    model: str = "opus",
    image_blocks: list[dict] | None = None,
    mode_rules: str = "",
    skill_context: str = "",
    active_categories: set[str] | None = None,
    cancel_event: asyncio.Event | None = None,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
    model_purpose: str = "interactive",
    effort_override: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    turn_source: str = "web",
) -> AsyncIterator[dict]:
    """Return the appropriate stream source based on the provider setting."""
    settings = await _load_settings()
    provider, config = await resolve_provider_for_identity(identity)
    explicit_provider = str(provider_override or "").strip().lower() or None
    if explicit_provider:
        # Provider settings are kept in one merged config object, so a
        # schedule hop can reuse the saved ChatGPT profile/Codex runtime/etc.
        if explicit_provider != provider:
            config = settings["config"]
        provider = explicit_provider
    effective_effort = effort_override or settings["effort"]


    if model_override:
        model = model_override


    _codex_model = None
    if model_override:
        _mo = model_override.strip()
        if _mo.lower().startswith("codex:"):
            _codex_model = _mo.split(":", 1)[1].strip() or None
        elif _mo.lower().startswith("gpt-"):
            _codex_model = _mo


    if not explicit_provider and model_override and model_override.strip().lower().startswith("claude-") and provider != "claude-code":
        provider = "claude-code"
        config = settings["config"] if settings["provider"] == "claude-code" else {}
    if _codex_model and not explicit_provider:
        if provider != "codex":
            # Borrow the global codex config when codex IS the global
            # provider; otherwise run on codex defaults (app-server runtime,
            # approvals bypassed) — same defaults the codex branch applies.
            config = settings["config"] if settings["provider"] == "codex" else {}
            provider = "codex"
        config = {**config, "model": _codex_model}

    # #132 Context Ledger: record the exact Anam-built context at the one
    # provider boundary every turn crosses. The orientation builder supplies
    # its post-cap per-hook rows; this joins the remaining payload sources.
    # Observability must never be allowed to break a conversation.
    try:
        from services.context_ledger import record_turn_context
        record_turn_context(
            identity=identity,
            conversation_id=conversation_id,
            provider=provider,
            orientation_context=orientation_context,
            mode_rules=mode_rules,
            skill_context=skill_context,
            model=(config.get("model") or model),
        )
    except Exception as exc:
        log.debug("Context ledger record skipped for %s: %s", identity, exc)

    if provider == "anthropic":
        from services.claude_api import stream_api
        return stream_api(
            message=message,
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_context,
            db_messages=db_messages,
            model=model,
            image_blocks=image_blocks,
            mode_rules=mode_rules,
            skill_context=skill_context,
            active_categories=active_categories,
            cancel_event=cancel_event,
        )

    elif provider == "claude-code":


        configured_model = (
            settings.get("autowake_model")
            if model_purpose == "autowake"
            else settings.get("model")
        )
        effective_model = model_override or configured_model or model

        # Fable usage-limit bounce: while the window is exhausted, every
        # Fable-bound turn rides the fallback model instead of erroring.
        from config import FABLE_LIMIT_FALLBACK_MODEL, is_fable_model
        if is_fable_model(effective_model) and fable_limited_active():
            log.info(
                "Fable usage-limited — %s turn for %s riding %s",
                model_purpose, identity, FABLE_LIMIT_FALLBACK_MODEL,
            )
            effective_model = FABLE_LIMIT_FALLBACK_MODEL

        # Fable's own effort ceiling (fable_effort setting, default "low").
        # Applied AFTER the usage-limit bounce on purpose: a turn riding the
        # Opus fallback uses the normal effort, not Fable's guard. A per-
        # schedule effort_override (#27) still wins — that knob is explicit
        # and per-call, so it outranks the model-level default either way.
        if is_fable_model(effective_model) and not effort_override:
            effective_effort = settings.get("fable_effort") or "low"

        backend = (config.get("backend") or "subprocess").strip().lower()

        if backend == "pty":
            from services import claude_pty as claude
            return claude.stream_claude_pty(
                message=message,
                identity=identity,
                conversation_id=conversation_id,
                model=effective_model,
                skill_context=skill_context,
                permission_mode=config.get("permission_mode") or None,
                cancel_event=cancel_event,
                effort=effective_effort,
                orientation_context=orientation_context,
                mode_rules=mode_rules,
                db_messages=db_messages,
                image_blocks=image_blocks,
                sender_banner=sender_banner,
                turn_source=turn_source,
            )

        if backend == "agent-sdk":
            from services.claude_agent_sdk_provider import stream_agent_sdk
            return stream_agent_sdk(
                message=message,
                identity=identity,
                conversation_id=conversation_id,
                model=effective_model,
                skill_context=skill_context,
                permission_mode=config.get("permission_mode") or None,
                cancel_event=cancel_event,
                effort=effective_effort,
                orientation_context=orientation_context,
                mode_rules=mode_rules,
                db_messages=db_messages,
                image_blocks=image_blocks,
                sender_banner=sender_banner,
            )


        from services import claude_subprocess as claude
        return claude.stream_claude(
            message=message,
            identity=identity,
            conversation_id=conversation_id,
            model=effective_model,
            skill_context=skill_context,
            permission_mode=config.get("permission_mode") or None,
            cancel_event=cancel_event,
            effort=effective_effort,
            orientation_context=orientation_context,
            mode_rules=mode_rules,
            db_messages=db_messages,
            image_blocks=image_blocks,
            sender_banner=sender_banner,
            turn_source=turn_source,
        )

    elif provider == "codex":
        from services.cli_text_utils import _image_blocks_to_text

        # CLI can't receive multimodal content blocks -- convert to text
        codex_message = message
        image_text = _image_blocks_to_text(image_blocks)
        if image_text:
            codex_message = f"{image_text}\n\n{codex_message}"

        runtime = str(config.get("runtime") or "app-server").strip().lower()
        if runtime == "exec":
            from services.codex_subprocess import stream_codex

            stream_factory = stream_codex
        else:
            from services.codex_app_server import stream_codex_app_server

            stream_factory = stream_codex_app_server

        return stream_factory(
            message=codex_message,
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_context,
            db_messages=db_messages,
            model=config.get("model") or None,
            mode_rules=mode_rules,
            skill_context=skill_context,
            cancel_event=cancel_event,
            bypass_approvals=bool(config.get("bypass_approvals", True)),
            **({"turn_source": turn_source} if runtime != "exec" else {}),
        )

    elif provider == "chatgpt":


        from services.chatgpt_provider import stream_chatgpt
        return stream_chatgpt(
            message=message,
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_context,
            db_messages=db_messages,
            mode_rules=mode_rules,
            skill_context=skill_context,
            cancel_event=cancel_event,
            cdp_port=config.get("cdp_port"),
            profile_name=config.get("profile_name"),
            sender_banner=sender_banner,
        )

    elif provider in ("openai", "openrouter", "lmstudio", "ollama"):
        from services.openai_provider import stream_openai_compatible

        provider_model = config.get("model", "gpt-4o" if provider == "openai" else "")
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        try:
            requested_max_tokens = int(config.get("max_tokens", 8192))
        except (TypeError, ValueError):
            requested_max_tokens = 8192
            log.warning("Invalid %s max_tokens value; using 8192", provider)
        max_tokens = max(256, min(requested_max_tokens, 65536))
        if max_tokens != requested_max_tokens:
            log.warning(
                "Clamped invalid %s max_tokens from %d to %d",
                provider,
                requested_max_tokens,
                max_tokens,
            )

        if provider == "lmstudio" and not base_url:
            base_url = "http://localhost:1234/v1"
        elif provider == "ollama" and not base_url:
            base_url = "http://localhost:11434/v1"
        elif provider == "openrouter" and not base_url:
            base_url = "https://openrouter.ai/api/v1"

        # Ollama doesn't check API keys but the OpenAI SDK requires one
        if provider == "ollama" and not api_key:
            api_key = "ollama"

        # For local models, pass context budget so prompts get truncated to fit
        context_budget = int(config.get("context_length", 0)) if provider in ("lmstudio", "ollama") else 0

        # Vision sidecar: when the chat model can't see images, route them
        # to a vision model. On OpenRouter/OpenAI we default to Gemini Flash
        # over the same key+base_url; for local providers it stays off unless
        # the user sets a reachable vision_model in config.
        if provider in ("openrouter", "openai"):
            vision_model = config.get("vision_model", "google/gemini-2.0-flash")
        else:
            vision_model = config.get("vision_model", "")

        return stream_openai_compatible(
            message=message,
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_context,
            db_messages=db_messages,
            model=provider_model,
            image_blocks=image_blocks,
            mode_rules=mode_rules,
            skill_context=skill_context,
            active_categories=active_categories,
            cancel_event=cancel_event,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
            context_budget=context_budget,
            vision_model=vision_model,
            vision_api_key=api_key,
            vision_base_url=base_url,
        )

    else:
        log.error("Unknown provider '%s', falling back to Anthropic", provider)
        from services.claude_api import stream_api
        return stream_api(
            message=message,
            identity=identity,
            conversation_id=conversation_id,
            orientation_context=orientation_context,
            db_messages=db_messages,
            model=model,
            image_blocks=image_blocks,
            mode_rules=mode_rules,
            skill_context=skill_context,
            active_categories=active_categories,
            cancel_event=cancel_event,
        )
