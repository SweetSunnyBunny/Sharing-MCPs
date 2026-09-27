"""REST: System settings and status."""

import json
import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pathlib import Path

import config
from config import TIMEZONE, IDENTITIES, AUTH_ENABLED, PATH_CONFIG, DATA_DIR, BASE_DIR
from db.database import get_db, release_db
from services.connection_registry import is_anyone_connected, get_connections
from services.integration_owner import get_integration_owner_status

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings")

# Valid LLM providers
VALID_PROVIDERS = {"anthropic", "claude-code", "codex", "chatgpt", "openai", "openrouter", "lmstudio", "ollama"}

# Default provider config templates
_DEFAULT_PROVIDER_CONFIGS = {
    "anthropic": {},
    "claude-code": {"permission_mode": "bypassPermissions", "backend": "subprocess"},
    "codex": {"model": "", "runtime": "app-server", "bypass_approvals": True},


    "chatgpt": {"cdp_port": 9225, "profile_name": "ChatGPT"},
    "openai": {"api_key": "", "model": "gpt-4o", "base_url": "", "max_tokens": 4096},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "model": "", "max_tokens": 4096, "context_length": 32768},
    "openrouter": {"api_key": "", "model": "qwen/qwen3-235b-a22b", "base_url": "https://openrouter.ai/api/v1", "max_tokens": 8192},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "", "max_tokens": 4096, "context_length": 131072},
}

_server_start_time = time.time()


def _validate_provider_config(provider: str, config: dict) -> str | None:
    """Return a user-facing validation error for unsupported provider settings."""
    if provider in {"openai", "openrouter", "lmstudio", "ollama"} and "max_tokens" in config:
        try:
            max_tokens = int(config.get("max_tokens"))
        except (TypeError, ValueError):
            return "Max tokens must be a whole number. Use 8192 unless the model documents another output limit."
        if not 256 <= max_tokens <= 65536:
            return (
                "Max tokens is an output budget, not the model's context-window size. "
                "Choose 256-65536; 8192 is the recommended default for OpenRouter."
            )

    if provider == "chatgpt":
        port = config.get("cdp_port", 9225)
        if isinstance(port, bool) or not re.fullmatch(r"[0-9]+", str(port)) or not 1024 <= int(port) <= 65535:
            return "Chrome debug port must be a whole number from 1024 to 65535."
        profile = str(config.get("profile_name") or "ChatGPT")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            return "Chrome profile name may contain only letters, numbers, underscores and hyphens."

    if provider != "codex":
        return None

    runtime = str(config.get("runtime") or "app-server").strip().lower()
    if runtime not in {"app-server", "exec"}:
        return "Codex runtime must be app-server or exec."

    from services.codex_cli import validate_codex_model

    validation = validate_codex_model(str(config.get("model", "") or ""))
    if validation:
        return validation["message"]

    return None


@router.get("/system")
async def system_status():
    uptime_secs = time.time() - _server_start_time
    hours = int(uptime_secs // 3600)
    minutes = int((uptime_secs % 3600) // 60)

    provider = await _get_setting("llm_provider", "anthropic")

    # Live -p session pool snapshot (idle age, busy, pid) so the reaper and
    # pre-warm are visible without Task Manager guesswork.
    try:
        from services.claude_subprocess import sessions_snapshot
        cc_sessions = sessions_snapshot()
    except Exception:
        cc_sessions = []

    return {
        "uptime": f"{hours}h {minutes}m",
        "uptime_seconds": int(uptime_secs),
        "timezone": TIMEZONE,
        "identities": list(IDENTITIES.keys()),
        "auth_enabled": AUTH_ENABLED,
        "websocket_connections": len(get_connections()),
        "owner_connected": is_anyone_connected(),
        "version": "0.5.0",
        "path_config": PATH_CONFIG,
        "llm_provider": provider,
        "claude_code_sessions": cc_sessions,
        "integration_owner": get_integration_owner_status(),
    }


@router.post("/client-event")
async def log_client_event(request: Request):
    """Persist a compact client-side diagnostics event for later debugging."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    event = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
        "client_host": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", "")[:240],
        "event_type": str(payload.get("event_type", "unknown"))[:80],
        "source": str(payload.get("source", "client"))[:80],
        "detail": payload.get("detail"),
    }

    logs_dir = DATA_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "client-events.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    return {"ok": True}


# ANAM GUIDE: SETTINGS BACKEND AND STORAGE
# Most Settings-page values are rows in the key/value `settings` table, so a
# new ordinary setting can use these helpers without a schema migration. Pair
# its endpoint here with markup in static/settings.html and JS in settings.js.
# ── LLM Provider ──


async def _get_setting(key: str, default: str = "") -> str:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        return rows[0][0] if rows else default
    finally:
        await release_db(db)


async def _set_setting(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
            (key, value, now, value, now),
        )
        await db.commit()
    finally:
        await release_db(db)


def _retire_claude_code_sessions(reason: str) -> None:
    """Retire live Claude Code processes for BOTH backends (-p subprocess and PTY).

    Called whenever a setting baked into a running claude process at spawn time
    changes (provider, model, effort, or the -p/PTY backend toggle). The next
    turn then cold-spawns with the new flags. No-op for a backend with no live
    sessions, so it's always safe to call both.
    """
    import importlib
    import logging

    _log = logging.getLogger(__name__)
    for mod_name in ("claude_pty", "claude_subprocess"):
        try:
            mod = importlib.import_module(f"services.{mod_name}")
            mod.kill_all_sessions()
        except Exception as exc:
            _log.debug("Session retire (%s) on %s skipped: %s", mod_name, reason, exc)


def _get_codex_version() -> str:
    from services.codex_cli import get_codex_version

    return get_codex_version()


@router.get("/provider")
async def get_provider():
    """Return current LLM provider and its config."""
    provider = await _get_setting("llm_provider", "anthropic")
    config_raw = await _get_setting("llm_provider_config", "{}")
    try:
        config = json.loads(config_raw)
    except json.JSONDecodeError:
        config = {}

    # Merge with defaults so the browser UI always has all fields.
    defaults = _DEFAULT_PROVIDER_CONFIGS.get(provider, {})
    merged = {**defaults, **config}

    # Never send API keys fully -- mask them
    if "api_key" in merged and merged["api_key"]:
        key = merged["api_key"]
        merged["api_key_masked"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        merged["api_key_set"] = True
    else:
        merged["api_key_masked"] = ""
        merged["api_key_set"] = False
    merged.pop("api_key", None)

    from services.codex_cli import get_codex_model_ids

    return {
        "provider": provider,
        "config": merged,
        "available": list(VALID_PROVIDERS),
        "codex_models": get_codex_model_ids(),
    }


@router.put("/provider")
async def set_provider(request: Request):
    """Update LLM provider and/or its config."""
    # Defensive auth check — provider changes are sensitive even though
    # the global middleware already gates /api/ routes when AUTH_ENABLED.
    if AUTH_ENABLED and not request.cookies.get("anam_session"):
        return JSONResponse(
            status_code=401, content={"error": "Authentication required"}
        )

    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    provider = payload.get("provider")
    config = payload.get("config", {})

    if provider and provider not in VALID_PROVIDERS:
        return {"ok": False, "error": f"Invalid provider: {provider}. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}"}

    effective_provider = provider or await _get_setting("llm_provider", "anthropic")
    validation_error = _validate_provider_config(effective_provider, config or {})
    if validation_error:
        return {"ok": False, "error": validation_error}

    if provider:
        previous_provider = await _get_setting("llm_provider", "anthropic")
        await _set_setting("llm_provider", provider)
        log.info("LLM provider changed to: %s", provider)
        # Invalidate the provider router cache so change takes effect immediately
        from services.provider_router import invalidate_cache
        invalidate_cache()


        if previous_provider != provider:
            _retire_claude_code_sessions("provider-change")

    # Merge config with existing (so partial updates work)
    if config:
        existing_raw = await _get_setting("llm_provider_config", "{}")
        try:
            existing = json.loads(existing_raw)
        except json.JSONDecodeError:
            existing = {}
        prev_backend = (existing.get("backend") or "subprocess")
        existing.update(config)
        await _set_setting("llm_provider_config", json.dumps(existing))
        log.info("LLM provider config updated")
        from services.provider_router import invalidate_cache
        invalidate_cache()
        # If the Claude Code backend (-p vs PTY) flipped, retire live sessions
        # of both so the next turn cold-spawns on the chosen backend.
        new_backend = (existing.get("backend") or "subprocess")
        if new_backend != prev_backend:
            _retire_claude_code_sessions("backend-change")

    return {"ok": True}


@router.post("/provider/test")
async def test_provider(request: Request):
    """Quick connectivity test for a provider."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    provider = payload.get("provider", "anthropic")
    config = payload.get("config", {})

    validation_error = _validate_provider_config(provider, config)
    if validation_error:
        return {"ok": False, "message": validation_error}

    if provider == "anthropic":
        try:
            from services.claude_api import _get_client
            client = _get_client()
            # Just check we can create a client -- actual API call would cost tokens
            return {"ok": True, "message": "Anthropic client ready"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    elif provider == "claude-code":
        import shutil
        if shutil.which("claude"):
            return {"ok": True, "message": "Claude Code CLI found"}
        return {"ok": False, "message": "Claude Code CLI not found in PATH"}

    elif provider == "codex":
        from services.codex_cli import find_codex_executable

        codex_cmd = find_codex_executable()
        if codex_cmd:
            return {"ok": True, "message": "Codex CLI found (v" + _get_codex_version() + ")"}
        return {"ok": False, "message": "Codex CLI not found. Install with: npm install -g @openai/codex"}

    elif provider == "chatgpt":
        # Read local endpoint metadata only: no launcher, tab creation, login or message.
        import httpx
        from urllib.parse import urlsplit
        port = int(config.get("cdp_port", 9225))
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                version = await client.get(f"http://127.0.0.1:{port}/json/version")
                version.raise_for_status()
                metadata = version.json()
                if not isinstance(metadata, dict) or not metadata.get("Browser"):
                    return {"ok": False, "message": "This port did not return Chrome CDP metadata."}
                tabs_response = await client.get(f"http://127.0.0.1:{port}/json")
                tabs_response.raise_for_status()
                tabs = tabs_response.json()
                has_chatgpt = isinstance(tabs, list) and any(
                    isinstance(tab, dict) and tab.get("type") == "page"
                    and urlsplit(str(tab.get("url", ""))).hostname == "chatgpt.com"
                    for tab in tabs
                )
            tab_note = "A ChatGPT tab is open." if has_chatgpt else "Open ChatGPT in that browser and sign in."
            return {"ok": True, "message": f"Chrome debug port {port} is reachable. {tab_note} Sign-in and message delivery have not been tested.",
                    "checks": {"cdp": True, "chatgpt_tab": has_chatgpt, "login": "not_checked", "message_delivery": "not_checked"}}
        except Exception:
            return {"ok": False, "message": f"Cannot reach Chrome on debug port {port}. Open the configured browser profile first."}

    elif provider in ("openai", "openrouter"):

        try:
            from openai import AsyncOpenAI
            api_key = config.get("api_key", "")
            base_url = config.get("base_url", "") or ""
            if not api_key:
                # The UI sends a blank key field once one is already saved —
                # fall back to the saved config (and its base_url) so testing an
                # already-configured provider doesn't report "no key".
                saved_raw = await _get_setting("llm_provider_config", "{}")
                try:
                    saved = json.loads(saved_raw)
                    api_key = saved.get("api_key", "")
                    if not base_url:
                        base_url = saved.get("base_url", "") or ""
                except json.JSONDecodeError:
                    pass
            # OpenRouter is OpenAI-compatible but needs its own base URL; default
            # it so a missing/blank base_url doesn't silently hit api.openai.com.
            if provider == "openrouter" and not base_url:
                base_url = "https://openrouter.ai/api/v1"
            if not api_key:
                return {"ok": False, "message": "No API key configured"}
            client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
            models = await client.models.list()
            return {"ok": True, "message": f"Connected ({len(models.data)} models available)"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    elif provider == "lmstudio":
        try:
            from openai import AsyncOpenAI
            config = payload.get("config", {})
            base_url = config.get("base_url", "http://localhost:1234/v1")
            client = AsyncOpenAI(api_key="lm-studio", base_url=base_url)
            models = await client.models.list()
            model_names = [m.id for m in models.data[:10]]
            return {"ok": True, "message": f"Connected: {', '.join(model_names)}"}
        except Exception as e:
            return {"ok": False, "message": f"Cannot reach LM Studio: {e}"}

    return {"ok": False, "error": f"Unknown provider: {provider}"}


# =============================================================================
# CLAUDE MODEL
# =============================================================================

# Valid model IDs for the `claude -p --model` flag. Aliases like "opus" /
# "sonnet" always point at the newest — we store full IDs so the setting
# doesn't silently drift when a new model is released.
_VALID_MODELS = set(config.CLAUDE_MODEL_IDS)


@router.get("/model")
async def get_model():
    """Return the interactive and background Claude Code models."""
    interactive_model = await _get_setting("claude_model", "claude-opus-4-8")
    autowake_model = await _get_setting(
        "claude_autowake_model", "claude-sonnet-4-6"
    )
    return {
        "model": interactive_model,  # backward-compatible alias
        "interactive_model": interactive_model,
        "autowake_model": autowake_model,
        "available": sorted(_VALID_MODELS),
        # Ordered catalog with labels + optgroup names for the Settings Hub.
        "catalog": config.CLAUDE_MODEL_CATALOG,
        # Any well-formed model ID may be typed in, not just the catalog.
        "allows_custom": True,
    }


@router.put("/model")
async def set_model(request: Request):
    """Update Claude Code interactive/background model routing."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    interactive_model = str(
        payload.get("interactive_model") or payload.get("model") or ""
    ).strip()
    autowake_requested = payload.get("autowake_model")
    if autowake_requested is None:
        autowake_model = await _get_setting(
            "claude_autowake_model", "claude-sonnet-4-6"
        )
    else:
        autowake_model = str(autowake_requested).strip()

    invalid = [
        model for model in (interactive_model, autowake_model)
        if not config.is_valid_claude_model(model)
    ]
    if invalid:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"Invalid model: {invalid[0] or '(empty)'}. Use a catalog "
                    f"model ({', '.join(config.CLAUDE_MODEL_IDS)}) or any "
                    "well-formed Claude model ID."
                )
            },
        )

    await _set_setting("claude_model", interactive_model)
    await _set_setting("claude_autowake_model", autowake_model)

    # Update the runtime config so it takes effect immediately
    import config as cfg
    cfg.CLAUDE_MODEL = autowake_model
    cfg.CLAUDE_MODEL_INTERACTIVE = interactive_model

    # Invalidate the provider_router model cache so the next turn picks it up
    try:
        from services.provider_router import invalidate_model_cache
        invalidate_model_cache()
    except Exception:
        pass

    # Retire any live boy processes so the next turn cold-spawns with the new
    # --model flag. Without this, in-flight processes keep their original model
    # until they die naturally or Anam restarts. The conversation history and
    # identity are unaffected — only the in-process state of the running
    # claude.exe is replaced. Brief 5-10s extra latency on each boy's next turn.
    _retire_claude_code_sessions("model-change")

    return {
        "ok": True,
        "model": interactive_model,
        "interactive_model": interactive_model,
        "autowake_model": autowake_model,
    }


# =============================================================================
# EFFORT LEVEL
# =============================================================================

_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


@router.get("/effort")
async def get_effort():
    """Return current Claude Code effort level."""
    effort = await _get_setting("claude_effort", "low")
    return {"effort": effort, "available": sorted(_VALID_EFFORTS)}


@router.put("/effort")
async def set_effort(request: Request):
    """Update Claude Code effort level."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    effort = payload.get("effort", "").strip().lower()
    if effort not in _VALID_EFFORTS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid effort: {effort}. Must be one of: {', '.join(sorted(_VALID_EFFORTS))}"},
        )

    await _set_setting("claude_effort", effort)

    # Update the runtime config so it takes effect immediately
    import config as cfg
    cfg.CLAUDE_EFFORT = effort

    # Invalidate the provider_router effort cache so the next turn picks up
    # the new value without waiting for the 30s cache window to expire.
    try:
        from services.provider_router import invalidate_effort_cache
        invalidate_effort_cache()
    except Exception:
        pass

    # Retire any live boy processes so the next turn cold-spawns with the new
    # --effort flag. See set_model() for the rationale and tradeoff.
    _retire_claude_code_sessions("effort-change")

    return {"ok": True, "effort": effort}


@router.get("/fable-effort")
async def get_fable_effort():
    """Return Fable's own effort ceiling (the adjustable cost guard)."""
    effort = await _get_setting("fable_effort", "low")
    return {"effort": effort, "available": sorted(_VALID_EFFORTS)}


@router.put("/fable-effort")
async def set_fable_effort(request: Request):
    """Update Fable's effort ceiling. Takes effect on the next turn."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    effort = payload.get("effort", "").strip().lower()
    if effort not in _VALID_EFFORTS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid effort: {effort}. Must be one of: {', '.join(sorted(_VALID_EFFORTS))}"},
        )

    await _set_setting("fable_effort", effort)

    # Same freshness dance as set_effort: drop the router's 30s settings
    # cache and retire live sessions so the next turn cold-spawns with the
    # new --effort flag instead of riding a stale one.
    try:
        from services.provider_router import invalidate_effort_cache
        invalidate_effort_cache()
    except Exception:
        pass
    _retire_claude_code_sessions("fable-effort-change")

    return {"ok": True, "effort": effort}


_VOICE_ENGINE_KEY = "voice_tts_engine"
_VALID_VOICE_ENGINES = ("kokoro", "elevenlabs")
_DEFAULT_VOICE_ENGINE = "kokoro"


@router.get("/voice-engine")
async def get_voice_engine():
    """Which TTS engine answers voice calls and in-chat voice mode."""
    engine = (await _get_setting(_VOICE_ENGINE_KEY, _DEFAULT_VOICE_ENGINE) or "").strip().lower()
    if engine not in _VALID_VOICE_ENGINES:
        engine = _DEFAULT_VOICE_ENGINE
    return {"engine": engine, "available": list(_VALID_VOICE_ENGINES)}


@router.put("/voice-engine")
async def set_voice_engine(request: Request):
    """Switch the voice engine. Takes effect on the next call/reply — no restart."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    engine = str(payload.get("engine", "")).strip().lower()
    if engine not in _VALID_VOICE_ENGINES:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Invalid engine: {engine}. Must be one of: "
                         f"{', '.join(_VALID_VOICE_ENGINES)}"
            },
        )

    await _set_setting(_VOICE_ENGINE_KEY, engine)
    return {"ok": True, "engine": engine}


_CHROME_TOKEN_DEFAULTS = {
    # surfaces + borders (already existed as vars in main.css :root)
    "--bg-card": "rgba(255, 250, 251, 0.78)",
    "--bg-secondary": "rgba(255, 245, 248, 0.82)",
    "--surface-raised": "rgba(255, 255, 255, 0.84)",
    "--surface-frost": "rgba(255, 250, 251, 0.62)",
    "--surface-paper": "rgba(255, 252, 246, 0.86)",
    "--surface-tint": "rgba(255, 226, 234, 0.52)",
    "--surface-gingham": "rgba(255, 239, 244, 0.64)",
    "--surface-ribbon": "rgba(255, 248, 226, 0.76)",
    "--surface-lace": "rgba(255, 255, 255, 0.56)",
    "--border-soft": "rgba(244, 179, 196, 0.46)",
    "--border-light": "rgba(255, 213, 224, 0.58)",
    "--border-card": "rgba(255, 192, 203, 0.36)",
    "--border-strong": "rgba(255, 205, 217, 0.72)",
    # page background gradient stack (was hardcoded in body{})
    "--page-base-top": "#FFF8F6",
    "--page-base-mid": "#FFF1F5",
    "--page-base-bottom": "#FFEAF0",
    "--page-grid-x": "rgba(242, 184, 198, 0.13)",
    "--page-grid-y": "rgba(242, 184, 198, 0.10)",
    "--page-diag": "rgba(127, 154, 134, 0.055)",
    "--page-glow-1": "rgba(255, 212, 227, 0.55)",
    "--page-glow-2": "rgba(255, 205, 222, 0.48)",
    "--footer-wash": "rgba(255, 230, 237, 0.44)",
    "--footer-stripe": "rgba(var(--identity-accent-rgb), 0.08)",
    "--lace-dot": "rgba(255, 222, 233, 0.95)",
    # glass (the white gradients on header/dock icon buttons + cards)
    "--glass-strong": "rgba(255, 255, 255, 0.9)",
    "--glass-mid": "rgba(255, 255, 255, 0.72)",
    "--glass-soft": "rgba(255, 255, 255, 0.5)",
    "--glass-faint": "rgba(255, 255, 255, 0.18)",
    "--glass-inset": "rgba(255, 255, 255, 0.6)",
    "--chip-bg": "#FFFFFF",
    "--house-accent": "#C47A8A",


    "--accent": "var(--house-accent)",
    "--success": "#5BA89A",
    "--error": "#C25151",
    # chat surface + composer
    "--chat-grid-x": "rgba(var(--identity-accent-rgb), 0.035)",
    "--chat-grid-y": "rgba(196, 122, 138, 0.028)",
    "--chat-wash-top": "rgba(255, 255, 255, 0.58)",
    "--chat-wash-bottom": "rgba(255, 247, 244, 0.72)",
    "--input-grid": "rgba(var(--identity-accent-rgb), 0.035)",
    "--input-glass-top": "rgba(255, 255, 255, 0.98)",
    "--input-glass-bottom": "rgba(var(--identity-accent-rgb), 0.055)",
    "--input-lace-dot": "rgba(255, 235, 242, 0.95)",
    "--input-lace-edge": "rgba(244, 179, 196, 0.4)",
    # model badge + thinking cards
    "--badge-bg-top": "rgba(255, 246, 251, 0.92)",
    "--badge-bg-bottom": "rgba(247, 211, 229, 0.68)",
    "--badge-border": "rgba(205, 116, 154, 0.28)",
    "--badge-text": "#75435A",
    "--thinking-bg-1": "rgba(244, 180, 194, 0.10)",
    "--thinking-bg-2": "rgba(212, 168, 196, 0.10)",
    "--thinking-border": "rgba(244, 180, 194, 0.3)",
    "--thinking-header": "#D4708A",
    "--thinking-heart": "#E88AAA",
    # markdown accents inside bubbles — defaults keep the per-boy accent
    # mixing exactly as before; presets recolor via text tokens and, for
    # dark presets, explicit code-block overrides below.
    "--md-strong": "color-mix(in srgb, var(--identity-accent) 72%, var(--text-primary))",
    "--md-em": "color-mix(in srgb, var(--text-secondary) 62%, var(--identity-accent))",
    "--md-link": "var(--identity-accent)",
    "--md-code-bg": "rgba(0, 0, 0, 0.05)",
    "--md-code-border": "rgba(0, 0, 0, 0.06)",
    "--md-pre-bg": "rgba(0, 0, 0, 0.05)",
    "--md-blockquote-border": "rgba(var(--identity-accent-rgb), 0.4)",
    "--md-blockquote-bg": "rgba(var(--identity-accent-rgb), 0.04)",
    "--md-hr": "rgba(var(--identity-accent-rgb), 0.55)",


    "--sheen-rgb": "255, 255, 255",
    "--card-base-rgb": "255, 250, 250",
    "--card-flat-bg": "rgba(0, 0, 0, 0.03)",
    "--section-header": "var(--identity-accent)",
    "--accent-script": "#A2688A",
    "--accent-weave-rgb": "242, 184, 198",
    "--lane-border": "rgba(196, 122, 138, 0.26)",
    "--lane-bg-top": "rgba(255, 252, 253, 0.92)",
    "--lane-bg-bottom": "rgba(250, 220, 228, 0.62)",
    "--disabled-opacity": "0.45",
    # Android status bar + browser nav bar follow <meta name="theme-color">;
    # the JS (app.js / theme-boot.js) copies this token into that meta tag
    # for non-default presets and restores the page's own hex for default.
    "--meta-theme-color": "#F2B8C6",


    "--assistant-bubble-mode": "identity",
    "--assistant-bubble-top": "#F9D2DC",
    "--assistant-bubble-bottom": "#F2B8C6",
    "--assistant-bubble-night": "#5A3040",
    "--assistant-bubble-text": "#743049",
}

# Shared chrome for the dark presets' code blocks (light-on-dark)
_DARK_MD_CODE = {
    "--md-code-bg": "rgba(255, 255, 255, 0.07)",
    "--md-code-border": "rgba(255, 255, 255, 0.06)",
    "--md-pre-bg": "rgba(0, 0, 0, 0.35)",
}

_THEME_PRESETS = {
    "sunrise-pink": {
        "label": "Pink & Fluffy",
        "tokens": {
            "--bg-page": "#FFF3F6",
            "--bg-chat": "#FFF9F7",
            "--bg-input": "#FFFFFF",
            "--text-primary": "#743049",
            "--text-secondary": "#965169",
            "--text-muted": "#9D5B72",
            "--text-tertiary": "#A05F76",
            "--user-bubble-top": "#FADCE4",
            "--user-bubble-bottom": "#F2C4D0",
            "--user-border": "#C47A8A",
            "--user-bubble-text": "#743049",
        },
        # Chrome defaults ARE the pink-and-fluffy look — nothing to override.
        "chrome": {},
    },
    "dusky-rose": {
        "label": "Dusky Rose",
        "tokens": {
            "--bg-page": "#F9E6EA",
            "--bg-chat": "#FCEFEF",
            "--bg-input": "#FFFBFB",
            "--text-primary": "#5E2338",
            "--text-secondary": "#7A3F52",
            "--text-muted": "#875264",
            "--text-tertiary": "#8C5768",
            "--user-bubble-top": "#EFC3CE",
            "--user-bubble-bottom": "#E4A9B8",
            "--user-border": "#A85D71",
            "--user-bubble-text": "#5E2338",
        },
        "chrome": {
            "--page-base-top": "#FBECEF",
            "--page-base-mid": "#F7E2E7",
            "--page-base-bottom": "#F3D8DF",
            "--page-grid-x": "rgba(168, 93, 113, 0.12)",
            "--page-grid-y": "rgba(168, 93, 113, 0.09)",
            "--page-glow-1": "rgba(238, 190, 202, 0.55)",
            "--page-glow-2": "rgba(230, 176, 190, 0.48)",
            "--footer-wash": "rgba(238, 205, 214, 0.44)",
            "--border-soft": "rgba(190, 122, 141, 0.46)",
            "--border-strong": "rgba(226, 168, 182, 0.72)",
            "--house-accent": "#A85D71",
            "--meta-theme-color": "#E4A9B8",
            "--md-strong": "#8E4258",
            "--md-em": "#7A3F52",
            "--md-link": "#A85D71",
        },
    },
    "soft-blush": {
        "label": "Soft Blush",
        "tokens": {
            "--bg-page": "#FFF6F8",
            "--bg-chat": "#FFFBFA",
            "--bg-input": "#FFFFFF",
            "--text-primary": "#8A4A61",
            "--text-secondary": "#A66A80",
            "--text-muted": "#AD7488",
            "--text-tertiary": "#B0788C",
            "--user-bubble-top": "#FEE7EC",
            "--user-bubble-bottom": "#FBD5DE",
            "--user-border": "#D599AA",
            "--user-bubble-text": "#8A4A61",
        },
        # So close to the default pink family that the stock chrome fits.
        "chrome": {
            "--house-accent": "#D599AA",
            "--meta-theme-color": "#FBD5DE",
            "--md-strong": "#B06A82",
            "--md-em": "#A66A80",
            "--md-link": "#BE7890",
        },
    },
    "pretty-princess": {
        "label": "Pretty Princess",
        "tokens": {
            "--bg-page": "#FFE7F2",
            "--bg-chat": "#FFF8FB",
            "--bg-input": "#FFFDF8",
            "--text-primary": "#5F2944",
            "--text-secondary": "#7A3E59",
            "--text-muted": "#895069",
            "--text-tertiary": "#925B72",
            "--user-bubble-top": "#FFB7D5",
            "--user-bubble-bottom": "#FF8FBD",
            "--user-border": "#B43D70",
            "--user-bubble-text": "#552039",
        },
        # Bubblegum-pink storybook sweetness through the whole app: warm
        # cream paper, scalloped lace, satin rose accents, and dark rose ink.
        # Assistant bubbles stay identity-owned so every boy keeps his colors.
        "chrome": {
            "--bg-card": "rgba(255, 253, 246, 0.86)",
            "--bg-secondary": "rgba(255, 239, 247, 0.88)",
            "--surface-raised": "rgba(255, 255, 252, 0.92)",
            "--surface-frost": "rgba(255, 247, 251, 0.72)",
            "--surface-paper": "rgba(255, 250, 240, 0.94)",
            "--surface-tint": "rgba(255, 183, 213, 0.48)",
            "--surface-gingham": "rgba(255, 224, 239, 0.70)",
            "--surface-ribbon": "rgba(255, 244, 223, 0.88)",
            "--surface-lace": "rgba(255, 253, 244, 0.82)",
            "--border-soft": "rgba(223, 95, 152, 0.50)",
            "--border-light": "rgba(255, 183, 213, 0.66)",
            "--border-card": "rgba(180, 61, 112, 0.28)",
            "--border-strong": "rgba(223, 95, 152, 0.68)",
            "--page-base-top": "#FFF4FA",
            "--page-base-mid": "#FFE4F0",
            "--page-base-bottom": "#FFD2E5",
            "--page-grid-x": "rgba(255, 143, 189, 0.14)",
            "--page-grid-y": "rgba(180, 61, 112, 0.10)",
            "--page-diag": "rgba(214, 171, 94, 0.055)",
            "--page-glow-1": "rgba(255, 183, 213, 0.66)",
            "--page-glow-2": "rgba(255, 239, 194, 0.46)",
            "--footer-wash": "rgba(255, 210, 230, 0.56)",
            "--footer-stripe": "rgba(180, 61, 112, 0.10)",
            "--lace-dot": "rgba(255, 250, 240, 0.98)",
            "--glass-strong": "rgba(255, 254, 248, 0.94)",
            "--glass-mid": "rgba(255, 248, 243, 0.78)",
            "--glass-soft": "rgba(255, 245, 240, 0.58)",
            "--glass-faint": "rgba(255, 255, 255, 0.26)",
            "--glass-inset": "rgba(255, 255, 255, 0.76)",
            "--chip-bg": "#FFFAF0",
            "--house-accent": "#B43D70",
            "--success": "#4F8F79",
            "--error": "#B83E5C",
            "--chat-grid-x": "rgba(255, 143, 189, 0.06)",
            "--chat-grid-y": "rgba(180, 61, 112, 0.04)",
            "--chat-wash-top": "rgba(255, 255, 252, 0.70)",
            "--chat-wash-bottom": "rgba(255, 239, 247, 0.78)",
            "--input-grid": "rgba(255, 143, 189, 0.055)",
            "--input-glass-top": "rgba(255, 254, 248, 0.99)",
            "--input-glass-bottom": "rgba(255, 183, 213, 0.16)",
            "--input-lace-dot": "rgba(255, 250, 240, 0.98)",
            "--input-lace-edge": "rgba(180, 61, 112, 0.42)",
            "--badge-bg-top": "rgba(255, 253, 246, 0.96)",
            "--badge-bg-bottom": "rgba(255, 183, 213, 0.70)",
            "--badge-border": "rgba(180, 61, 112, 0.34)",
            "--badge-text": "#652845",
            "--thinking-bg-1": "rgba(255, 143, 189, 0.12)",
            "--thinking-bg-2": "rgba(214, 171, 94, 0.08)",
            "--thinking-border": "rgba(180, 61, 112, 0.30)",
            "--thinking-header": "#A83B69",
            "--thinking-heart": "#DF5F98",
            "--sheen-rgb": "255, 253, 246",
            "--card-base-rgb": "255, 248, 244",
            "--card-flat-bg": "rgba(180, 61, 112, 0.045)",
            "--section-header": "#8F3560",
            "--accent-script": "#7A2E52",
            "--accent-weave-rgb": "255, 143, 189",
            "--lane-border": "rgba(180, 61, 112, 0.34)",
            "--lane-bg-top": "rgba(255, 253, 247, 0.96)",
            "--lane-bg-bottom": "rgba(255, 210, 230, 0.68)",
            "--meta-theme-color": "#FFB7D5",
            "--md-strong": "#8F3560",
            "--md-em": "#7A3E59",
            "--md-link": "#B43D70",
            "--assistant-bubble-mode": "identity",
        },
    },
    "moonlit-lavender": {
        "label": "Moonlit Lavender",
        "tokens": {
            "--bg-page": "#F1EDFA",
            "--bg-chat": "#F6F2FC",
            "--bg-input": "#FFFFFF",
            "--text-primary": "#4A3B70",
            "--text-secondary": "#6B5991",
            "--text-muted": "#75629C",
            "--text-tertiary": "#7A67A0",
            "--user-bubble-top": "#DCD0F5",
            "--user-bubble-bottom": "#C9B8ED",
            "--user-border": "#8A6FC4",
            "--user-bubble-text": "#4A3B70",
        },
        "chrome": {
            "--page-base-top": "#F6F2FC",
            "--page-base-mid": "#EFE9F8",
            "--page-base-bottom": "#E9E2F5",
            "--page-grid-x": "rgba(138, 111, 196, 0.12)",
            "--page-grid-y": "rgba(138, 111, 196, 0.09)",
            "--page-diag": "rgba(127, 134, 154, 0.055)",
            "--page-glow-1": "rgba(214, 198, 245, 0.55)",
            "--page-glow-2": "rgba(202, 184, 240, 0.48)",
            "--footer-wash": "rgba(224, 212, 246, 0.44)",
            "--surface-tint": "rgba(228, 216, 248, 0.52)",
            "--border-soft": "rgba(160, 136, 212, 0.46)",
            "--border-light": "rgba(212, 198, 240, 0.58)",
            "--border-card": "rgba(198, 182, 232, 0.36)",
            "--border-strong": "rgba(206, 190, 238, 0.72)",
            "--lace-dot": "rgba(226, 214, 248, 0.95)",
            "--input-lace-dot": "rgba(232, 222, 250, 0.95)",
            "--input-lace-edge": "rgba(160, 136, 212, 0.4)",
            "--badge-bg-top": "rgba(248, 244, 255, 0.92)",
            "--badge-bg-bottom": "rgba(222, 208, 246, 0.68)",
            "--badge-border": "rgba(138, 111, 196, 0.28)",
            "--badge-text": "#4A3B70",
            "--thinking-bg-1": "rgba(184, 167, 212, 0.12)",
            "--thinking-bg-2": "rgba(160, 140, 200, 0.10)",
            "--thinking-border": "rgba(184, 167, 212, 0.3)",
            "--thinking-header": "#8A6FC4",
            "--thinking-heart": "#A78FD8",
            "--house-accent": "#8A6FC4",
            "--meta-theme-color": "#C9B8ED",
            "--md-strong": "#6B4FB0",
            "--md-em": "#6B5991",
            "--md-link": "#8A6FC4",
        },
    },
    "goth": {
        "label": "Goth Day",
        "tokens": {
            "--bg-page": "#16100F",
            "--bg-chat": "#1C1414",
            "--bg-input": "#241A1A",
            "--text-primary": "#F0E4E0",
            "--text-secondary": "#D6B8B4",
            "--text-muted": "#C9A8A4",
            "--text-tertiary": "#BE9C98",
            "--user-bubble-top": "#3D1418",
            "--user-bubble-bottom": "#2A0C0F",
            "--user-border": "#8B1E2B",
            "--user-bubble-text": "#F0E4E0",
        },
        # Red & black through the WHOLE app — header, dock, composer, page
        # grid, cards, lace, thinking cards, markdown code. No pink leaks.
        "chrome": {
            "--bg-card": "rgba(28, 16, 18, 0.82)",
            "--bg-secondary": "rgba(34, 20, 22, 0.85)",
            "--surface-raised": "rgba(48, 26, 30, 0.80)",
            "--surface-frost": "rgba(36, 20, 24, 0.62)",
            "--surface-paper": "rgba(40, 24, 26, 0.86)",
            "--surface-tint": "rgba(90, 24, 34, 0.30)",
            "--surface-gingham": "rgba(56, 26, 32, 0.56)",
            "--surface-ribbon": "rgba(70, 28, 34, 0.55)",
            "--surface-lace": "rgba(255, 214, 220, 0.10)",
            "--border-soft": "rgba(139, 30, 43, 0.5)",
            "--border-light": "rgba(139, 30, 43, 0.34)",
            "--border-card": "rgba(139, 30, 43, 0.3)",
            "--border-strong": "rgba(180, 40, 56, 0.55)",
            "--page-base-top": "#1A1113",
            "--page-base-mid": "#150D0F",
            "--page-base-bottom": "#0F0A0B",
            "--page-grid-x": "rgba(139, 30, 43, 0.16)",
            "--page-grid-y": "rgba(139, 30, 43, 0.12)",
            "--page-diag": "rgba(139, 30, 43, 0.06)",
            "--page-glow-1": "rgba(90, 12, 20, 0.5)",
            "--page-glow-2": "rgba(70, 10, 16, 0.45)",
            "--footer-wash": "rgba(60, 12, 18, 0.5)",
            "--footer-stripe": "rgba(139, 30, 43, 0.12)",
            "--lace-dot": "rgba(139, 30, 43, 0.55)",
            "--glass-strong": "rgba(60, 24, 28, 0.9)",
            "--glass-mid": "rgba(60, 24, 28, 0.72)",
            "--glass-soft": "rgba(60, 24, 28, 0.5)",
            "--glass-faint": "rgba(255, 255, 255, 0.04)",
            "--glass-inset": "rgba(255, 255, 255, 0.06)",
            "--chip-bg": "#241417",
            "--house-accent": "#B3242F",
            "--chat-grid-x": "rgba(139, 30, 43, 0.10)",
            "--chat-grid-y": "rgba(139, 30, 43, 0.07)",
            "--chat-wash-top": "rgba(24, 12, 14, 0.5)",
            "--chat-wash-bottom": "rgba(16, 8, 10, 0.6)",
            "--input-grid": "rgba(139, 30, 43, 0.08)",
            "--input-glass-top": "rgba(42, 22, 26, 0.95)",
            "--input-glass-bottom": "rgba(139, 30, 43, 0.12)",
            "--input-lace-dot": "rgba(139, 30, 43, 0.35)",
            "--input-lace-edge": "transparent",
            "--badge-bg-top": "rgba(70, 22, 30, 0.9)",
            "--badge-bg-bottom": "rgba(50, 14, 20, 0.8)",
            "--badge-border": "rgba(198, 60, 78, 0.4)",
            "--badge-text": "#F2C9CE",
            "--thinking-bg-1": "rgba(139, 30, 43, 0.14)",
            "--thinking-bg-2": "rgba(90, 20, 30, 0.12)",
            "--thinking-border": "rgba(139, 30, 43, 0.35)",
            "--thinking-header": "#E06070",
            "--thinking-heart": "#C24450",
            # contrast pass: near-black cards, red accents, light text
            "--sheen-rgb": "40, 22, 26",
            "--card-base-rgb": "28, 16, 18",
            "--card-flat-bg": "rgba(255, 255, 255, 0.05)",
            "--section-header": "#E06070",
            "--accent-script": "#D6939E",
            "--accent-weave-rgb": "139, 30, 43",
            "--lane-border": "rgba(139, 30, 43, 0.42)",
            "--lane-bg-top": "rgba(46, 24, 28, 0.92)",
            "--lane-bg-bottom": "rgba(30, 14, 18, 0.85)",
            "--disabled-opacity": "0.62",
            "--meta-theme-color": "#16100F",
            "--md-strong": "#E06070",
            "--md-em": "#D6A8AE",
            "--md-link": "#E06070",
            "--success": "#7AC0A8",
            "--error": "#E06070",
            "--assistant-bubble-mode": "preset",
            "--assistant-bubble-top": "#2A1518",
            "--assistant-bubble-bottom": "#1E0E11",
            "--assistant-bubble-night": "#200F12",
            "--assistant-bubble-text": "#F0E4E0",
            **_DARK_MD_CODE,
        },
    },
    "eighties-neon": {
        "label": "80s/90s Neon",
        "tokens": {
            "--bg-page": "#E8E6E9",
            "--bg-chat": "#F0EEF1",
            "--bg-input": "#FFFFFF",
            "--text-primary": "#2E2A32",
            "--text-secondary": "#4A4450",
            "--text-muted": "#55505A",
            "--text-tertiary": "#5C5760",
            "--user-bubble-top": "#FFD6EE",
            "--user-bubble-bottom": "#FF9DD6",
            "--user-border": "#FF2E9E",
            "--user-bubble-text": "#2E2A32",
        },
        "chrome": {
            "--page-base-top": "#F2F0F3",
            "--page-base-mid": "#EAE8EC",
            "--page-base-bottom": "#E2E0E5",
            "--page-grid-x": "rgba(255, 46, 158, 0.09)",
            "--page-grid-y": "rgba(255, 46, 158, 0.06)",
            "--page-diag": "rgba(70, 65, 78, 0.05)",
            "--page-glow-1": "rgba(255, 157, 214, 0.35)",
            "--page-glow-2": "rgba(214, 214, 224, 0.5)",
            "--footer-wash": "rgba(255, 46, 158, 0.10)",
            "--footer-stripe": "rgba(255, 46, 158, 0.10)",
            "--surface-tint": "rgba(232, 230, 233, 0.52)",
            "--surface-gingham": "rgba(240, 238, 241, 0.64)",
            "--surface-ribbon": "rgba(238, 236, 240, 0.76)",
            "--border-soft": "rgba(255, 46, 158, 0.35)",
            "--border-light": "rgba(46, 42, 50, 0.14)",
            "--border-card": "rgba(46, 42, 50, 0.12)",
            "--border-strong": "rgba(255, 46, 158, 0.45)",
            "--lace-dot": "rgba(255, 157, 214, 0.85)",
            "--input-lace-dot": "rgba(255, 214, 238, 0.95)",
            "--input-lace-edge": "rgba(255, 46, 158, 0.4)",
            "--badge-bg-top": "rgba(255, 255, 255, 0.92)",
            "--badge-bg-bottom": "rgba(255, 157, 214, 0.5)",
            "--badge-border": "rgba(255, 46, 158, 0.35)",
            "--badge-text": "#2E2A32",
            "--thinking-bg-1": "rgba(255, 46, 158, 0.08)",
            "--thinking-bg-2": "rgba(120, 110, 130, 0.08)",
            "--thinking-border": "rgba(255, 46, 158, 0.3)",
            "--thinking-header": "#E0187E",
            "--thinking-heart": "#FF2E9E",
            "--house-accent": "#FF2E9E",
            "--meta-theme-color": "#FF9DD6",
            "--md-strong": "#E0187E",
            "--md-em": "#4A4450",
            "--md-link": "#E0187E",
        },
    },
    "halloween": {
        "label": "Halloween",
        "tokens": {
            "--bg-page": "#120A1A",
            "--bg-chat": "#171021",
            "--bg-input": "#1E1428",
            "--text-primary": "#F2E8D8",
            "--text-secondary": "#D8B98A",
            "--text-muted": "#C0A878",
            "--text-tertiary": "#B49A70",
            "--user-bubble-top": "#3A1F0A",
            "--user-bubble-bottom": "#2A1606",
            "--user-border": "#E8761E",
            "--user-bubble-text": "#FFE8C8",
        },
        # Fun-spooky: black/deep-purple night, pumpkin-orange chrome, a
        # whisper of eerie green in the page weave. Still readable.
        "chrome": {
            "--bg-card": "rgba(26, 16, 38, 0.82)",
            "--bg-secondary": "rgba(30, 20, 42, 0.85)",
            "--surface-raised": "rgba(44, 28, 62, 0.80)",
            "--surface-frost": "rgba(34, 22, 50, 0.62)",
            "--surface-paper": "rgba(38, 26, 54, 0.86)",
            "--surface-tint": "rgba(232, 118, 30, 0.16)",
            "--surface-gingham": "rgba(48, 30, 66, 0.56)",
            "--surface-ribbon": "rgba(56, 34, 74, 0.55)",
            "--surface-lace": "rgba(255, 214, 170, 0.10)",
            "--border-soft": "rgba(232, 118, 30, 0.42)",
            "--border-light": "rgba(232, 118, 30, 0.28)",
            "--border-card": "rgba(150, 90, 210, 0.26)",
            "--border-strong": "rgba(232, 118, 30, 0.5)",
            "--page-base-top": "#1A0F26",
            "--page-base-mid": "#140A1E",
            "--page-base-bottom": "#0D0714",
            "--page-grid-x": "rgba(232, 118, 30, 0.10)",
            "--page-grid-y": "rgba(232, 118, 30, 0.07)",
            "--page-diag": "rgba(114, 245, 128, 0.05)",
            "--page-glow-1": "rgba(110, 40, 160, 0.35)",
            "--page-glow-2": "rgba(232, 118, 30, 0.16)",
            "--footer-wash": "rgba(70, 30, 100, 0.45)",
            "--footer-stripe": "rgba(232, 118, 30, 0.12)",
            "--lace-dot": "rgba(232, 118, 30, 0.5)",
            "--glass-strong": "rgba(48, 30, 66, 0.9)",
            "--glass-mid": "rgba(48, 30, 66, 0.72)",
            "--glass-soft": "rgba(48, 30, 66, 0.5)",
            "--glass-faint": "rgba(255, 255, 255, 0.04)",
            "--glass-inset": "rgba(255, 255, 255, 0.06)",
            "--chip-bg": "#221632",
            "--house-accent": "#E8761E",
            "--chat-grid-x": "rgba(232, 118, 30, 0.06)",
            "--chat-grid-y": "rgba(150, 90, 210, 0.06)",
            "--chat-wash-top": "rgba(26, 16, 38, 0.5)",
            "--chat-wash-bottom": "rgba(16, 10, 24, 0.6)",
            "--input-grid": "rgba(232, 118, 30, 0.07)",
            "--input-glass-top": "rgba(38, 26, 54, 0.95)",
            "--input-glass-bottom": "rgba(232, 118, 30, 0.10)",
            "--input-lace-dot": "rgba(232, 118, 30, 0.35)",
            "--input-lace-edge": "transparent",
            "--badge-bg-top": "rgba(56, 34, 74, 0.9)",
            "--badge-bg-bottom": "rgba(40, 24, 56, 0.8)",
            "--badge-border": "rgba(232, 118, 30, 0.4)",
            "--badge-text": "#FFD9A8",
            "--thinking-bg-1": "rgba(150, 90, 210, 0.14)",
            "--thinking-bg-2": "rgba(232, 118, 30, 0.10)",
            "--thinking-border": "rgba(150, 90, 210, 0.35)",
            "--thinking-header": "#FFA347",
            "--thinking-heart": "#8FE87A",
            # contrast pass: pumpkin orange on deep-purple-black cards
            "--sheen-rgb": "38, 26, 54",
            "--card-base-rgb": "26, 16, 38",
            "--card-flat-bg": "rgba(255, 255, 255, 0.05)",
            "--section-header": "#FFA347",
            "--accent-script": "#D8B98A",
            "--accent-weave-rgb": "232, 118, 30",
            "--lane-border": "rgba(232, 118, 30, 0.36)",
            "--lane-bg-top": "rgba(44, 28, 62, 0.92)",
            "--lane-bg-bottom": "rgba(26, 16, 38, 0.85)",
            "--disabled-opacity": "0.62",
            "--meta-theme-color": "#120A1A",
            "--md-strong": "#FFA347",
            "--md-em": "#C9AEE0",
            "--md-link": "#FFA347",
            "--success": "#8FE87A",
            "--error": "#FF7A5C",
            "--assistant-bubble-mode": "preset",
            "--assistant-bubble-top": "#241733",
            "--assistant-bubble-bottom": "#180F24",
            "--assistant-bubble-night": "#1A1026",
            "--assistant-bubble-text": "#F2E8D8",
            **_DARK_MD_CODE,
        },
    },
    "fall": {
        "label": "Fall",
        "tokens": {
            "--bg-page": "#F7EDDC",
            "--bg-chat": "#FBF3E4",
            "--bg-input": "#FFFBF2",
            "--text-primary": "#4A2E14",
            "--text-secondary": "#6E4A26",
            "--text-muted": "#7E5A34",
            "--text-tertiary": "#86603A",
            "--user-bubble-top": "#F2D6A8",
            "--user-bubble-bottom": "#E8BE84",
            "--user-border": "#B4651E",
            "--user-bubble-text": "#4A2E14",
        },
        # Cozy autumn: warm cream/amber, burnt orange, deep browns, gold.
        "chrome": {
            "--bg-card": "rgba(255, 249, 238, 0.78)",
            "--bg-secondary": "rgba(252, 243, 228, 0.82)",
            "--surface-raised": "rgba(255, 252, 244, 0.84)",
            "--surface-frost": "rgba(253, 246, 232, 0.62)",
            "--surface-paper": "rgba(255, 250, 238, 0.86)",
            "--surface-tint": "rgba(244, 214, 160, 0.52)",
            "--surface-gingham": "rgba(250, 238, 216, 0.64)",
            "--surface-ribbon": "rgba(250, 236, 200, 0.76)",
            "--surface-lace": "rgba(255, 252, 244, 0.56)",
            "--border-soft": "rgba(196, 132, 60, 0.46)",
            "--border-light": "rgba(226, 186, 130, 0.58)",
            "--border-card": "rgba(212, 168, 110, 0.36)",
            "--border-strong": "rgba(214, 164, 100, 0.72)",
            "--page-base-top": "#FBF2DE",
            "--page-base-mid": "#F6E8CC",
            "--page-base-bottom": "#F0DEBC",
            "--page-grid-x": "rgba(180, 101, 30, 0.12)",
            "--page-grid-y": "rgba(180, 101, 30, 0.09)",
            "--page-diag": "rgba(160, 120, 40, 0.06)",
            "--page-glow-1": "rgba(240, 196, 120, 0.5)",
            "--page-glow-2": "rgba(226, 158, 90, 0.4)",
            "--footer-wash": "rgba(240, 210, 150, 0.44)",
            "--footer-stripe": "rgba(180, 101, 30, 0.10)",
            "--lace-dot": "rgba(244, 214, 160, 0.95)",
            "--glass-strong": "rgba(255, 252, 242, 0.9)",
            "--glass-mid": "rgba(255, 250, 238, 0.72)",
            "--glass-soft": "rgba(255, 250, 238, 0.5)",
            "--glass-faint": "rgba(255, 250, 238, 0.18)",
            "--glass-inset": "rgba(255, 252, 244, 0.6)",
            "--chip-bg": "#FFF9EE",
            "--house-accent": "#B4651E",
            "--chat-grid-x": "rgba(180, 101, 30, 0.05)",
            "--chat-grid-y": "rgba(180, 101, 30, 0.035)",
            "--chat-wash-top": "rgba(255, 250, 240, 0.58)",
            "--chat-wash-bottom": "rgba(250, 240, 220, 0.72)",
            "--input-grid": "rgba(180, 101, 30, 0.045)",
            "--input-glass-top": "rgba(255, 252, 244, 0.98)",
            "--input-glass-bottom": "rgba(180, 101, 30, 0.06)",
            "--input-lace-dot": "rgba(250, 236, 206, 0.95)",
            "--input-lace-edge": "rgba(196, 132, 60, 0.4)",
            "--badge-bg-top": "rgba(255, 250, 238, 0.92)",
            "--badge-bg-bottom": "rgba(240, 208, 150, 0.68)",
            "--badge-border": "rgba(180, 101, 30, 0.32)",
            "--badge-text": "#6E4218",
            "--thinking-bg-1": "rgba(210, 140, 60, 0.12)",
            "--thinking-bg-2": "rgba(180, 120, 40, 0.10)",
            "--thinking-border": "rgba(196, 132, 60, 0.32)",
            "--thinking-header": "#C27438",
            "--thinking-heart": "#D98E4A",
            # contrast pass: amber/burnt-orange on warm cream cards
            "--sheen-rgb": "255, 250, 240",
            "--card-base-rgb": "255, 250, 238",
            "--card-flat-bg": "rgba(120, 80, 30, 0.05)",
            "--section-header": "#B4651E",
            "--accent-script": "#8A5A28",
            "--accent-weave-rgb": "226, 186, 130",
            "--lane-border": "rgba(196, 132, 60, 0.36)",
            "--lane-bg-top": "rgba(255, 250, 242, 0.94)",
            "--lane-bg-bottom": "rgba(244, 222, 180, 0.62)",
            "--meta-theme-color": "#F0DEBC",
            "--md-strong": "#A85712",
            "--md-em": "#6E4A26",
            "--md-link": "#B4651E",
            "--assistant-bubble-mode": "preset",
            "--assistant-bubble-top": "#F6E3C2",
            "--assistant-bubble-bottom": "#EDD2A6",
            "--assistant-bubble-night": "#4A331C",
            "--assistant-bubble-text": "#4A2E14",
        },
    },
}
_DEFAULT_THEME_PRESET = "sunrise-pink"

_THEME_FONT_OPTIONS = {
    "fredoka": {"label": "Fredoka (default, rounded & friendly)", "stack": "'Fredoka', 'Nunito', 'Segoe UI', sans-serif"},
    "nunito": {"label": "Nunito (clean & soft)", "stack": "'Nunito', 'Segoe UI', sans-serif"},
    "baloo": {"label": "Baloo 2 (playful & bubbly)", "stack": "'Baloo 2', 'Fredoka', sans-serif"},
    "lora": {"label": "Lora (cozy serif)", "stack": "'Lora', Georgia, serif"},
    "kalam": {"label": "Kalam (handwritten)", "stack": "'Kalam', cursive"},
}
_DEFAULT_THEME_FONT = "fredoka"
# Stock heading/title stacks (mirror main.css :root) — used when the default
# body font is active; any non-default font takes over all three roles.
_DEFAULT_FONT_DISPLAY = "'Kalam', 'Segoe UI', cursive"
_DEFAULT_FONT_TITLE = "'Princess Sofia', 'Great Vibes', cursive"


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_BUBBLE_SETTING_KEY = "theme_assistant_bubbles"


def _darken_hex(hex_color: str, factor: float) -> str:
    """Scale an #RRGGBB color toward black (factor < 1)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * factor))),
        max(0, min(255, round(g * factor))),
        max(0, min(255, round(b * factor))),
    )


def _mix_hex(hex_color: str, other: str, amount: float) -> str:
    """Blend hex_color toward `other` by `amount` (0..1)."""
    r1, g1, b1 = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    r2, g2, b2 = int(other[1:3], 16), int(other[3:5], 16), int(other[5:7], 16)
    mix = lambda a, b: max(0, min(255, round(a + (b - a) * amount)))
    return "#{:02X}{:02X}{:02X}".format(mix(r1, r2), mix(g1, g2), mix(b1, b2))


def _hex_luminance(hex_color: str) -> float:
    """Cheap perceived luminance 0..255 for light/dark branching."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _hex_rgb_triplet(hex_color: str) -> str:
    """'#RRGGBB' -> 'r, g, b' matching config.py's accent_rgb format."""
    return "{}, {}, {}".format(
        int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    )


def _sanitize_bubble_overrides(raw: str) -> dict:
    """Parse + re-validate the stored overrides blob (defense in depth)."""
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict = {}
    for identity, ov in data.items():
        if identity not in IDENTITIES or not isinstance(ov, dict):
            continue
        entry = {}
        for field in ("bg", "text"):
            val = ov.get(field)
            if isinstance(val, str) and _HEX_RE.match(val):
                entry[field] = val
        font = ov.get("font")
        if isinstance(font, str) and font in _THEME_FONT_OPTIONS:
            entry["font"] = font
        if entry:
            clean[identity] = entry
    return clean


async def _get_bubble_overrides() -> dict:
    return _sanitize_bubble_overrides(await _get_setting(_BUBBLE_SETTING_KEY, "{}"))


def _resolve_bubble_tokens(overrides: dict) -> dict:
    """Turn stored overrides into ready-to-apply CSS custom properties,
    keyed by identity. Only identities with an override appear; only the
    overridden tokens appear, so everything else falls back to the boy's
    own colors from config.py / the stylesheet defaults."""
    resolved: dict = {}
    for identity, ov in overrides.items():
        tokens: dict = {}
        bg = ov.get("bg")
        if bg:
            tokens["--identity-bubble-top"] = bg
            tokens["--identity-bubble-bottom"] = _darken_hex(bg, 0.90)
            # Night mode swaps the bubble gradient to --identity-bubble-night;
            # pin it too so her chosen color holds after candlelight.
            tokens["--identity-bubble-night"] = _darken_hex(bg, 0.72)


            if _hex_luminance(bg) >= 128:
                accent = _mix_hex(bg, "#000000", 0.45)
                gingham = _mix_hex(bg, "#000000", 0.12)
            else:
                accent = _mix_hex(bg, "#FFFFFF", 0.40)
                gingham = _mix_hex(bg, "#FFFFFF", 0.14)
            tokens["--identity-accent"] = accent
            tokens["--identity-accent-rgb"] = _hex_rgb_triplet(accent)
            tokens["--identity-gingham"] = gingham
            tokens["--identity-gingham-night"] = _mix_hex(gingham, "#000000", 0.45)
        text = ov.get("text")
        if text:
            tokens["--identity-bubble-text"] = text
        font = ov.get("font")
        if font:
            tokens["--identity-bubble-font"] = _THEME_FONT_OPTIONS[font]["stack"]
        if tokens:
            resolved[identity] = tokens
    return resolved


def _build_theme_tokens(preset: str, font: str) -> dict:
    """Full token map for a preset: chrome defaults (the exact pink literals
    the stylesheets fall back to) + the preset's chrome overrides + its core
    hex tokens + the chosen font. Every preset therefore defines the SAME
    key set, so switching presets always overwrites every previously applied
    inline variable — no stale tokens leak from the last vibe."""
    preset_key = preset if preset in _THEME_PRESETS else _DEFAULT_THEME_PRESET
    font_key = font if font in _THEME_FONT_OPTIONS else _DEFAULT_THEME_FONT
    tokens = dict(_CHROME_TOKEN_DEFAULTS)
    tokens.update(_THEME_PRESETS[preset_key].get("chrome", {}))
    tokens.update(_THEME_PRESETS[preset_key]["tokens"])
    stack = _THEME_FONT_OPTIONS[font_key]["stack"]
    tokens["--font-body"] = stack


    if font_key == _DEFAULT_THEME_FONT:
        tokens["--font-display"] = _DEFAULT_FONT_DISPLAY
        tokens["--font-title"] = _DEFAULT_FONT_TITLE
    else:
        tokens["--font-display"] = stack
        tokens["--font-title"] = stack
    return tokens


_ICON_BASE_SETTING_KEY = "theme_icon_base"
_ICON_BASE_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&*+,;=%-]+$")


def _sanitize_icon_base(raw) -> str:
    val = str(raw or "").strip().rstrip("/")
    return val if val and _ICON_BASE_RE.match(val) else ""


@router.get("/theme")
async def get_theme():
    """Current vibe preset + font, plus the computed token set and both
    allowlists so the frontend never hardcodes a hex or font stack twice."""
    preset = await _get_setting("theme_preset", _DEFAULT_THEME_PRESET)
    if preset not in _THEME_PRESETS:
        preset = _DEFAULT_THEME_PRESET
    font = await _get_setting("theme_font", _DEFAULT_THEME_FONT)
    if font not in _THEME_FONT_OPTIONS:
        font = _DEFAULT_THEME_FONT

    bubbles = await _get_bubble_overrides()
    icon_base = _sanitize_icon_base(await _get_setting(_ICON_BASE_SETTING_KEY, ""))

    return {
        "preset": preset,
        "font": font,
        "icon_base": icon_base,
        "tokens": _build_theme_tokens(preset, font),
        "bubbles": bubbles,
        "bubble_tokens": _resolve_bubble_tokens(bubbles),
        "identities": [
            {
                "key": name,
                "bubble_top": info["bubble_top"],
                "bubble_bottom": info["bubble_bottom"],
                "accent": info["accent"],
            }
            for name, info in IDENTITIES.items()
        ],
        "available": [
            {"key": key, "label": val["label"], "tokens": val["tokens"]}
            for key, val in _THEME_PRESETS.items()
        ],
        "font_options": [
            {"key": key, "label": val["label"]} for key, val in _THEME_FONT_OPTIONS.items()
        ],
    }


@router.put("/theme")
async def set_theme(request: Request):
    """Switch the vibe preset and/or font. Both are allowlist keys only —
    see module note above for why there is no raw-color field here."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    preset = (payload.get("preset") or _DEFAULT_THEME_PRESET).strip().lower()
    if preset not in _THEME_PRESETS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid preset: {preset}. Must be one of: {', '.join(sorted(_THEME_PRESETS))}"},
        )

    font = (payload.get("font") or _DEFAULT_THEME_FONT).strip().lower()
    if font not in _THEME_FONT_OPTIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid font: {font}. Must be one of: {', '.join(sorted(_THEME_FONT_OPTIONS))}"},
        )

    await _set_setting("theme_preset", preset)
    await _set_setting("theme_font", font)

    # Optional custom button-art base URL; only touched when the key is
    # present so plain preset/font saves never clobber it. Empty clears.
    icon_base = None
    if "icon_base" in payload:
        raw_base = payload.get("icon_base")
        icon_base = _sanitize_icon_base(raw_base)
        if raw_base and str(raw_base).strip() and not icon_base:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid icon_base: must be an http(s) URL"},
            )
        await _set_setting(_ICON_BASE_SETTING_KEY, icon_base)

    # Best-effort live refresh for every connected browser tab — mirrors
    # api/hub.py's _hub_changed() broadcast pattern. A broadcast failure
    # (or nobody connected) must never turn a theme save into an error.
    try:
        from services.connection_registry import broadcast
        from services.task_manager import spawn
        spawn(
            broadcast({"type": "hub_update", "reason": "theme_changed"}),
            name="theme_update_broadcast",
        )
    except Exception as e:
        log.debug("theme_changed broadcast failed to schedule: %s", e)

    result = {
        "ok": True,
        "preset": preset,
        "font": font,
        "tokens": _build_theme_tokens(preset, font),
    }
    if icon_base is not None:
        result["icon_base"] = icon_base
    return result


@router.put("/theme/bubbles")
async def set_theme_bubbles(request: Request):
    """Set (or clear) one identity's assistant-bubble override.

    Payload: {"identity": "Avery", "bg": "#RRGGBB"|null, "text": "#RRGGBB"|null,
              "font": "<font key>"|null}. Null/empty/missing fields clear that
    piece back to the boy's own look; all-cleared removes the entry entirely.
    Colors are strict #RRGGBB only; fonts must be _THEME_FONT_OPTIONS keys."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    identity = str(payload.get("identity") or "").strip()
    if identity not in IDENTITIES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown identity: {identity}. Must be one of: {', '.join(sorted(IDENTITIES))}"},
        )

    entry = {}
    for field in ("bg", "text"):
        val = payload.get(field)
        if val in (None, ""):
            continue
        val = str(val).strip()
        if not _HEX_RE.match(val):
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid {field} color: must be #RRGGBB hex"},
            )
        entry[field] = val

    font = payload.get("font")
    if font not in (None, ""):
        font = str(font).strip().lower()
        if font not in _THEME_FONT_OPTIONS:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid font: {font}. Must be one of: {', '.join(sorted(_THEME_FONT_OPTIONS))}"},
            )
        entry["font"] = font

    overrides = await _get_bubble_overrides()
    if entry:
        overrides[identity] = entry
    else:
        overrides.pop(identity, None)
    await _set_setting(_BUBBLE_SETTING_KEY, json.dumps(overrides))

    try:
        from services.connection_registry import broadcast
        from services.task_manager import spawn
        spawn(
            broadcast({"type": "hub_update", "reason": "theme_changed"}),
            name="theme_update_broadcast",
        )
    except Exception as e:
        log.debug("theme_changed broadcast failed to schedule: %s", e)

    return {
        "ok": True,
        "bubbles": overrides,
        "bubble_tokens": _resolve_bubble_tokens(overrides),
    }


@router.get("/usage")
async def get_usage(days: int = 7):
    """Token usage aggregates for the Settings Hub Usage panel.

    Fed by services/usage_tracker.py, which records the CLI's per-turn
    usage counters. Returns per-day totals plus today's identity split.
    """
    from services.usage_tracker import usage_summary
    return await usage_summary(days=days)


# =============================================================================
# ANAM GUIDE: A COMPLETE SETTINGS FEATURE EXAMPLE
# The Scribe shows the usual pattern in one place: GET current values, validate
# a PUT payload, save key/value rows, then let services/scribe.py consume them.
# SCRIBE (daily conversation digest) — five configurable knobs:
# provider, model, interval_minutes, message_threshold, digest_path.
# Settings table is source of truth at runtime; env vars + config.py
# defaults are the fallback chain. See services/scribe.py.
# =============================================================================

from services.background_generation import VALID_PROVIDERS as _VALID_SCRIBE_PROVIDERS


@router.get("/scribe")
async def get_scribe_settings():
    """Return effective Scribe config (settings > env > defaults)."""
    from services.scribe import _get_scribe_config
    from services.background_generation import resolve_background_provider
    config = await _get_scribe_config()
    effective_provider, effective_model, _options = await resolve_background_provider(
        config["provider"], config["model"]
    )
    return {
        **config,
        "effective_provider": effective_provider,
        "effective_model": effective_model,
        "valid_providers": sorted(_VALID_SCRIBE_PROVIDERS),
    }


@router.put("/scribe")
async def set_scribe_settings(request: Request):
    """Update one or more Scribe settings.

    Body: { "provider"?: str, "model"?: str, "interval_minutes"?: int,
            "message_threshold"?: int, "digest_path"?: str }

    Any field omitted is left untouched. Interval changes reschedule the
    running APScheduler job in place — no restart required.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    changes: dict[str, str] = {}
    errors: list[str] = []

    if "provider" in payload:
        provider = str(payload.get("provider") or "").strip().lower()
        if provider and provider not in _VALID_SCRIBE_PROVIDERS:
            errors.append(
                f"Invalid provider: {provider}. Must be one of: "
                f"{', '.join(sorted(_VALID_SCRIBE_PROVIDERS))}"
            )
        else:
            changes["scribe.provider"] = provider

    if "model" in payload:
        model = str(payload.get("model") or "").strip()
        changes["scribe.model"] = model

    if "interval_minutes" in payload:
        try:
            interval = int(payload.get("interval_minutes"))
            if interval < 1 or interval > 24 * 60:
                errors.append("interval_minutes must be between 1 and 1440")
            else:
                changes["scribe.interval_minutes"] = str(interval)
        except (TypeError, ValueError):
            errors.append("interval_minutes must be an integer")

    if "message_threshold" in payload:
        try:
            threshold = int(payload.get("message_threshold"))
            if threshold < 0:
                errors.append("message_threshold must be >= 0")
            else:
                changes["scribe.message_threshold"] = str(threshold)
        except (TypeError, ValueError):
            errors.append("message_threshold must be an integer")

    if "digest_path" in payload:
        path = str(payload.get("digest_path") or "").strip()
        if not path:
            errors.append("digest_path cannot be empty")
        else:
            # Don't allow stupid paths — must be relative or absolute, no nulls
            if "\x00" in path:
                errors.append("digest_path contains invalid characters")
            else:
                changes["scribe.digest_path"] = path

    if errors:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": errors},
        )

    if not changes:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "No valid fields provided"},
        )

    for key, value in changes.items():
        await _set_setting(key, value)

    # If interval changed, reschedule the running job so the new cadence
    # takes effect immediately.
    interval_changed = "scribe.interval_minutes" in changes
    rescheduled = False
    if interval_changed:
        try:
            from services.scribe import reschedule_scribe_job
            rescheduled = reschedule_scribe_job(int(changes["scribe.interval_minutes"]))
        except Exception as e:
            log.warning("Scribe: reschedule attempt failed: %s", e)

    # Return the new effective config
    from services.scribe import _get_scribe_config
    new_config = await _get_scribe_config()
    log.info("Scribe settings updated: %s", list(changes.keys()))
    return {
        "ok": True,
        "updated": [k.removeprefix("scribe.") for k in changes.keys()],
        "rescheduled": rescheduled if interval_changed else None,
        "config": new_config,
    }


# =============================================================================
# CLAUDE CODE PERMISSIONS
# =============================================================================

# The two settings files that control CC permissions.
# Project-scoped rules go in <project>/.claude/settings.local.json — that's the
# file CC actually reads for per-project permissions. The ~/.claude/projects/<slug>/
# directory stores session transcripts, not permissions, so writes there are silently
# ignored by CC (approval UI shows success but tools stay blocked).
_CC_GLOBAL_SETTINGS = Path.home() / ".claude" / "settings.json"
_CC_PROJECT_SETTINGS = BASE_DIR / ".claude" / "settings.local.json"


def _read_cc_settings(path: Path) -> dict:
    """Read a CC settings.json file, returning {} if missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_cc_settings(path: Path, data: dict) -> None:
    """Write a CC settings.json file, creating dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@router.post("/cc-permissions/allow")
async def add_cc_permission(request: Request):
    """Add a permission rule to CC settings.json.

    Body: { "rule": "Write(path/pattern*)", "scope": "global"|"project" }
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    rule = payload.get("rule", "").strip()
    scope = payload.get("scope", "project").strip()

    if not rule:
        return JSONResponse(status_code=400, content={"error": "Missing 'rule'"})
    if scope not in ("project", "global"):
        return JSONResponse(status_code=400, content={"error": "scope must be 'project' or 'global'"})

    path = _CC_PROJECT_SETTINGS if scope == "project" else _CC_GLOBAL_SETTINGS
    settings = _read_cc_settings(path)

    # Ensure permissions.allow exists
    if "permissions" not in settings:
        settings["permissions"] = {}
    if "allow" not in settings["permissions"]:
        settings["permissions"]["allow"] = []

    # Don't duplicate
    if rule not in settings["permissions"]["allow"]:
        settings["permissions"]["allow"].append(rule)
        _write_cc_settings(path, settings)
        log.info("Added CC permission rule '%s' to %s", rule, scope)

    return {"ok": True, "rule": rule, "scope": scope}
