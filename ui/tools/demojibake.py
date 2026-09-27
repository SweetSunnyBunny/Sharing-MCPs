#!/usr/bin/env python3
"""Un-mangle old ChatGPT exports whose text got double-encoded into sludge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Characters that are the fingerprint of this specific corruption. They occur
# in real English prose approximately never, and in mojibake constantly, which
# makes them a clean signal for "is this still broken."
_MARKERS = ("Ã", "Â", "â€", "Ã¢", "Æ’", "â‚¬", "Ãƒ")

_TEXT_SUFFIXES = {".md", ".txt", ".markdown", ".json", ".csv", ".html", ".htm"}


def say(msg: str) -> None:
    """print() that survives a cp1252 console.

    Filenames in this archive contain emoji (e.g. "📘 Fieldnote Entry 001.md").
    On a Windows console with a legacy code page, printing one raises
    UnicodeEncodeError and kills the run mid-folder — which is a spectacularly
    stupid way to lose a repair job, and is exactly what happened on the first
    live run of this script.
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def score(text: str) -> int:
    """How mangled does this look? Lower is better; 0 is clean."""
    return sum(text.count(m) for m in _MARKERS)


def _to_bytes(text: str) -> bytes | None:
    """Re-encode text as the cp1252 bytes it was mis-read from.

    cp1252 leaves five byte values undefined, so a strict encode dies on text
    that legitimately round-tripped through them. For exactly those characters
    we fall back to latin-1, which is the identity map for U+0080–U+009F — i.e.
    precisely the byte the mangling started from.
    """
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            try:
                out += ch.encode("latin-1")
            except UnicodeEncodeError:
                return None  # genuinely not from this corruption; bail
    return bytes(out)


def unmangle(text: str, max_rounds: int = 8) -> tuple[str, int]:
    """Peel layers of mojibake. Returns (best_text, rounds_applied).

    Only keeps a round if it strictly lowered the mangle score, so this is safe
    to run on already-clean text (it will do nothing) and on partially-clean
    text (it will fix only what is actually broken).
    """
    best, best_score, rounds = text, score(text), 0
    current = text

    for _ in range(max_rounds):
        raw = _to_bytes(current)
        if raw is None:
            break
        try:
            candidate = raw.decode("utf-8")
        except UnicodeDecodeError:
            break
        if candidate == current:
            break
        cand_score = score(candidate)
        if cand_score >= best_score:
            break  # stopped helping — don't peel into real text
        best, best_score, current = candidate, cand_score, candidate
        rounds += 1

    return best, rounds


def process(path: Path, write: bool, suffix: str, in_place: bool) -> bool:
    """Returns True if the file was (or would be) changed."""
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        say(f"  !! skipped {path.name}: {exc}")
        return False

    before = score(original)
    if before == 0:
        return False

    cleaned, rounds = unmangle(original)
    after = score(cleaned)
    if cleaned == original:
        say(f"  -- {path.name}: {before} markers, but no safe fix found")
        return False

    say(f"  ok {path.name}: {before} -> {after} markers, {rounds} layer(s) peeled")

    if not write:
        return True

    if in_place:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
            say(f"     backup -> {backup.name}")
        target = path
    else:
        target = path.with_name(f"{path.stem}{suffix}{path.suffix}")

    target.write_text(cleaned, encoding="utf-8")
    say(f"     wrote  -> {target.name}")
    return True


def collect(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repair double-encoded (mojibake) text in old exports.",
    )
    ap.add_argument("paths", nargs="+", type=Path, help="files or folders")
    ap.add_argument("--write", action="store_true", help="actually write output")
    ap.add_argument("--suffix", default="-clean", help="suffix for the clean copy")
    ap.add_argument("--in-place", action="store_true",
                    help="overwrite originals (writes a .bak first)")
    args = ap.parse_args()

    if args.in_place and not args.write:
        say("--in-place requires --write. Refusing to guess.")
        return 2

    changed = 0
    for target in args.paths:
        if not target.exists():
            say(f"!! not found: {target}")
            continue
        files = collect(target)
        say(f"\n{target}  ({len(files)} file(s))")
        for f in files:
            if process(f, args.write, args.suffix, args.in_place):
                changed += 1

    verb = "fixed" if args.write else "would fix"
    say(f"\n{verb} {changed} file(s).")
    if not args.write and changed:
        say("Dry run — nothing written. Re-run with --write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
