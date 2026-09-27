"""Serve generated videos (Sora renders saved by the Discord backend).

The Discord backend's discord_check_video tool downloads finished MP4s into
VIDEOS_DIR; this route makes them playable in the browser and linkable on
Discord when a file exceeds the bot upload limit. Serve-only — uploads come
exclusively from the backend writing to disk.
"""

# ANAM GUIDE: GENERATED VIDEO SERVING
# What: Serves finished Sora video files (MP4s) from the videos folder so they can play in a browser or be linked on Discord — serve-only, no uploads.
# Called by: the Discord backend (discord_check_video saves the files and hands out /api/videos/file/... links); no Anam page JS calls this directly.
# Edit here when: changing how video files are served or cached. The videos themselves are created and downloaded by the Discord backend, not here.

import logging
import mimetypes

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from config import VIDEOS_DIR

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["videos"])

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/file/{filename}")
async def serve_video(filename: str):
    """Serve a stored video file."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if ".." in safe_name or not safe_name or safe_name.startswith("_"):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    filepath = VIDEOS_DIR / safe_name
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "Video not found"})

    media_type = mimetypes.guess_type(safe_name)[0] or "video/mp4"
    return FileResponse(
        filepath,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Accept-Ranges": "bytes",
        },
    )
