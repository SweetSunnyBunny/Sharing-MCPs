"""Reusable gifs support."""

# ANAM GUIDE: GIF SEARCH AND PICKER ROUTES
# What: /api/gifs — search GIPHY (Tenor is dead), list the GIFs already saved in her image folder, and download a picked GIF so chat can attach it like a normal image.
# Called by: static/js/chat.js (the GIF picker button on the chat page).
# Edit here when: Swapping GIF providers, changing search behavior, or adjusting which hosts /pick will download from.

import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.images import _validate_image_magic, informative_filename
from config import IMAGE_MAX_SIZE_MB, IMAGES_DIR
from services.rate_limit import limiter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gifs")

TENOR_API_KEY = os.environ.get("TENOR_API_KEY", "").strip()
GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "").strip()

# Hosts we will download picked GIFs from. Keeps /pick from being an
# open server-side fetch proxy.
_ALLOWED_PICK_HOSTS = (
    "media.tenor.com",
    "c.tenor.com",
    "media.giphy.com",
    "i.giphy.com",
    "media0.giphy.com",
    "media1.giphy.com",
    "media2.giphy.com",
    "media3.giphy.com",
    "media4.giphy.com",
)


def _provider() -> str | None:

    if GIPHY_API_KEY:
        return "giphy"
    if TENOR_API_KEY:
        return "tenor"
    return None


async def _search_tenor(q: str, limit: int) -> list[dict]:
    params = {
        "q": q,
        "key": TENOR_API_KEY,
        "limit": limit,
        "media_filter": "gif,tinygif",
        "contentfilter": "medium",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://tenor.googleapis.com/v2/search", params=params)
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("results", []):
        media = item.get("media_formats", {})
        gif = media.get("gif", {}).get("url")
        preview = media.get("tinygif", {}).get("url") or gif
        if gif:
            results.append({
                "id": item.get("id", ""),
                "title": item.get("content_description", ""),
                "preview": preview,
                "url": gif,
            })
    return results


async def _search_giphy(q: str, limit: int) -> list[dict]:
    params = {"q": q, "api_key": GIPHY_API_KEY, "limit": limit, "rating": "pg-13"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://api.giphy.com/v1/gifs/search", params=params)
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("data", []):
        images = item.get("images", {})
        gif = images.get("original", {}).get("url")
        preview = images.get("fixed_width_small", {}).get("url") or gif
        if gif:
            results.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "preview": preview,
                "url": gif,
            })
    return results


@router.get("/search")
@limiter.limit("30/minute")
async def search_gifs(request: Request, q: str = "", limit: int = 24):
    """Search the configured GIF provider. Empty q returns trending (GIPHY) / featured (Tenor)."""
    provider = _provider()
    if not provider:
        return JSONResponse(status_code=503, content={"error": "no_key"})
    limit = max(1, min(limit, 50))
    try:
        if provider == "tenor":
            if not q:
                # Tenor featured endpoint for an empty query
                params = {"key": TENOR_API_KEY, "limit": limit, "media_filter": "gif,tinygif"}
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get("https://tenor.googleapis.com/v2/featured", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                results = []
                for item in data.get("results", []):
                    media = item.get("media_formats", {})
                    gif = media.get("gif", {}).get("url")
                    preview = media.get("tinygif", {}).get("url") or gif
                    if gif:
                        results.append({
                            "id": item.get("id", ""),
                            "title": item.get("content_description", ""),
                            "preview": preview,
                            "url": gif,
                        })
            else:
                results = await _search_tenor(q, limit)
        else:
            if not q:
                params = {"api_key": GIPHY_API_KEY, "limit": limit, "rating": "pg-13"}
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get("https://api.giphy.com/v1/gifs/trending", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                results = []
                for item in data.get("data", []):
                    images = item.get("images", {})
                    gif = images.get("original", {}).get("url")
                    preview = images.get("fixed_width_small", {}).get("url") or gif
                    if gif:
                        results.append({
                            "id": item.get("id", ""),
                            "title": item.get("title", ""),
                            "preview": preview,
                            "url": gif,
                        })
            else:
                results = await _search_giphy(q, limit)
    except httpx.HTTPError as exc:
        log.warning("GIF search failed (%s): %s", provider, exc)
        return JSONResponse(status_code=502, content={"error": "provider_error"})
    return {"provider": provider, "results": results}


@router.get("/mine")
async def my_gifs(limit: int = 100):
    """List .gif files already in the image library, newest first — her own gifs, no key needed."""
    limit = max(1, min(limit, 300))
    gifs: list[tuple[float, str]] = []
    if IMAGES_DIR.exists():
        for p in IMAGES_DIR.glob("*.gif"):
            if p.name.startswith("_"):
                continue
            try:
                gifs.append((p.stat().st_mtime, p.name))
            except OSError:
                continue
    gifs.sort(reverse=True)
    return {
        "results": [
            {"filename": name, "url": f"/api/images/file/{name}"}
            for _, name in gifs[:limit]
        ]
    }


@router.post("/pick")
@limiter.limit("20/minute")
async def pick_gif(request: Request, data: dict):
    """Download a chosen remote GIF into the image library. Returns the upload-shaped payload."""
    url = (data or {}).get("url", "")
    identity = (data or {}).get("identity")
    if not url or not url.startswith("https://"):
        return JSONResponse(status_code=400, content={"error": "Invalid url"})
    host = httpx.URL(url).host or ""
    if host not in _ALLOWED_PICK_HOSTS:
        return JSONResponse(status_code=400, content={"error": "Host not allowed"})

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.content
    except httpx.HTTPError as exc:
        log.warning("GIF pick download failed: %s", exc)
        return JSONResponse(status_code=502, content={"error": "download_failed"})

    if len(payload) > IMAGE_MAX_SIZE_MB * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": f"GIF too large (max {IMAGE_MAX_SIZE_MB}MB)"})
    if not _validate_image_magic(payload, ".gif"):
        return JSONResponse(status_code=400, content={"error": "Not a GIF"})

    filename = informative_filename(identity, ".gif")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filepath = Path(IMAGES_DIR) / filename
    filepath.write_bytes(payload)
    log.info("GIF picked: %s (%d bytes) from %s", filename, len(payload), host)
    return {
        "image_id": filename.rsplit(".", 1)[0],
        "filename": filename,
        "url": f"/api/images/file/{filename}",
    }
