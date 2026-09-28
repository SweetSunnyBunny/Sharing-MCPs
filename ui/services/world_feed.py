"""Persistent private social worlds for story characters.

The feed is story infrastructure, not bonded-identity memory.  Everything is
scoped to a world, every generated artifact has provenance, and user-controlled
characters can never be authored by an AI-origin write.
"""

from __future__ import annotations

import json
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite


DEFAULT_WORLD_ID = "example-world"
DEFAULT_WORLD_SLUG = "starter-world"

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
_HASHTAG_RE = re.compile(r"(?<![\w&])#([\w]{1,64})", re.UNICODE)
_MENTION_RE = re.compile(r"(?<![\w&])@([A-Za-z0-9_]{1,32})")

ACCOUNT_TYPES = {
    "character", "user", "hero", "villain", "student", "teacher",
    "civilian", "fan", "news", "organization", "anonymous",
}
CANON_LEVELS = {"ambient", "interaction", "major"}
CANON_STATUSES = {"draft", "approved", "rejected"}
POST_TYPES = {"post", "reply", "quote", "repost"}
POST_ORIGINS = {"human", "ai", "system"}


class WorldFeedError(ValueError):
    """Expected validation or state error returned to the private UI."""


class WorldFeedNotFound(WorldFeedError):
    """Requested story-social object does not exist."""


class WorldFeedConflict(WorldFeedError):
    """A unique story-social object already exists."""


def _now() -> tuple[str, int]:
    dt = datetime.now(timezone.utc)
    return dt.isoformat(), int(dt.timestamp())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def normalize_handle(handle: str) -> str:
    value = str(handle or "").strip().lstrip("@")
    if not _HANDLE_RE.fullmatch(value):
        raise WorldFeedError("Handle must be 1-32 letters, numbers, or underscores")
    return value


def normalize_slug(slug: str) -> str:
    value = str(slug or "").strip().lower()
    if not _SLUG_RE.fullmatch(value):
        raise WorldFeedError("World slug must use lowercase letters, numbers, and hyphens")
    return value


def extract_hashtags(body: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for match in _HASHTAG_RE.finditer(body or ""):
        display = match.group(1)
        found.setdefault(display.casefold(), display)
    return list(found.items())


def extract_mentions(body: str) -> list[str]:
    found: dict[str, str] = {}
    for match in _MENTION_RE.finditer(body or ""):
        handle = match.group(1)
        found.setdefault(handle.casefold(), handle)
    return list(found.values())


def _world(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "story_identity": row["story_identity"],
        "story_branch": row["story_branch"],
        "source_package": row["source_package"],
        "fictional_now": row["fictional_now"],
        "clock_label": row["clock_label"],
        "posting_enabled": _bool(row["posting_enabled"]),
        "metadata": _loads(row["metadata"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _profile(row: aiosqlite.Row, prefix: str = "") -> dict[str, Any]:
    key = lambda name: f"{prefix}{name}"  # noqa: E731
    return {
        "id": row[key("id")],
        "world_id": row[key("world_id")],
        "handle": row[key("handle")],
        "display_name": row[key("display_name")],
        "account_type": row[key("account_type")],
        "bio": row[key("bio")],
        "location": row[key("location")],
        "website": row[key("website")],
        "avatar_url": row[key("avatar_url")],
        "header_url": row[key("header_url")],
        "accent_color": row[key("accent_color")],
        "is_user_controlled": _bool(row[key("is_user_controlled")]),
        "is_verified": _bool(row[key("is_verified")]),
        "is_active": _bool(row[key("is_active")]),
        "prompt_notes": row[key("prompt_notes")],
        "posting_style": row[key("posting_style")],
        "knowledge": _loads(row[key("knowledge_json")], []),
        "metadata": _loads(row[key("metadata")], {}),
        "created_at": row[key("created_at")],
        "updated_at": row[key("updated_at")],
    }


async def ensure_default_world(db: aiosqlite.Connection) -> dict[str, Any]:
    row = await db.execute_fetchall("SELECT * FROM story_worlds WHERE id = ?", (DEFAULT_WORLD_ID,))
    if not row:
        iso, epoch = _now()
        await db.execute(
            "INSERT INTO story_worlds "
            "(id, slug, name, description, story_identity, story_branch, source_package, "
            "fictional_now, clock_label, posting_enabled, metadata, created_at, "
            "created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
            (
                DEFAULT_WORLD_ID,
                DEFAULT_WORLD_SLUG,
                "Starter World",
                "A configurable fictional social world.",
                "Avery",
                "",
                "",
                "Story opening",
                "Story time",
                _json({"automatic_pulses": False, "canon_contract": "ambient-by-default"}),
                iso,
                epoch,
                iso,
                epoch,
            ),
        )
        await db.commit()
    return await get_world(db, DEFAULT_WORLD_ID)


async def list_worlds(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    await ensure_default_world(db)
    rows = await db.execute_fetchall(
        "SELECT w.*, "
        "(SELECT COUNT(*) FROM story_feed_profiles p WHERE p.world_id = w.id AND p.is_active = 1) profile_count, "
        "(SELECT COUNT(*) FROM story_feed_posts s WHERE s.world_id = w.id AND s.canon_status = 'approved') post_count "
        "FROM story_worlds w ORDER BY w.updated_at_epoch DESC"
    )
    result = []
    for row in rows:
        item = _world(row)
        item["profile_count"] = row["profile_count"]
        item["post_count"] = row["post_count"]
        result.append(item)
    return result


async def get_world(db: aiosqlite.Connection, world_id: str) -> dict[str, Any]:
    rows = await db.execute_fetchall("SELECT * FROM story_worlds WHERE id = ?", (world_id,))
    if not rows:
        raise WorldFeedNotFound("Story world not found")
    return _world(rows[0])


async def create_world(db: aiosqlite.Connection, data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise WorldFeedError("World name is required")
    slug = normalize_slug(data.get("slug") or name.lower().replace(" ", "-"))
    world_id = str(data.get("id") or slug)
    iso, epoch = _now()
    try:
        await db.execute(
            "INSERT INTO story_worlds "
            "(id, slug, name, description, story_identity, story_branch, source_package, "
            "fictional_now, clock_label, posting_enabled, metadata, created_at, "
            "created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                world_id,
                slug,
                name,
                str(data.get("description") or "").strip(),
                str(data.get("story_identity") or "Avery").strip(),
                str(data.get("story_branch") or "").strip(),
                str(data.get("source_package") or "").strip() or None,
                str(data.get("fictional_now") or "").strip() or None,
                str(data.get("clock_label") or "").strip(),
                int(bool(data.get("posting_enabled", False))),
                _json(data.get("metadata") or {}),
                iso,
                epoch,
                iso,
                epoch,
            ),
        )
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        raise WorldFeedConflict("A world with that id or slug already exists") from exc
    return await get_world(db, world_id)


async def update_world(db: aiosqlite.Connection, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await get_world(db, world_id)
    allowed = {
        "name", "description", "story_identity", "story_branch", "source_package",
        "fictional_now", "clock_label", "posting_enabled", "metadata",
    }
    values: list[Any] = []
    assignments: list[str] = []
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key == "posting_enabled":
            value = int(bool(value))
        elif key == "metadata":
            value = _json(value or {})
        elif isinstance(value, str):
            value = value.strip()
        assignments.append(f"{key} = ?")
        values.append(value)
    if not assignments:
        return await get_world(db, world_id)
    iso, epoch = _now()
    assignments.extend(["updated_at = ?", "updated_at_epoch = ?"])
    values.extend([iso, epoch, world_id])
    await db.execute(f"UPDATE story_worlds SET {', '.join(assignments)} WHERE id = ?", tuple(values))
    await db.commit()
    return await get_world(db, world_id)


async def get_profile(db: aiosqlite.Connection, profile_id: str) -> dict[str, Any]:
    rows = await db.execute_fetchall("SELECT * FROM story_feed_profiles WHERE id = ?", (profile_id,))
    if not rows:
        raise WorldFeedNotFound("Profile not found")
    return _profile(rows[0])


async def list_profiles(db: aiosqlite.Connection, world_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    await get_world(db, world_id)
    where = "world_id = ?" if include_inactive else "world_id = ? AND is_active = 1"
    rows = await db.execute_fetchall(
        f"SELECT * FROM story_feed_profiles WHERE {where} "
        "ORDER BY is_user_controlled DESC, display_name COLLATE NOCASE",
        (world_id,),
    )
    return [_profile(row) for row in rows]


async def create_profile(db: aiosqlite.Connection, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    await get_world(db, world_id)
    handle = normalize_handle(data.get("handle") or "")
    display_name = str(data.get("display_name") or "").strip()
    if not display_name:
        raise WorldFeedError("Display name is required")
    account_type = str(data.get("account_type") or "character").strip().lower()
    if account_type not in ACCOUNT_TYPES:
        raise WorldFeedError("Unknown account type")
    profile_id = str(data.get("id") or _id("profile"))
    iso, epoch = _now()
    try:
        await db.execute(
            "INSERT INTO story_feed_profiles "
            "(id, world_id, handle, display_name, account_type, bio, location, website, "
            "avatar_url, header_url, accent_color, is_user_controlled, is_verified, is_active, "
            "prompt_notes, posting_style, knowledge_json, metadata, created_at, created_at_epoch, "
            "updated_at, updated_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                world_id,
                handle,
                display_name,
                account_type,
                str(data.get("bio") or "").strip(),
                str(data.get("location") or "").strip(),
                str(data.get("website") or "").strip(),
                str(data.get("avatar_url") or "").strip(),
                str(data.get("header_url") or "").strip(),
                str(data.get("accent_color") or "#D9485F").strip(),
                int(bool(data.get("is_user_controlled", False))),
                int(bool(data.get("is_verified", False))),
                int(bool(data.get("is_active", True))),
                str(data.get("prompt_notes") or "").strip(),
                str(data.get("posting_style") or "").strip(),
                _json(data.get("knowledge") or []),
                _json(data.get("metadata") or {}),
                iso,
                epoch,
                iso,
                epoch,
            ),
        )
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        raise WorldFeedConflict(f"@{handle} already exists in this world") from exc
    return await get_profile(db, profile_id)


async def update_profile(db: aiosqlite.Connection, profile_id: str, data: dict[str, Any]) -> dict[str, Any]:
    current = await get_profile(db, profile_id)
    allowed = {
        "handle", "display_name", "account_type", "bio", "location", "website",
        "avatar_url", "header_url", "accent_color", "is_user_controlled", "is_verified",
        "is_active", "prompt_notes", "posting_style", "knowledge", "metadata",
    }
    assignments: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        column = key
        if key == "handle":
            value = normalize_handle(value)
        elif key == "display_name":
            value = str(value or "").strip()
            if not value:
                raise WorldFeedError("Display name is required")
        elif key == "account_type":
            value = str(value or "").strip().lower()
            if value not in ACCOUNT_TYPES:
                raise WorldFeedError("Unknown account type")
        elif key in {"is_user_controlled", "is_verified", "is_active"}:
            value = int(bool(value))
        elif key == "knowledge":
            column = "knowledge_json"
            value = _json(value or [])
        elif key == "metadata":
            value = _json(value or {})
        elif isinstance(value, str):
            value = value.strip()
        assignments.append(f"{column} = ?")
        values.append(value)
    if not assignments:
        return current
    iso, epoch = _now()
    assignments.extend(["updated_at = ?", "updated_at_epoch = ?"])
    values.extend([iso, epoch, profile_id])
    try:
        await db.execute(
            f"UPDATE story_feed_profiles SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )
        await db.commit()
    except aiosqlite.IntegrityError as exc:
        raise WorldFeedConflict("That handle is already used in this world") from exc
    return await get_profile(db, profile_id)


async def upsert_relationship(db: aiosqlite.Connection, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from_id = str(data.get("from_profile_id") or "")
    to_id = str(data.get("to_profile_id") or "")
    if not from_id or not to_id or from_id == to_id:
        raise WorldFeedError("A relationship needs two different profiles")
    source = await get_profile(db, from_id)
    target = await get_profile(db, to_id)
    if source["world_id"] != world_id or target["world_id"] != world_id:
        raise WorldFeedError("Both relationship profiles must belong to this world")
    iso, epoch = _now()
    rows = await db.execute_fetchall(
        "SELECT id FROM story_feed_relationships WHERE world_id = ? AND from_profile_id = ? AND to_profile_id = ?",
        (world_id, from_id, to_id),
    )
    relationship_id = rows[0]["id"] if rows else _id("rel")
    values = (
        str(data.get("relationship_type") or "").strip(),
        str(data.get("public_summary") or "").strip(),
        str(data.get("private_context") or "").strip(),
        str(data.get("status") or "active").strip(),
        str(data.get("visibility") or "private").strip(),
        _json(data.get("metadata") or {}),
        iso,
        epoch,
    )
    if rows:
        await db.execute(
            "UPDATE story_feed_relationships SET relationship_type = ?, public_summary = ?, "
            "private_context = ?, status = ?, visibility = ?, metadata = ?, updated_at = ?, "
            "updated_at_epoch = ? WHERE id = ?",
            values + (relationship_id,),
        )
    else:
        await db.execute(
            "INSERT INTO story_feed_relationships "
            "(id, world_id, from_profile_id, to_profile_id, relationship_type, public_summary, "
            "private_context, status, visibility, metadata, created_at, created_at_epoch, updated_at, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (relationship_id, world_id, from_id, to_id) + values[:6] + (iso, epoch, iso, epoch),
        )
    await db.commit()
    result = await db.execute_fetchall("SELECT * FROM story_feed_relationships WHERE id = ?", (relationship_id,))
    return _relationship(result[0], source, target)


def _relationship(row: aiosqlite.Row, source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "world_id": row["world_id"],
        "from_profile": source,
        "to_profile": target,
        "relationship_type": row["relationship_type"],
        "public_summary": row["public_summary"],
        "private_context": row["private_context"],
        "status": row["status"],
        "visibility": row["visibility"],
        "metadata": _loads(row["metadata"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_relationships(db: aiosqlite.Connection, world_id: str) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        "SELECT * FROM story_feed_relationships WHERE world_id = ? ORDER BY updated_at_epoch DESC",
        (world_id,),
    )
    profiles = {item["id"]: item for item in await list_profiles(db, world_id, include_inactive=True)}
    return [
        _relationship(row, profiles[row["from_profile_id"]], profiles[row["to_profile_id"]])
        for row in rows
        if row["from_profile_id"] in profiles and row["to_profile_id"] in profiles
    ]


async def _validate_linked_post(db: aiosqlite.Connection, world_id: str, post_id: str | None, label: str) -> None:
    if not post_id:
        return
    rows = await db.execute_fetchall("SELECT world_id, canon_status FROM story_feed_posts WHERE id = ?", (post_id,))
    if not rows:
        raise WorldFeedNotFound(f"{label} post not found")
    if rows[0]["world_id"] != world_id:
        raise WorldFeedError(f"{label} post belongs to another world")
    if rows[0]["canon_status"] != "approved":
        raise WorldFeedError(f"{label} post is not published")


async def create_post(db: aiosqlite.Connection, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Commit the post and retry receipt together; a lost response is safe to replay."""
    key = data.get("submission_id")
    payload = {k: v for k, v in data.items() if k not in {"submission_id", "viewer_profile_id"}}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Importers may already own a transaction; a savepoint preserves their rollback.
    owned = not db.in_transaction
    if owned:
        await db.execute("BEGIN IMMEDIATE")
    await db.execute("SAVEPOINT feed_post")
    try:
        if key:
            rows = await db.execute_fetchall("SELECT * FROM story_feed_submissions WHERE id=?", (key,))
            if rows:
                receipt = rows[0]
                if receipt["world_id"] != world_id or receipt["fingerprint"] != fingerprint:
                    raise WorldFeedConflict("Submission identifier already used for a different post")
                post = await get_post(db, receipt["post_id"], viewer_profile_id=data.get("viewer_profile_id"))
            else:
                post = await _create_post(db, world_id, data)
                await db.execute("INSERT INTO story_feed_submissions VALUES (?,?,?,?)", (key, world_id, fingerprint, post["id"]))
        else:
            post = await _create_post(db, world_id, data)
        await db.execute("RELEASE feed_post")
        if owned:
            await db.commit()
        return post
    except BaseException:
        await db.execute("ROLLBACK TO feed_post")
        await db.execute("RELEASE feed_post")
        if owned:
            await db.rollback()
        raise


async def _create_post(db: aiosqlite.Connection, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    world = await get_world(db, world_id)
    author_id = str(data.get("author_profile_id") or "")
    author = await get_profile(db, author_id)
    if author["world_id"] != world_id:
        raise WorldFeedError("Author does not belong to this world")
    body = str(data.get("body") or "").strip()
    if len(body) > 4000:
        raise WorldFeedError("A post can contain at most 4000 characters")
    media = list(data.get("media") or [])
    if len(media) > 4:
        raise WorldFeedError("A post can contain at most four images")
    parent_id = str(data.get("parent_post_id") or "").strip() or None
    quote_id = str(data.get("quote_post_id") or "").strip() or None
    await _validate_linked_post(db, world_id, parent_id, "Parent")
    await _validate_linked_post(db, world_id, quote_id, "Quoted")
    if not body and not media and not quote_id:
        raise WorldFeedError("Write something, attach an image, or quote a post")
    post_type = "reply" if parent_id else ("repost" if quote_id and data.get("post_type") == "repost" else "quote" if quote_id else str(data.get("post_type") or "post"))
    if post_type not in POST_TYPES:
        raise WorldFeedError("Unknown post type")
    origin = str(data.get("origin") or "human").lower()
    if origin not in POST_ORIGINS:
        raise WorldFeedError("Unknown post origin")
    if origin == "ai" and author["is_user_controlled"]:
        raise WorldFeedError("AI generation cannot author a user-controlled character")
    canon_level = str(data.get("canon_level") or "ambient").lower()
    canon_status = str(data.get("canon_status") or "approved").lower()
    if canon_level not in CANON_LEVELS or canon_status not in CANON_STATUSES:
        raise WorldFeedError("Unknown canon level or status")
    if origin == "ai" and canon_level == "major" and canon_status == "approved":
        canon_status = "draft"
    post_id = str(data.get("id") or _id("post"))
    iso, epoch = _now()
    await db.execute(
        "INSERT INTO story_feed_posts "
        "(id, world_id, author_profile_id, body, post_type, parent_post_id, quote_post_id, "
        "fictional_at, canon_level, canon_status, origin, ai_run_id, metadata, created_at, "
        "created_at_epoch, updated_at, updated_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            post_id,
            world_id,
            author_id,
            body,
            post_type,
            parent_id,
            quote_id,
            str(data.get("fictional_at") or world.get("fictional_now") or "").strip() or None,
            canon_level,
            canon_status,
            origin,
            str(data.get("ai_run_id") or "").strip() or None,
            _json(data.get("metadata") or {}),
            iso,
            epoch,
            iso,
            epoch,
        ),
    )
    for index, item in enumerate(media):
        url = str(item.get("url") or "").strip()
        if not url:
            raise WorldFeedError("Every image needs a URL")
        await db.execute(
            "INSERT INTO story_feed_media "
            "(id, post_id, media_type, url, alt_text, caption, sort_order, metadata, created_at, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("media"), post_id, str(item.get("media_type") or "image"), url,
                str(item.get("alt_text") or "").strip(), str(item.get("caption") or "").strip(),
                index, _json(item.get("metadata") or {}), iso, epoch,
            ),
        )
    for normalized, display in extract_hashtags(body):
        await db.execute(
            "INSERT OR IGNORE INTO story_feed_post_hashtags "
            "(post_id, world_id, tag, display_tag, created_at_epoch) VALUES (?, ?, ?, ?, ?)",
            (post_id, world_id, normalized, display, epoch),
        )

    for handle in extract_mentions(body):
        matches = await db.execute_fetchall(
            "SELECT id, handle FROM story_feed_profiles WHERE world_id = ? AND handle = ? COLLATE NOCASE AND is_active = 1",
            (world_id, handle),
        )
        if not matches:
            continue
        target_id = matches[0]["id"]
        await db.execute(
            "INSERT OR IGNORE INTO story_feed_mentions (post_id, profile_id, handle, created_at_epoch) VALUES (?, ?, ?, ?)",
            (post_id, target_id, matches[0]["handle"], epoch),
        )
    if canon_status == "approved":
        await _notify_post(db, post_id, iso, epoch)

    await db.execute(
        "UPDATE story_worlds SET updated_at = ?, updated_at_epoch = ? WHERE id = ?",
        (iso, epoch, world_id),
    )
    created = await get_post(db, post_id, viewer_profile_id=data.get("viewer_profile_id"))
    if canon_status == "approved":
        from services.world_feed_memory import record_post_interactions
        await record_post_interactions(db, created)
    return created


async def _notify_post(db: aiosqlite.Connection, post_id: str, iso: str, epoch: int) -> None:
    """Publish interaction notices once, including on draft approval/reapproval."""
    rows = await db.execute_fetchall("SELECT * FROM story_feed_posts WHERE id = ?", (post_id,))
    post = rows[0]
    # Commissioned archive imports are old story events, not fresh alerts.
    if _loads(post['metadata'], {}).get('history_batch'):
        return
    mentions = await db.execute_fetchall(
        "SELECT profile_id FROM story_feed_mentions WHERE post_id = ?", (post_id,),
    )
    targets = {row["profile_id"]: "mention" for row in mentions}
    for key, event in (("parent_post_id", "reply"), ("quote_post_id", "repost" if post["post_type"] == "repost" else "quote")):
        if post[key]:
            linked = await db.execute_fetchall(
                "SELECT author_profile_id FROM story_feed_posts WHERE id = ? AND canon_status = 'approved'",
                (post[key],),
            )
            if linked:
                targets.setdefault(linked[0]["author_profile_id"], event)
    for target_id, event in targets.items():
        if target_id == post["author_profile_id"]:
            continue
        existing = await db.execute_fetchall(
            "SELECT 1 FROM story_feed_notifications WHERE post_id = ? AND target_profile_id = ? "
            "AND event_type IN ('mention', 'reply', 'quote', 'repost')",
            (post_id, target_id),
        )
        if not existing:
            await _notify(db, post["world_id"], target_id, post["author_profile_id"], event, post_id, iso, epoch)


async def _notify(
    db: aiosqlite.Connection,
    world_id: str,
    target_id: str,
    actor_id: str | None,
    event_type: str,
    post_id: str | None,
    iso: str,
    epoch: int,
) -> None:
    await db.execute(
        "INSERT INTO story_feed_notifications "
        "(id, world_id, target_profile_id, actor_profile_id, event_type, post_id, is_read, metadata, created_at, created_at_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, '{}', ?, ?)",
        (_id("notice"), world_id, target_id, actor_id, event_type, post_id, iso, epoch),
    )


_POST_SELECT = """
SELECT p.*,
       a.id a_id, a.world_id a_world_id, a.handle a_handle, a.display_name a_display_name,
       a.account_type a_account_type, a.bio a_bio, a.location a_location, a.website a_website,
       a.avatar_url a_avatar_url, a.header_url a_header_url, a.accent_color a_accent_color,
       a.is_user_controlled a_is_user_controlled, a.is_verified a_is_verified, a.is_active a_is_active,
       a.prompt_notes a_prompt_notes, a.posting_style a_posting_style,
       a.knowledge_json a_knowledge_json, a.metadata a_metadata,
       a.created_at a_created_at, a.updated_at a_updated_at,
       (SELECT COUNT(*) FROM story_feed_reactions r WHERE r.post_id = p.id AND r.kind = 'like') like_count,
       (SELECT COUNT(*) FROM story_feed_posts c WHERE c.parent_post_id = p.id AND c.canon_status = 'approved') reply_count
FROM story_feed_posts p
JOIN story_feed_profiles a ON a.id = p.author_profile_id
"""


async def _decorate_posts(
    db: aiosqlite.Connection,
    rows: Iterable[aiosqlite.Row],
    *,
    viewer_profile_id: str | None = None,
    include_previews: bool = True,
) -> list[dict[str, Any]]:
    rows = list(rows)
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    marks = ",".join("?" for _ in ids)
    media_rows = await db.execute_fetchall(
        f"SELECT * FROM story_feed_media WHERE post_id IN ({marks}) ORDER BY post_id, sort_order",
        tuple(ids),
    )
    media: dict[str, list[dict[str, Any]]] = {post_id: [] for post_id in ids}
    for row in media_rows:
        media[row["post_id"]].append({
            "id": row["id"], "media_type": row["media_type"], "url": row["url"],
            "alt_text": row["alt_text"], "caption": row["caption"], "sort_order": row["sort_order"],
            "metadata": _loads(row["metadata"], {}),
        })
    tag_rows = await db.execute_fetchall(
        f"SELECT post_id, display_tag FROM story_feed_post_hashtags WHERE post_id IN ({marks}) ORDER BY display_tag",
        tuple(ids),
    )
    tags: dict[str, list[str]] = {post_id: [] for post_id in ids}
    for row in tag_rows:
        tags[row["post_id"]].append(row["display_tag"])
    mention_rows = await db.execute_fetchall(
        f"SELECT post_id, handle FROM story_feed_mentions WHERE post_id IN ({marks}) ORDER BY handle",
        tuple(ids),
    )
    mentions: dict[str, list[str]] = {post_id: [] for post_id in ids}
    for row in mention_rows:
        mentions[row["post_id"]].append(row["handle"])
    liked: set[str] = set()
    bookmarked: set[str] = set()
    reposted: set[str] = set()
    if viewer_profile_id:
        like_rows = await db.execute_fetchall(
            f"SELECT post_id FROM story_feed_reactions WHERE profile_id = ? AND kind = 'like' AND post_id IN ({marks})",
            (viewer_profile_id, *ids),
        )
        liked = {row["post_id"] for row in like_rows}
        bookmarked = {row["post_id"] for row in await db.execute_fetchall(
            f"SELECT post_id FROM story_feed_bookmarks WHERE profile_id=? AND post_id IN ({marks})", (viewer_profile_id, *ids))}
        reposted = {row["quote_post_id"] for row in await db.execute_fetchall(
            f"SELECT quote_post_id FROM story_feed_posts WHERE author_profile_id=? AND post_type='repost' AND canon_status='approved' AND quote_post_id IN ({marks})", (viewer_profile_id, *ids))}

    linked_ids = {
        linked
        for row in rows
        for linked in (row["parent_post_id"], row["quote_post_id"])
        if linked
    }
    previews: dict[str, dict[str, Any]] = {}
    if include_previews and linked_ids:
        linked_marks = ",".join("?" for _ in linked_ids)
        linked_rows = await db.execute_fetchall(
            _POST_SELECT + f" WHERE p.id IN ({linked_marks}) AND p.canon_status = 'approved'",
            tuple(linked_ids),
        )
        for linked in await _decorate_posts(db, linked_rows, viewer_profile_id=viewer_profile_id, include_previews=False):
            linked.pop("parent_post", None)
            linked.pop("quoted_post", None)
            previews[linked["id"]] = linked

    result: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "id": row["id"],
            "world_id": row["world_id"],
            "author": _profile(row, "a_"),
            "body": row["body"],
            "post_type": row["post_type"],
            "parent_post_id": row["parent_post_id"],
            "quote_post_id": row["quote_post_id"],
            "fictional_at": row["fictional_at"],
            "canon_level": row["canon_level"],
            "canon_status": row["canon_status"],
            "origin": row["origin"],
            "ai_run_id": row["ai_run_id"],
            "metadata": _loads(row["metadata"], {}),
            "created_at": row["created_at"],
            "created_at_epoch": row["created_at_epoch"],
            "timeline_order": row["timeline_order"] if row["timeline_order"] is not None else row["created_at_epoch"],
            "updated_at": row["updated_at"],
            "like_count": row["like_count"],
            "reply_count": row["reply_count"],
            "viewer_liked": row["id"] in liked,
            "viewer_bookmarked": row["id"] in bookmarked,
            "viewer_reposted": row["id"] in reposted,
            "media": media[row["id"]],
            "hashtags": tags[row["id"]],
            "mentions": mentions[row["id"]],
            "parent_post": previews.get(row["parent_post_id"]),
            "quoted_post": previews.get(row["quote_post_id"]),
        }
        result.append(item)
    return result


async def get_post(db: aiosqlite.Connection, post_id: str, *, viewer_profile_id: str | None = None) -> dict[str, Any]:
    rows = await db.execute_fetchall(_POST_SELECT + " WHERE p.id = ?", (post_id,))
    if not rows:
        raise WorldFeedNotFound("Post not found")
    return (await _decorate_posts(db, rows, viewer_profile_id=viewer_profile_id))[0]


async def list_feed(
    db: aiosqlite.Connection,
    world_id: str,
    *,
    viewer_profile_id: str | None = None,
    limit: int = 40,
    before_epoch: int | None = None,
    before_id: str | None = None,
    hashtag: str | None = None,
    author_profile_id: str | None = None,
    canon_status: str | None = None,
    media_only: bool = False,
    parent_post_id: str | None = None,
    following_only: bool = False,
    bookmarks_only: bool = False,
    search: str | None = None,
) -> list[dict[str, Any]]:
    await get_world(db, world_id)
    where = ["p.world_id = ?"]
    params: list[Any] = [world_id]
    # Withdrawn originals must not remain visible through a repost.
    where.append("(p.post_type != 'repost' OR EXISTS (SELECT 1 FROM story_feed_posts o WHERE o.id=p.quote_post_id AND o.canon_status='approved'))")
    if bookmarks_only:
        if not viewer_profile_id or (await get_profile(db, viewer_profile_id))["world_id"] != world_id:
            raise WorldFeedError("Bookmarks require a viewer in this world")
        where.append("EXISTS (SELECT 1 FROM story_feed_bookmarks b WHERE b.post_id=p.id AND b.profile_id=?)")
        params.append(viewer_profile_id)
    if search and search.strip():
        where.append("(instr(lower(p.body), lower(?)) > 0 OR instr(lower(a.handle), lower(?)) > 0 OR instr(lower(a.display_name), lower(?)) > 0)")
        params.extend([search.strip()] * 3)
    if following_only:
        if not viewer_profile_id or (await get_profile(db, viewer_profile_id))["world_id"] != world_id:
            raise WorldFeedError("Following requires a viewer in this world")
        where.append("(p.author_profile_id = ? OR EXISTS (SELECT 1 FROM story_feed_follows f WHERE f.world_id = p.world_id AND f.follower_profile_id = ? AND f.followed_profile_id = p.author_profile_id))")
        params.extend([viewer_profile_id, viewer_profile_id])
    if parent_post_id:
        where.append("p.parent_post_id = ?")
        params.append(parent_post_id)
    if canon_status:
        if canon_status not in CANON_STATUSES:
            raise WorldFeedError("Unknown canon status")
        where.append("p.canon_status = ?")
        params.append(canon_status)
    else:
        where.append("p.canon_status = 'approved'")
    if before_id is not None and before_epoch is None:
        raise WorldFeedError("before_id requires before_epoch")
    if before_epoch is not None:
        if before_id is not None:
            where.append("(COALESCE(p.timeline_order, p.created_at_epoch), p.id) < (?, ?)")
            params.extend([before_epoch, before_id])
        else:
            # Legacy clients may still request a strict timestamp boundary.
            where.append("COALESCE(p.timeline_order, p.created_at_epoch) < ?")
            params.append(before_epoch)
    if author_profile_id:
        where.append("p.author_profile_id = ?")
        params.append(author_profile_id)
    if hashtag:
        where.append(
            "EXISTS (SELECT 1 FROM story_feed_post_hashtags h WHERE h.post_id = p.id AND h.tag = ? COLLATE NOCASE)"
        )
        params.append(hashtag.lstrip("#").casefold())
    if media_only:
        where.append("EXISTS (SELECT 1 FROM story_feed_media m WHERE m.post_id = p.id)")
    bounded = max(1, min(int(limit), 100))
    params.append(bounded)
    rows = await db.execute_fetchall(
        _POST_SELECT + f" WHERE {' AND '.join(where)} ORDER BY COALESCE(p.timeline_order, p.created_at_epoch) DESC, p.id DESC LIMIT ?",
        tuple(params),
    )
    return await _decorate_posts(db, rows, viewer_profile_id=viewer_profile_id)


async def toggle_like(db: aiosqlite.Connection, post_id: str, profile_id: str, *, desired: bool | None = None) -> dict[str, Any]:
    owned = not db.in_transaction
    if owned:
        await db.execute("BEGIN IMMEDIATE")
    try:
        result = await _toggle_like(db, post_id, profile_id, desired=desired)
        if owned:
            await db.commit()
        return result
    except BaseException:
        if owned:
            await db.rollback()
        raise


async def _toggle_like(db: aiosqlite.Connection, post_id: str, profile_id: str, *, desired: bool | None = None) -> dict[str, Any]:
    post = await get_post(db, post_id, viewer_profile_id=profile_id)
    if post["canon_status"] != "approved":
        raise WorldFeedError("Only published posts can be liked")
    profile = await get_profile(db, profile_id)
    if profile["world_id"] != post["world_id"]:
        raise WorldFeedError("Profile and post belong to different worlds")
    existing = await db.execute_fetchall(
        "SELECT 1 FROM story_feed_reactions WHERE post_id = ? AND profile_id = ? AND kind = 'like'",
        (post_id, profile_id),
    )
    iso, epoch = _now()
    active = not bool(existing) if desired is None else desired
    if bool(existing) == active:
        return {"active": active, "like_count": post["like_count"]}
    if active:
        await db.execute(
            "INSERT OR IGNORE INTO story_feed_reactions (post_id, profile_id, kind, created_at, created_at_epoch) VALUES (?, ?, 'like', ?, ?)",
            (post_id, profile_id, iso, epoch),
        )
        if post["author"]["id"] != profile_id:
            await _notify(db, post["world_id"], post["author"]["id"], profile_id, "like", post_id, iso, epoch)
            from services.world_feed_memory import remember
            if not post["author"]["is_user_controlled"]:
                await remember(
                    db, world_id=post["world_id"], profile_id=post["author"]["id"],
                    other_profile_id=profile_id, source_kind="feed_like",
                    source_id=f"{post_id}:{profile_id}",
                    summary=f'@{profile["handle"]} liked your post: “{post["body"][:500]}”',
                    fictional_at=post.get("fictional_at"),
                )
            if not profile["is_user_controlled"]:
                await remember(
                    db, world_id=post["world_id"], profile_id=profile_id,
                    other_profile_id=post["author"]["id"], source_kind="feed_like",
                    source_id=f"{post_id}:{profile_id}",
                    summary=f'You liked @{post["author"]["handle"]}’s post: “{post["body"][:500]}”',
                    fictional_at=post.get("fictional_at"),
                )
    else:
        await db.execute(
            "DELETE FROM story_feed_reactions WHERE post_id = ? AND profile_id = ? AND kind = 'like'",
            (post_id, profile_id),
        )
    refreshed = await get_post(db, post_id, viewer_profile_id=profile_id)
    return {"active": active, "like_count": refreshed["like_count"]}


async def set_post_canon(
    db: aiosqlite.Connection,
    post_id: str,
    *,
    canon_level: str | None = None,
    canon_status: str | None = None,
) -> dict[str, Any]:
    current = await get_post(db, post_id)
    assignments: list[str] = []
    values: list[Any] = []
    if canon_level is not None:
        if canon_level not in CANON_LEVELS:
            raise WorldFeedError("Unknown canon level")
        assignments.append("canon_level = ?")
        values.append(canon_level)
    if canon_status is not None:
        if canon_status not in CANON_STATUSES:
            raise WorldFeedError("Unknown canon status")
        assignments.append("canon_status = ?")
        values.append(canon_status)
    if assignments:
        iso, epoch = _now()
        assignments.extend(["updated_at = ?", "updated_at_epoch = ?"])
        values.extend([iso, epoch, post_id])
        await db.execute(
            f"UPDATE story_feed_posts SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )
        if canon_status == "approved" and current["canon_status"] != "approved":
            await _notify_post(db, post_id, iso, epoch)
            from services.world_feed_memory import record_post_interactions
            await record_post_interactions(db, await get_post(db, post_id))
        await db.commit()
    return await get_post(db, post_id)


async def list_trends(db: aiosqlite.Connection, world_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        "SELECT h.tag, MAX(h.display_tag) display_tag, COUNT(*) post_count, MAX(h.created_at_epoch) last_used "
        "FROM story_feed_post_hashtags h "
        "JOIN story_feed_posts p ON p.id = h.post_id "
        "WHERE h.world_id = ? AND p.canon_status = 'approved' "
        "GROUP BY h.tag ORDER BY post_count DESC, last_used DESC LIMIT ?",
        (world_id, max(1, min(int(limit), 50))),
    )
    return [
        {"tag": row["display_tag"], "normalized": row["tag"], "post_count": row["post_count"]}
        for row in rows
    ]


async def list_notifications(
    db: aiosqlite.Connection,
    world_id: str,
    profile_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    profile = await get_profile(db, profile_id)
    if profile["world_id"] != world_id:
        raise WorldFeedError("Profile does not belong to this world")
    rows = await db.execute_fetchall(
        "SELECT n.*, a.handle actor_handle, a.display_name actor_name, a.avatar_url actor_avatar, "
        "a.is_verified actor_verified FROM story_feed_notifications n "
        "LEFT JOIN story_feed_profiles a ON a.id = n.actor_profile_id "
        "LEFT JOIN story_feed_posts p ON p.id = n.post_id "
        "WHERE n.world_id = ? AND n.target_profile_id = ? "
        "AND (n.post_id IS NULL OR p.canon_status = 'approved') "
        "ORDER BY n.created_at_epoch DESC LIMIT ?",
        (world_id, profile_id, max(1, min(int(limit), 100))),
    )
    return [{
        "id": row["id"],
        "event_type": row["event_type"],
        "post_id": row["post_id"],
        "is_read": _bool(row["is_read"]),
        "created_at": row["created_at"],
        "actor": None if not row["actor_profile_id"] else {
            "id": row["actor_profile_id"], "handle": row["actor_handle"],
            "display_name": row["actor_name"], "avatar_url": row["actor_avatar"],
            "is_verified": _bool(row["actor_verified"]),
        },
        "metadata": _loads(row["metadata"], {}),
    } for row in rows]


async def mark_notifications_read(db: aiosqlite.Connection, world_id: str, profile_id: str) -> int:
    cursor = await db.execute(
        "UPDATE story_feed_notifications SET is_read = 1 WHERE world_id = ? AND target_profile_id = ? AND is_read = 0",
        (world_id, profile_id),
    )
    await db.commit()
    return cursor.rowcount


async def toggle_follow(
    db: aiosqlite.Connection,
    world_id: str,
    follower_profile_id: str,
    followed_profile_id: str,
    *,
    desired: bool | None = None,
) -> dict[str, Any]:
    if follower_profile_id == followed_profile_id:
        raise WorldFeedError("A profile cannot follow itself")
    follower = await get_profile(db, follower_profile_id)
    followed = await get_profile(db, followed_profile_id)
    if follower["world_id"] != world_id or followed["world_id"] != world_id:
        raise WorldFeedError("Both profiles must belong to this world")
    rows = await db.execute_fetchall(
        "SELECT 1 FROM story_feed_follows WHERE world_id = ? AND follower_profile_id = ? AND followed_profile_id = ?",
        (world_id, follower_profile_id, followed_profile_id),
    )
    active = not bool(rows) if desired is None else desired
    if active == bool(rows):
        return {"active": active}
    if active:
        iso, epoch = _now()
        await db.execute(
            "INSERT INTO story_feed_follows (world_id, follower_profile_id, followed_profile_id, created_at, created_at_epoch) "
            "VALUES (?, ?, ?, ?, ?)",
            (world_id, follower_profile_id, followed_profile_id, iso, epoch),
        )
        await _notify(db, world_id, followed_profile_id, follower_profile_id, "follow", None, iso, epoch)
        from services.world_feed_memory import remember
        source_id = f"{follower_profile_id}:{followed_profile_id}"
        if not followed["is_user_controlled"]:
            await remember(
                db, world_id=world_id, profile_id=followed_profile_id,
                other_profile_id=follower_profile_id, source_kind="feed_follow",
                source_id=source_id,
                summary=f'@{follower["handle"]} followed your account.',
                fictional_at=(await get_world(db, world_id))["fictional_now"],
            )
        if not follower["is_user_controlled"]:
            await remember(
                db, world_id=world_id, profile_id=follower_profile_id,
                other_profile_id=followed_profile_id, source_kind="feed_follow",
                source_id=source_id,
                summary=f'You followed @{followed["handle"]}.',
                fictional_at=(await get_world(db, world_id))["fictional_now"],
            )
    else:
        await db.execute(
            "DELETE FROM story_feed_follows WHERE world_id = ? AND follower_profile_id = ? AND followed_profile_id = ?",
            (world_id, follower_profile_id, followed_profile_id),
        )
    await db.commit()
    return {"active": active}


async def profile_detail(db: aiosqlite.Connection, profile_id: str, *, viewer_profile_id: str | None = None) -> dict[str, Any]:
    profile = await get_profile(db, profile_id)
    if viewer_profile_id and (await get_profile(db, viewer_profile_id))["world_id"] != profile["world_id"]:
        raise WorldFeedError("Viewer belongs to a different world")
    rows = await db.execute_fetchall(
        "SELECT (SELECT COUNT(*) FROM story_feed_follows WHERE followed_profile_id = ?) followers, "
        "(SELECT COUNT(*) FROM story_feed_follows WHERE follower_profile_id = ?) following, "
        "(SELECT COUNT(*) FROM story_feed_posts WHERE author_profile_id = ? AND canon_status = 'approved') posts, "
        "EXISTS(SELECT 1 FROM story_feed_follows WHERE follower_profile_id = ? AND followed_profile_id = ?) viewer_follows, "
        "EXISTS(SELECT 1 FROM story_feed_follows WHERE follower_profile_id = ? AND followed_profile_id = ?) follows_viewer",
        (profile_id, profile_id, profile_id, viewer_profile_id, profile_id, profile_id, viewer_profile_id),
    )
    return {**profile, **dict(rows[0])}


async def list_connections(db: aiosqlite.Connection, profile_id: str, *, direction: str = "followers", after_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    await get_profile(db, profile_id)
    if direction not in {"followers", "following"}:
        raise WorldFeedError("Unknown connection direction")
    owner, other = ("followed_profile_id", "follower_profile_id") if direction == "followers" else ("follower_profile_id", "followed_profile_id")
    rows = await db.execute_fetchall(
        f"SELECT p.* FROM story_feed_follows f JOIN story_feed_profiles p ON p.id = f.{other} "
        f"WHERE f.{owner} = ? AND p.id > ? ORDER BY p.id LIMIT ?",
        (profile_id, after_id, max(1, min(int(limit), 100))),
    )
    return [_profile(row) for row in rows]


async def get_thread(db: aiosqlite.Connection, post_id: str, *, viewer_profile_id: str | None = None, limit: int = 40, before_epoch: int | None = None, before_id: str | None = None) -> dict[str, Any]:
    post = await get_post(db, post_id, viewer_profile_id=viewer_profile_id)
    if post["canon_status"] != "approved":
        raise WorldFeedNotFound("Published post not found")
    if viewer_profile_id and (await get_profile(db, viewer_profile_id))["world_id"] != post["world_id"]:
        raise WorldFeedError("Viewer belongs to a different world")
    replies = await list_feed(db, post["world_id"], viewer_profile_id=viewer_profile_id, parent_post_id=post_id, limit=limit, before_epoch=before_epoch, before_id=before_id)
    return {"post": post, "posts": replies, "next_cursor": {"before_epoch": replies[-1]["timeline_order"], "before_id": replies[-1]["id"]} if len(replies) == limit else None}


async def set_bookmark(db, post_id, profile_id, active):
    post = await get_post(db, post_id)
    profile = await get_profile(db, profile_id)
    if profile["world_id"] != post["world_id"] or post["canon_status"] != "approved":
        raise WorldFeedError("Bookmark requires a published post in your world")
    if active:
        await db.execute("INSERT OR IGNORE INTO story_feed_bookmarks VALUES (?,?)", (post_id, profile_id))
    else:
        await db.execute("DELETE FROM story_feed_bookmarks WHERE post_id=? AND profile_id=?", (post_id, profile_id))
    await db.commit()
    return {"active": active}


async def set_repost(db, post_id, profile_id, active):
    await db.execute("BEGIN IMMEDIATE")
    try:
        original = await get_post(db, post_id)
        profile = await get_profile(db, profile_id)
        if profile["world_id"] != original["world_id"] or original["canon_status"] != "approved":
            raise WorldFeedError("Repost requires a published post in your world")
        if original["post_type"] == "repost":
            raise WorldFeedError("Repost the original post instead")
        rows = await db.execute_fetchall("SELECT id FROM story_feed_posts WHERE author_profile_id=? AND quote_post_id=? AND post_type='repost'", (profile_id, post_id))
        if rows:
            iso, epoch = _now()
            await db.execute("UPDATE story_feed_posts SET canon_status=?,updated_at=?,updated_at_epoch=? WHERE id=?", ("approved" if active else "rejected", iso, epoch, rows[0]["id"]))
        elif active:
            await create_post(db, original["world_id"], {"author_profile_id": profile_id, "quote_post_id": post_id, "post_type": "repost"})
        await db.commit()
        return {"active": active}
    except BaseException:
        await db.rollback()
        raise


async def notification_summary(db, world_id, profile_id):
    profile = await get_profile(db, profile_id)
    if profile["world_id"] != world_id:
        raise WorldFeedError("Profile does not belong to this world")
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) unread_count FROM story_feed_notifications n LEFT JOIN story_feed_posts p ON p.id=n.post_id "
        "WHERE n.world_id=? AND n.target_profile_id=? AND n.is_read=0 AND (n.post_id IS NULL OR p.canon_status='approved')",
        (world_id, profile_id))
    return rows[0]["unread_count"]


async def mark_seen_notifications(db, world_id, profile_id, ids):
    await notification_summary(db, world_id, profile_id)
    if not ids:
        return 0
    marks = ','.join('?' for _ in ids)
    cursor = await db.execute(f"UPDATE story_feed_notifications SET is_read=1 WHERE world_id=? AND target_profile_id=? AND id IN ({marks}) AND is_read=0", (world_id, profile_id, *ids))
    await db.commit()
    return cursor.rowcount


async def update_world_settings(db, world_id, data):
    """Save the clock and activity policy in one transaction, retaining photo settings."""
    await db.execute("BEGIN IMMEDIATE")
    try:
        world = await get_world(db, world_id)
        data = dict(data)
        activity = dict(world["metadata"].get("activity") or {})
        for key in ("daily_limit", "auto_publish", "scene_reactions"):
            value = data.pop("activity_" + key, None)
            if value is not None:
                activity[key] = value
        if activity:
            data["metadata"] = {**world["metadata"], **(data.get("metadata") or {}), "activity": activity}
        result = await update_world(db, world_id, data)
        await db.commit()
        return result
    except BaseException:
        await db.rollback()
        raise
