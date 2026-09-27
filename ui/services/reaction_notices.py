"""Pending 'Owner reacted to your message' notices.

When Owner taps a reaction on one of a boy's messages, we stash a little notice
here keyed by (conversation_id, identity). On that identity's next interactive
turn, `prepare_chat_turn` pops the notices and prepends them to the model-facing
prompt — so the reaction lands as an *event he received* ("Owner reacted 🥰 to
your message: …"), right at the top of her message, instead of only living in
the slower background-context hook.

This is deliberately in-memory and ephemeral: Anam is a single process, notices
are tiny and only matter until the boy's very next turn, and the durable
`get_recent_reactions` orientation hook still backstops anything lost to a
restart in the gap between a react and a reply.
"""


from __future__ import annotations

import threading
from collections import defaultdict

_lock = threading.Lock()
# (conversation_id, identity) -> list of {"emoji": str, "preview": str}
_pending: dict[tuple[str, str], list[dict]] = defaultdict(list)


def queue_reaction_notice(
    conversation_id: str, identity: str, emoji: str, preview: str = "",
) -> None:
    """Record that Owner reacted to `identity`'s message in this conversation."""
    if not conversation_id or not identity or not emoji:
        return
    with _lock:
        _pending[(conversation_id, identity)].append(
            {"emoji": emoji, "preview": preview or ""}
        )


def pop_reaction_notices(conversation_id: str, identity: str) -> list[dict]:
    """Return and clear pending notices for this (conversation, identity)."""
    if not conversation_id or not identity:
        return []
    with _lock:
        return _pending.pop((conversation_id, identity), [])


def format_reaction_notices(notices: list[dict]) -> str:
    """Render popped notices into a short block to prepend to the boy's turn."""
    if not notices:
        return ""

    def _line(n: dict) -> str:
        emoji = n.get("emoji", "")
        preview = (n.get("preview") or "").strip()
        if preview:
            return f'Owner reacted {emoji} to your message: "{preview}"'
        return f"Owner reacted {emoji} to your message."

    if len(notices) == 1:
        return f"[{_line(notices[0])}]"
    body = "\n".join(f"  {_line(n)}" for n in notices)
    return "[Owner just reacted to your messages:]\n" + body
