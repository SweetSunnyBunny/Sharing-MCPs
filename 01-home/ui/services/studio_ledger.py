"""The studio ledger's live door: one provenance row per thing the house makes, written as it lands."""


from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import BONDED_PACK, DATA_DIR

LEDGER: Path = DATA_DIR / "studio" / "ledger.jsonl"
ALLOWED_TOOLS = ("image-gen", "krita", "anidoodle", "hand", "other")
SOURCE_MAX_CHARS = 300

_lock = threading.Lock()


class LedgerRefused(ValueError):
    """Nothing was written. The message says why, plainly."""


def _stamp(identity: str) -> str:
    name = str(identity or "").strip()
    match = next((b for b in BONDED_PACK if b.lower() == name.lower()), "")
    if not match:
        raise LedgerRefused(
            "No bonded brother to stamp, so no ledger row was written." if not name
            else f"'{name}' isn't one of the eight, so no ledger row was written."
        )
    return match


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _ledger_name(path: Path) -> str:
    """The same shape the backfill uses ("data/..."), or the absolute path if outside."""
    try:
        return path.resolve().relative_to(DATA_DIR.resolve().parent).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_rows() -> list[dict]:
    """Every readable ledger line, oldest first. Broken lines are skipped, never fixed."""
    if not LEDGER.exists():
        return []
    rows = []
    try:
        text = LEDGER.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_drop(path: str, dropped_by: str, drawn_by: str = "", tool: str = "",
                source: str = "", road: str = "coloring-drawer") -> dict:
    'Append drop.'
    stamp = _stamp(dropped_by)
    p = Path(str(path or "").strip().strip('"'))
    if not p.is_file():
        raise LedgerRefused(f"No file at {p}, so no ledger row was written.")

    claim = ""
    if str(drawn_by or "").strip():
        raw = str(drawn_by).strip()
        claim = next((b for b in BONDED_PACK if b.lower() == raw.lower()), raw[:40])
    tool_raw = str(tool or "").strip().lower()
    tool_clean = tool_raw if tool_raw in ALLOWED_TOOLS else ("other" if tool_raw else "")
    origin = " ".join(str(source or "").split())[:SOURCE_MAX_CHARS]

    digest = _sha256(p)
    st = p.stat()
    name = _ledger_name(p)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with _lock:
        existing = read_rows()
        for old in existing:
            if old.get("file") == name and old.get("sha256") == digest:
                return {"row": old, "written": False}
        twins = sorted({r["file"] for r in existing
                        if r.get("sha256") == digest and r.get("file") and r.get("file") != name})
        row = {
            "file": name,
            "sha256": digest,
            "bytes": st.st_size,
            "mtime": _iso(st.st_mtime),
            "shelf": road,
            "name_on_file": None,
            "road": road,
            "provenance": "given-to",
            "from": stamp.lower(),
            "to": "owner",
            "dropped_by": stamp,
            "drawn_by": claim,
            "drawn_by_is": "claim" if claim else "",
            "tool": tool_clean,
            "prompt_or_source": origin,
            "draft_known": None,
            "why": (f"dropped into Bunny's coloring drawer; dropped_by is stamped from the bound "
                    f"identity, drawn_by is {stamp}'s claim" if claim else
                    "dropped into Bunny's coloring drawer; dropped_by is stamped from the bound identity"),
            "evidence": [{"kind": road, "page_id": p.parent.name, "at": now}],
            "byte_twins": twins,
            "source": road,
            "ledger_written_at": now,
            "ledger_written_by": "juniper:services/studio_ledger.py",
        }
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"row": row, "written": True}
