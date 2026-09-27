"""Code-span masking for reply-tag parsers (`<face>`, `<orb>`, …)."""

from __future__ import annotations

import re

# Fenced blocks first (they may contain lone backticks), then inline spans.
# DOTALL on the fence so it spans lines; inline spans deliberately may NOT
# cross a newline, which is how Markdown treats them.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`[^`\n]*`")

# A filler that cannot participate in any tag and cannot close a code span.
_FILL = "\x00"


def _blank(match: re.Match) -> str:
    """Same-length replacement so every offset in the original stays valid."""
    return _FILL * (match.end() - match.start())


def mask_code(content: str) -> str:
    """Return a same-length copy of `content` with code spans blanked out.

    len(mask_code(x)) == len(x) is an invariant the callers rely on: they find
    tag spans in the masked copy and then slice the ORIGINAL by those offsets.
    """
    if not content:
        return content
    masked = _FENCE_RE.sub(_blank, content)
    masked = _INLINE_RE.sub(_blank, masked)
    return masked


def find_tag_spans(content: str, pattern: re.Pattern) -> list[tuple[int, int, str]]:
    """Find every real (non-code) occurrence of `pattern` in `content`.

    Returns a list of (start, end, group1_from_the_ORIGINAL_text). The pattern
    is matched against the masked copy so prose-in-backticks can never open a
    tag; the captured text is then sliced out of the untouched original so
    nothing the boy actually wrote is lost or mangled.
    """
    if not content:
        return []
    masked = mask_code(content)
    spans: list[tuple[int, int, str]] = []
    for m in pattern.finditer(masked):
        # Group 1's offsets are identical in both strings (same length), so
        # slice the real content rather than trusting the blanked copy.
        try:
            g_start, g_end = m.span(1)
        except IndexError:  # pattern with no capture group
            g_start, g_end = m.span()
        spans.append((m.start(), m.end(), content[g_start:g_end]))
    return spans


def strip_spans(content: str, spans: list[tuple[int, int, str]], filler: str = " ") -> str:
    """Remove the given (start, end, _) spans from `content`, back to front."""
    if not spans:
        return content
    out = content
    for start, end, _ in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + filler + out[end:]
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out
