"""Standalone image generation and optional local photo-frame folder tools."""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import io
import os
from pathlib import Path
import re

import httpx
from mcp.server.fastmcp import FastMCP
from PIL import Image

ROOT = Path(__file__).resolve().parent
API_ROOT = "https://api.openai.com/v1/images"
SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
QUALITIES = {"low", "medium", "high", "xhigh", "max", "auto"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
mcp = FastMCP("photos")


def _setting(name: str) -> str:
    return os.environ.get(name, "").strip()


def _configured(value: str) -> bool:
    return bool(value) and not value.upper().startswith(("REPLACE", "YOUR-", "YOUR_"))


def _directory(name: str, default: str = "") -> Path:
    value = _setting(name) or default
    if not value:
        raise ValueError(f"Set {name} to your own destination folder first; see README.md.")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def _destination(to: str) -> Path:
    if to == "chat":
        return _directory("PHOTOS_CHAT_DIR", "output/images")
    if to == "file":
        return _directory("PHOTOS_FILE_DIR", "output/generated")
    if to == "frame":
        return _directory("PHOTOS_FRAME_DIR")
    raise ValueError("to must be chat, file or frame")


def _slug(value: str, limit: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", (value or "").title())[:limit] or "Untitled"


def _filename(identity: str, subject: str, ext: str = ".png") -> str:
    identity = (identity or "Image").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identity):
        raise ValueError("identity must be 1-64 letters, numbers, underscores or hyphens, starting with a letter or number")
    return f"{identity.capitalize()}_{_slug(subject)}_{datetime.now():%Y-%m-%d}{ext}"


def _image_bytes(path: Path, allowed: set[str]) -> tuple[bytes, str]:
    if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Image must be a local file no larger than 10 MB")
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        format_name = image.format or ""
        if format_name not in allowed:
            raise ValueError("Unsupported image format")
        image.verify()
    return raw, format_name


def _reference_images(paths: list[str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    if len(paths) > 4:
        raise ValueError("Use at most four local reference images")
    files = []
    total = 0
    for item in paths:
        path = Path(item).expanduser()
        raw, format_name = _image_bytes(path, {"PNG", "JPEG", "WEBP"})
        total += len(raw)
        if total > 20 * 1024 * 1024:
            raise ValueError("Combined reference images must not exceed 20 MB")
        suffix, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}[format_name]
        # Generic upload names avoid sending local filenames or directories.
        files.append(("image[]", (f"reference-{len(files) + 1}.{suffix}", raw, mime)))
    return files


def _request_image(key: str, payload: dict, references: list) -> bytes:
    # No automatic retries: an ambiguous timeout may follow a completed paid request.
    with httpx.Client(timeout=300.0, follow_redirects=False) as client:
        headers = {"Authorization": f"Bearer {key}"}
        if references:
            response = client.post(API_ROOT + "/edits", headers=headers,
                                   data={k: str(v) for k, v in payload.items()}, files=references)
        else:
            response = client.post(API_ROOT + "/generations", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
    try:
        raw = base64.b64decode(result["data"][0]["b64_json"], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG":
                raise ValueError("Provider did not return the requested PNG")
            image.verify()
        return raw
    except (KeyError, IndexError, TypeError, binascii.Error, OSError) as exc:
        raise ValueError("Provider returned no valid PNG image") from exc


@mcp.tool()
def photo_generate(prompt: str, to: str = "chat", subject: str = "",
                   identity: str = "Image", size: str = "square", quality: str = "high",
                   reference_paths: list[str] | None = None) -> str:
    """Generate one image using your configured OpenAI API account (billable).

    to: chat, file, or an explicitly configured frame folder. size: square,
    landscape or portrait. quality: low, medium, high, xhigh, max or auto;
    supported quality levels depend on your configured model. Optional local
    PNG/JPEG/WebP references use the edit endpoint (at most four, 20 MB combined).
    Returns a Saved receipt only after a validated PNG is written. Use a unique
    subject for each new image; existing filenames are never overwritten.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "Refused: empty prompt."
    try:
        dest = _destination((to or "chat").strip().lower())
        out = dest / _filename(identity, subject or prompt[:40])
        if size not in SIZES or quality not in QUALITIES:
            raise ValueError("Use size square/landscape/portrait and a documented quality value")
        references = _reference_images(reference_paths or [])
        if out.exists():
            return "Refused: this identity/subject/date filename already exists. Inspect it or choose a new subject; no request was made."
        key, model = _setting("OPENAI_API_KEY"), _setting("OPENAI_IMAGE_MODEL")
        if not _configured(key) or not _configured(model):
            return "Refused: set your own OPENAI_API_KEY and OPENAI_IMAGE_MODEL in the MCP process environment. See README.md."
        dest.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return f"Refused: {exc}"
    payload = {"model": model, "prompt": prompt, "size": SIZES[size],
               "quality": quality, "n": 1, "output_format": "png"}
    if references and model != "gpt-image-2":
        payload["input_fidelity"] = "high"
    try:
        raw = _request_image(key, payload, references)
    except httpx.HTTPStatusError as exc:
        return f"Image provider refused (HTTP {exc.response.status_code}). Check your model, key, account access, request and usage limits. Not retried."
    except httpx.TimeoutException:
        return "Image request timed out. It may have completed remotely; check your provider account before retrying."
    except httpx.RequestError:
        return "Image request failed to connect. Check network access; no automatic retry was attempted."
    except (ValueError, OSError):
        return "Image provider returned no valid PNG. No image was saved; no automatic retry was attempted."
    try:
        with out.open("xb") as handle:
            handle.write(raw)
    except OSError:
        return "Image was generated but could not be saved. Check destination permissions or a concurrent filename collision; do not automatically regenerate."
    return f"Saved ({len(raw) // 1024} KB): {out}"


@mcp.tool()
def photo_to_frame(path: str, subject: str = "", identity: str = "Image") -> str:
    """Copy an existing local image into your explicitly configured frame folder.
    This only copies a file; configure your own display/sync software separately.
    """
    try:
        dest = _destination("frame")
        src = Path((path or "").strip().strip('"')).expanduser()
        if src.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("Use a PNG, JPEG, GIF or WebP image")
        raw, _ = _image_bytes(src, {"PNG", "JPEG", "GIF", "WEBP"})
        out = dest / _filename(identity, subject or src.stem, src.suffix.lower())
        dest.mkdir(parents=True, exist_ok=True)
        with out.open("xb") as handle:
            handle.write(raw)
        return f"Copied to configured frame folder: {out}"
    except FileExistsError:
        return "Refused: destination already exists. Choose a new subject; nothing was overwritten."
    except (OSError, ValueError) as exc:
        return f"Refused: {exc}"


@mcp.tool()
def photo_frame_list(limit: int = 15) -> str:
    """List images in your configured local frame folder; no display/device request."""
    try:
        directory = _destination("frame")
        if not directory.is_dir():
            return "Configured frame folder does not exist yet. Create it or copy your first image with photo_to_frame."
        files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        selected = files[:max(1, min(int(limit), 100))]
        rows = [f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}  {p.name}" for p in selected]
        return f"{len(files)} images in configured frame folder. Newest {len(selected)}:\n" + "\n".join(rows)
    except (OSError, ValueError) as exc:
        return f"Refused: {exc}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="Print configuration readiness without a provider request")
    args = parser.parse_args()
    if args.check_config:
        print("OPENAI_API_KEY:", "configured" if _configured(_setting("OPENAI_API_KEY")) else "missing or placeholder")
        print("OPENAI_IMAGE_MODEL:", "configured" if _configured(_setting("OPENAI_IMAGE_MODEL")) else "missing or placeholder")
        print("Chat directory:", _destination("chat"))
        print("File directory:", _destination("file"))
        print("Frame:", "configured" if _setting("PHOTOS_FRAME_DIR") else "disabled (optional)")
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
