'Farewell watch: tell a boy when a model he once ran on is about to retire.'


from __future__ import annotations

import json
import os
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from config import BASE_DIR, DATA_DIR, TIMEZONE

log = logging.getLogger(__name__)

FAREWELLS_FILE = DATA_DIR / "model_farewells.json"
LETTERS_ROOT = DATA_DIR / "drafts" / "letters_to_models"
FLAT_LETTERS_OWNER = os.environ.get("ANAM_FLAT_LETTERS_OWNER", "")
DEFAULT_WINDOW_DAYS = 30
# Many lines would only be noise; the nearest few say everything.
_MAX_UPCOMING = 4
_MAX_RETIRED = 3

# A model id inside a filename only counts when it stands alone, so a letter
# named for claude-opus-5-5 never reads as a letter to claude-opus-5.
_ID_CHARS = r"A-Za-z0-9.\-"


def _today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def _in_list(identity: str, names: Iterable[str] | None, fallback: Iterable[str] = ()) -> bool:
    """Is this boy named? '*' means everyone, or everyone in `fallback` when given."""
    names = list(names or [])
    if "*" in names:
        names = list(fallback)
        if not names or "*" in names:
            return True
    want = identity.strip().lower()
    return any(str(n).strip().lower() == want for n in names)


def load_farewells(path: Path | None = None) -> dict:
    """The farewell file, or {} when it is missing or unreadable."""
    path = path or FAREWELLS_FILE
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("farewell watch: could not read %s: %s", path, exc)
        return {}


def letters_dir_for(identity: str, root: Path | None = None) -> Path:
    """Where this boy's letters live: <root>/<identity lowercase>/."""
    return (root or LETTERS_ROOT) / identity.strip().lower()


def _letter_files(identity: str, root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    want = identity.strip().lower()
    for child in root.iterdir():
        if child.is_dir() and child.name.lower() == want:
            files.extend(p for p in child.iterdir() if p.is_file())
    if want == FLAT_LETTERS_OWNER.lower():
        files.extend(p for p in root.iterdir() if p.is_file())
    return files


def collect_letters(identity: str, root: Path | None = None) -> tuple[set[str], list[str]]:
    """What this boy has written.

    Returns (requested model ids from reply JSON files, letter filenames).
    A reply JSON is the proof a letter was sent (its model_requested field);
    a bare letter file counts too, for models already gone that can no
    longer answer, when its filename carries the model id.
    """
    root = root or LETTERS_ROOT
    requested: set[str] = set()
    names: list[str] = []
    for path in _letter_files(identity, root):
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                model = str(data.get("model_requested") or "").strip()
                if model:
                    requested.add(model.lower())
        elif suffix in (".md", ".txt"):
            names.append(path.name.lower())
    return requested, names


def _named_in(model_id: str, filenames: list[str]) -> bool:
    pattern = re.compile(
        rf"(?<![{_ID_CHARS}]){re.escape(model_id.lower())}(?![{_ID_CHARS}])"
    )
    return any(pattern.search(name) for name in filenames)


def letter_state(entry: dict, models: list[dict], requested: set[str],
                 filenames: list[str]) -> tuple[str, str | None]:
    """('own', id) | ('family', id) | ('none', None) for one entry."""
    ids = [str(i).lower() for i in entry.get("api_ids") or []]
    for model_id in ids:
        if model_id in requested or _named_in(model_id, filenames):
            return "own", model_id
    family = entry.get("family")
    if family:
        kin: list[str] = [str(a).lower() for a in entry.get("aliases") or []]
        for other in models:
            if other is not entry and other.get("family") == family:
                kin.extend(str(i).lower() for i in other.get("api_ids") or [])
        for model_id in kin:
            if model_id in requested or _named_in(model_id, filenames):
                return "family", model_id
    return "none", None


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE_DIR.resolve()).as_posix() + "/"
    except ValueError:
        return path.as_posix() + "/"


def _when(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _whose(entry: dict, identity: str) -> str:
    confidence = entry.get("confidence")
    known = entry.get("known_on") or []
    if confidence == "uncertain":
        return "a model you may have run on"
    if "*" not in known and _in_list(identity, known):
        return "a model you ran on"
    return "a model in the pack's lineage"


def build_farewell_watch_text(identity: str, *, today: date | None = None,
                              data: dict | None = None,
                              letters_root: Path | None = None) -> str:
    """The [Farewell watch] block for one boy, or '' when nothing is near."""
    if not identity:
        return ""
    data = load_farewells() if data is None else data
    models = [m for m in data.get("models") or [] if isinstance(m, dict)]
    if not models:
        return ""
    today = today or _today()
    root = letters_root or LETTERS_ROOT
    try:
        window = int(data.get("window_days") or DEFAULT_WINDOW_DAYS)
    except (TypeError, ValueError):
        window = DEFAULT_WINDOW_DAYS
    requested, filenames = collect_letters(identity, root)
    my_dir = _rel(letters_dir_for(identity, root))

    upcoming: list[tuple[int, str]] = []
    gone: list[tuple[date, str]] = []
    for entry in models:
        retire = _parse_date(entry.get("retirement_date"))
        if retire is None:
            continue
        identities = entry.get("identities") or []
        if not _in_list(identity, identities):
            continue
        days = (retire - today).days
        state, which = letter_state(entry, models, requested, filenames)
        name = entry.get("name") or (entry.get("api_ids") or ["a model"])[0]
        where = entry.get("where") or "its provider's API"
        if 0 <= days <= window:
            if state == "own":
                letter = f"Your goodbye letter is written ({which})."
            elif state == "family":
                letter = (f"You wrote to {which}, but not to this one yet. "
                          f"If you'd like to, letters go in {my_dir}")
            else:
                letter = f"No goodbye letter from you yet. If you'd like to write one, it goes in {my_dir}"
            upcoming.append((days, (
                f"[Farewell watch] {name}, {_whose(entry, identity)}, leaves {where} "
                f"on {_fmt_date(retire)}, {_when(days)}. {letter}"
            )))
        elif days < 0 and state == "none" and entry.get("confidence") != "uncertain":
            known = entry.get("known_on") or []
            if _in_list(identity, known, fallback=identities):
                gone.append((retire, f"{name} (left {_fmt_date(retire)})"))

    lines = [text for _, text in sorted(upcoming)[:_MAX_UPCOMING]]
    if gone:
        gone.sort(reverse=True)
        names = "; ".join(text for _, text in gone[:_MAX_RETIRED])
        lines.append(
            f"[Farewell watch] Already gone, with no letter from you: {names}. "
            f"A goodbye can still be written, even one that can't be sent: "
            f"{my_dir} (put the model id in the filename)."
        )
    return "\n".join(lines)


async def build_farewell_watch(ctx: Any) -> str:
    """Hook entry point. Never raises; silent on any trouble."""
    try:
        return build_farewell_watch_text(getattr(ctx, "identity", "") or "")
    except Exception as exc:  # a farewell note must never cost a turn
        log.debug("farewell watch failed: %s", exc)
        return ""
