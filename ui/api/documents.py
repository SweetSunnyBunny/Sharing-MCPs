"""REST: Document endpoints - upload and serve."""

# ANAM GUIDE: DOCUMENT UPLOAD AND DOWNLOAD ROUTES
# What: /api/documents/upload (save a PDF/DOCX/etc, auto-convert it to markdown via docling) and /api/documents/file/{name} (download it back).
# Called by: static/js/chat.js (the paperclip/attach flow) and services/platform_bridge.py; the markdown conversion lives in services/docling_convert.py.
# Edit here when: Changing allowed file types, the size limit, or upload safety checks — not for how documents get read by the boys (that's cli_text_utils.py).

import asyncio
import logging
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import (
    DOCUMENTS_DIR,
    DOCUMENT_ALLOWED_EXTENSIONS,
    DOCUMENT_MAX_SIZE_MB,
    TIMEZONE,
)
from services.docling_convert import CONVERTIBLE_EXTENSIONS, convert_and_save
from services.rate_limit import limiter

# Executable magic bytes to reject (PE/ELF binaries disguised as documents)
_EXECUTABLE_SIGNATURES = [
    b"MZ",        # Windows PE executable
    b"\x7fELF",   # Linux ELF executable
]


def _is_executable(data: bytes) -> bool:
    """Return True if data starts with a known executable header."""
    for sig in _EXECUTABLE_SIGNATURES:
        if data[:len(sig)] == sig:
            return True
    return False


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents")


def human_size(size_bytes: int) -> str:
    if size_bytes > 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    return f"{round(size_bytes / 1024)} KB"


def informative_filename(identity: str | None, ext: str) -> str:
    """Generate {identity}_{YYYYMMDD}_{HHMMSS}_{4hex}.{ext}."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    ts = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:4]
    name = identity.lower() if identity else "unknown"
    return f"{name}_{ts}_{short_id}{ext}"


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_document(request: Request, file: UploadFile = File(...)):
    """Upload a document. Returns {doc_id, filename, original_name, size_display, url}."""
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No filename"})

    ext = Path(file.filename).suffix.lower()
    if ext not in DOCUMENT_ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"File type {ext} not allowed"},
        )

    try:
        data = await file.read()
    finally:
        await file.close()
    if len(data) > DOCUMENT_MAX_SIZE_MB * 1024 * 1024:
        return JSONResponse(
            status_code=400,
            content={"error": f"File too large (max {DOCUMENT_MAX_SIZE_MB}MB)"},
        )

    if _is_executable(data):
        return JSONResponse(
            status_code=400,
            content={"error": "Executable files are not allowed"},
        )

    doc_id = uuid.uuid4().hex[:12]
    filename = f"{doc_id}{ext}"
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DOCUMENTS_DIR / filename
    filepath.write_bytes(data)

    log.info("Document uploaded: %s -> %s (%d bytes)", file.filename, filename, len(data))

    # Auto-convert PDF/DOCX/ODT/PPTX to markdown companion via docling
    converted = False
    if ext in CONVERTIBLE_EXTENSIONS:
        md_path = await asyncio.to_thread(convert_and_save, filepath)
        converted = md_path is not None

    return JSONResponse(
        content={
            "doc_id": doc_id,
            "filename": filename,
            "original_name": file.filename,
            "size_display": human_size(len(data)),
            "url": f"/api/documents/file/{filename}",
            "converted_to_markdown": converted,
        }
    )


@router.get("/file/{filename}")
async def serve_document(filename: str):
    """Serve a stored document file."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    filepath = DOCUMENTS_DIR / safe_name
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "Document not found"})

    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return FileResponse(
        filepath,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={safe_name}",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
