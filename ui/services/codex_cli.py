"""Helpers for locating and interrogating the Codex CLI reliably."""

# ANAM GUIDE: CODEX CLI FINDER
# What: Tiny helper that finds where the Codex program is installed on this PC and reads its version — nothing more.
# Called by: services/codex_subprocess.py (to launch Codex) and api/settings.py (to show the version in Settings).
# Edit here when: Codex gets installed somewhere new and Anam can't find it.

from __future__ import annotations

import difflib
import json
import platform
import shutil
import subprocess
from pathlib import Path


_KNOWN_CHATGPT_CODEX_MODELS = {
    # Codex's local models_cache can lag behind the CLI/account route. The CLI
    # Keep confirmed account routes available even when the cache omits them.
    "gpt-5.6-sol",
    "gpt-6-astra",
}


def find_codex_executable() -> str | None:
    """Locate Codex even if the running server's PATH is stale."""
    direct = shutil.which("codex")
    if direct:
        return _native_codex_binary(Path(direct)) or direct

    home = Path.home()
    candidates = [
        home / "AppData" / "Roaming" / "npm" / "codex.cmd",
        home / "AppData" / "Roaming" / "npm" / "codex.exe",
        home / "AppData" / "Roaming" / "npm" / "codex",
        home / ".local" / "bin" / "codex",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _native_codex_binary(candidate) or str(candidate)
    return None


def _native_codex_binary(shim: Path) -> str | None:
    """Avoid cmd.exe's 8191-character limit for Anam's complete MCP map.

    npm's Windows launcher goes through a .cmd file, even though Codex itself
    is a native executable. Resolve the binary belonging to that installation
    rather than truncating tool config or depending on a separate installation.
    """
    if shim.suffix.lower() not in {".cmd", ".bat"}:
        return None
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    package = shim.parent / "node_modules" / "@openai" / "codex"
    candidates = sorted(package.glob(f"node_modules/@openai/codex-win32-{arch}/vendor/*/bin/codex.exe"))
    candidates += sorted(package.glob("vendor/*-pc-windows-msvc/codex/codex.exe"))
    return next((str(path) for path in candidates if path.is_file()), None)


def get_codex_version() -> str:
    """Return the installed Codex version, or 'unknown' if it can't be read."""
    cmd = find_codex_executable()
    if not cmd:
        return "unknown"

    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            timeout=5,
            text=True,
        )
    except Exception:
        return "unknown"

    output = (result.stdout or result.stderr or "").strip()
    return output or "unknown"


def get_codex_model_ids(cache_path: Path | None = None) -> list[str]:
    """Return cached models supported by Codex's ChatGPT account route.

    Codex refreshes ``models_cache.json`` itself. If it is absent or malformed,
    return an empty list so Settings does not block a potentially valid model.
    """
    path = cache_path or (Path.home() / ".codex" / "models_cache.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []

    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    model_ids: set[str] = set(_KNOWN_CHATGPT_CODEX_MODELS)
    for item in raw_models:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        # Responses-lite models can appear in the shared cache while the CLI's
        # ChatGPT subscription auth rejects them as unsupported.
        if item.get("use_responses_lite") is True:
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not slug:
            continue
        model_ids.add(slug)

    return sorted(model_ids)


def get_codex_default_model_id(cache_path: Path | None = None) -> str | None:
    """Return the visible Codex model with the highest cache priority."""
    path = cache_path or (Path.home() / ".codex" / "models_cache.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    candidates: list[tuple[int, str]] = []
    for item in raw_models:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        if item.get("use_responses_lite") is True:
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not slug:
            continue
        try:
            priority = int(item.get("priority", 9999))
        except (TypeError, ValueError):
            priority = 9999
        candidates.append((priority, slug))

    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][1]


def normalize_codex_model(model: str | None) -> str | None:
    """Return the canonical model id Anam should pass to `codex exec`.

    The Settings UI may accept friendlier forms like "5.6"; this route's CLI
    requires the full slug (for the new tier, "gpt-5.6-sol").
    """
    requested = str(model or "").strip().lower()
    if not requested:
        return None
    available = get_codex_model_ids()
    if not available or requested in available:
        return requested
    prefixed = f"gpt-{requested}" if not requested.startswith("gpt-") else requested
    if prefixed in available:
        return prefixed
    family_tier = next(
        (candidate for candidate in available if candidate.startswith(f"{prefixed}-")),
        None,
    )
    if family_tier:
        return family_tier
    return requested


def validate_codex_model(model: str) -> dict | None:
    """Return an inline-error payload when ``model`` is unknown to Codex."""
    requested = str(model or "").strip().lower()
    if not requested:
        return None

    available = get_codex_model_ids()
    prefixed = f"gpt-{requested}" if not requested.startswith("gpt-") else requested
    normalized = normalize_codex_model(requested)
    if not available or normalized in available:
        return None

    suggestion = None
    matches = difflib.get_close_matches(prefixed, available, n=1, cutoff=0.48)
    suggestion = matches[0] if matches else None

    message = f'"{model.strip()}" is not available to this Codex CLI.'
    if suggestion:
        message += f" Did you mean {suggestion}?"
    else:
        message += " Choose one of the suggested models or leave it blank for the default."
    return {
        "message": message,
        "suggestion": suggestion,
        "available": available,
    }
