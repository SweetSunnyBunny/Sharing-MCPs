'Head-and-tail excerpts: shorten a message without cutting off its ending.'


from __future__ import annotations

DEFAULT_MARKER = " [...] "

# The tail gets the larger share: that's where the result and the ask live.
_HEAD_SHARE = 0.4


def _soft_cut_end(text: str, n: int) -> str:
    """First n chars, pulled back to the last space if one is close by."""
    piece = text[:n]
    space = piece.rfind(" ")
    if space >= n * 0.8:
        piece = piece[:space]
    return piece.rstrip()


def _soft_cut_start(text: str, n: int) -> str:
    """Last n chars, pushed forward to the next space if one is close by."""
    piece = text[-n:] if n > 0 else ""
    space = piece.find(" ")
    if 0 <= space <= n * 0.2:
        piece = piece[space + 1:]
    return piece.lstrip()


def head_tail(text: str, limit: int, marker: str = DEFAULT_MARKER) -> str:
    """Return text unchanged if it fits in limit; otherwise head + marker + tail.

    The result is never longer than limit + len(marker). A limit of 0 or less
    means "no limit".
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    head_n = max(1, int(limit * _HEAD_SHARE))
    tail_n = max(1, limit - head_n)
    return _soft_cut_end(text, head_n) + marker + _soft_cut_start(text, tail_n)
