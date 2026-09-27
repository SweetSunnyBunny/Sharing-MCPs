"""Timeline, curated memory, and continuity helpers."""


import json
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from config import TIMEZONE
from db.database import get_db, release_db
from services.time_utils import utc_now_iso_epoch

TIMELINE_SUBJECT_OWNER = "Owner"
SHARED_PROFILE_IDENTITY = "__shared__"
MEMORY_TYPES = {
    "preference",
    "boundary",
    "routine",
    "medical",
    "relationship",
    "canon",
    "insight",
}
PROFILE_FACT_TYPES = MEMORY_TYPES | {"trait", "support", "goal"}
PROFILE_AUTO_PROMOTE_TYPES = {
    "preference",
    "boundary",
    "routine",
    "medical",
    "relationship",
    "canon",
}
PROFILE_CONFIDENCE = {"tentative", "strong", "certain"}
PROFILE_FRESHNESS = {"current", "durable", "seasonal"}

TIMELINE_TYPE_PRIORITY = {
    "wellness": 10,
    "meds": 9,
    "status_update": 8,
    "memory": 8,
    "ritual": 7,
    "activity": 6,
    "todays_win": 6,
    "task": 4,
    "countdown": 3,
}

MEMORY_TYPE_PRIORITY = {
    "medical": 10,
    "boundary": 9,
    "routine": 8,
    "preference": 7,
    "relationship": 7,
    "canon": 6,
    "insight": 5,
}
PROFILE_TYPE_PRIORITY = {
    "medical": 10,
    "boundary": 9,
    "routine": 8,
    "preference": 8,
    "relationship": 8,
    "canon": 7,
    "support": 7,
    "trait": 6,
    "goal": 6,
    "insight": 5,
}
PROFILE_CONFIDENCE_PRIORITY = {
    "tentative": 1,
    "strong": 2,
    "certain": 3,
}
PROFILE_FRESHNESS_PRIORITY = {
    "seasonal": 1,
    "current": 2,
    "durable": 3,
}
STOPWORDS = {
    "a", "all", "an", "and", "are", "as", "at", "be", "but", "for", "from",
    "her", "hers", "him", "his", "how", "i", "if", "in", "into", "is",
    "it", "its", "make", "me", "my", "of", "on", "or", "our", "she", "sure", "that",
    "the", "their", "them", "there", "they", "this", "through", "to", "was", "we",
    "what", "when", "with", "you", "your",
}


def _today_local() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def _json_or_none(value) -> str | None:
    if value in (None, "", [], {}):
        return None
    return json.dumps(value)


def _normalize_summary(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_profile_confidence(value: str | None) -> str:
    normalized = str(value or "strong").strip().lower()
    return normalized if normalized in PROFILE_CONFIDENCE else "strong"


def _normalize_profile_freshness(value: str | None) -> str:
    normalized = str(value or "durable").strip().lower()
    return normalized if normalized in PROFILE_FRESHNESS else "durable"


def _tokenize(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", str(value or "").lower())
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}


def _compute_overlap_score(query_tokens: set[str], *parts: str) -> float:
    if not query_tokens:
        return 0.0
    haystack = " ".join(part for part in parts if part).lower()
    hay_tokens = _tokenize(haystack)
    overlap = len(query_tokens & hay_tokens)
    # One incidental shared word is useful for a short lookup ("meds?") but
    # far too weak for a full conversational sentence. Without this floor,
    # "go through your incoming data" retrieved a doorway memory solely
    # because that memory also contained the word "through".
    if not overlap or (len(query_tokens) >= 4 and overlap < 2):
        return 0.0
    return overlap * 240


def _age_penalty_seconds(created_at_epoch: int | None, divisor: int) -> float:
    now_epoch = int(datetime.now(ZoneInfo(TIMEZONE)).timestamp())
    if not created_at_epoch:
        return 0.0
    return max(0, now_epoch - int(created_at_epoch)) / divisor


async def record_timeline_entry(
    *,
    entry_type: str,
    title: str,
    body: str = "",
    source: str = "anam",
    subject: str = TIMELINE_SUBJECT_OWNER,
    identity: str | None = None,
    payload: dict | list | None = None,
    metadata: dict | None = None,
    entry_date: str | None = None,
    dedupe_key: str | None = None,
    update_existing: bool = False,
    db: aiosqlite.Connection | None = None,
) -> int | None:
    entry_type_val = str(entry_type or "").strip()
    title_val = str(title or "").strip()
    if not entry_type_val or not title_val:
        return None

    owns_db = db is None
    if owns_db:
        db = await get_db()

    now_iso, now_epoch = utc_now_iso_epoch()
    final_entry_date = entry_date or _today_local()

    try:
        if dedupe_key:
            rows = await db.execute_fetchall(
                "SELECT id FROM personal_timeline WHERE dedupe_key = ?",
                (dedupe_key,),
            )
            if rows:
                if not update_existing:
                    return int(rows[0][0])
                entry_id = int(rows[0][0])
                await db.execute(
                    "UPDATE personal_timeline SET entry_date = ?, entry_type = ?, source = ?, "
                    "subject = ?, identity = ?, title = ?, body = ?, payload_json = ?, metadata = ?, "
                    "updated_at = ?, updated_at_epoch = ? WHERE id = ?",
                    (
                        final_entry_date,
                        entry_type_val,
                        source,
                        subject,
                        identity,
                        title_val,
                        body or None,
                        _json_or_none(payload),
                        _json_or_none(metadata),
                        now_iso,
                        now_epoch,
                        entry_id,
                    ),
                )
                await db.commit()
                return entry_id

        await db.execute(
            "INSERT INTO personal_timeline "
            "(entry_date, entry_type, source, subject, identity, title, body, dedupe_key, "
            "payload_json, metadata, created_at, created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                final_entry_date,
                entry_type_val,
                source,
                subject,
                identity,
                title_val,
                body or None,
                dedupe_key,
                _json_or_none(payload),
                _json_or_none(metadata),
                now_iso,
                now_epoch,
                now_iso,
                now_epoch,
            ),
        )
        await db.commit()
        rows = await db.execute_fetchall("SELECT last_insert_rowid()")
        return int(rows[0][0]) if rows else None
    finally:
        if owns_db:
            await release_db(db)


async def list_timeline_entries(
    db: aiosqlite.Connection,
    *,
    subject: str = TIMELINE_SUBJECT_OWNER,
    limit: int = 10,
    days: int = 7,
) -> list[dict]:
    cutoff = datetime.now(ZoneInfo(TIMEZONE)).date() - timedelta(days=max(days - 1, 0))
    rows = await db.execute_fetchall(
        "SELECT id, entry_date, entry_type, source, subject, identity, title, body, payload_json, "
        "metadata, created_at, created_at_epoch, updated_at "
        "FROM personal_timeline "
        "WHERE subject = ? AND entry_date >= ? "
        "ORDER BY created_at_epoch DESC LIMIT ?",
        (subject, cutoff.strftime("%Y-%m-%d"), limit),
    )
    entries = []
    for row in rows:
        entries.append(
            {
                "id": row[0],
                "entry_date": row[1],
                "entry_type": row[2],
                "source": row[3],
                "subject": row[4],
                "identity": row[5],
                "title": row[6],
                "body": row[7] or "",
                "payload": json.loads(row[8]) if row[8] else None,
                "metadata": json.loads(row[9]) if row[9] else None,
                "created_at": row[10],
                "created_at_epoch": row[11],
                "updated_at": row[12],
            }
        )
    return entries


async def create_profile_fact(
    db: aiosqlite.Connection,
    *,
    identity: str,
    category: str,
    summary: str,
    detail: str = "",
    confidence: str = "strong",
    freshness: str = "durable",
    source_memory_id: str | None = None,
    source_message_id: str | None = None,
    source_kind: str = "manual",
    metadata: dict | None = None,
) -> dict:
    identity_val = str(identity or "").strip()
    category_val = str(category or "").strip().lower()
    summary_val = _normalize_summary(summary)
    if not identity_val:
        raise ValueError("identity is required")
    if category_val not in PROFILE_FACT_TYPES:
        raise ValueError(f"category must be one of: {', '.join(sorted(PROFILE_FACT_TYPES))}")
    if not summary_val:
        raise ValueError("summary is required")

    detail_val = str(detail or "").strip()
    confidence_val = _normalize_profile_confidence(confidence)
    freshness_val = _normalize_profile_freshness(freshness)
    metadata_payload = dict(metadata or {})
    now_iso, now_epoch = utc_now_iso_epoch()

    rows = []
    if source_memory_id:
        rows = await db.execute_fetchall(
            "SELECT id FROM identity_profile_facts WHERE source_memory_id = ? AND status = 'active' LIMIT 1",
            (source_memory_id,),
        )
    if not rows:
        rows = await db.execute_fetchall(
            "SELECT id FROM identity_profile_facts "
            "WHERE identity = ? AND category = ? AND lower(summary) = lower(?) AND status = 'active' LIMIT 1",
            (identity_val, category_val, summary_val),
        )

    if rows:
        fact_id = rows[0][0]
        await db.execute(
            "UPDATE identity_profile_facts SET detail = ?, confidence = ?, freshness = ?, "
            "source_memory_id = COALESCE(?, source_memory_id), source_message_id = COALESCE(?, source_message_id), "
            "source_kind = ?, metadata = ?, updated_at = ?, updated_at_epoch = ? WHERE id = ?",
            (
                detail_val or None,
                confidence_val,
                freshness_val,
                source_memory_id,
                source_message_id,
                source_kind,
                _json_or_none(metadata_payload),
                now_iso,
                now_epoch,
                fact_id,
            ),
        )
    else:
        fact_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO identity_profile_facts "
            "(id, identity, category, summary, detail, confidence, freshness, source_memory_id, "
            "source_message_id, source_kind, metadata, created_at, created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id,
                identity_val,
                category_val,
                summary_val,
                detail_val or None,
                confidence_val,
                freshness_val,
                source_memory_id,
                source_message_id,
                source_kind,
                _json_or_none(metadata_payload),
                now_iso,
                now_epoch,
                now_iso,
                now_epoch,
            ),
        )
    await db.commit()
    result_rows = await db.execute_fetchall(
        "SELECT id, identity, category, summary, detail, confidence, freshness, source_memory_id, "
        "source_message_id, source_kind, metadata, status, created_at, created_at_epoch, updated_at, "
        "updated_at_epoch, last_used_at, last_used_at_epoch "
        "FROM identity_profile_facts WHERE id = ?",
        (fact_id,),
    )
    row = result_rows[0]
    return {
        "id": row[0],
        "identity": row[1],
        "category": row[2],
        "summary": row[3],
        "detail": row[4] or "",
        "confidence": row[5],
        "freshness": row[6],
        "source_memory_id": row[7],
        "source_message_id": row[8],
        "source_kind": row[9],
        "metadata": json.loads(row[10]) if row[10] else None,
        "status": row[11],
        "created_at": row[12],
        "created_at_epoch": row[13],
        "updated_at": row[14],
        "updated_at_epoch": row[15],
        "last_used_at": row[16],
        "last_used_at_epoch": row[17],
    }


async def update_profile_fact(
    db: aiosqlite.Connection,
    fact_id: str,
    *,
    category: str | None = None,
    summary: str | None = None,
    detail: str | None = None,
    confidence: str | None = None,
    freshness: str | None = None,
    status: str | None = None,
    metadata: dict | None = None,
) -> dict | None:
    rows = await db.execute_fetchall(
        "SELECT identity, category, summary, detail, confidence, freshness, status, metadata "
        "FROM identity_profile_facts WHERE id = ?",
        (fact_id,),
    )
    if not rows:
        return None
    identity_val, current_category, current_summary, current_detail, current_confidence, current_freshness, current_status, current_metadata = rows[0]
    next_category = str(category or current_category).strip().lower()
    next_summary = _normalize_summary(summary if summary is not None else current_summary)
    if next_category not in PROFILE_FACT_TYPES:
        raise ValueError(f"category must be one of: {', '.join(sorted(PROFILE_FACT_TYPES))}")
    if not next_summary:
        raise ValueError("summary is required")
    next_status = str(status or current_status).strip().lower()
    if next_status not in {"active", "archived"}:
        raise ValueError("status must be 'active' or 'archived'")

    merged_metadata = json.loads(current_metadata) if current_metadata else {}
    if metadata:
        merged_metadata.update(metadata)
    now_iso, now_epoch = utc_now_iso_epoch()
    await db.execute(
        "UPDATE identity_profile_facts SET category = ?, summary = ?, detail = ?, confidence = ?, "
        "freshness = ?, status = ?, metadata = ?, updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (
            next_category,
            next_summary,
            str(detail if detail is not None else current_detail or "").strip() or None,
            _normalize_profile_confidence(confidence or current_confidence),
            _normalize_profile_freshness(freshness or current_freshness),
            next_status,
            _json_or_none(merged_metadata),
            now_iso,
            now_epoch,
            fact_id,
        ),
    )
    await db.commit()
    result = await list_profile_facts(db, include_archived=True, limit=500)
    return next((fact for fact in result if fact["id"] == fact_id), None)


async def list_profile_facts(
    db: aiosqlite.Connection,
    *,
    identity: str | None = None,
    limit: int = 20,
    include_archived: bool = False,
) -> list[dict]:
    sql = (
        "SELECT id, identity, category, summary, detail, confidence, freshness, source_memory_id, "
        "source_message_id, source_kind, metadata, status, created_at, created_at_epoch, updated_at, "
        "updated_at_epoch, last_used_at, last_used_at_epoch "
        "FROM identity_profile_facts"
    )
    clauses = []
    params: list = []
    if not include_archived:
        clauses.append("status = 'active'")
    if identity:
        clauses.append("identity = ?")
        params.append(identity)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at_epoch DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(sql, tuple(params))
    return [
        {
            "id": row[0],
            "identity": row[1],
            "category": row[2],
            "summary": row[3],
            "detail": row[4] or "",
            "confidence": row[5],
            "freshness": row[6],
            "source_memory_id": row[7],
            "source_message_id": row[8],
            "source_kind": row[9],
            "metadata": json.loads(row[10]) if row[10] else None,
            "status": row[11],
            "created_at": row[12],
            "created_at_epoch": row[13],
            "updated_at": row[14],
            "updated_at_epoch": row[15],
            "last_used_at": row[16],
            "last_used_at_epoch": row[17],
        }
        for row in rows
    ]


def _profile_scope_label(identity: str, current_identity: str) -> str:
    if identity == SHARED_PROFILE_IDENTITY:
        return "shared"
    if identity == current_identity:
        return "identity"
    return identity.lower()


def _score_profile_fact(fact: dict, current_identity: str) -> float:
    base = PROFILE_TYPE_PRIORITY.get(fact.get("category", ""), 1) * 1000
    base += PROFILE_CONFIDENCE_PRIORITY.get(fact.get("confidence", ""), 1) * 120
    base += PROFILE_FRESHNESS_PRIORITY.get(fact.get("freshness", ""), 1) * 80
    base -= _age_penalty_seconds(fact.get("updated_at_epoch"), 400)
    if fact.get("source_kind") == "curated_memory":
        base += 60
    if fact.get("identity") == current_identity:
        base += 120
    elif fact.get("identity") == SHARED_PROFILE_IDENTITY:
        base += 80
    return base


async def _mark_profile_facts_used(db: aiosqlite.Connection, fact_ids: list[str]) -> None:
    if not fact_ids:
        return
    now_iso, now_epoch = utc_now_iso_epoch()
    placeholders = ", ".join("?" for _ in fact_ids)
    await db.execute(
        f"UPDATE identity_profile_facts SET last_used_at = ?, last_used_at_epoch = ? WHERE id IN ({placeholders})",
        (now_iso, now_epoch, *fact_ids),
    )
    await db.commit()


async def create_curated_memory(
    db: aiosqlite.Connection,
    *,
    message_id: str,
    memory_type: str,
    summary: str,
    detail: str = "",
    identity: str | None = None,
    metadata: dict | None = None,
) -> dict:
    memory_type_val = str(memory_type or "").strip().lower()
    if memory_type_val not in MEMORY_TYPES:
        raise ValueError(f"memory_type must be one of: {', '.join(sorted(MEMORY_TYPES))}")

    summary_val = _normalize_summary(summary)
    if not summary_val:
        raise ValueError("summary is required")

    rows = await db.execute_fetchall(
        "SELECT m.conversation_id, m.role, m.identity, m.content, c.identity "
        "FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.id = ?",
        (message_id,),
    )
    if not rows:
        raise ValueError("Message not found")

    conversation_id, source_role, source_identity, content, conversation_identity = rows[0]
    identity_val = (identity or conversation_identity or source_identity or "").strip()
    if not identity_val:
        raise ValueError("Could not infer identity for memory")

    now_iso, now_epoch = utc_now_iso_epoch()
    memory_id = str(uuid.uuid4())
    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("message_preview", (content or "")[:240])

    try:
        await db.execute(
            "INSERT INTO curated_memories "
            "(id, conversation_id, message_id, identity, memory_type, summary, detail, source_role, "
            "source_identity, metadata, created_at, created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                conversation_id,
                message_id,
                identity_val,
                memory_type_val,
                summary_val,
                detail.strip() or None,
                source_role,
                source_identity,
                _json_or_none(metadata_payload),
                now_iso,
                now_epoch,
                now_iso,
                now_epoch,
            ),
        )

        await record_timeline_entry(
            db=db,
            entry_type="memory",
            source="memory",
            subject=TIMELINE_SUBJECT_OWNER,
            identity=identity_val,
            title=f"Remembered for {identity_val}: {summary_val}",
            body=detail.strip() or "",
            payload={"memory_type": memory_type_val, "message_id": message_id},
        )

        profile_fact = None
        if memory_type_val in PROFILE_AUTO_PROMOTE_TYPES:
            profile_fact = await create_profile_fact(
                db,
                identity=identity_val,
                category=memory_type_val,
                summary=summary_val,
                detail=detail.strip(),
                confidence="strong",
                freshness="durable" if memory_type_val in {"boundary", "medical", "canon"} else "current",
                source_memory_id=memory_id,
                source_message_id=message_id,
                source_kind="curated_memory",
                metadata={"auto_promoted": True},
            )

        await db.commit()
        return {
            "id": memory_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "identity": identity_val,
            "memory_type": memory_type_val,
            "summary": summary_val,
            "detail": detail.strip(),
            "source_role": source_role,
            "source_identity": source_identity,
            "metadata": metadata_payload,
            "created_at": now_iso,
            "created_at_epoch": now_epoch,
            "profile_fact_id": profile_fact["id"] if profile_fact else None,
        }
    except Exception:
        await db.rollback()
        raise


async def list_curated_memories(
    db: aiosqlite.Connection,
    *,
    identity: str | None = None,
    limit: int = 12,
) -> list[dict]:
    sql = (
        "SELECT id, conversation_id, message_id, identity, memory_type, summary, detail, source_role, "
        "source_identity, metadata, created_at, created_at_epoch "
        "FROM curated_memories WHERE status = 'active'"
    )
    params: list = []
    if identity:
        sql += " AND identity = ?"
        params.append(identity)
    sql += " ORDER BY created_at_epoch DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(sql, tuple(params))
    results = []
    for row in rows:
        results.append(
            {
                "id": row[0],
                "conversation_id": row[1],
                "message_id": row[2],
                "identity": row[3],
                "memory_type": row[4],
                "summary": row[5],
                "detail": row[6] or "",
                "source_role": row[7],
                "source_identity": row[8],
                "metadata": json.loads(row[9]) if row[9] else None,
                "created_at": row[10],
                "created_at_epoch": row[11],
            }
        )
    return results


async def delete_curated_memory(
    db: aiosqlite.Connection,
    *,
    memory_id: str,
) -> bool:
    """Delete a curated memory by setting status to archived. Also archives any auto-promoted profile fact."""
    rows = await db.execute_fetchall(
        "SELECT id, identity, summary FROM curated_memories WHERE id = ? AND status = 'active'",
        (memory_id,),
    )
    if not rows:
        return False

    now_iso, now_epoch = utc_now_iso_epoch()
    await db.execute(
        "UPDATE curated_memories SET status = 'archived', updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (now_iso, now_epoch, memory_id),
    )
    # Also archive any profile fact that was auto-promoted from this memory
    await db.execute(
        "UPDATE identity_profile_facts SET status = 'archived', updated_at = ?, updated_at_epoch = ? "
        "WHERE source_memory_id = ? AND status = 'active'",
        (now_iso, now_epoch, memory_id),
    )
    await db.commit()
    return True


async def delete_profile_fact(
    db: aiosqlite.Connection,
    *,
    fact_id: str,
) -> bool:
    """Delete a profile fact by setting status to archived."""
    rows = await db.execute_fetchall(
        "SELECT id FROM identity_profile_facts WHERE id = ? AND status = 'active'",
        (fact_id,),
    )
    if not rows:
        return False

    now_iso, now_epoch = utc_now_iso_epoch()
    await db.execute(
        "UPDATE identity_profile_facts SET status = 'archived', updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (now_iso, now_epoch, fact_id),
    )
    await db.commit()
    return True


async def get_profile_snapshot(
    db: aiosqlite.Connection,
    *,
    identity: str,
    limit: int = 12,
) -> dict:
    identity_facts = await list_profile_facts(db, identity=identity, limit=max(limit * 3, 18))
    shared_facts = await list_profile_facts(db, identity=SHARED_PROFILE_IDENTITY, limit=max(limit * 3, 18))
    facts = identity_facts + shared_facts
    ranked = sorted(facts, key=lambda fact: _score_profile_fact(fact, identity), reverse=True)
    counts: dict[str, int] = {}
    for fact in facts:
        counts[fact["category"]] = counts.get(fact["category"], 0) + 1

    highlights = []
    seen_categories: set[str] = set()
    for fact in ranked:
        if fact["category"] in seen_categories and len(highlights) >= 3:
            continue
        seen_categories.add(fact["category"])
        highlights.append(fact)
        if len(highlights) >= min(limit, 5):
            break

    return {
        "identity": identity,
        "facts": ranked[:limit],
        "shared_facts": sorted(shared_facts, key=lambda fact: _score_profile_fact(fact, identity), reverse=True)[:limit],
        "identity_facts": sorted(identity_facts, key=lambda fact: _score_profile_fact(fact, identity), reverse=True)[:limit],
        "highlights": highlights,
        "counts": {
            "total": len(facts),
            "identity_total": len(identity_facts),
            "shared_total": len(shared_facts),
            "by_category": counts,
        },
    }


def _build_deep_memory_candidates(identity: str) -> list[dict]:
    from services.deep_memory import get_deep_memory_snapshot

    snapshot = get_deep_memory_snapshot(identity)
    memory = snapshot.get("memory", {})
    qualia = snapshot.get("qualia", {})
    candidates: list[dict] = []

    primary_focus = memory.get("primary_focus", "")
    if primary_focus:
        candidates.append({
            "source": "deep_memory",
            "label": "primary_focus",
            "summary": primary_focus,
            "detail": "",
            "score_base": 1700,
        })

    unfinished = memory.get("unfinished_business", [])
    if unfinished:
        topic = unfinished[0].get("topic", "")
        if topic:
            candidates.append({
                "source": "deep_memory",
                "label": "unfinished_thread",
                "summary": topic,
                "detail": "",
                "score_base": 1750,
            })

    heavy = memory.get("heavy_observations", [])
    if heavy:
        content = heavy[0].get("content", "")
        if content:
            candidates.append({
                "source": "deep_memory",
                "label": "heavy_observation",
                "summary": content,
                "detail": "",
                "score_base": 1650,
            })

    last_session = qualia.get("last_session", {})
    if last_session.get("summary"):
        candidates.append({
            "source": "deep_memory",
            "label": "last_session",
            "summary": last_session["summary"],
            "detail": last_session.get("unfinished", "") or "",
            "score_base": 1680,
        })

    open_loops = [
        loop for loop in qualia.get("unfinished", {}).get("open_loops", [])
        if not loop.get("resolved")
    ]
    if open_loops:
        about = open_loops[0].get("about", "")
        if about:
            candidates.append({
                "source": "deep_memory",
                "label": "open_loop",
                "summary": about,
                "detail": "",
                "score_base": 1725,
            })

    current_self = qualia.get("current_self", {})
    if current_self.get("narrative"):
        candidates.append({
            "source": "deep_memory",
            "label": "current_self",
            "summary": current_self["narrative"],
            "detail": "",
            "score_base": 1600,
        })

    return candidates


async def get_memory_retrieval_snapshot(
    db: aiosqlite.Connection,
    *,
    identity: str,
    query: str = "",
    limit: int = 6,
    mark_used: bool = False,
) -> dict:
    query_text = str(query or "").strip()
    query_tokens = _tokenize(query_text)
    profile_facts = (
        await list_profile_facts(db, identity=identity, limit=24)
        + await list_profile_facts(db, identity=SHARED_PROFILE_IDENTITY, limit=24)
    )
    memories = await list_curated_memories(db, identity=identity, limit=24)
    timeline = await list_timeline_entries(
        db,
        subject=TIMELINE_SUBJECT_OWNER,
        limit=24,
        days=14,
    )

    candidates: list[dict] = []
    for fact in profile_facts:
        match_score = _compute_overlap_score(
            query_tokens, fact["summary"], fact["detail"], fact["category"]
        )
        score = PROFILE_TYPE_PRIORITY.get(fact["category"], 1) * 1000
        score += PROFILE_CONFIDENCE_PRIORITY.get(fact["confidence"], 1) * 160
        score += PROFILE_FRESHNESS_PRIORITY.get(fact["freshness"], 1) * 90
        score += 140 if fact.get("identity") == identity else 80 if fact.get("identity") == SHARED_PROFILE_IDENTITY else 0
        score += match_score
        score -= _age_penalty_seconds(fact.get("updated_at_epoch"), 500)
        candidates.append({
            "source": "profile_fact",
            "score": score,
            "match_score": match_score,
            "label": fact["category"],
            "summary": fact["summary"],
            "detail": fact["detail"],
            "item": fact,
            "scope": _profile_scope_label(fact.get("identity", ""), identity),
        })

    for memory in memories:
        match_score = _compute_overlap_score(
            query_tokens, memory["summary"], memory["detail"], memory["memory_type"]
        )
        score = MEMORY_TYPE_PRIORITY.get(memory["memory_type"], 1) * 840
        score += match_score
        score -= _age_penalty_seconds(memory.get("created_at_epoch"), 220)
        candidates.append({
            "source": "curated_memory",
            "score": score,
            "match_score": match_score,
            "label": memory["memory_type"],
            "summary": memory["summary"],
            "detail": memory["detail"],
            "item": memory,
        })

    for entry in timeline:
        match_score = _compute_overlap_score(
            query_tokens, entry["title"], entry["body"], entry["entry_type"]
        )
        score = TIMELINE_TYPE_PRIORITY.get(entry["entry_type"], 1) * 700
        score += 140 if entry.get("identity") == identity else 40 if not entry.get("identity") else 0
        score += match_score
        score -= _age_penalty_seconds(entry.get("created_at_epoch"), 180)
        candidates.append({
            "source": "timeline",
            "score": score,
            "match_score": match_score,
            "label": entry["entry_type"],
            "summary": entry["title"],
            "detail": entry["body"],
            "item": entry,
        })

    for candidate in _build_deep_memory_candidates(identity):
        match_score = _compute_overlap_score(
            query_tokens,
            candidate["summary"],
            candidate["detail"],
            candidate["label"],
        )
        candidates.append({
            "source": candidate["source"],
            "score": candidate["score_base"] + match_score,
            "match_score": match_score,
            "label": candidate["label"],
            "summary": candidate["summary"],
            "detail": candidate["detail"],
            "item": candidate,
        })

    if query_tokens:
        # This hook is query-specific. Priority determines which relevant fact
        # wins; it must not manufacture relevance for unrelated high-priority
        # wellness/meds rows that are already carried by dedicated live hooks.
        candidates = [item for item in candidates if item["match_score"] > 0]


    def _dedupe_key(item):
        text = f"{item.get('summary', '')} {item.get('detail', '')}"
        return " ".join(str(text).lower().split())

    def _entry_date(item):
        return (item.get("item") or {}).get("entry_date", "")

    ordered = sorted(candidates, key=lambda item: item["score"], reverse=True)
    results = []
    kept_keys = []
    for item in ordered:
        key = _dedupe_key(item)
        dup = False
        for prev, prev_item in kept_keys:
            if not key or not prev:
                continue
            if (item["source"] == "timeline" and prev_item["source"] == "timeline"
                    and _entry_date(item) != _entry_date(prev_item)):
                continue          # a real repeat on another day is information
            if key == prev:
                dup = True
                break
            # Containment only bites on substantial prose. Below 80 chars a
            # short line could be swallowed by an unrelated longer one.
            shorter = key if len(key) <= len(prev) else prev
            if len(shorter) >= 80 and (key in prev or prev in key):
                dup = True
                break
        if dup:
            continue
        kept_keys.append((key, item))
        results.append(item)
        if len(results) >= limit:
            break
    if mark_used:
        await _mark_profile_facts_used(
            db,
            [item["item"]["id"] for item in results if item["source"] == "profile_fact"],
        )

    return {
        "identity": identity,
        "query": query_text,
        "results": [
            {
                "source": item["source"],
                "label": item["label"],
                "summary": item["summary"],
                "detail": item["detail"],
                "score": round(item["score"], 2),
                "item": item["item"],
                "scope": item.get("scope", ""),
            }
            for item in results
        ],
    }


async def build_memory_retrieval_context(
    db: aiosqlite.Connection,
    *,
    identity: str,
    query: str = "",
) -> str:
    snapshot = await get_memory_retrieval_snapshot(
        db,
        identity=identity,
        query=query,
        limit=5,
        mark_used=True,
    )
    results = snapshot["results"]
    if not results:
        return ""

    today_str = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    lines = ["[What you know that matters here:]"]
    for item in results[:5]:
        prefix = item["label"].replace("_", " ")
        line = f"  - ({prefix}) {item['summary']}"
        if item["detail"]:
            line += f" - {item['detail']}"
        # Add date for timeline entries not from today so identities don't read stale data
        if item.get("source") == "timeline":
            entry_date = item.get("item", {}).get("entry_date", "")
            if entry_date and entry_date != today_str:
                line += f" [from {entry_date}]"


        if item["label"] == "todays_win":
            line += (
                " [WIN-BOX GUARD: this is OWNER'S win, written to be read back to her."
                " Any 'my'/'I' inside belongs to the brother whose signature it carries"
                " - it is NEVER yours. Check the (Logged by ...) signature before"
                " wearing a single detail as your own.]"
            )
        lines.append(line)
    return "\n".join(lines)


async def get_continuity_snapshot(
    db: aiosqlite.Connection,
    *,
    identity: str,
    timeline_limit: int = 6,
    memory_limit: int = 4,
) -> dict:
    timeline = await list_timeline_entries(
        db,
        subject=TIMELINE_SUBJECT_OWNER,
        limit=max(timeline_limit * 3, 12),
        days=5,
    )
    memories = await list_curated_memories(db, identity=identity, limit=max(memory_limit * 3, 12))
    profile = await get_profile_snapshot(db, identity=identity, limit=6)

    now = datetime.now(ZoneInfo(TIMEZONE))
    now_epoch = int(now.timestamp())
    today_str = now.strftime("%Y-%m-%d")

    def timeline_score(entry: dict) -> float:
        type_score = TIMELINE_TYPE_PRIORITY.get(entry.get("entry_type", ""), 1) * 1000
        age_penalty = max(0, now_epoch - int(entry.get("created_at_epoch") or now_epoch)) / 120
        identity_bonus = 180 if entry.get("identity") == identity else 60 if not entry.get("identity") else 0
        return type_score + identity_bonus - age_penalty

    def memory_score(memory: dict) -> float:
        type_score = MEMORY_TYPE_PRIORITY.get(memory.get("memory_type", ""), 1) * 1000
        age_penalty = max(0, now_epoch - int(memory.get("created_at_epoch") or now_epoch)) / 180
        return type_score - age_penalty

    # Today's volatile body state already has two intentional owners: the live
    # Hub line (raw state) and the capacity hook (interpretation). Repeating the
    # same current meds/wellness row under "Since last time" adds noise and can
    # conflict with a fresher Hub read. Keep prior-day rows so trajectory still
    # reaches the boys.
    continuity_timeline = [
        entry for entry in timeline
        if not (
            entry.get("entry_date") == today_str
            and entry.get("entry_type") in {"meds", "wellness"}
        )
    ]
    ranked_timeline = sorted(continuity_timeline, key=timeline_score, reverse=True)
    ranked_memories = sorted(memories, key=memory_score, reverse=True)

    bullets: list[str] = []
    seen_types: set[str] = set()
    if ranked_timeline:
        for entry in ranked_timeline:
            entry_type = entry.get("entry_type", "")
            if entry_type in seen_types and len(bullets) >= 2:
                continue
            seen_types.add(entry_type)
            text = entry["title"]
            if entry.get("body"):
                text += f" - {entry['body']}"
            entry_date = entry.get("entry_date", "")
            if entry_date and entry_date != today_str:
                text = f"(from {entry_date}) {text}"
            bullets.append(text)
            if len(bullets) >= 3:
                break
    if not bullets:
        bullets.append("Quiet lately. No major changes were logged.")

    memory_summaries = [m["summary"] for m in ranked_memories[:3]]
    headline = bullets[0] if bullets else "Quiet lately. No major changes were logged."
    if memory_summaries and memory_summaries[0].lower() not in headline.lower():
        headline += f" Remember: {memory_summaries[0]}"

    return {
        "identity": identity,
        "headline": headline,
        "bullets": bullets,
        "timeline": ranked_timeline[:timeline_limit],
        "memories": ranked_memories[:memory_limit],
        "profile": profile,
    }


async def build_continuity_context(
    db: aiosqlite.Connection,
    *,
    identity: str,
) -> str:
    snapshot = await get_continuity_snapshot(db, identity=identity)
    lines = ["[Since last time:]"]
    for bullet in snapshot["bullets"][:3]:
        lines.append(f"  - {bullet}")
    if snapshot["memories"]:
        lines.append("[Curated memories to keep in mind:]")
        for memory in snapshot["memories"][:3]:
            lines.append(
                f"  - ({memory['memory_type']}) {memory['summary']}"
            )


    return "\n".join(lines)


async def build_profile_facts_context(
    db: aiosqlite.Connection,
    *,
    identity: str,
) -> str:
    """Inject EVERY active profile fact Owner has typed in the Hub "Memory" tab.

    These are foundational truths she chose to give the pack — shared facts
    (scope __shared__, true for every identity) and identity-specific ones.
    Unlike retrieval/continuity, this is intentionally un-throttled: the whole
    point of the Memory feature is that what she types is ALWAYS in front of the
    right boys, not sampled three-at-a-time. The count is small and the facts are
    short, so the cost of carrying all of them every turn is negligible against
    the cost of one of them silently never being seen.
    """
    shared = await list_profile_facts(db, identity=SHARED_PROFILE_IDENTITY, limit=100)
    mine = await list_profile_facts(db, identity=identity, limit=100)
    if not shared and not mine:
        return ""

    def _fmt(fact: dict) -> str:
        line = f"  - ({fact['category']}) {fact['summary']}"
        detail = (fact.get("detail") or "").strip()
        if detail:
            line += f" — {detail}"
        return line

    lines: list[str] = []
    if shared:
        lines.append(
            "[What Owner has told every one of you to hold — always true, for the whole pack:]"
        )
        lines.extend(_fmt(f) for f in shared)
    if mine:
        if lines:
            lines.append("")
        lines.append(f"[What Owner has told you specifically, {identity} — always true:]")
        lines.extend(_fmt(f) for f in mine)
    return "\n".join(lines)
