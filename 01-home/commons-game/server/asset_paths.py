
"""The CDN checkout owns Commons images, including same-origin image URLs."""
from functools import lru_cache
import hashlib
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

COMMONS_ROOT = Path(__file__).resolve().parents[1]
from settings import ASSET_ROOT
CDN_ROOT = ASSET_ROOT
IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"))

def is_cdn_asset(relative):
    path = Path(relative)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        return False
    if path == Path("maps/README.md"):
        return True
    return (path.parts[0] in ("art", "assets", "maps", "uploads", "item-art")
            and (path.suffix.lower() in IMAGE_SUFFIXES
                 or (path.parts[0] in ("art", "maps") and path.suffix.lower() == ".json")))

@lru_cache(maxsize=128)
def _image_digest(filename, mtime_ns, ctime_ns, size, inode):
    """The stat signature invalidates the bounded cache after an art edit."""
    digest = hashlib.sha256()
    with open(filename, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]

def versioned_image_url(value, cdn_root=CDN_ROOT):
    """[commons:loading] Version known public images without fetching other hosts.

    This decorates a response only. Authored room files and editor revisions
    retain their original URLs; missing or unsafe references stay unchanged.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        url = urlsplit(value)
        image_path = url.path
        if url.scheme or url.netloc:
            if (url.scheme != "https" or url.netloc.lower() != "cdn.example.com"
                    or not image_path.startswith("/commons/")):
                return value
            image_path = image_path[len("/commons/"):]
        decoded = unquote(image_path).lstrip("/")
        parts = decoded.split("/")
        if ("\\" in decoded or any(not p or p.startswith(".") or ".bak" in p.lower()
                                   for p in parts)):
            return value
        relative = Path(decoded)
        if relative.suffix.lower() not in IMAGE_SUFFIXES or not is_cdn_asset(relative):
            return value
        root = Path(cdn_root).resolve()
        image = (root / relative).resolve()
        if not image.is_relative_to(root) or not image.is_file():
            return value
        stat = image.stat()
        version = _image_digest(str(image), stat.st_mtime_ns, stat.st_ctime_ns,
                                stat.st_size, stat.st_ino)
        query = [(key, item) for key, item in parse_qsl(url.query, keep_blank_values=True)
                 if key != "v"]
        query.append(("v", version))
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment))
    except (OSError, ValueError):
        return value
