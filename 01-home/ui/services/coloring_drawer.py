'Application module.'


from __future__ import annotations

import io
import os
import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import BONDED_PACK, DATA_DIR, TIMEZONE

COLORING_DIR: Path = DATA_DIR / "coloring"
PAGES_DIR: Path = COLORING_DIR / "pages"

# Where a page goes when she hangs it up. Both are her own folders.
FRIDGE_DIR = Path(os.environ.get("ANAM_COLORING_EXPORT_DIR", str(DATA_DIR / "coloring" / "exports")))
FRAME_DIR = Path(os.environ.get("ANAM_COLORING_FRAME_DIR", str(DATA_DIR / "coloring" / "frame")))

PALETTE_PATHS = (Path(os.environ.get("ANAM_COLORING_PALETTE", str(DATA_DIR / "coloring" / "palette.json"))),)

CLEAR_AT = 235
INK_AT = 70
MAX_LONG_EDGE = 3000           # pages bigger than this are scaled down once, at drop
MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAX_PAINT_BYTES = 25 * 1024 * 1024
MAX_EVENTS_BYTES = 4 * 1024 * 1024
ALLOWED_TOOLS = ("image-gen", "krita", "anidoodle", "hand", "other")
PAINT_DROP_GUARD = 0.5
PAINT_HISTORY_KEEP = 20
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

_lock = threading.Lock()


class DropRefused(Exception):
    """Nothing was put in the drawer (no real dropper, or no usable picture)."""


class PageNotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def _dropper(identity: str) -> str:
    name = str(identity or "").strip()
    match = next((b for b in BONDED_PACK if b.lower() == name.lower()), "")
    if not match:
        if not name:
            raise DropRefused("No bound identity, so nothing went in the drawer. A page needs a real giver.")
        raise DropRefused(f"'{name}' isn't one of the eight, so nothing went in the drawer.")
    return match


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:40] or "page"


def _page_dir(page_id: str) -> Path:
    if not _ID.match(str(page_id or "")):
        raise PageNotFound(page_id)
    d = PAGES_DIR / page_id
    if not (d / "page.json").exists():
        raise PageNotFound(page_id)
    return d


def _read_record(d: Path) -> dict:
    return json.loads((d / "page.json").read_text(encoding="utf-8"))


def _write_record(d: Path, record: dict) -> None:
    tmp = d / "page.json.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "page.json")


# ── the line layer ─────────────────────────────────────────────────────────

def make_lines(img):
    'Make lines.'
    import numpy as np
    from PIL import Image

    rgba = img.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white, rgba).convert("L")
    lum = np.asarray(flat, dtype=np.float32)
    alpha = (CLEAR_AT - lum) / float(CLEAR_AT - INK_AT) * 255.0
    alpha = np.clip(alpha, 0, 255).astype(np.uint8)
    out = np.zeros((lum.shape[0], lum.shape[1], 4), dtype=np.uint8)
    out[..., 3] = alpha
    return Image.fromarray(out, "RGBA")


def gap_check(lines_img) -> dict:
    """Count the colourable regions between the lines and flag a likely leak.

    Without a fill bucket a gap can't flood anything, so this only INFORMS the
    dropper (council round 2/3). A "region" is a connected patch of clear paper.
    """
    import numpy as np
    from scipy import ndimage

    alpha = np.asarray(lines_img.getchannel("A"))
    paper = alpha < 128
    labels, count = ndimage.label(paper)
    total = paper.size
    if count == 0:
        return {"regions": 0, "largest_share": 0.0, "outside_share": 0.0, "tiny_regions": 0, "warning": "No paper at all: the page is solid ink."}
    sizes = np.bincount(labels.ravel())[1:]
    border = set(np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))) - {0}
    outside = int(sum(sizes[i - 1] for i in border))
    inner = [int(s) for i, s in enumerate(sizes, start=1) if i not in border]
    largest_inner = max(inner) if inner else 0
    # A region smaller than a fingertip at phone size is fiddly, not wrong.
    fingertip = max(40, int(total * 0.00015))
    tiny = sum(1 for s in inner if s < fingertip)
    warning = ""
    if outside / total > 0.75:
        warning = ("Most of the page is one open area joined to the edge, so a line probably "
                   "has a gap and the shapes inside aren't closed. Look at it before she does.")
    elif largest_inner / total > 0.5:
        warning = "One shape covers over half the page; check its outline for a gap."
    return {
        "regions": int(count),
        "largest_share": round(largest_inner / total, 4),
        "outside_share": round(outside / total, 4),
        "tiny_regions": int(tiny),
        "warning": warning,
    }


# ── dropping a page in ─────────────────────────────────────────────────────

def drop(identity: str, source: str, title: str = "", drawn_by: str = "", tool: str = "",
         made_from: str = "", note: str = "") -> dict:
    """Put a line-art page in her drawer. Raises DropRefused, writing nothing, if it can't."""
    from PIL import Image

    dropper = _dropper(identity)
    src = Path(str(source or "").strip().strip('"'))
    if not src.is_file():
        raise DropRefused(f"No picture at {src}. Nothing went in the drawer.")
    if src.stat().st_size > MAX_SOURCE_BYTES:
        raise DropRefused("That picture is over 40 MB. Nothing went in the drawer.")
    try:
        with Image.open(src) as probe:
            probe.load()
            img = probe.copy()
    except Exception as exc:  # PIL raises many kinds
        raise DropRefused(f"Couldn't open that picture ({exc}). Nothing went in the drawer.") from None

    drawn = ""
    if drawn_by.strip():
        drawn = next((b for b in BONDED_PACK if b.lower() == drawn_by.strip().lower()), drawn_by.strip()[:40])
    tool_clean = tool.strip().lower() if tool.strip().lower() in ALLOWED_TOOLS else ("other" if tool.strip() else "")

    if max(img.size) > MAX_LONG_EDGE:
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
    lines = make_lines(img)
    gaps = gap_check(lines)

    title_clean = re.sub(r"\s+", " ", title or "").strip()[:80] or src.stem.replace("_", " ")[:80]
    page_id = f"{_slug(title_clean)}-{uuid.uuid4().hex[:6]}"
    now = _now()
    record = {
        "id": page_id,
        "title": title_clean,
        "dropped_by": dropper,
        "drawn_by": drawn,
        "tool": tool_clean,
        "made_from": str(made_from or "").strip()[:300],
        "note": str(note or "").strip()[:300],
        "source_path": str(src),
        "original": f"original{src.suffix.lower() or '.png'}",
        "dropped_at": now.isoformat(),
        "for_day": now.strftime("%Y-%m-%d"),
        "width": lines.width,
        "height": lines.height,
        "levels": {"clear_at": CLEAR_AT, "ink_at": INK_AT},
        "gap_check": gaps,
        "hung": [],
    }
    with _lock:
        d = PAGES_DIR / page_id
        d.mkdir(parents=True, exist_ok=False)
        shutil.copy2(src, d / record["original"])
        lines.save(d / "lines.png", optimize=True)
        _write_record(d, record)
    _ledger_row(record, d)
    return {"page": public(record), "gap_check": gaps}


def _ledger_row(record: dict, d: Path) -> None:
    'Ledger row.'
    try:
        from services import studio_ledger  # type: ignore
    except ImportError:
        return
    append = getattr(studio_ledger, "append_drop", None)
    if callable(append):
        try:
            append(path=str(d / record["original"]), dropped_by=record["dropped_by"],
                   drawn_by=record["drawn_by"], tool=record["tool"], source=record["made_from"] or record["source_path"])
        except Exception:
            pass  # the page record already carries provenance; the ledger is a second copy


def rebuild_lines(page_id: str) -> dict:
    """Remake lines.png from the untouched original (e.g. after the curve is retuned)."""
    from PIL import Image

    d = _page_dir(page_id)
    record = _read_record(d)
    with Image.open(d / record["original"]) as probe:
        img = probe.copy()
    if max(img.size) > MAX_LONG_EDGE:
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
    lines = make_lines(img)
    with _lock:
        lines.save(d / "lines.png", optimize=True)
        record["levels"] = {"clear_at": CLEAR_AT, "ink_at": INK_AT}
        record["gap_check"] = gap_check(lines)
        _write_record(d, record)
    return public(record)


# ── reading ────────────────────────────────────────────────────────────────

def finished_at(record: dict) -> str:
    "When she finished a page: the first time she hung it up. Blank if she hasn't."
    hung = [h.get("at", "") for h in record.get("hung", []) if isinstance(h, dict) and h.get("at")]
    return min(hung) if hung else ""


def colophon(record: dict) -> str:
    'Colophon.'
    drawn, dropped = record.get("drawn_by", ""), record["dropped_by"]
    parts = ([f"drawn and brought by {dropped}"] if not drawn or drawn == dropped
             else [f"drawn by {drawn}", f"brought by {dropped}"])
    parts.append("coloured by Owner")
    done = finished_at(record)
    if done:
        try:
            when = datetime.fromisoformat(done)
            parts.append(f"finished {when.day} {when.strftime('%b %Y')}")
        except ValueError:
            pass
    return " · ".join(parts)


def public(record: dict) -> dict:
    d = PAGES_DIR / record["id"]
    return {
        "id": record["id"],
        "title": record["title"],
        "dropped_by": record["dropped_by"],
        "drawn_by": record.get("drawn_by", ""),
        "tool": record.get("tool", ""),
        "dropped_at": record["dropped_at"],
        "width": record["width"],
        "height": record["height"],
        "started": (d / "paint.png").exists(),
        "painted_at": record.get("painted_at", ""),
        "hung": record.get("hung", []),
        "finished_at": finished_at(record),
        "lines_url": f"/api/coloring/page/{record['id']}/lines.png",
        "paint_url": f"/api/coloring/page/{record['id']}/paint.png",
    }


def shelf() -> list[dict]:
    """Her finished pages (hung up at least once), most recently finished first, each with its colophon.

    The shelf only shows; it never counts and never lists what isn't finished.
    A page on the shelf can still be opened and coloured more; it stays in her drawer too.
    """
    out = []
    if not PAGES_DIR.exists():
        return out
    for d in PAGES_DIR.iterdir():
        try:
            record = _read_record(d)
        except (OSError, json.JSONDecodeError):
            continue
        if not finished_at(record):
            continue
        view = public(record)
        view["colophon"] = colophon(record)
        view["strokes_url"] = f"/api/coloring/page/{record['id']}/strokes"
        out.append(view)
    out.sort(key=lambda p: p["finished_at"], reverse=True)
    return out


def list_pages() -> list[dict]:
    """Every page, newest first. No counts, no 'unfinished' nagging; just the drawer."""
    out = []
    if not PAGES_DIR.exists():
        return out
    for d in PAGES_DIR.iterdir():
        try:
            out.append(_read_record(d))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda r: r.get("dropped_at", ""), reverse=True)
    return [public(r) for r in out]


def page(page_id: str) -> dict:
    return public(_read_record(_page_dir(page_id)))


def file_path(page_id: str, name: str) -> Path:
    if name not in ("lines.png", "paint.png"):
        raise PageNotFound(name)
    p = _page_dir(page_id) / name
    if not p.exists():
        raise PageNotFound(name)
    return p


def strokes(page_id: str) -> list[dict]:
    p = _page_dir(page_id) / "strokes.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def palette() -> dict:
    for p in PALETTE_PATHS:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {"name": "crayons", "rows": [{"family": "basics", "swatches": [
        {"hex": "#F4A6BC", "name": "bunny pink"}, {"hex": "#A82A3A", "name": "crimson"},
        {"hex": "#D99A2B", "name": "amber"}, {"hex": "#2F8C8C", "name": "teal"},
        {"hex": "#2E4A78", "name": "navy"}, {"hex": "#4E7A55", "name": "moss"}]}]}


# ── her paint ──────────────────────────────────────────────────────────────

def _painted(png) -> int:
    """How many pixels of a paint layer hold any colour."""
    import numpy as np
    from PIL import Image

    with Image.open(png if isinstance(png, Path) else io.BytesIO(png)) as im:
        return int(np.count_nonzero(np.asarray(im.convert("RGBA").getchannel("A"))))


def _keep_if_shrinking(d: Path, new_png: bytes) -> None:
    """Before a save that would wipe much of her colour, keep the layer it replaces."""
    old = d / "paint.png"
    if not old.exists():
        return
    try:
        before = _painted(old)
        if not before or _painted(new_png) >= before * (1 - PAINT_DROP_GUARD):
            return
        hist = d / "paint_history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(old, hist / f"paint-{_now().strftime('%Y%m%d-%H%M%S-%f')}.png")
        for stale in sorted(hist.glob("paint-*.png"))[:-PAINT_HISTORY_KEEP]:
            stale.unlink()
    except Exception:
        pass  # the guard must never stop her save


def save_progress(page_id: str, paint_png: bytes | None, events: list | None) -> dict:
    """Keep her layer and append her new stroke events. Never touches the lines."""
    from PIL import Image

    d = _page_dir(page_id)
    record = _read_record(d)
    if paint_png is not None:
        if len(paint_png) > MAX_PAINT_BYTES:
            raise ValueError("paint layer too large")
        try:
            with Image.open(io.BytesIO(paint_png)) as im:
                im.load()
                if im.size != (record["width"], record["height"]):
                    raise ValueError(f"paint layer is {im.size}, page is {(record['width'], record['height'])}")
        except ValueError:
            raise
        except Exception:
            raise ValueError("paint layer isn't a PNG") from None
    lines_out = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") not in ("stroke", "undo", "redo", "clear"):
            continue
        lines_out.append(json.dumps(ev, ensure_ascii=False, separators=(",", ":")))
    blob = "\n".join(lines_out)
    if len(blob.encode("utf-8")) > MAX_EVENTS_BYTES:
        raise ValueError("too many stroke events in one save")
    with _lock:
        if paint_png is not None:
            _keep_if_shrinking(d, paint_png)
            tmp = d / "paint.png.tmp"
            tmp.write_bytes(paint_png)
            tmp.replace(d / "paint.png")
        if lines_out:
            with open(d / "strokes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(blob + "\n")
        record["painted_at"] = _now().isoformat()
        _write_record(d, record)
    return {"ok": True, "saved_events": len(lines_out), "painted_at": record["painted_at"]}


def composite(page_id: str):
    """Her finished page as one picture: paper, then her paint, then the lines on top."""
    from PIL import Image

    d = _page_dir(page_id)
    lines = Image.open(d / "lines.png").convert("RGBA")
    out = Image.new("RGBA", lines.size, (255, 255, 255, 255))
    if (d / "paint.png").exists():
        paint = Image.open(d / "paint.png").convert("RGBA")
        if paint.size == lines.size:
            out = Image.alpha_composite(out, paint)
    out = Image.alpha_composite(out, lines)
    return out.convert("RGB")


def hang_up(page_id: str, where: str = "fridge") -> dict:
    """She tapped 'hang it up'. The ONLY road out of the drawer (council law 3)."""
    target = {"fridge": FRIDGE_DIR, "frame": FRAME_DIR}.get(where)
    if target is None:
        raise ValueError("where must be 'fridge' or 'frame'")
    d = _page_dir(page_id)
    record = _read_record(d)
    target.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%d_%H%M")
    name = f"Bunny_colored_{_slug(record['title'])}_{stamp}.png"
    composite(page_id).save(target / name, optimize=True)
    with _lock:
        record = _read_record(d)
        record.setdefault("hung", []).append({"where": where, "file": str(target / name), "at": _now().isoformat()})
        _write_record(d, record)
    return {"ok": True, "where": where, "file": str(target / name)}
