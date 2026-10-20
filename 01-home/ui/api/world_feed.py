"""REST API for Anam's private story-world social feed."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db.database import get_db, release_db
from services import world_feed, world_feed_dms, world_feed_photos


router = APIRouter(prefix="/api/world-feed", tags=["world-feed"])


class WorldCreate(BaseModel):
    id: str | None = None
    slug: str
    name: str
    description: str = ""
    story_identity: str = "Avery"
    story_branch: str = ""
    source_package: str | None = None
    fictional_now: str | None = None
    clock_label: str = ""
    posting_enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldUpdate(BaseModel):
    activity_scene_reactions: bool | None = None
    activity_daily_limit: int | None = Field(default=None, ge=1, le=48)
    activity_auto_publish: bool | None = None
    name: str | None = None
    description: str | None = None
    story_identity: str | None = None
    story_branch: str | None = None
    source_package: str | None = None
    fictional_now: str | None = None
    clock_label: str | None = None
    posting_enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class ProfileCreate(BaseModel):
    id: str | None = None
    handle: str
    display_name: str
    account_type: str = "character"
    bio: str = ""
    location: str = ""
    website: str = ""
    avatar_url: str = ""
    header_url: str = ""
    accent_color: str = "#D9485F"
    is_user_controlled: bool = False
    is_verified: bool = False
    is_active: bool = True
    prompt_notes: str = ""
    posting_style: str = ""
    knowledge: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    handle: str | None = None
    display_name: str | None = None
    account_type: str | None = None
    bio: str | None = None
    location: str | None = None
    website: str | None = None
    avatar_url: str | None = None
    header_url: str | None = None
    accent_color: str | None = None
    is_user_controlled: bool | None = None
    is_verified: bool | None = None
    is_active: bool | None = None
    prompt_notes: str | None = None
    posting_style: str | None = None
    knowledge: list[Any] | None = None
    metadata: dict[str, Any] | None = None


class RelationshipUpsert(BaseModel):
    from_profile_id: str
    to_profile_id: str
    relationship_type: str = ""
    public_summary: str = ""
    private_context: str = ""
    status: str = "active"
    visibility: Literal["public", "private"] = "private"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MediaInput(BaseModel):
    url: str
    media_type: str = "image"
    alt_text: str = ""
    caption: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostCreate(BaseModel):
    submission_id: str | None = Field(default=None, min_length=16, max_length=80)
    id: str | None = None
    author_profile_id: str
    body: str = ""
    post_type: Literal["post", "reply", "quote"] = "post"
    parent_post_id: str | None = None
    quote_post_id: str | None = None
    fictional_at: str | None = None
    canon_level: Literal["ambient", "interaction", "major"] = "ambient"
    canon_status: Literal["draft", "approved", "rejected"] = "approved"
    origin: Literal["human", "ai", "system"] = "human"
    ai_run_id: str | None = None
    media: list[MediaInput] = Field(default_factory=list, max_length=4)
    metadata: dict[str, Any] = Field(default_factory=dict)
    viewer_profile_id: str | None = None


class CanonUpdate(BaseModel):
    canon_level: Literal["ambient", "interaction", "major"] | None = None
    canon_status: Literal["draft", "approved", "rejected"] | None = None


class ProfileAction(BaseModel):
    profile_id: str


class FollowSet(ProfileAction):
    active: bool = True


class DMThreadCreate(BaseModel):
    profile_id: str
    other_profile_id: str


class DMMessageCreate(ProfileAction):
    body: str = Field(min_length=1, max_length=4000)
    submission_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class PhotoRequest(BaseModel):
    profile_id: str
    idea: str = Field(default="", max_length=1500)
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class PhotoSettings(BaseModel):
    enabled: bool = False
    daily_limit: int = Field(default=1, ge=1, le=world_feed_photos.MAX_DAILY_LIMIT)
    profile_ids: list[str] = Field(default_factory=list, max_length=100)


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, world_feed.WorldFeedNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, world_feed.WorldFeedConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, world_feed.WorldFeedError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="World feed operation failed")


async def _call(fn, *args, **kwargs):
    db = await get_db()
    try:
        return await fn(db, *args, **kwargs)
    except world_feed.WorldFeedError as exc:
        raise _http_error(exc) from exc
    finally:
        await release_db(db)


@router.get("/worlds")
async def worlds_list():
    return {"worlds": await _call(world_feed.list_worlds)}


@router.post("/worlds")
async def worlds_create(body: WorldCreate):
    return await _call(world_feed.create_world, _payload(body))


@router.get("/worlds/{world_id}")
async def worlds_get(world_id: str):
    world = await _call(world_feed.get_world, world_id)
    profiles = await _call(world_feed.list_profiles, world_id)
    return {
        "world": world,
        "profiles": profiles,
        "user_profiles": [item for item in profiles if item["is_user_controlled"]],
    }


@router.patch("/worlds/{world_id}")
async def worlds_update(world_id: str, body: WorldUpdate):
    return await _call(world_feed.update_world_settings, world_id, _payload(body))


@router.get("/worlds/{world_id}/profiles")
async def profiles_list(world_id: str, include_inactive: bool = False):
    return {"profiles": await _call(world_feed.list_profiles, world_id, include_inactive=include_inactive)}


@router.post("/worlds/{world_id}/profiles")
async def profiles_create(world_id: str, body: ProfileCreate):
    return await _call(world_feed.create_profile, world_id, _payload(body))


@router.patch("/profiles/{profile_id}")
async def profiles_update(profile_id: str, body: ProfileUpdate):
    return await _call(world_feed.update_profile, profile_id, _payload(body))


@router.get("/profiles/{profile_id}")
async def profiles_get(profile_id: str, viewer_profile_id: str | None = None):
    return await _call(world_feed.profile_detail, profile_id, viewer_profile_id=viewer_profile_id)


@router.get("/profiles/{profile_id}/connections")
async def profiles_connections(profile_id: str, direction: Literal["followers", "following"] = "followers", after_id: str = "", limit: int = Query(default=50, ge=1, le=100)):
    profiles = await _call(world_feed.list_connections, profile_id, direction=direction, after_id=after_id, limit=limit)
    return {"profiles": profiles, "next_cursor": profiles[-1]["id"] if len(profiles) == limit else None}


@router.get("/worlds/{world_id}/relationships")
async def relationships_list(world_id: str):
    return {"relationships": await _call(world_feed.list_relationships, world_id)}


@router.post("/worlds/{world_id}/relationships")
async def relationships_upsert(world_id: str, body: RelationshipUpsert):
    return await _call(world_feed.upsert_relationship, world_id, _payload(body))


@router.get("/worlds/{world_id}/feed")
async def feed_list(
    world_id: str,
    viewer_profile_id: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    before_epoch: int | None = None,
    before_id: str | None = None,
    hashtag: str | None = None,
    author_profile_id: str | None = None,
    canon_status: Literal["draft", "approved", "rejected"] | None = None,
    media_only: bool = False,
    following_only: bool = False,
    bookmarks_only: bool = False,
    search: str | None = Query(default=None, max_length=200),
):
    posts = await _call(
        world_feed.list_feed,
        world_id,
        viewer_profile_id=viewer_profile_id,
        limit=limit,
        before_epoch=before_epoch,
        before_id=before_id,
        hashtag=hashtag,
        author_profile_id=author_profile_id,
        canon_status=canon_status,
        media_only=media_only,
        following_only=following_only,
        bookmarks_only=bookmarks_only,
        search=search,
    )
    return {
        "posts": posts,
        "next_cursor": {"before_epoch": posts[-1]["timeline_order"], "before_id": posts[-1]["id"]}
        if len(posts) == limit else None,
    }


@router.post("/worlds/{world_id}/posts")
async def posts_create(world_id: str, body: PostCreate):
    return await _call(world_feed.create_post, world_id, _payload(body))


@router.get("/posts/{post_id}")
async def posts_get(post_id: str, viewer_profile_id: str | None = None):
    return await _call(world_feed.get_post, post_id, viewer_profile_id=viewer_profile_id)


@router.get("/posts/{post_id}/thread")
async def posts_thread(post_id: str, viewer_profile_id: str | None = None, limit: int = Query(default=40, ge=1, le=100), before_epoch: int | None = None, before_id: str | None = None):
    return await _call(world_feed.get_thread, post_id, viewer_profile_id=viewer_profile_id, limit=limit, before_epoch=before_epoch, before_id=before_id)


@router.patch("/posts/{post_id}/canon")
async def posts_canon(post_id: str, body: CanonUpdate):
    return await _call(world_feed.set_post_canon, post_id, **_payload(body))


@router.post("/posts/{post_id}/like")
async def posts_like(post_id: str, body: ProfileAction):
    return await _call(world_feed.toggle_like, post_id, body.profile_id)


@router.post("/worlds/{world_id}/follow/{followed_profile_id}")
async def profiles_follow(world_id: str, followed_profile_id: str, body: ProfileAction):
    return await _call(
        world_feed.toggle_follow,
        world_id,
        body.profile_id,
        followed_profile_id,
    )


@router.put("/worlds/{world_id}/follow/{followed_profile_id}")
async def profiles_set_follow(world_id: str, followed_profile_id: str, body: FollowSet):
    return await _call(world_feed.toggle_follow, world_id, body.profile_id, followed_profile_id, desired=body.active)


@router.get("/worlds/{world_id}/trends")
async def trends_list(world_id: str, limit: int = Query(default=10, ge=1, le=50)):
    return {"trends": await _call(world_feed.list_trends, world_id, limit=limit)}


@router.get("/worlds/{world_id}/dms")
async def dms_list(world_id: str, profile_id: str):
    return {
        "threads": await _call(world_feed_dms.list_threads, world_id, profile_id),
        "unread_count": await _call(world_feed_dms.unread_count, world_id, profile_id),
    }


@router.post("/worlds/{world_id}/dms")
async def dms_create(world_id: str, body: DMThreadCreate):
    return await _call(
        world_feed_dms.create_thread, world_id, body.profile_id, body.other_profile_id,
    )


@router.get("/dms/{thread_id}")
async def dms_get(thread_id: str, profile_id: str, mark_read: bool = True,
                  before_rowid: int | None = Query(default=None, ge=1),
                  limit: int = Query(default=100, ge=1, le=300)):
    return await _call(
        world_feed_dms.get_thread, thread_id, profile_id, mark_read=mark_read,
        before_rowid=before_rowid, limit=limit,
    )


@router.post("/dms/{thread_id}/messages")
async def dms_send(thread_id: str, body: DMMessageCreate):
    return await _call(
        world_feed_dms.send_message, thread_id, body.profile_id, body.body,
        submission_id=body.submission_id,
    )


@router.post("/dms/{thread_id}/retry")
async def dms_retry(thread_id: str, body: ProfileAction):
    return await _call(world_feed_dms.retry_reply, thread_id, body.profile_id)


@router.get("/worlds/{world_id}/photos")
async def photos_list(world_id: str):
    return await _call(world_feed_photos.list_jobs, world_id)


@router.post("/worlds/{world_id}/photos", status_code=202)
async def photos_create(world_id: str, body: PhotoRequest):
    return await _call(world_feed_photos.enqueue, world_id, body.profile_id, body.idea, job_id=body.job_id)


@router.put("/worlds/{world_id}/photos/settings")
async def photos_settings(world_id: str, body: PhotoSettings):
    return await _call(world_feed_photos.configure, world_id, body.enabled, body.daily_limit, body.profile_ids)


@router.get("/worlds/{world_id}/notifications")
async def notifications_list(
    world_id: str,
    profile_id: str,
    limit: int = Query(default=50, ge=1, le=100),
):
    return {
        "unread_count": await _call(world_feed.notification_summary, world_id, profile_id),
        "notifications": await _call(
            world_feed.list_notifications,
            world_id,
            profile_id,
            limit=limit,
        )
    }


@router.post("/worlds/{world_id}/notifications/read")
async def notifications_read(world_id: str, body: ProfileAction):
    count = await _call(world_feed.mark_notifications_read, world_id, body.profile_id)
    return {"ok": True, "updated": count}


class SeenNotifications(ProfileAction):
    ids: list[str] = Field(default_factory=list, max_length=100)


@router.post("/worlds/{world_id}/notifications/seen")
async def notifications_seen(world_id: str, body: SeenNotifications):
    return {"updated": await _call(world_feed.mark_seen_notifications, world_id, body.profile_id, body.ids)}


@router.put("/posts/{post_id}/like")
async def posts_set_like(post_id: str, body: FollowSet):
    return await _call(world_feed.toggle_like, post_id, body.profile_id, desired=body.active)


@router.put("/posts/{post_id}/bookmark")
async def posts_bookmark(post_id: str, body: FollowSet):
    return await _call(world_feed.set_bookmark, post_id, body.profile_id, body.active)


@router.put("/posts/{post_id}/repost")
async def posts_repost(post_id: str, body: FollowSet):
    return await _call(world_feed.set_repost, post_id, body.profile_id, body.active)


class ActivitySettings(BaseModel):
    scene_reactions: bool | None = None
    enabled: bool = False
    daily_limit: int = Field(default=12, ge=1, le=48)
    auto_publish: bool = False


@router.get("/worlds/{world_id}/activity")
async def activity_status(world_id: str):
    from services import world_feed_activity
    return await _call(world_feed_activity.status, world_id)


@router.put("/worlds/{world_id}/activity/settings")
async def activity_settings(world_id: str, body: ActivitySettings):
    from services import world_feed_activity
    return await _call(world_feed_activity.configure, world_id, body.enabled, body.daily_limit, body.auto_publish, body.scene_reactions)
