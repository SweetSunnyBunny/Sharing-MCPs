'Split a long chat reply into pieces a chat platform will accept.'
from __future__ import annotations

import re


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CLOSE = "\n```"


def split_chat_message(text: str, limit: int = 1900) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    cur: list[str] = []
    fence: str | None = None  # the opening fence line while inside a code block

    def cur_len() -> int:
        return len("\n".join(cur))

    def flush(inside_fence: bool) -> None:
        body = "\n".join(cur).strip("\n")
        if not body.strip():
            return
        if inside_fence:
            body += _CLOSE
        chunks.append(body)

    def restart() -> None:
        cur.clear()
        if fence:
            cur.append(fence)

    def overhead() -> int:
        return len(_CLOSE) if fence else 0

    for line in text.split("\n"):
        sep = 1 if cur else 0
        if cur and cur_len() + sep + len(line) + overhead() > limit:
            # Prefer a paragraph break in the back half of this piece, as long
            # as no fence opens or closes between there and here.
            cut_at = None
            if fence is None:
                for i in range(len(cur) - 1, len(cur) // 2, -1):
                    if cur[i] == "" and not any(_FENCE_RE.match(x) for x in cur[i:]):
                        cut_at = i
                        break
            if cut_at is not None:
                carry = cur[cut_at + 1:]
                del cur[cut_at:]
                flush(False)
                cur.clear()
                cur.extend(carry)
            else:
                flush(fence is not None)
                restart()

        # A single line longer than a whole piece: hard-split it.
        while cur_len() + (1 if cur else 0) + len(line) + overhead() > limit:
            room = limit - cur_len() - (1 if cur else 0) - overhead()
            if room <= 0:
                flush(fence is not None)
                restart()
                continue
            cur.append(line[:room])
            line = line[room:]
            flush(fence is not None)
            restart()

        cur.append(line)
        if _FENCE_RE.match(line):
            fence = None if fence else line.strip()

    if cur:
        flush(False)
    return chunks
