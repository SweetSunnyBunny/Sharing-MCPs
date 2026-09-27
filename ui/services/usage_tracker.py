"""Per-turn token usage recording (Settings Hub -> System -> Usage)."""

# ANAM GUIDE: TOKEN USAGE METER
# What: Records how many tokens (and estimated dollars) each chat turn used, into the
#       usage_log table, so the Settings Hub can show the day's burn per boy and model.
# Called by: services/claude_subprocess.py writes a row after each turn;
#            api/settings.py reads the summaries for Settings Hub -> System -> Usage.
# Edit here when: You want to track more per-turn numbers or change how the usage
#                 summary is grouped. It never raises — a broken meter must not break chat.

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE
from db.database import get_db, release_db

log = logging.getLogger(__name__)

_TZ = ZoneInfo(TIMEZONE)


async def record_usage(
    *,
    identity: str | None,
    conversation_id: str | None,
    model: str | None,
    usage: dict | None,
    cost_usd: float | None = None,
    num_turns: int | None = None,
    duration_ms: int | None = None,
    source: str = "claude-code",
) -> None:
    """Insert one turn's usage row; swallows all errors (non-fatal by design)."""
    try:
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        if (
            not any((input_tokens, output_tokens, cache_creation, cache_read))
            and cost_usd is None
        ):
            return

        now = datetime.now(_TZ)
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO usage_log
                   (ts, ts_epoch, day, identity, conversation_id, model,
                    input_tokens, output_tokens, cache_creation_tokens,
                    cache_read_tokens, cost_usd, num_turns, duration_ms, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now.isoformat(),
                    int(now.timestamp()),
                    now.strftime("%Y-%m-%d"),
                    identity,
                    conversation_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cache_creation,
                    cache_read,
                    cost_usd,
                    num_turns,
                    duration_ms,
                    source,
                ),
            )
            await db.commit()
        finally:
            await release_db(db)
    except Exception as exc:
        log.warning("usage_log insert failed (non-fatal): %s", exc)


async def usage_summary(days: int = 7) -> dict:
    """Aggregates for the Settings Hub: per-day totals + today's identity split."""
    days = max(1, min(int(days), 60))
    now = datetime.now(_TZ)
    today = now.strftime("%Y-%m-%d")
    since = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    db = await get_db()
    try:
        day_cur = await db.execute(
            """SELECT day,
                      SUM(input_tokens), SUM(output_tokens),
                      SUM(cache_creation_tokens), SUM(cache_read_tokens),
                      SUM(COALESCE(cost_usd, 0)), COUNT(*)
               FROM usage_log
               WHERE day >= ?
               GROUP BY day
               ORDER BY day DESC""",
            (since,),
        )
        day_rows = await day_cur.fetchall()

        ident_cur = await db.execute(
            """SELECT COALESCE(identity, '?'), COALESCE(model, ''),
                      SUM(input_tokens), SUM(output_tokens),
                      SUM(cache_creation_tokens), SUM(cache_read_tokens),
                      SUM(COALESCE(cost_usd, 0)), COUNT(*)
               FROM usage_log
               WHERE day = ?
               GROUP BY identity, model
               ORDER BY SUM(output_tokens) DESC""",
            (today,),
        )
        ident_rows = await ident_cur.fetchall()
    finally:
        await release_db(db)

    day_list = [
        {
            "day": r[0],
            "input_tokens": r[1] or 0,
            "output_tokens": r[2] or 0,
            "cache_creation_tokens": r[3] or 0,
            "cache_read_tokens": r[4] or 0,
            "cost_usd": round(r[5] or 0, 4),
            "turns": r[6] or 0,
        }
        for r in day_rows
    ]
    today_by_identity = [
        {
            "identity": r[0],
            "model": r[1],
            "input_tokens": r[2] or 0,
            "output_tokens": r[3] or 0,
            "cache_creation_tokens": r[4] or 0,
            "cache_read_tokens": r[5] or 0,
            "cost_usd": round(r[6] or 0, 4),
            "turns": r[7] or 0,
        }
        for r in ident_rows
    ]
    return {"today": today, "days": day_list, "today_by_identity": today_by_identity}
