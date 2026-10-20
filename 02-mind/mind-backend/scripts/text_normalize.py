from __future__ import annotations

from typing import Any


REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("â€™", "'"),
    ("â€˜", "'"),
    ("â€œ", '"'),
    ("â€�", '"'),
    ("â€”", "-"),
    ("â€“", "-"),
    ("â€¦", "..."),
    ("\u00e2\u0080\u0099", "'"),
    ("\u00e2\u0080\u0098", "'"),
    ("\u00e2\u0080\u009c", '"'),
    ("\u00e2\u0080\u009d", '"'),
    ("\u00e2\u0080\u0094", "-"),
    ("\u00e2\u0080\u0093", "-"),
    ("\u00e2\u0080\u00a6", "..."),
    ("\u00e2\u0088\u0092", "-"),
    ("â", "-"),
)


def normalize_text_artifacts(text: str) -> str:
    normalized = text
    for old, new in REPLACEMENTS:
        normalized = normalized.replace(old, new)
    return normalized


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text_artifacts(value)
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(item) for item in value)
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    return value
