"""Safety and path compatibility for tools exposed to remote API models."""

# ANAM GUIDE: REMOTE MODEL TOOL SAFETY
# What: The guard rails for the direct-API path — blocks remote models from reading credential files (.env, keys), blocks credential-reading commands, and scrubs secrets out of tool output. Also translates WSL/Git-Bash paths to Windows.
# Called by: services/claude_api.py (the direct Anthropic API provider) around every file/command tool call.
# Edit here when: adding a new secret filename or command pattern to block, or fixing path translation. Be careful loosening anything here — it protects the API keys.

from __future__ import annotations

import os
import re
from pathlib import Path


_WSL_PATH_RE = re.compile(r"^/(?:mnt/)?([a-zA-Z])/(.*)$")
_SENSITIVE_NAMES = {
    ".env",
    ".credentials.json",
    "credentials.json",
    "client_secret.json",
    "token.pickle",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_COMMAND_RE = re.compile(
    r"(?:^|[\s\\/\"'])\.env(?:\.[\w.-]+)?[*?]?(?:\s|$|[\"'])|"
    r"(?:credentials\.json|client_secret\.json|token\.pickle|\.credentials\.json)|"
    r"authorization\s*:|"
    r"(?:^|[;&|]\s*)(?:printenv|env)(?:\s|$)|"
    r"(?:^|[;&|]\s*)(?:set|export)\s*(?:$|[|>])",
    re.IGNORECASE,
)


def normalize_windows_tool_path(raw_path: str) -> Path:
    """Accept native Windows, WSL /mnt/c, or Git-Bash /c paths."""
    value = str(raw_path or "").strip()
    match = _WSL_PATH_RE.match(value.replace("\\", "/"))
    if match:
        drive, rest = match.groups()
        value = f"{drive.upper()}:/{rest}"
    return Path(value)


def sensitive_path_reason(path: Path | str) -> str | None:
    value = normalize_windows_tool_path(str(path))
    lowered_parts = {part.lower() for part in value.parts}
    name = value.name.lower()
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return "credential and environment files are not available to remote API models"
    if ".ssh" in lowered_parts:
        return "SSH credential paths are not available to remote API models"
    if name.endswith((".pem", ".p12", ".pfx")):
        return "private key and certificate bundles are not available to remote API models"
    return None


def sensitive_command_reason(command: str) -> str | None:
    text = str(command or "")
    if _SENSITIVE_COMMAND_RE.search(text):
        return "commands that read credentials, environment files, or authorization headers are blocked"
    return None


def redact_sensitive_output(text: str) -> str:
    """Remove configured secret values before tool output returns to a remote model."""
    result = str(text or "")
    for key, value in os.environ.items():
        upper = key.upper()
        if not any(marker in upper for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY")):
            continue
        value = str(value or "")
        if len(value) >= 8 and value in result:
            result = result.replace(value, f"[REDACTED:{key}]")
    return result
