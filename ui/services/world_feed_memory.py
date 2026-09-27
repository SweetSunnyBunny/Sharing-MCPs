"""Durable, source-linked interaction memory for private story worlds.

These records are not omniscient canon.  Each row says that one profile may
carry one witnessed interaction into later feed or DM generations.  Exact
source ids make retries idempotent and keep the remembered claim auditable.
"""

from __future__ import annotations

import json
import uuid
from typing import Any


def _clip(text: str, limit: int = 900) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


async def remember(
    db,
    *,
    world_id: str,
    profile_id: str,
    source_kind: str,
    source_id: str,
    summary: str,
    other_profile_id: str | None = None,
    visibility: str = "private",
    fictional_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Record one witnessed fact once, with no relationship score attached."""
    from services import world_feed as feed

    profile = await feed.get_profile(db, profile_id)
    if profile["world_id"] != world_id:
        raise feed.WorldFeedError("Memory profile does not belong to this world")
    if other_profile_id:
        other = await feed.get_profile(db, other_profile_id)
        if other["world_id"] != world_id or other_profile_id == profile_id:
            raise feed.WorldFeedError("Memory counterpart must be another profile in this world")
    text = _clip(summary)
    if not text:
        raise feed.WorldFeedError("A remembered interaction needs a summary")
    if visibility not in {"private", "public"}:
        raise feed.WorldFeedError("Unknown memory visibility")
    iso, epoch = feed._now()
    cursor = await db.execute(
        "INSERT OR IGNORE INTO story_feed_character_memories "
        "(id,world_id,profile_id,other_profile_id,source_kind,source_id,summary,visibility,"
        "fictional_at,metadata,created_at,created_at_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "memory_" + uuid.uuid4().hex,
            world_id,
            profile_id,
            other_profile_id,
            source_kind,
            source_id,
            text,
            visibility,
            fictional_at,
            json.dumps(metadata or {}, ensure_ascii=False),
            iso,
            epoch,
        ),
    )
    return cursor.rowcount > 0


async def list_memories(db, profile_id: str, *, limit: int = 24) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        "SELECT m.*,p.handle other_handle,p.display_name other_name "
        "FROM story_feed_character_memories m "
        "LEFT JOIN story_feed_profiles p ON p.id=m.other_profile_id "
        "WHERE m.profile_id=? AND (m.source_kind!='roleplay_turn' OR EXISTS "
        "(SELECT 1 FROM messages a WHERE a.id=m.source_id AND a.role='assistant')) "
        "AND (m.source_kind NOT IN ('feed_reply','feed_quote','feed_repost','feed_mention','feed_like') "
        "OR EXISTS (SELECT 1 FROM story_feed_posts s WHERE s.world_id=m.world_id "
        "AND (s.id=m.source_id OR (m.source_kind='feed_like' AND "
        "m.source_id IN (s.id || ':' || m.profile_id, s.id || ':' || m.other_profile_id))) "
        "AND s.canon_status='approved' "
        "AND (s.parent_post_id IS NULL OR EXISTS (SELECT 1 FROM story_feed_posts q WHERE q.id=s.parent_post_id AND q.canon_status='approved')) "
        "AND (s.quote_post_id IS NULL OR EXISTS (SELECT 1 FROM story_feed_posts q WHERE q.id=s.quote_post_id AND q.canon_status='approved')))) "
        # Historical public exchanges retain their place behind current memories.
        "ORDER BY COALESCE((SELECT s.timeline_order FROM story_feed_posts s "
        "WHERE s.id=m.source_id AND s.world_id=m.world_id),m.created_at_epoch) DESC,m.rowid DESC LIMIT ?",
        (profile_id, max(1, min(100, limit))),
    )
    return [
        {
            "id": row["id"],
            "source_kind": row["source_kind"],
            "source_id": row["source_id"],
            "summary": row["summary"],
            "visibility": row["visibility"],
            "fictional_at": row["fictional_at"],
            "other": ({"handle": row["other_handle"], "display_name": row["other_name"]}
                      if row["other_profile_id"] else None),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def public_user_posts(db, world_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """Longer-lived public record authored by protected/user-controlled profiles."""
    rows = await db.execute_fetchall(
        "SELECT s.id,s.body,s.post_type,s.fictional_at,s.created_at,p.handle,p.display_name "
        "FROM story_feed_posts s JOIN story_feed_profiles p ON p.id=s.author_profile_id "
        "WHERE s.world_id=? AND s.canon_status='approved' AND p.is_user_controlled=1 "
        "AND (s.parent_post_id IS NULL OR EXISTS (SELECT 1 FROM story_feed_posts q WHERE q.id=s.parent_post_id AND q.canon_status='approved')) "
        "AND (s.quote_post_id IS NULL OR EXISTS (SELECT 1 FROM story_feed_posts q WHERE q.id=s.quote_post_id AND q.canon_status='approved')) "
        "ORDER BY s.created_at_epoch DESC,s.id DESC LIMIT ?",
        (world_id, max(1, min(100, limit))),
    )
    return [dict(row) for row in rows]


async def record_post_interactions(db, post: dict[str, Any]) -> None:
    """Give directly involved NPCs an exact, non-interpretive memory of a post."""
    from services import world_feed as feed

    author = post["author"]
    targets: dict[str, tuple[dict[str, Any], str]] = {}
    linked = post.get("parent_post") or post.get("quoted_post")
    if linked and linked["author"]["id"] != author["id"]:
        targets[linked["author"]["id"]] = (linked["author"], post["post_type"])
    for handle in post.get("mentions", []):
        rows = await db.execute_fetchall(
            "SELECT * FROM story_feed_profiles WHERE world_id=? AND handle=? COLLATE NOCASE",
            (post["world_id"], handle),
        )
        if rows and rows[0]["id"] != author["id"]:
            targets[rows[0]["id"]] = (feed._profile(rows[0]), "mention")
    for target_id, (target, kind) in targets.items():
        if not target["is_user_controlled"]:
            await remember(
                db,
                world_id=post["world_id"],
                profile_id=target_id,
                other_profile_id=author["id"],
                source_kind="feed_" + kind,
                source_id=post["id"],
                summary=f'@{author["handle"]} {kind}ed you: “{_clip(post["body"], 500)}”',
                fictional_at=post.get("fictional_at"),
            )
    if not author["is_user_controlled"]:
        for kind in {kind for _, kind in targets.values()}:
            involved = [target for target, target_kind in targets.values() if target_kind == kind]
            handles = ", ".join("@" + target["handle"] for target in involved)
            await remember(
                db,
                world_id=post["world_id"],
                profile_id=author["id"],
                other_profile_id=involved[0]["id"] if len(involved) == 1 else None,
                source_kind="feed_" + kind,
                source_id=post["id"],
                summary=f'Your {kind} involved {handles}: “{_clip(post["body"], 500)}”',
                fictional_at=post.get("fictional_at"),
            )
