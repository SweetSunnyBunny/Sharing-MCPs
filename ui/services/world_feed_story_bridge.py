"""Conservative bridge from completed roleplay turns into character memory.

This layer records source excerpts, which can contain unwitnessed narration or OOC:
the exact user turn and its own response. It does not tell uninvolved profiles,
advance the World Feed clock, or invent a scene summary.
"""

from __future__ import annotations

import json
import logging
import re

from services.world_feed_memory import remember

log = logging.getLogger(__name__)


def _excerpt(text: str, limit: int = 700) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def capture_roleplay_turn(
    db,
    *,
    conversation_id: str,
    identity: str,
    assistant_message_id: str,
    assistant_content: str,
    user_message_id: str | None = None,
) -> int:
    if not user_message_id:
        return 0
    rows = await db.execute_fetchall(
        "SELECT session_type,metadata FROM conversations WHERE id=?", (conversation_id,),
    )
    if not rows or rows[0]["session_type"] != "roleplay":
        return 0
    conversation_meta = json.loads(rows[0]["metadata"] or "{}")
    if not isinstance(conversation_meta, dict):
        return 0
    worlds = await db.execute_fetchall(
        "SELECT * FROM story_worlds WHERE story_identity=? COLLATE NOCASE",
        (identity,),
    )
    requested_world = conversation_meta.get("story_world_id")
    if requested_world:
        worlds = [row for row in worlds if row["id"] == requested_world]
    branch = str(conversation_meta.get("story_branch") or "")
    if branch:
        worlds = [row for row in worlds if row["story_branch"] == branch]
    if len(worlds) != 1:
        return 0

    world = worlds[0]
    profiles = await db.execute_fetchall(
        "SELECT * FROM story_feed_profiles WHERE world_id=? AND is_active=1",
        (world["id"],),
    )
    users = [row for row in profiles if row["is_user_controlled"]]
    if len(users) != 1:
        return 0
    identity_key = identity.casefold()
    actors = []
    for row in profiles:
        if row["is_user_controlled"]:
            continue
        metadata = json.loads(row["metadata"] or "{}")
        if not isinstance(metadata, dict):
            continue
        names = {
            str(metadata.get("story_identity") or "").casefold(),
            row["handle"].casefold(),
            row["display_name"].casefold(),
            row["display_name"].split()[0].casefold(),
        }
        if identity_key in names:
            actors.append(row)
    if len(actors) != 1:
        return 0

    user_rows = await db.execute_fetchall(
        "SELECT id,content FROM messages WHERE conversation_id=? AND role='user' AND id=?",
        (conversation_id, user_message_id),
    )
    if not user_rows:
        return 0
    assistant_rows = await db.execute_fetchall(
        "SELECT content FROM messages WHERE id=? AND conversation_id=? AND role='assistant' AND identity=? COLLATE NOCASE",
        (assistant_message_id, conversation_id, identity),
    )
    if not assistant_rows:
        return 0
    assistant_content = assistant_rows[0]["content"]
    user_profile, actor = users[0], actors[0]
    summary = (
        f'Private roleplay source with @{user_profile["handle"]}; narration/OOC is not automatically witnessed. '
        f'They wrote: “{_excerpt(user_rows[0]["content"], 350)}” '
        f'You answered: “{_excerpt(assistant_content, 350)}”'
    )
    created = await remember(
        db,
        world_id=world["id"],
        profile_id=actor["id"],
        other_profile_id=user_profile["id"],
        source_kind="roleplay_turn",
        source_id=assistant_message_id,
        summary=summary,
        visibility="private",
        fictional_at=world["fictional_now"],
        metadata={"conversation_id": conversation_id, "user_message_id": user_rows[0]["id"]},
    )
    from services.world_feed_scene_observer import enqueue
    await enqueue(db, world["id"], conversation_id, user_message_id, assistant_message_id)
    await db.commit()
    return int(created)


async def capture_roleplay_turn_safely(db, **kwargs) -> None:
    try:
        await capture_roleplay_turn(db, **kwargs)
    except Exception:
        await db.rollback()
        log.exception("World Feed roleplay memory bridge failed; chat turn remains saved")
