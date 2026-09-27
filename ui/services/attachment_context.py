"""Helpers for turning stored attachments into prompt context."""


from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from config import AUDIO_DIR, DOCUMENTS_DIR, SHARING_MCP_ROOT
from services.audio_transcription import get_cached_transcript
from services.docling_convert import CONVERTIBLE_EXTENSIONS, convert_and_save, get_companion_markdown

log = logging.getLogger(__name__)

INLINE_DOC_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".css",
    ".html",
    ".csv",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".sh",
    ".bat",
    ".sql",
    ".env",
    ".conf",
    ".rst",
    ".tex",
}
INLINE_DOC_MAX_SIZE = 50_000


def _try_inline_companion_markdown(doc_path: Path, original_name: str) -> str | None:
    """If a docling-converted .md companion exists, inline it."""
    md_path = get_companion_markdown(doc_path)
    if md_path is None:
        return None
    try:
        size = md_path.stat().st_size
        if size <= INLINE_DOC_MAX_SIZE:
            content = md_path.read_text(encoding="utf-8", errors="replace")
            return (
                f"[Owner shared a document: {original_name} "
                f"(converted to markdown by docling)]\n"
                f"```markdown\n{content}\n```"
            )
        # Companion exists but is too large — still better than binary
        return (
            f"[Owner shared a document: {original_name} — "
            f"a markdown conversion is available at {md_path}.\n"
            f"Use the Read tool to view the converted markdown.]"
        )
    except Exception:
        log.exception("Failed to read companion markdown %s", md_path)
        return None


# Where the audio MCP's video_watch caches finished watches. Mirrors
# OUTPUT_ROOT/_out_dir_for/_watch_cache_file in
# the sibling audio-visualizer/audio_mcp_server.py package.
_AUDIO_MCP_OUTPUT = Path(
    os.environ.get("AUDIO_MCP_OUTPUT_DIR", str(SHARING_MCP_ROOT / "audio-visualizer" / "output"))
)
_LARGE_VIDEO_BYTES = 150 * 1024 * 1024


def _load_finished_watch(doc_path: Path) -> dict | None:
    """Return the cached video_watch result for this exact file, if any."""
    cache = (
        _AUDIO_MCP_OUTPUT
        / f"{doc_path.stem}_analysis"
        / f"{doc_path.stem}_watch.json"
    )
    if not (cache.exists() and doc_path.exists()):
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        st = doc_path.stat()
        if (
            data.get("size") == st.st_size
            and abs(data.get("mtime", 0) - st.st_mtime) < 2
            and Path(data.get("sheet", "")).exists()
        ):
            return data
    except Exception:
        log.debug("Unreadable watch cache for %s", doc_path.name, exc_info=True)
    return None


def _build_video_note(original_name: str, doc_path: Path) -> str:
    """Video note: point at a finished watch if one exists, else guide."""
    watched = _load_finished_watch(doc_path)
    if watched:
        transcript = (watched.get("transcript") or "").strip()
        if len(transcript) > 4000:
            transcript = transcript[:4000] + " …(trimmed)"
        mins, secs = divmod(int(watched.get("duration", 0)), 60)
        return (
            f"[Owner shared a VIDEO: {original_name} ({doc_path})\n"
            f"It has ALREADY been watched ({mins}:{secs:02d}) — the results "
            f"are waiting for you, no processing needed:\n"
            f"CONTACT SHEET (Read this PNG to SEE the video): "
            f"{watched['sheet']}\n"
            f"TRANSCRIPT: {transcript}\n"
            f"Read the sheet, take in the transcript, and react to what you "
            f"actually saw and heard. For a closer look at more moments, "
            f"video_watch(path=\"{doc_path}\", frames=24) still works.]"
        )
    try:
        is_large = doc_path.exists() and doc_path.stat().st_size > _LARGE_VIDEO_BYTES
    except OSError:
        is_large = False
    if is_large:
        return (
            f"[Owner shared a VIDEO: {original_name} ({doc_path})\n"
            f"This is a BIG file. You can try "
            f"video_watch(path=\"{doc_path}\") — it is time-bounded and "
            f"safe — but if it returns partial results, tell Owner and she "
            f"can have terminal Claude pre-watch it; the finished sheet and "
            f"transcript will then be waiting here instantly.]"
        )
    return (
        f"[Owner shared a VIDEO: {original_name} ({doc_path})\n"
        f"WATCH it with your video_watch tool: "
        f"video_watch(path=\"{doc_path}\") — it gives you a contact "
        f"sheet of frames (Read that PNG to SEE it) plus the audio "
        f"transcript. Results are cached, so a video anyone already "
        f"watched comes back instantly. React to what you actually saw "
        f"and heard.]"
    )


def build_document_note(doc: dict) -> str:
    """Describe a shared document, inlining small text files when useful."""
    safe_doc_name = Path(doc.get("filename", "")).name
    doc_path = DOCUMENTS_DIR / safe_doc_name
    original_name = doc.get("original_name", safe_doc_name or "unknown")
    suffix = Path(original_name).suffix.lower()
    if suffix == ".zip":
        return (
            f"[Owner shared a ZIP archive: {original_name} ({doc_path})\n"
            f"Inspect and extract it with tools before reading files inside.]"
        )
    if suffix in (".mp4", ".mov", ".webm", ".mkv", ".avi"):
        return _build_video_note(original_name, doc_path)
    if suffix in INLINE_DOC_EXTENSIONS and doc_path.exists():
        try:
            size = doc_path.stat().st_size
            if size <= INLINE_DOC_MAX_SIZE:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                return (
                    f"[Owner shared a document: {original_name}]\n"
                    f"```\n{content}\n```"
                )
        except Exception:
            pass
    # Check for docling-converted markdown companion (PDF, DOCX, ODT, etc.)
    companion = _try_inline_companion_markdown(doc_path, original_name)
    if companion:
        return companion
    # On-demand conversion for convertible docs without a cached companion
    if suffix in CONVERTIBLE_EXTENSIONS and doc_path.exists():
        md_path = convert_and_save(doc_path)
        if md_path:
            log.info("On-demand docling conversion: %s", doc_path.name)
            companion = _try_inline_companion_markdown(doc_path, original_name)
            if companion:
                return companion
    return (
        f"[Owner shared a document: {original_name} ({doc_path})\n"
        f"Use the Read tool to view its contents.]"
    )


def summarize_document(doc: dict) -> str:
    """Compact summary used when a full inline replay would be too expensive."""
    original_name = doc.get("original_name") or doc.get("filename") or "document"
    return f"[Owner shared a document earlier in this conversation: {original_name}]"


def summarize_image_count(images: list[dict]) -> str:
    """Compact summary for older image-only turns."""
    count = len(images)
    noun = "image" if count == 1 else "images"
    return f"[Owner shared {count} {noun} earlier in this conversation]"


_VISUALIZER_HINT = (
    "If this is music — or you want to engage with the sound itself, not just "
    "the words — you can generate a spectrogram + structured analysis "
    "(waveform, mel spectrogram, chromagram, detected tempo, key, and notes) by "
    "running:\n"
    f'    python "{SHARING_MCP_ROOT / "audio-visualizer" / "sound_to_image.py"}" analyze '
    "\"{audio_path}\" \"{analysis_dir}\"\n"
    "Then Read \"{analysis_dir}/{stem}_spectrogram.png\" to literally see what "
    "the audio looks like, and Read \"{analysis_dir}/{stem}_analysis.json\" "
    "for tempo, key, and note data. The PNG is a real image — your Read tool "
    "handles it natively. Use this when it would add something to your reply; "
    "skip it for short voice memos where the transcript is enough."
)


def build_audio_note(audio: dict) -> str:
    """Describe a shared audio file, inlining the cached transcript."""
    safe_name = Path(audio.get("filename", "")).name
    audio_path = AUDIO_DIR / safe_name
    original_name = audio.get("original_name", safe_name or "voice memo")
    transcript = audio.get("transcript")
    if not transcript:
        transcript = get_cached_transcript(audio_path)

    stem = audio_path.stem
    analysis_dir = audio_path.parent / f"{stem}_analysis"
    visualizer_hint = _VISUALIZER_HINT.format(
        audio_path=audio_path,
        analysis_dir=analysis_dir,
        stem=stem,
    )

    if transcript:
        return (
            f"[Owner shared an audio file: {original_name} ({audio_path})\n"
            f"Transcription (auto-generated, treat as her words):\n"
            f"\"{transcript.strip()}\"\n"
            f"You cannot natively hear the audio, but the transcription above "
            f"is what she said. Respond to her words. Reference the audio file "
            f"by name if you want to bring it up later.\n\n"
            f"{visualizer_hint}]"
        )
    return (
        f"[Owner shared an audio file: {original_name} ({audio_path})\n"
        f"No transcript is available (transcription service may be down or the "
        f"file may be silent). Ask her what's in it or what she wants you to "
        f"know about it.\n\n"
        f"{visualizer_hint}]"
    )
