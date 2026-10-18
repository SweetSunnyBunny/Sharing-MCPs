"Keeps Anam's shared-media folders tidy and gives the pack's videos a home."


from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from config import (
    BONDED_PACK,
    DATA_DIR,
    DOCUMENTS_DIR,
    IMAGES_DIR,
    PACK_VIDEOS_DIR,
    VIDEOS_DIR,
)

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
FOLD_FOLDERS = (IMAGES_DIR, DOCUMENTS_DIR)
VIDEO_SOURCES = (VIDEOS_DIR, IMAGES_DIR, DOCUMENTS_DIR)
FILMS_DIR = DATA_DIR / "films"
STATE_PATH = DATA_DIR / "media_tidy_state.json"

# Files younger than this are left alone: they may still be mid-write, or
# about to be compressed in place right after upload (api/images.py), which
# would change every linked name at once.
FOLD_MIN_AGE_SECONDS = 3600
VIDEO_MIN_AGE_SECONDS = 120

# OneDrive "files on demand" placeholders: reading one downloads it. Such
# files are compared by name + size instead of being hashed.
_CLOUD_ONLY_ATTRIBUTES = 0x400000 | 0x40000 | 0x1000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE

_CHUNK = 1024 * 1024
_lock = threading.Lock()


# ─── Shared helpers ───────────────────────────────────────────────────────────

class HashCache:
    """SHA-256 per file, remembered across runs while size + mtime are unchanged."""

    def __init__(self, path: Path | None):
        self.path = path
        self.entries: dict[str, list] = {}
        if path and path.is_file():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8")).get("hashes", {})
            except (OSError, ValueError):
                log.warning("Media tidy hash cache unreadable; starting fresh")

    def digest(self, path: Path, info: os.stat_result) -> str:
        key = str(path)
        known = self.entries.get(key)
        if known and known[0] == info.st_size and known[1] == info.st_mtime_ns:
            return known[2]
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(_CHUNK), b""):
                h.update(block)
        value = h.hexdigest()
        self.entries[key] = [info.st_size, info.st_mtime_ns, value]
        return value

    def save(self) -> None:
        if not self.path:
            return
        # Forget files that no longer exist so the cache cannot grow forever.
        self.entries = {k: v for k, v in self.entries.items() if os.path.exists(k)}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"hashes": self.entries}), encoding="utf-8")
        os.replace(temporary, self.path)


def _plain_files(folder: Path):
    """Regular files directly inside `folder`, skipping _bookkeeping and .temp names."""
    if not folder.is_dir():
        return
    for entry in os.scandir(folder):
        if entry.name.startswith(("_", ".")):
            continue
        try:
            if entry.is_file(follow_symlinks=False):
                # os.lstat, not entry.stat(): on Windows DirEntry.stat() always
                # reports st_ino/st_dev/st_nlink as 0, which would make every
                # file look like a hard link of every other.
                yield Path(entry.path), os.lstat(entry.path)
        except OSError:
            continue


def _is_cloud_only(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _CLOUD_ONLY_ATTRIBUTES)


# ─── Job 1: fold duplicates into hard links ───────────────────────────────────

def fold_duplicates(folders, *, cache: HashCache, dry_run: bool = False,
                    min_age_seconds: float = FOLD_MIN_AGE_SECONDS) -> dict:
    """Replace byte-identical twins with hard links to one kept copy.

    The oldest copy in each group is kept. A twin is only replaced after its
    size and modified-time are re-checked, so a file that changed mid-run is
    skipped rather than overwritten.
    """
    report = {"groups": 0, "linked": 0, "bytes_freed": 0, "skipped": []}
    now = time.time()
    for folder in folders:
        by_size: dict[int, list] = defaultdict(list)
        for path, info in _plain_files(Path(folder)):
            if info.st_size and not (min_age_seconds and now - info.st_mtime < min_age_seconds):
                by_size[info.st_size].append((path, info))
        for members in by_size.values():
            if len(members) < 2:
                continue
            by_digest: dict[str, list] = defaultdict(list)
            for path, info in members:
                try:
                    by_digest[cache.digest(path, info)].append((path, info))
                except OSError as exc:
                    report["skipped"].append(f"{path.name}: unreadable ({exc})")
            for group in by_digest.values():
                if len(group) < 2:
                    continue
                group.sort(key=lambda m: (m[1].st_mtime_ns, m[0].name))
                keeper, keeper_info = group[0]
                twins = [m for m in group[1:]
                         if (m[1].st_dev, m[1].st_ino) != (keeper_info.st_dev, keeper_info.st_ino)]
                if not twins:
                    continue
                report["groups"] += 1
                for path, info in twins:
                    freed = info.st_size if info.st_nlink == 1 else 0
                    if dry_run:
                        report["linked"] += 1
                        report["bytes_freed"] += freed
                        continue
                    problem = _link_over(keeper, keeper_info, path, info)
                    if problem:
                        report["skipped"].append(f"{path.name}: {problem}")
                    else:
                        report["linked"] += 1
                        report["bytes_freed"] += freed
    return report


def _link_over(keeper: Path, keeper_info: os.stat_result, twin: Path, twin_info: os.stat_result) -> str | None:
    """Atomically swap `twin` for a hard link to `keeper`. Returns a reason on skip."""
    temporary = twin.with_name(f".{twin.name}.{uuid.uuid4().hex[:8]}.link")
    try:
        for path, before in ((keeper, keeper_info), (twin, twin_info)):
            now = path.stat()
            if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                return "changed during the run"
        os.link(keeper, temporary)
        os.replace(temporary, twin)
        return None
    except OSError as exc:
        return f"left as a separate copy ({exc.__class__.__name__})"
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("Could not remove temporary link %s", temporary)


# ─── Job 2: copy the pack's videos into OneDrive ──────────────────────────────

def _pack_prefixes() -> tuple[str, ...]:
    return tuple(name.lower() + "_" for name in BONDED_PACK)


def pack_video_candidates(sources=VIDEO_SOURCES, films_dir: Path = FILMS_DIR):
    """Every video Anam holds that a boy made or shared."""
    prefixes = _pack_prefixes()
    for folder in sources:
        for path, info in _plain_files(Path(folder)):
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.name.lower().startswith(prefixes):
                yield path, info
    films = Path(films_dir)
    if films.is_dir():
        for project in films.iterdir():
            if project.is_dir() and not project.name.startswith((".", "_")):
                for path, info in _plain_files(project / "out"):
                    if path.suffix.lower() in VIDEO_EXTENSIONS:
                        yield path, info


def sweep_pack_videos(destination: Path, *, cache: HashCache, dry_run: bool = False,
                      sources=VIDEO_SOURCES, films_dir: Path = FILMS_DIR,
                      min_age_seconds: float = VIDEO_MIN_AGE_SECONDS) -> dict:
    """Copy each pack video into `destination` unless an identical one is there."""
    report = {"copied": [], "already_there": 0, "skipped": []}
    destination = Path(destination)
    if not destination.is_dir():
        report["skipped"].append(f"destination missing: {destination}")
        return report

    held_digests: set[str] = set()
    held_cloud: set[tuple[str, int]] = set()   # (lower name, size) for cloud-only files
    for path, info in _plain_files(destination):
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if _is_cloud_only(info):
            held_cloud.add((path.name.lower(), info.st_size))
            continue
        try:
            held_digests.add(cache.digest(path, info))
        except OSError:
            held_cloud.add((path.name.lower(), info.st_size))

    now = time.time()
    for path, info in pack_video_candidates(sources, films_dir):
        if min_age_seconds and now - info.st_mtime < min_age_seconds:
            continue  # may still be rendering or downloading
        if (path.name.lower(), info.st_size) in held_cloud:
            report["already_there"] += 1
            continue
        try:
            digest = cache.digest(path, info)
        except OSError as exc:
            report["skipped"].append(f"{path.name}: unreadable ({exc})")
            continue
        if digest in held_digests:
            report["already_there"] += 1
            continue
        target = destination / path.name
        if target.exists():
            target = destination / f"{path.stem}_{digest[:8]}{path.suffix}"
        if not dry_run:
            temporary = destination / f".{target.name}.{uuid.uuid4().hex[:8]}.part"
            try:
                shutil.copyfile(path, temporary)
                os.replace(temporary, target)
            except OSError as exc:
                report["skipped"].append(f"{path.name}: copy failed ({exc})")
                continue
            finally:
                temporary.unlink(missing_ok=True)
        held_digests.add(digest)
        report["copied"].append(target.name)
    return report


# ─── Scheduled entry points ───────────────────────────────────────────────────

def _run_videos(dry_run: bool, cache: HashCache) -> dict:
    return sweep_pack_videos(PACK_VIDEOS_DIR, cache=cache, dry_run=dry_run)


def run_media_tidy(dry_run: bool = False, *, fold: bool = True, videos: bool = True) -> dict:
    """One full pass. Serialized so the nightly job and the sweep never overlap."""
    with _lock:
        cache = HashCache(STATE_PATH)
        result: dict = {}
        if videos:
            result["videos"] = _run_videos(dry_run, cache)
        if fold:
            result["fold"] = fold_duplicates(FOLD_FOLDERS, cache=cache, dry_run=dry_run)
        try:
            cache.save()
        except OSError as exc:
            log.warning("Media tidy could not save its hash cache: %s", exc)
        return result


def _log_result(result: dict) -> None:
    videos = result.get("videos")
    if videos:
        if videos["copied"]:
            log.info("Media tidy: copied %d pack video(s) to OneDrive: %s",
                     len(videos["copied"]), ", ".join(videos["copied"]))
        for problem in videos["skipped"]:
            log.warning("Media tidy (videos): %s", problem)
    fold = result.get("fold")
    if fold:
        log.info("Media tidy: folded %d duplicate file(s) in %d group(s), %.1f MB freed",
                 fold["linked"], fold["groups"], fold["bytes_freed"] / 1048576)
        for problem in fold["skipped"][:20]:
            log.warning("Media tidy (fold): %s", problem)


async def media_tidy_job() -> None:
    """Nightly: fold duplicates and sweep videos."""
    try:
        _log_result(await asyncio.to_thread(run_media_tidy))
    except Exception:
        log.exception("Media tidy FAILED")


async def video_sweep_job() -> None:
    """Every 15 minutes: only the video keepsakes."""
    try:
        _log_result(await asyncio.to_thread(run_media_tidy, fold=False))
    except Exception:
        log.exception("Pack video sweep FAILED")


def _main(argv: list[str]) -> int:
    apply = "--apply" in argv
    result = run_media_tidy(dry_run=not apply)
    videos, fold = result["videos"], result["fold"]
    verb = "Copied" if apply else "Would copy"
    print(f"{verb} {len(videos['copied'])} video(s) to {PACK_VIDEOS_DIR} "
          f"({videos['already_there']} already there).")
    for name in videos["copied"]:
        print(f"  + {name}")
    verb = "Folded" if apply else "Would fold"
    print(f"{verb} {fold['linked']} duplicate file(s) in {fold['groups']} group(s): "
          f"{fold['bytes_freed'] / 1048576:.1f} MB freed.")
    for problem in videos["skipped"] + fold["skipped"]:
        print(f"  ! {problem}")
    if not apply:
        print("Dry run: nothing changed. Add --apply to do it.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
