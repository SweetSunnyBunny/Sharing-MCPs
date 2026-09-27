"""REST: read/write STATE.md for character identities (Bakugou, Dean).

Backs the chat UI's story-state panel. Only character identities (those
with an entry in STORY_BY_IDENTITY) have a STATE.md and are valid targets.
"""

# ANAM GUIDE: STORY STATE PANEL API
# What: Lets the browser read and edit a character's STATE.md (where the roleplay stands — era, location, mood, open threads) for character identities like Bakugou and Dean.
# Called by: static/js/story-state.js (the story-state panel on the chat page).
# Edit here when: adding a new editable story-state field (add it to StoryStateUpdate here AND to services/story_state.py, which owns the actual file reading/writing).

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import IDENTITIES
from services.story_state import (
    INLINE_FIELDS,
    SECTION_FIELDS,
    STORY_BY_IDENTITY,
    is_character_identity,
    read_state,
    write_state,
)

router = APIRouter(prefix="/api/story-state")


class StoryStateUpdate(BaseModel):
    """Partial update — only fields present are touched."""
    last_updated: str | None = None
    updated_by: str | None = None
    era: str | None = None
    in_fic_day: str | None = None
    location: str | None = None
    who_else: str | None = None
    tone_flavor: str | None = None
    emotional_temperature: str | None = None
    last_scene: str | None = None
    character_state: str | None = None
    whats_promised: str | None = None
    beats_to_hit: str | None = None
    not_this_today: str | None = None
    open_threads: str | None = None
    recent_beats: str | None = None
    callback_anchors: str | None = None


def _field_descriptors() -> list[dict[str, str]]:
    """Field metadata for the browser UI (label + type)."""
    out = []
    for key, label in INLINE_FIELDS:
        out.append({"key": key, "label": label, "kind": "inline"})
    for key, label in SECTION_FIELDS:
        out.append({"key": key, "label": label, "kind": "section"})
    return out


@router.get("/identities")
async def list_story_identities():
    """Identities that have a STATE.md (i.e., character identities)."""
    return {
        "identities": list(STORY_BY_IDENTITY.keys()),
        "fields": _field_descriptors(),
    }


@router.get("/{identity}")
async def get_story_state(identity: str):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    if not is_character_identity(identity):
        return JSONResponse(
            status_code=400,
            content={"error": f"{identity} is not a character identity (no STATE.md)"},
        )
    state = read_state(identity)
    return JSONResponse(content={
        "identity": state["identity"],
        "story_key": state["story_key"],
        "path": state.get("path"),
        "exists": state.get("exists", False),
        "fields": state.get("fields", {}),
        "field_descriptors": _field_descriptors(),
    })


@router.put("/{identity}")
async def update_story_state(identity: str, body: StoryStateUpdate):
    if identity not in IDENTITIES:
        return JSONResponse(status_code=404, content={"error": "Unknown identity"})
    if not is_character_identity(identity):
        return JSONResponse(
            status_code=400,
            content={"error": f"{identity} is not a character identity (no STATE.md)"},
        )
    # Only include fields the client actually sent (not None)
    update_fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_fields:
        return JSONResponse(status_code=400, content={"error": "No fields to update"})
    try:
        state = write_state(identity, update_fields)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse(content={
        "ok": True,
        "fields": state.get("fields", {}),
    })
