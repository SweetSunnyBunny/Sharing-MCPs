"""REST: Image endpoints - upload, serve, register external images."""

# ANAM GUIDE: IMAGE UPLOAD AND SERVING API
# What: Receives image uploads from the chat page, checks they are real images, shrinks big ones, and serves them back at /api/images/file/... (public, so Anthropic can fetch them too).
# Called by: static/js/chat.js and static/js/utils.js in the browser; services/chat_turn_prep.py, session_lifecycle.py, platform_bridge.py build image links with these routes.
# Edit here when: changing allowed image types, the max upload size, how images get compressed, or which folders count as safe sources for registering an existing image.

import json
import asyncio
import hashlib
import logging
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Request, UploadFile, Query
from fastapi.responses import FileResponse, JSONResponse

from config import (
    DATA_DIR,
    IMAGE_ALLOWED_EXTENSIONS,
    IMAGE_MAX_DIMENSION,
    IMAGE_MAX_SIZE_MB,
    IMAGES_DIR,
    TIMEZONE,
    VOICE_VAULT_DIR,
)
from db.database import get_db, release_db
from services.rate_limit import limiter

# Magic byte signatures for image validation
_IMAGE_MAGIC_BYTES = {
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],  # also check "WEBP" at offset 8
}


def _validate_image_magic(data: bytes, ext: str) -> bool:
    """Verify the file's magic bytes match its claimed extension."""
    signatures = _IMAGE_MAGIC_BYTES.get(ext)
    if not signatures:
        return True  # unknown extension, skip magic check
    for sig in signatures:
        if data[:len(sig)] == sig:
            # WEBP needs additional check: bytes 8-12 must be "WEBP"
            if ext == ".webp" and data[8:12] != b"WEBP":
                return False
            return True
    return False


# Directories allowed as sources for image registration
ALLOWED_IMAGE_SOURCES = [
    IMAGES_DIR,
    VOICE_VAULT_DIR,
    DATA_DIR,
]

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/images")


def _compress_image(filepath: Path, ext: str) -> None:
    """Resize/compress an image in-place if it exceeds IMAGE_MAX_DIMENSION.

    Skips WebP (already compressed) and GIF (may be animated).
    On any failure the original file is left untouched.
    """
    if ext in {".webp", ".gif"}:
        return

    try:
        from PIL import Image

        original_size = filepath.stat().st_size
        with Image.open(filepath) as img:
            w, h = img.size
            if max(w, h) <= IMAGE_MAX_DIMENSION:
                return

            # Proportional resize so longest side == IMAGE_MAX_DIMENSION
            ratio = IMAGE_MAX_DIMENSION / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            resized = img.resize((new_w, new_h), Image.LANCZOS)

            if ext in {".jpg", ".jpeg"}:
                # Preserve RGB (drop alpha if present)
                if resized.mode in ("RGBA", "P"):
                    resized = resized.convert("RGB")
                resized.save(filepath, format="JPEG", quality=85, optimize=True)
            elif ext == ".png":
                resized.save(filepath, format="PNG", optimize=True)
            else:
                resized.save(filepath)

        new_size = filepath.stat().st_size
        log.info(
            "Image compressed: %s  %dx%d -> %dx%d  %d -> %d bytes",
            filepath.name, w, h, new_w, new_h, original_size, new_size,
        )
    except Exception:
        log.warning("Image compression failed for %s, keeping original", filepath.name, exc_info=True)


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_allowed_source_path(path: Path) -> bool:
    for allowed in ALLOWED_IMAGE_SOURCES:
        try:
            allowed_root = allowed.resolve()
        except OSError:
            continue
        if _is_path_within(path, allowed_root):
            return True
    return False


def informative_filename(identity: str | None, ext: str) -> str:
    """Generate {identity}_{YYYYMMDD}_{HHMMSS}_{12hex}.{ext}."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    ts = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:12]
    name = identity.lower() if identity else "unknown"
    return f"{name}_{ts}_{short_id}{ext}"


@router.post("/upload")
@limiter.limit("20/minute")
async def upload_image(request: Request, file: UploadFile = File(...), identity: str | None = Form(None)):
    """Upload an image from the client. Returns {image_id, filename, url}."""
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No filename"})

    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"File type {ext} not allowed"},
        )

    try:
        data = await file.read()
    finally:
        await file.close()
    if len(data) > IMAGE_MAX_SIZE_MB * 1024 * 1024:
        return JSONResponse(
            status_code=400,
            content={"error": f"File too large (max {IMAGE_MAX_SIZE_MB}MB)"},
        )

    if not _validate_image_magic(data, ext):
        return JSONResponse(
            status_code=400,
            content={"error": f"File content does not match {ext} format"},
        )

    filename = informative_filename(identity, ext)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filepath = IMAGES_DIR / filename
    filepath.write_bytes(data)
    _compress_image(filepath, ext)

    log.info("Image uploaded: %s (%d bytes)", filename, filepath.stat().st_size)
    return JSONResponse(
        content={
            "image_id": filename.rsplit(".", 1)[0],
            "filename": filename,
            "url": f"/api/images/file/{filename}",
        }
    )


# Dedup manifest: maps collapsed image filenames -> their canonical byte-
# identical survivor, written by scripts/image_dedup_migrate.py. It lets every
# historical URL (in messages AND Qualia memories) keep resolving after exact-
# duplicate images are collapsed to one physical copy. Quarantined orphan-
# uniques live in IMAGES_DIR/_quarantine and are resolved here too, so nothing
# 404s while they await a later purge.
_QUARANTINE_DIR = IMAGES_DIR / "_quarantine"
_MANIFEST_PATH = IMAGES_DIR / "_dedup_manifest.json"
_manifest_cache: dict[str, str] = {}
_manifest_mtime: float = -1.0


def _load_dedup_manifest() -> dict[str, str]:
    """Load the dedup manifest, refreshing the cache when the file changes."""
    global _manifest_cache, _manifest_mtime
    try:
        mtime = _MANIFEST_PATH.stat().st_mtime
    except OSError:
        _manifest_cache, _manifest_mtime = {}, -1.0
        return _manifest_cache
    if mtime != _manifest_mtime:
        try:
            _manifest_cache = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            _manifest_cache = {}
        _manifest_mtime = mtime
    return _manifest_cache


def _resolve_image_path(safe_name: str) -> Path | None:
    """Resolve a requested image name to a real file on disk.

    Order: live file -> dedup-manifest canonical -> quarantine. Returns None
    only when nothing matches (a genuine 404).
    """
    direct = IMAGES_DIR / safe_name
    if direct.exists():
        return direct
    target = _load_dedup_manifest().get(safe_name, safe_name)
    canonical = IMAGES_DIR / target
    if canonical.exists():
        return canonical
    for candidate in (target, safe_name):
        quar = _QUARANTINE_DIR / candidate
        if quar.exists():
            return quar
    return None


@router.get("/file/{filename}")
async def serve_image(filename: str, width: int | None = Query(default=None, ge=160, le=1280)):
    """Serve a stored image file."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    # Underscore-prefixed names are internal bookkeeping (_dedup_manifest.json,
    # _quarantine/...), never real image uploads — without this check the dedup
    # manifest itself would be publicly downloadable through this route.
    if safe_name.startswith("_"):
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    filepath = _resolve_image_path(safe_name)
    if filepath is None:
        return JSONResponse(status_code=404, content={"error": "Image not found"})

    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    if isinstance(width, int):
        # A bounded pair of sizes avoids an unbounded cache. Keep originals intact.
        preview = await asyncio.to_thread(_image_preview, filepath, 640 if width <= 640 else 1280)
        if preview:
            filepath, media_type = preview, "image/webp"
    return FileResponse(
        filepath,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _image_preview(filepath: Path, width: int) -> Path | None:
    from PIL import Image, ImageOps
    import os

    temporary = None
    try:
        stamp = filepath.stat()
        key = hashlib.sha256(f"{filepath}:{stamp.st_mtime_ns}:{stamp.st_size}:{width}".encode()).hexdigest()
        directory = IMAGES_DIR / "_previews"
        destination = directory / (key + ".webp")
        if destination.is_file():
            return destination
        with Image.open(filepath) as source:
            if getattr(source, "is_animated", False):
                return None
            image = ImageOps.exif_transpose(source)
            image.thumbnail((width, width), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            directory.mkdir(exist_ok=True)
            temporary = directory / (uuid.uuid4().hex + ".tmp")
            image.save(temporary, format="WEBP", quality=82)
            os.replace(temporary, destination)
        return destination
    except Exception:
        log.warning("Image preview unavailable for %s; serving original", filepath.name)
        return None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@router.get("/all")
async def list_all_images(identity: str | None = None, limit: int = 200):
    """Return all images across all conversations, newest first."""
    db = await get_db()
    try:
        # Query messages that have image metadata
        query = (
            "SELECT m.id, m.role, m.identity, m.created_at, m.metadata, "
            "c.title AS conversation_title "
            "FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            "WHERE m.metadata IS NOT NULL "
            "AND (json_extract(m.metadata, '$.images') IS NOT NULL "
            "     AND json_extract(m.metadata, '$.images') != '[]') "
        )
        params: list = []

        if identity:
            query += (
                "AND EXISTS ("
                "SELECT 1 FROM conversation_participants cp "
                "WHERE cp.conversation_id = c.id AND cp.identity = ?"
                ") "
            )
            params.append(identity)

        query += "ORDER BY m.created_at_epoch DESC LIMIT ?"
        params.append(limit)

        rows = await db.execute_fetchall(query, tuple(params))
        images = []
        for row in rows:
            mid, role, ident, created, meta_str, conv_title = row
            meta = json.loads(meta_str) if meta_str else {}
            for img in meta.get("images", []):
                url = img.get("url", "")
                if not url:
                    continue
                images.append({
                    "url": url,
                    "identity": ident,
                    "role": role,
                    "created_at": created,
                    "conversation": conv_title or "",
                })
        return {"images": images}
    finally:
        await release_db(db)
