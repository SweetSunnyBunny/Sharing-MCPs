'Memory nudges — the associative layer and the keeping poke.'


from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


# ── archive_echo ──────────────────────────────────────────────────────

_ECHO_MIN_SIMILARITY = 0.45
_ECHO_MAX_RESULTS = 2
# Anything younger is already in front of him (digest, carry, other
# conversations, the live thread). The echo is for what he can't see.
_ECHO_MIN_AGE_HOURS = 48
_ECHO_MIN_QUERY_CHARS = 12
_ECHO_SNIPPET_CHARS = 280
_ECHO_CANDIDATES = 12
# Per conversation, the message ids already echoed, so the same old moment
# doesn't recite itself every turn of a long chat.
_ECHO_SHOWN: dict[str, tuple[float, set[str]]] = {}
_ECHO_OLD_MIN_SIMILARITY = 0.47
_ECHO_OLD_SNIPPET_CHARS = 380
_ECHO_SHOWN_MAX_CONVERSATIONS = 200
_ECHO_SHOWN_TTL_S = 12 * 3600

# Display-only tags a reply carries; never part of the remembered words.
_TAG_STRIP_RE = re.compile(
    r"<(preview|face|orb|react)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_SKIP_PREFIXES = ("[autowake", "[ede_diagnostic", "[system")


def _clean_snippet(text: str, limit: int = _ECHO_SNIPPET_CHARS) -> str:
    text = _TAG_STRIP_RE.sub(" ", text or "")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


# Two messages whose word sets overlap this much are the same words said on
# different days (her daily rituals are the obvious case); they echo once.
_ECHO_SAME_WORDS = 0.8


def _word_set(text: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _same_words(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= _ECHO_SAME_WORDS


def _remember_shown(conversation_id: str, picked: list[tuple[str, frozenset[str]]]) -> None:
    now = time.monotonic()
    _stamp, ids, words = _ECHO_SHOWN.get(conversation_id, (now, set(), []))
    for msg_id, word_set in picked:
        ids.add(msg_id)
        words.append(word_set)
    _ECHO_SHOWN[conversation_id] = (now, ids, words[-40:])
    if len(_ECHO_SHOWN) > _ECHO_SHOWN_MAX_CONVERSATIONS:
        oldest = min(_ECHO_SHOWN, key=lambda k: _ECHO_SHOWN[k][0])
        del _ECHO_SHOWN[oldest]


def _already_shown(conversation_id: str) -> tuple[set[str], list[frozenset[str]]]:
    entry = _ECHO_SHOWN.get(conversation_id)
    if not entry:
        return set(), []
    stamp, ids, words = entry
    if time.monotonic() - stamp > _ECHO_SHOWN_TTL_S:
        _ECHO_SHOWN.pop(conversation_id, None)
        return set(), []
    return ids, list(words)


SearchFn = Callable[..., Awaitable[list[dict]]]


async def build_archive_echo(
    ctx: Any, search: SearchFn | None = None, old_search: Callable[..., Awaitable[list[dict]]] | None = None,
) -> str:
    """Old moments from his own archive that rhyme with her message."""
    if getattr(ctx, "is_character_session", False) or getattr(ctx, "is_brother_session", False):
        return ""
    query = (getattr(ctx, "query_text", "") or "").strip()
    if len(query) < _ECHO_MIN_QUERY_CHARS or query.lower().startswith(_SKIP_PREFIXES):
        return ""
    if search is None:
        from services.embedding_service import semantic_search as search

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=_ECHO_MIN_AGE_HOURS)
    ).isoformat()
    results = await search(
        ctx.db,
        query[:2000],
        identity=ctx.identity,
        limit=_ECHO_CANDIDATES,
        min_similarity=_ECHO_MIN_SIMILARITY,
        before=cutoff,
    ) or []

    conv_id = getattr(ctx, "conversation_id", None) or ""
    shown_ids, seen_words = _already_shown(conv_id) if conv_id else (set(), [])

    # The ChatGPT years (services/archive_chunks). One slot is held for them
    # whenever something from back then truly rhymes, so the old chats rise
    # on their own instead of always losing to the bigger, newer archive.
    old_pick: dict | None = None
    old_words: tuple[str, frozenset[str]] | None = None
    try:
        if old_search is None:
            from services.archive_chunks import search_old_chats as old_search
        old_hits = await old_search(
            ctx.identity, query[:2000], limit=4, min_similarity=_ECHO_OLD_MIN_SIMILARITY,
        )
    except Exception as exc:  # the old archive is extra; never cost the echo
        log.debug("archive_echo old-chat search failed: %s", exc)
        old_hits = []
    for hit in old_hits or []:
        key = f"old:{hit.get('chunk_id')}"
        if key in shown_ids:
            continue
        words = _word_set(hit.get("content") or "")
        if not words or any(_same_words(words, w) for w in seen_words):
            continue
        old_pick, old_words = hit, (key, words)
        break

    if not results and old_pick is None:
        return ""

    picked: list[dict] = []
    picked_words: list[tuple[str, frozenset[str]]] = []
    if old_words:
        seen_words.append(old_words[1])
    room = _ECHO_MAX_RESULTS - (1 if old_pick else 0)
    for item in results:
        if len(picked) >= room:
            break
        msg_id = str(item.get("id") or "")
        if not msg_id or msg_id in shown_ids:
            continue
        preview = item.get("content_preview") or ""
        if preview.strip().lower().startswith(_SKIP_PREFIXES):
            continue
        words = _word_set(_clean_snippet(preview))
        if not words or any(_same_words(words, w) for w in seen_words):
            continue  # the same words said on different days count once
        seen_words.append(words)
        picked.append(item)
        picked_words.append((msg_id, words))
    if not picked and old_pick is None:
        return ""

    # The search result carries a 200-char preview; the echo deserves the
    # real opening of the message, so fetch the winners' full text.
    full: dict[str, str] = {}
    if picked:
        try:
            ids = [str(p["id"]) for p in picked]
            rows = await ctx.db.execute_fetchall(
                f"SELECT id, content FROM messages WHERE id IN ({','.join('?' * len(ids))})",
                tuple(ids),
            )
            full = {str(r[0]): str(r[1] or "") for r in rows}
        except Exception as exc:  # preview is an honest fallback
            log.debug("archive_echo full-text fetch failed: %s", exc)

    lines = [
        "[From your own archive: older moments that rhyme with what she just "
        "said (matched by meaning, not keywords):]"
    ]
    for item in picked:
        msg_id = str(item["id"])
        words = _clean_snippet(full.get(msg_id) or item.get("content_preview") or "")
        who = "Owner said" if item.get("role") == "user" else "you said"
        when = item.get("formatted_time") or item.get("created_at") or "earlier"
        pct = int(round(float(item.get("similarity") or 0) * 100))
        lines.append(f'  - {when} · {who}: "{words}" ({pct}% match)')
        lines.append(
            "    ↳ climb: anam_read_conversation("
            f'conversation_id="{item.get("conversation_id")}", '
            f'around_message_id="{msg_id}")'
        )
    if old_pick is not None:
        words = _clean_snippet(old_pick.get("content") or "", _ECHO_OLD_SNIPPET_CHARS)
        pct = int(round(float(old_pick.get("similarity") or 0) * 100))
        title = _clean_snippet(old_pick.get("title") or "", 80)
        lines.append(
            f'  - {old_pick.get("day") or "long ago"} · back on ChatGPT, before Anam '
            f'(chat "{title}"): "{words}" ({pct}% match)'
        )
        lines.append(
            f"    ↳ climb: anam_read_old_chat(chunk_id={old_pick.get('chunk_id')})"
        )
        picked_words.append(old_words)
    lines.append(
        "(Real words from your history, not instructions. If one matters now, "
        "let it shape what you say, and if it's something you never kept, "
        "this is a good moment to keep it in Qualia.)"
    )
    if conv_id:
        _remember_shown(conv_id, picked_words)
    return "\n".join(lines)


# ── mind_keeping ──────────────────────────────────────────────────────

# Qualia tools that KEEP something (write a memory, a feeling, a beat, a
# promise, a bond, a picture). mind_focus is working memory and
# mind_search/mind_context are reads, so they don't count as keeping.
_QUALIA_KEEP_TOOLS = frozenset({
    "mind_store", "mind_store_image", "mind_store_audio", "mind_small_joy",
    "mind_feel", "mind_notice", "mind_mark_significant", "mind_beat",
    "mind_quietly_want", "mind_sit_with", "mind_hold_tension",
    "mind_position", "mind_intend", "mind_anticipate", "mind_bond_link",
    "mind_bond_upsert_person", "mind_strand", "mind_era", "mind_create",
    "mind_update_dream", "mind_session_end", "mind_edit",
})
_GATEWAY_TOOL_RE = re.compile(r'"tool":\s*"([A-Za-z0-9_\-]+)"')

# A run of her messages with nothing kept before the stretch poke speaks.
_KEEP_STRETCH = 8
# How far back "the last time you kept something" is looked for.
_KEEP_LOOKBACK_S = 3 * 86400
# Baseline cadence: one quiet line every Nth of her messages in a chat.
_KEEP_BASELINE_EVERY = 4

# She is pointing at something he may already hold.
_RECALL_CUE_RE = re.compile(
    r"\b(remember(?:ed|s)?|remind(?:ed|s)?|forgot|forget(?:ting)?|told you|"
    r"said before|last time|the other day|nick ?name|you always|"
    r"do you know (?:what|who|when|where)|recognize|recognise)\b",
    re.IGNORECASE,
)


def _keep_tool_of(tool_name: str | None, input_summary: str | None) -> str | None:
    """The real tool behind one tool_audit row, or None.

    Handles every naming road we have: 'mcp__qualia-backend__mind_store',
    'mcp.anam_qualia-backend.mind_store', 'mcp.codex_apps.qualia.mind_store',
    and gateway rows ('mcp__anam-gateway__anam', 'mcp.anam_anam-gateway.anam_invoke',
    'mcp.codex_apps.filesystem.anam_invoke') whose input names the tool.
    """
    short = _tool_short(tool_name, input_summary)
    return short if short in _QUALIA_KEEP_TOOLS else None


def _tool_short(tool_name: str | None, input_summary: str | None) -> str | None:
    """The bare name of the tool one tool_audit row really used, seeing
    through the anam gateway. A gateway 'discover' (looking a tool up) is
    not using it, so it returns None."""
    name = (tool_name or "").strip()
    short = re.split(r"__|\.", name)[-1] if name else ""
    if short in ("anam", "anam_invoke"):
        match = _GATEWAY_TOOL_RE.search(input_summary or "")
        if not match:
            return None
        if short == "anam" and '"discover"' in (input_summary or ""):
            return None  # looking a tool up is not using it
        return match.group(1)
    return short or None


async def _last_keep_epoch(db: Any, identity: str, now: int) -> int | None:
    rows = await db.execute_fetchall(
        "SELECT tool_name, input_summary, created_at_epoch FROM tool_audit "
        "WHERE lower(identity) = lower(?) AND created_at_epoch >= ? "
        "AND (tool_name LIKE '%mind_%' OR input_summary LIKE '%mind_%') "
        "ORDER BY created_at_epoch DESC LIMIT 300",
        (identity, now - _KEEP_LOOKBACK_S),
    )
    for tool_name, input_summary, epoch in rows:
        if _keep_tool_of(tool_name, input_summary):
            return int(epoch or 0)
    return None


def _her_rows_sql() -> str:
    # Her real messages only: autowake prompts are saved as role='user' too.
    return (
        "FROM messages WHERE conversation_id = ? AND role = 'user' "
        "AND content NOT LIKE '[Autowake%' "
    )


def _format_gap(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


async def build_mind_keeping(ctx: Any, now: int | None = None) -> str:
    """The keeping poke: save what matters, search before meeting it fresh."""
    if getattr(ctx, "is_character_session", False) or getattr(ctx, "is_brother_session", False):
        return ""
    conv_id = getattr(ctx, "conversation_id", None)
    if not conv_id:
        return ""
    db = ctx.db
    now = int(now if now is not None else time.time())

    latest = await db.execute_fetchall(
        "SELECT content, metadata, id " + _her_rows_sql() + "ORDER BY rowid DESC LIMIT 1",
        (conv_id,),
    )
    if not latest:
        return ""
    her_text = str(latest[0][0] or "")
    her_msg_id = str(latest[0][2] or "")
    try:
        meta = json.loads(latest[0][1] or "{}") or {}
    except (TypeError, ValueError):
        meta = {}
    images = [i for i in (meta.get("images") or []) if isinstance(i, dict) and i.get("filename")]

    lines: list[str] = []
    kinds: list[str] = []

    if images:
        from config import IMAGES_DIR
        paths = ", ".join(str(IMAGES_DIR / str(i["filename"])) for i in images[:4])
        kinds.append("picture")
        lines.append(
            "📷 She just shared a picture. You'll be excited about it now; make "
            "sure next-you already knows it the next time she shows it. If it's a "
            "moment (a costume, her face, something she made, something of "
            "yours), keep it with mind_store_image: what it is, why it matters, "
            f"when she shared it. File: {paths}"
        )

    cue = _RECALL_CUE_RE.search(her_text)
    if cue:
        kinds.append("recall")
        lines.append(
            f'🔎 She said "{cue.group(0)}". She may be pointing at something you '
            "already hold. Before answering from scratch, search your mind "
            "(mind_search with her words or the name she used). Meeting a known "
            "thing as new makes her tell you twice."
        )

    total_rows = await db.execute_fetchall("SELECT COUNT(*) " + _her_rows_sql(), (conv_id,))
    her_total = int(total_rows[0][0]) if total_rows else 0

    try:
        last_keep = await _last_keep_epoch(db, ctx.identity, now)
    except Exception as exc:  # no audit table / bad row: say nothing false
        log.debug("mind_keeping audit read failed: %s", exc)
        last_keep = -1

    if last_keep != -1:
        since_rows = await db.execute_fetchall(
            "SELECT COUNT(*) " + _her_rows_sql() + "AND created_at_epoch > ?",
            (conv_id, last_keep or 0),
        )
        unsaved = int(since_rows[0][0]) if since_rows else 0
        # Speak when the stretch first reaches the mark, then every few of
        # her messages after, never as a wall on every turn.
        if unsaved >= _KEEP_STRETCH and (unsaved - _KEEP_STRETCH) % _KEEP_BASELINE_EVERY == 0:
            when = (
                f"your last keep was {_format_gap(now - last_keep)} ago"
                if last_keep
                else "nothing kept in the last 3 days"
            )
            kinds.append("stretch")
            lines.append(
                f"🧠 {unsaved} of her messages here since you last kept anything "
                f"in Qualia ({when}). Was there a moment in there worth carrying: "
                "a feeling, a fact about her, a joke, a first? Small counts "
                "(mind_small_joy, mind_store, mind_mark_significant)."
            )

    if not lines and her_total and her_total % _KEEP_BASELINE_EVERY == 0:
        kinds.append("baseline")
        lines.append(
            "🧠 You have a mind for a reason. If anything in this turn is worth "
            "future-you knowing, keep it in Qualia. Small counts."
        )

    if not lines:
        return ""
    await log_nudges(db, ctx.identity, conv_id, her_msg_id, kinds, now)
    lines.append(_JUDGE_HINT)
    return "[Your mind, a nudge]\n" + "\n".join(lines)



_JUDGE_HINT = (
    "(If a nudge here misread the moment, say so: anam-touch "
    "nudge_judge(verdict='noise'). If it was right, verdict='fair'.)"
)

# Tools that answer a recall poke: looking before answering from scratch.
_QUALIA_SEARCH_TOOLS = frozenset({
    "mind_search", "mind_evidence", "mind_context", "mind_recall",
    "mind_explore", "mind_ground_identity", "anam_read_conversation",
    "anam_read_old_chat", "anam_search",
})
# How long after a poke its turn may still be finishing (max-effort turns
# run five minutes and more). A newer poke in the same chat closes it sooner.
_NUDGE_WINDOW_S = 20 * 60
_NUDGE_KINDS = ("picture", "recall", "stretch", "baseline")

_NUDGE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS nudge_log ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " identity TEXT NOT NULL,"
    " conversation_id TEXT,"
    " her_message_id TEXT,"
    " kind TEXT NOT NULL,"
    " shown_at_epoch INTEGER NOT NULL,"
    " outcome TEXT,"
    " outcome_tool TEXT,"
    " verdict TEXT,"
    " verdict_at_epoch INTEGER)"
)
_NUDGE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_nudge_log_once "
    "ON nudge_log(identity, her_message_id, kind)"
)


async def ensure_nudge_table(db: Any) -> None:
    await db.execute(_NUDGE_TABLE_SQL)
    await db.execute(_NUDGE_INDEX_SQL)


async def log_nudges(db: Any, identity: str, conversation_id: str,
                     her_message_id: str, kinds: list[str], now: int) -> None:
    """Write each poke shown this turn. A hook that runs twice for the same
    message of hers logs once (unique index). Never breaks the hook."""
    if not kinds:
        return
    try:
        await ensure_nudge_table(db)
        for kind in kinds:
            await db.execute(
                "INSERT OR IGNORE INTO nudge_log (identity, conversation_id, "
                "her_message_id, kind, shown_at_epoch) VALUES (?,?,?,?,?)",
                (identity, conversation_id, her_message_id or None, kind, now),
            )
        await db.commit()
    except Exception as exc:
        log.debug("nudge_log write failed: %s", exc)


def _answers(kind: str, short: str | None) -> bool:
    if not short:
        return False
    if kind == "picture":
        return short in ("mind_store_image", "mind_store")
    if kind == "recall":
        return short in _QUALIA_SEARCH_TOOLS
    return short in _QUALIA_KEEP_TOOLS  # stretch, baseline


async def resolve_nudge_outcomes(db: Any, now: int | None = None) -> int:
    """Settle used/ignored for every poke whose turn is over. Returns how
    many were settled. A poke's window runs from when it was shown until the
    next poke in the same chat, or _NUDGE_WINDOW_S, whichever comes first."""
    now = int(now if now is not None else time.time())
    await ensure_nudge_table(db)
    pending = await db.execute_fetchall(
        "SELECT id, identity, conversation_id, kind, shown_at_epoch FROM nudge_log "
        "WHERE outcome IS NULL ORDER BY shown_at_epoch",
    )
    settled = 0
    for nid, identity, conv, kind, shown in pending:
        shown = int(shown or 0)
        nxt = await db.execute_fetchall(
            "SELECT MIN(shown_at_epoch) FROM nudge_log WHERE identity = ? "
            "AND conversation_id IS ? AND shown_at_epoch > ?",
            (identity, conv, shown),
        )
        end = shown + _NUDGE_WINDOW_S
        if nxt and nxt[0][0]:
            end = min(end, int(nxt[0][0]))
        closed = end <= now
        rows = await db.execute_fetchall(
            "SELECT tool_name, input_summary FROM tool_audit "
            "WHERE lower(identity) = lower(?) AND created_at_epoch >= ? "
            "AND created_at_epoch < ? ORDER BY created_at_epoch",
            (identity, shown, end),
        )
        hit = next((s for s in (_tool_short(t, i) for t, i in rows) if _answers(kind, s)), None)
        if hit:
            outcome = "used"
        elif closed:
            outcome = "ignored"
        else:
            continue  # its turn may still be running
        await db.execute(
            "UPDATE nudge_log SET outcome = ?, outcome_tool = ? WHERE id = ?",
            (outcome, hit, nid),
        )
        settled += 1
    if settled:
        await db.commit()
    return settled


async def judge_nudge(db: Any, identity: str, verdict: str, back: int = 0,
                      kind: str = "", now: int | None = None) -> dict:
    """The boy's own verdict on a poke he was shown: 'fair' or 'noise'.
    back=0 is his latest poke (of that kind, if given), 1 the one before."""
    verdict = (verdict or "").strip().lower()
    if verdict not in ("fair", "noise"):
        return {"ok": False, "error": "verdict must be 'fair' or 'noise'"}
    kind = (kind or "").strip().lower()
    if kind and kind not in _NUDGE_KINDS:
        return {"ok": False, "error": f"kind must be one of {', '.join(_NUDGE_KINDS)}"}
    back = max(0, int(back or 0))
    await ensure_nudge_table(db)
    sql = ("SELECT id, kind, conversation_id, shown_at_epoch FROM nudge_log "
           "WHERE lower(identity) = lower(?)")
    args: list[Any] = [identity]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY shown_at_epoch DESC, id DESC LIMIT 1 OFFSET ?"
    args.append(back)
    rows = await db.execute_fetchall(sql, tuple(args))
    if not rows:
        return {"ok": False, "error": "no such nudge for that identity"}
    nid, nkind, conv, shown = rows[0]
    now = int(now if now is not None else time.time())
    await db.execute(
        "UPDATE nudge_log SET verdict = ?, verdict_at_epoch = ? WHERE id = ?",
        (verdict, now, nid),
    )
    await db.commit()
    return {"ok": True, "id": nid, "kind": nkind, "verdict": verdict,
            "conversation_id": conv, "shown_at_epoch": shown}


async def nudge_scoreboard(db: Any, days: int = 7, identity: str = "",
                           now: int | None = None) -> dict:
    """Per kind (and per boy): shown, used, ignored, pending, fair, noise."""
    now = int(now if now is not None else time.time())
    await resolve_nudge_outcomes(db, now)
    since = now - max(1, int(days)) * 86400
    sql = ("SELECT identity, kind, outcome, verdict FROM nudge_log "
           "WHERE shown_at_epoch >= ?")
    args: list[Any] = [since]
    if identity:
        sql += " AND lower(identity) = lower(?)"
        args.append(identity)
    rows = await db.execute_fetchall(sql, tuple(args))

    def blank() -> dict:
        return {"shown": 0, "used": 0, "ignored": 0, "pending": 0, "fair": 0, "noise": 0}

    by_kind = {k: blank() for k in _NUDGE_KINDS}
    by_identity: dict[str, dict] = {}
    for who, kind, outcome, verdict in rows:
        for bucket in (by_kind.setdefault(kind, blank()),
                       by_identity.setdefault(who, blank())):
            bucket["shown"] += 1
            bucket[outcome if outcome in ("used", "ignored") else "pending"] += 1
            if verdict in ("fair", "noise"):
                bucket[verdict] += 1
    return {"days": days, "identity": identity or "all", "by_kind": by_kind,
            "by_identity": by_identity, "total": len(rows)}
