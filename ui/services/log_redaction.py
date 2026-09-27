"""Central log-output redaction for credentials and authenticated URLs."""

# ANAM GUIDE: SECRET SCRUBBER FOR LOGS
# What: safety filter that blanks out API keys, passwords, tokens, and secret-looking URLs before anything is written to the server logs.
# Called by: server.py installs it on the logging system at startup — after that it runs on every log line automatically.
# Edit here when: a new kind of secret is showing up in logs and needs a pattern added (the _*_RE regexes at the top).

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"authorization|bearer[_-]?token|password)\b\s*[=:]\s*[\"']?)"
    r"([^\s,;\"'}]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_KEY_RE = re.compile(r"\b(?:sk-(?:ant-|or-)?[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{40,})\b")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|access[_-]?token|auth|secret)=)[^&#\s]+"
)
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,;:)]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", raw) + trailing

    host = parsed.hostname or ""
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"

    segments = []
    for segment in parsed.path.split("/"):
        # Authenticated MCP endpoints commonly use a single high-entropy path
        # segment. Preserve readable route names while hiding token-shaped ones.
        if len(segment) >= 24 and re.search(r"[A-Za-z]", segment) and re.search(r"\d", segment):
            segments.append("[REDACTED]")
        else:
            segments.append(segment)
    path = "/".join(segments)
    query = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", f"?{parsed.query}").lstrip("?") if parsed.query else ""
    return urlunsplit((parsed.scheme, netloc, path, query, parsed.fragment)) + trailing


def redact_text(value: object) -> str:
    """Return a display-safe string without common credential shapes."""
    text = str(value)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _KEY_RE.sub("[REDACTED]", text)
    return _URL_RE.sub(_redact_url, text)


class RedactingFormatter(logging.Formatter):
    """Redact the fully rendered record, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def install_redacting_formatters(handlers: list[logging.Handler], fmt: str) -> None:
    formatter = RedactingFormatter(fmt)
    for handler in handlers:
        handler.setFormatter(formatter)
