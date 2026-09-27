"""Convert documents (PDF, DOCX, ODT, PPTX) to Markdown via docling."""

# ANAM GUIDE: DOCUMENT TO MARKDOWN CONVERTER
# What: Small helper that turns an uploaded document (PDF, Word, PowerPoint, etc.) into a plain-text Markdown file saved next to the original, so the boys can actually read it.
# Called by: api/documents.py and api/chat.py when a document is uploaded; services/attachment_context.py when building what a boy sees.
# Edit here when: You want to support a new file type for upload-and-read, or change where/how the converted Markdown is saved.

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Extensions docling can convert to markdown
CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".doc", ".odt", ".pptx", ".xlsx"}


def convert_to_markdown(source_path: Path) -> str | None:
    """Convert a document to markdown text. Returns None on failure."""
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(source_path))
        md = result.document.export_to_markdown()
        if md and md.strip():
            return md.strip()
        log.warning("Docling returned empty markdown for %s", source_path.name)
        return None
    except Exception:
        log.exception("Docling conversion failed for %s", source_path.name)
        return None


def convert_and_save(source_path: Path) -> Path | None:
    """Convert a document and save the markdown alongside the original."""
    md = convert_to_markdown(source_path)
    if md is None:
        return None

    md_path = source_path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")
    log.info("Docling: %s -> %s (%d chars)", source_path.name, md_path.name, len(md))
    return md_path


def get_companion_markdown(doc_path: Path) -> Path | None:
    """Return the companion .md file if it exists."""
    md_path = doc_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_size > 0:
        return md_path
    return None
