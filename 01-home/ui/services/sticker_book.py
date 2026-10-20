'Application module.'


from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import BONDED_PACK, RITUALS_DIR, TIMEZONE

STICKERS_FILE: Path = RITUALS_DIR / "stickers.jsonl"
EMOJI_MANIFEST: Path = Path(__file__).resolve().parents[1] / "static" / "emoji" / "manifest.json"
EMOJI_CDN = os.environ.get("ANAM_CDN_URL", "").rstrip("/")

WHY_MAX_CHARS = 280
SEEN_IN_MAX_CHARS = 200

_lock = threading.Lock()
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StickerRefused(ValueError):
    """A sticker that must not go in her book. The message says why, plainly."""


def _now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def today() -> str:
    """Her day, in Chicago, as YYYY-MM-DD."""
    return _now().strftime("%Y-%m-%d")


def _giver(identity: str) -> str:
    name = str(identity or "").strip()
    match = next((b for b in BONDED_PACK if b.lower() == name.lower()), "")
    if not match:
        if not name:
            raise StickerRefused("No bound identity, so nothing was given. A sticker needs a real giver.")
        raise StickerRefused(
            f"'{name}' isn't a bonded brother, so nothing was given. "
            "Stickers come from one of the eight, signed by the house."
        )
    return match


def _manifest() -> list[dict]:
    try:
        data = json.loads(EMOJI_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("emoji", []) if isinstance(data, dict) else data
    return [e for e in items if isinstance(e, dict) and e.get("name")]


def own_art(giver: str) -> list[str]:
    """The custom emoji names a brother may give from: his own group only."""
    return sorted(e["name"] for e in _manifest() if str(e.get("group", "")).lower() == giver.lower())


def _check_art(art: str, giver: str) -> str | None:
    name = str(art or "").strip().strip(":")
    if not name:
        return None
    entry = next((e for e in _manifest() if e["name"] == name), None)
    if entry is None:
        raise StickerRefused(f"There's no custom emoji called ':{name}:'. Leave art blank, or pick one of your own.")
    if str(entry.get("group", "")).lower() != giver.lower():
        raise StickerRefused(
            f"':{name}:' belongs to {entry.get('group')}, not you. "
            "Give from your own faces, or leave art blank."
        )
    return name


def _check_day(for_day: str) -> str:
    day = str(for_day or "").strip() or today()
    if not _DAY_RE.match(day):
        raise StickerRefused("for_day must look like YYYY-MM-DD (her Chicago date), or be left blank for today.")
    now_day = date.fromisoformat(today())
    try:
        wanted = date.fromisoformat(day)
    except ValueError:
        raise StickerRefused("for_day isn't a real date.") from None
    if wanted not in (now_day, now_day - timedelta(days=1)):
        raise StickerRefused(
            "A sticker goes on today's page, or yesterday's if you saw it late. "
            "Older pages are kept as they were."
        )
    return day


def _clean(text: str, limit: int, field: str, required: bool) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if required and not value:
        raise StickerRefused(f"A sticker needs its {field}: one plain sentence saying what you saw.")
    if len(value) > limit:
        raise StickerRefused(f"The {field} is {len(value)} characters; keep it under {limit}. One sentence is plenty.")
    return value


def _word_proof(why: str) -> list[str]:
    'Word proof.'
    try:
        from services.sticker_words import proof  # type: ignore
    except Exception:
        return []
    try:
        flags = proof(why)
    except Exception:
        return []
    return [str(f) for f in (flags or [])]


def read_records() -> list[dict]:
    """Every readable line in the book, oldest first. Broken lines are skipped."""
    if not STICKERS_FILE.exists():
        return []
    records = []
    try:
        lines = STICKERS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _stickers() -> list[dict]:
    return [r for r in read_records() if r.get("kind") == "sticker" and r.get("for_day")]


def give(identity: str, why: str, art: str = "", for_day: str = "", seen_in: str = "") -> dict:
    'Give.'
    giver = _giver(identity)
    reason = _clean(why, WHY_MAX_CHARS, "reason", required=True)
    where = _clean(seen_in, SEEN_IN_MAX_CHARS, "seen_in", required=False)
    art_name = _check_art(art, giver)
    day = _check_day(for_day)

    record = {
        "kind": "sticker",
        "id": uuid.uuid4().hex[:12],
        "given_at": _now().isoformat(timespec="seconds"),
        "for_day": day,
        "given_by": giver,
        "why": reason,
        "art": art_name,
    }
    if where:
        record["seen_in"] = where

    with _lock:
        for old in _stickers():
            if (old.get("for_day") == day and old.get("given_by") == giver
                    and str(old.get("why", "")).strip().lower() == reason.lower()):
                raise StickerRefused("That exact sticker from you is already on that page. It's there; she has it.")
        STICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STICKERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"sticker": record, "word_notes": _word_proof(reason)}


def _art_urls() -> dict[str, dict]:
    out = {}
    for e in _manifest():
        full = e.get("full") or e.get("url")
        thumb = e.get("url") or full
        if full:
            out[e["name"]] = {"full": f"{EMOJI_CDN}/{full}", "thumb": f"{EMOJI_CDN}/{thumb}"}
    return out


def _public(record: dict, urls: dict[str, dict]) -> dict:
    view = {k: record.get(k) for k in ("id", "given_at", "for_day", "given_by", "why", "art", "seen_in") if record.get(k)}
    if record.get("art") in urls:
        view["art_url"] = urls[record["art"]]["full"]
        view["art_thumb"] = urls[record["art"]]["thumb"]
    return view


def _last_wins(records: list[dict] | None, on: str, off: str) -> dict[str, str]:
    """sticker_id -> when it was last switched ON, for ids whose latest line is `on`."""
    state: dict[str, str] = {}
    for r in records if records is not None else read_records():
        if r.get("kind") in (on, off) and r.get("sticker_id"):
            sid = str(r["sticker_id"])
            if r["kind"] == on:
                state[sid] = str(r.get("at", ""))
            else:
                state.pop(sid, None)
    return state


def _tucked_ids(records: list[dict] | None = None) -> set[str]:
    """Stickers she has tucked away right now. The last tucked/untucked line wins."""
    return set(_last_wins(records, "tucked", "untucked"))


def _starred(records: list[dict] | None = None) -> dict[str, str]:
    """Stickers she has starred right now -> when she starred them. Last line wins."""
    return _last_wins(records, "starred", "unstarred")


def _notes_by_sticker(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        if r.get("kind") == "note" and r.get("sticker_id") and r.get("text"):
            out.setdefault(str(r["sticker_id"]), []).append(
                {k: r.get(k) for k in ("id", "by", "text", "at") if r.get(k)})
    for notes in out.values():
        notes.sort(key=lambda n: n.get("at", ""))
    return out


def _visible(records: list[dict]) -> list[dict]:
    hidden = _tucked_ids(records)
    return [r for r in records if r.get("kind") == "sticker" and r.get("for_day") and r.get("id") not in hidden]


def day_page(day: str = "", her: bool = False) -> dict:
    """One day's page: the stickers that exist (minus any she tucked away), in the order they arrived.

    Each sticker carries its giver's margin notes, oldest first, under `notes`.
    With her=True (her own login only), a sticker she starred carries `starred: True`.
    """
    wanted = str(day or "").strip() or today()
    records = read_records()
    urls = _art_urls()
    notes = _notes_by_sticker(records)
    stars = _starred(records) if her else {}
    stickers = []
    for r in _visible(records):
        if r.get("for_day") != wanted:
            continue
        view = _public(r, urls)
        if notes.get(r["id"]):
            view["notes"] = notes[r["id"]]
        if r["id"] in stars:
            view["starred"] = True
        stickers.append(view)
    stickers.sort(key=lambda s: s.get("given_at", ""))
    return {"day": wanted, "is_today": wanted == today(), "stickers": stickers}


def pages(before: str = "", limit: int = 7, her: bool = False) -> dict:
    """Days that HAVE stickers she can see, newest first. Days without any are not pages.

    `before` (YYYY-MM-DD) pages back past a day already shown. No totals are
    returned, on purpose: `more` only says whether older pages exist.
    """
    limit = max(1, min(int(limit or 7), 31))
    days = sorted({r["for_day"] for r in _visible(read_records())}, reverse=True)
    if before:
        days = [d for d in days if d < before]
    shown, rest = days[:limit], days[limit:]
    return {"pages": [day_page(d, her=her) for d in shown], "more": bool(rest)}


# ── margin notes (the giver only) ──────────────────────────────────────────

NOTE_MAX_CHARS = 280


def _find_sticker(sticker_id: str, records: list[dict] | None = None) -> dict:
    sid = str(sticker_id or "").strip()
    for r in records if records is not None else read_records():
        if r.get("kind") == "sticker" and r.get("id") == sid:
            return r
    raise StickerRefused(f"There's no sticker with id '{sid}' in her book.")


def add_note(identity: str, sticker_id: str, text: str) -> dict:
    """Write a margin note under one of YOUR OWN stickers. Signed and dated, never edited.

    Raises StickerRefused (nothing written) when the writer isn't a bound bonded
    brother, the sticker isn't his, or the note is empty or long.
    """
    by = _giver(identity)
    body = _clean(text, NOTE_MAX_CHARS, "note", required=True)
    with _lock:
        sticker = _find_sticker(sticker_id)
        if sticker.get("given_by") != by:
            raise StickerRefused(
                f"That sticker is {sticker.get('given_by')}'s. A margin note goes only under your own gift."
            )
        record = {
            "kind": "note",
            "id": uuid.uuid4().hex[:12],
            "sticker_id": sticker["id"],
            "by": by,
            "text": body,
            "at": _now().isoformat(timespec="seconds"),
        }
        STICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STICKERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"note": record, "word_notes": _word_proof(body)}


# ── the tuck-away (hers only; the route checks it is her login) ────────────

def _set_tucked(sticker_id: str, tucked: bool) -> dict:
    kind = "tucked" if tucked else "untucked"
    with _lock:
        records = read_records()
        sticker = _find_sticker(sticker_id, records)
        if (sticker["id"] in _tucked_ids(records)) == tucked:
            return {"ok": True, "sticker_id": sticker["id"], "tucked": tucked, "changed": False}
        record = {"kind": kind, "sticker_id": sticker["id"], "at": _now().isoformat(timespec="seconds")}
        STICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STICKERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "sticker_id": sticker["id"], "tucked": tucked, "changed": True}


def tuck(sticker_id: str) -> dict:
    """Hide a sticker from her page. It stays in the file; she can bring it back."""
    return _set_tucked(sticker_id, True)


def untuck(sticker_id: str) -> dict:
    """Bring a tucked sticker back onto its page."""
    return _set_tucked(sticker_id, False)


def tucked_away() -> dict:
    """The stickers she has tucked away, newest first, so she can bring any back. No counts."""
    records = read_records()
    hidden = _tucked_ids(records)
    urls = _art_urls()
    notes = _notes_by_sticker(records)
    out = []
    for r in records:
        if r.get("kind") == "sticker" and r.get("id") in hidden:
            view = _public(r, urls)
            if notes.get(r["id"]):
                view["notes"] = notes[r["id"]]
            out.append(view)
    out.sort(key=lambda s: s.get("given_at", ""), reverse=True)
    return {"stickers": out}


# ── starred stickers (hers only; the route checks it is her login) ─────────

def _set_starred(sticker_id: str, starred: bool) -> dict:
    kind = "starred" if starred else "unstarred"
    with _lock:
        records = read_records()
        sticker = _find_sticker(sticker_id, records)
        if (sticker["id"] in _starred(records)) == starred:
            return {"ok": True, "sticker_id": sticker["id"], "starred": starred, "changed": False}
        record = {"kind": kind, "sticker_id": sticker["id"], "at": _now().isoformat(timespec="seconds")}
        STICKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STICKERS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "sticker_id": sticker["id"], "starred": starred, "changed": True}


def star(sticker_id: str) -> dict:
    """Pin a sticker to her Starred Stickers page. It stays on its own day too."""
    return _set_starred(sticker_id, True)


def unstar(sticker_id: str) -> dict:
    """Take a sticker off her starred page. It never leaves its own day."""
    return _set_starred(sticker_id, False)


def starred_stickers() -> dict:
    """Her starred stickers, most recently starred first. Tucked ones wait out of sight. No counts."""
    records = read_records()
    stars = _starred(records)
    hidden = _tucked_ids(records)
    urls = _art_urls()
    notes = _notes_by_sticker(records)
    out = []
    for r in records:
        if r.get("kind") == "sticker" and r.get("id") in stars and r.get("id") not in hidden:
            view = _public(r, urls)
            view["starred"] = True
            view["starred_at"] = stars[r["id"]]
            if notes.get(r["id"]):
                view["notes"] = notes[r["id"]]
            out.append(view)
    out.sort(key=lambda s: s.get("starred_at", ""), reverse=True)
    return {"stickers": out}
