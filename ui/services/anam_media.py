"""Bounded private image previews shared by native tools and bridge attachments."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import threading
import time
import uuid

from PIL import Image, ImageOps
from config import DATA_DIR

CACHE = Path(DATA_DIR) / "runtime" / "media"
MAX_SOURCE = 50 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_PREVIEW = 5 * 1024 * 1024
MAX_CACHE = 512 * 1024 * 1024
TTL = 24 * 3600
_lock = threading.RLock()


def _record(media_id):
    if not re.fullmatch(r"[a-f0-9]{64}", media_id or ""):
        raise ValueError("Invalid media ID")
    try:
        record = json.loads((CACHE / (media_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Preview expired; request the source image again") from None
    if time.time() - record["created"] > TTL:
        raise ValueError("Preview expired; request the source image again")
    return record


def attachment_path(media_id):
    record = _record(media_id)
    path = CACHE / (media_id + record["suffix"])
    if path.resolve().parent != CACHE.resolve() or not path.is_file():
        raise ValueError("Preview is unavailable; request the source image again")
    return path


def _atomic(path, data):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _prune():
    records = []
    for path in CACHE.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            image = CACHE / (path.stem + record["suffix"])
            if path.resolve().parent != CACHE.resolve() or image.resolve().parent != CACHE.resolve():
                continue
            records.append((record["created"], path, image, image.stat().st_size))
        except (OSError, ValueError, KeyError):
            continue
    total = sum(r[3] for r in records)
    for created, record, image, size in sorted(records):
        if time.time() - created > TTL or total > MAX_CACHE - MAX_PREVIEW:
            image.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            total -= size


def preview(path="", media_id="", crop=None, max_dimension=1536):
    if bool(path) == bool(media_id):
        raise ValueError("Provide exactly one source path or media_id")
    if type(max_dimension) is not int or not 128 <= max_dimension <= 2048:
        raise ValueError("max_dimension must be between 128 and 2048")
    old = _record(media_id) if media_id else None
    source = Path(old["source"]) if old and old.get("source") else attachment_path(media_id) if old else Path(path)
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_SOURCE:
        raise ValueError("Image source must be a file no larger than 50 MiB")
    with source.open("rb") as handle:
        data = handle.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise ValueError("Image source exceeds 50 MiB")
    digest = hashlib.sha256(data).hexdigest()
    if old and old.get("source") and digest != old["source_hash"]:
        raise ValueError("The source changed; request its path again before cropping")
    return _prepare(data, crop, max_dimension, str(source), digest)


def _prepare(data, crop=None, max_dimension=1536, source=None, source_hash=None):
    with Image.open(BytesIO(data)) as opened:
        if opened.format not in {"PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"}:
            raise ValueError("Unsupported image format")
        if opened.width * opened.height > MAX_PIXELS:
            raise ValueError("Image exceeds 50 million pixels")
        image = ImageOps.exif_transpose(opened).copy()
    dimensions = list(image.size)
    if crop is not None:
        if (not isinstance(crop, list) or len(crop) != 4 or any(type(x) is not int for x in crop)
                or not (0 <= crop[0] < crop[2] <= image.width and 0 <= crop[1] < crop[3] <= image.height)):
            raise ValueError("crop must be [left, top, right, bottom] within the original image")
        image = image.crop(tuple(crop))
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
    image.info.clear()
    out = BytesIO()
    image.save(out, format="PNG")
    suffix, mime = ".png", "image/png"
    if out.tell() > MAX_PREVIEW:
        out = BytesIO()
        image.save(out, format="WEBP", quality=88)
        suffix, mime = ".webp", "image/webp"
    encoded = out.getvalue()
    if len(encoded) > MAX_PREVIEW:
        raise ValueError("Preview exceeds the attachment limit; use a smaller max_dimension")
    key = hashlib.sha256(encoded + json.dumps([source, source_hash, crop, max_dimension]).encode()).hexdigest()
    record = {"media_id": key, "source": source, "source_hash": source_hash,
              "dimensions": dimensions, "preview_dimensions": list(image.size), "crop": crop,
              "suffix": suffix, "mimeType": mime, "bytes": len(encoded), "created": time.time()}
    with _lock:
        CACHE.mkdir(parents=True, exist_ok=True)
        _prune()
        target = CACHE / (key + suffix)
        if not target.is_file():
            _atomic(target, encoded)
        _atomic(CACHE / (key + ".json"), json.dumps(record).encode())
    return {k: record[k] for k in ("media_id", "dimensions", "preview_dimensions", "crop", "mimeType", "bytes")}


def native_preview(path="", media_id="", crop=None, max_dimension=1536):
    from fastmcp.tools.tool import ToolResult
    from mcp.types import ImageContent, TextContent
    record = preview(path, media_id, crop, max_dimension)
    data = attachment_path(record["media_id"]).read_bytes()
    return ToolResult(content=[TextContent(type="text", text=json.dumps(record)),
        ImageContent(type="image", mimeType=record["mimeType"], data=base64.b64encode(data).decode())])


def cache_result_images(result):
    """Convert native image blocks into attachable private previews, never prose."""
    media = []
    for block in result.get("content", []):
        if block.get("type") != "image":
            continue
        if len(media) >= 4:
            raise ValueError("At most four images can be attached per action; request a smaller batch")
        encoded = block.get("data", "")
        if not isinstance(encoded, str) or len(encoded) > 8 * 1024 * 1024:
            raise ValueError("Native image exceeds preview transport limit")
        data = base64.b64decode(encoded, validate=True)
        media.append(_prepare(data))
    return media
