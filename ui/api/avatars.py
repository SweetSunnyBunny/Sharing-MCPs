"""REST endpoints for Pack avatar discovery and GLB delivery."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services.avatar_assets import (
    AvatarAssetError,
    avatar_model_path,
    avatar_preview_path,
    get_avatar,
    list_avatars,
)


router = APIRouter(prefix="/api/avatars", tags=["avatars"])


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


@router.get("")
async def avatars_index():
    """List every locally available Pack body and its readiness metadata."""
    return {"avatars": list_avatars()}


@router.get("/{identity}")
async def avatar_metadata(identity: str):
    """Return the active manifest and inspected capabilities for one body."""
    try:
        avatar = get_avatar(identity)
    except AvatarAssetError as error:
        raise _not_found(error) from error
    avatar.pop("_model_path", None)
    avatar.pop("_preview_path", None)
    return avatar


@router.get("/{identity}/model")
async def avatar_model(identity: str):
    """Serve the active GLB with byte-range support for browser renderers."""
    try:
        path = avatar_model_path(identity)
    except AvatarAssetError as error:
        raise _not_found(error) from error
    return FileResponse(
        path,
        media_type="model/gltf-binary",
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "bytes",
        },
    )


@router.get("/{identity}/preview")
async def avatar_preview(identity: str):
    """Serve the current Tripo preview when one exists."""
    try:
        path = avatar_preview_path(identity)
    except AvatarAssetError as error:
        raise _not_found(error) from error
    if not path:
        raise HTTPException(status_code=404, detail="avatar preview not found")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/webp",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )
