"""Read-only report of chapter coverage in an explicitly selected story folder.

Usage: python story_agent_coverage.py --story <folder> [--gaps]
"""

import argparse
import re
import sys
from pathlib import Path


# Files each agent appends its work to, and the column label for the report.
TRACKED = [
    ("continuity", "bible/CONTINUITY_NOTES.md"),
    ("romance", "bible/ROMANCE_NOTES.md"),
    ("editor", "bible/EDITOR_REVIEWS.md"),
]

# Pages that are not chapters and should never be counted as gaps.
NON_CHAPTER = {"index.html", "au_bible.html", "soundtrack.html"}


def chapter_key(filename):
    """'34_5_Interlude_All_Dressed_Up.html' -> '34.5';  '37_Chapter.html' -> '37'."""
    m = re.match(r"^(\d+)(?:_(\d+))?_", filename)
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1)


def chapter_title(filename):
    """'06_The_Bookstore.html' -> 'The Bookstore'; drops the number and Interlude tag."""
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"^\d+(?:_\d+)?_", "", stem)
    stem = re.sub(r"^Interlude_", "", stem)
    return stem.replace("_", " ").strip()


def mentions_chapter(haystack, key):
    """Numbered reference: 'Ch 34', 'Ch. 34', 'Chapter 34', 'Ch 34.5'.

    Deliberately requires the Ch prefix — a bare '34' would match page numbers,
    ages, times and give a false all-clear.
    """
    num = re.escape(key)
    return re.search(rf"\bCh(?:apter|\.)?\s*{num}(?![\d.])", haystack, re.IGNORECASE) is not None


def mentions_title(haystack, title):
    """Weak signal: the chapter named by title rather than number.

    THIS EXISTS BECAUSE THE FIRST VERSION OF THIS SCRIPT LIED TO ME.
    Numbered-reference-only reported 15/56 chapters "missing from continuity" —
    but spot-checking found the early chapters referenced by title all along
    ('The Bookstore' appears 10 times in CONTINUITY_NOTES with no 'Ch 6').
    An instrument that can only see one notation reports the other as absence.
    So both signals get rendered, separately, and neither pretends to be the
    other: a numbered ref means an agent filed against that chapter; a title-only
    ref means it was merely mentioned in passing. Not the same thing. Don't
    collapse them.
    """
    if len(title) < 5:  # too short to be distinctive; would false-positive
        return False
    return title.lower() in haystack.lower()


def main():
    ap = argparse.ArgumentParser(description="Report story-agent coverage per chapter")
    ap.add_argument("--story", required=True, help="your story folder")
    ap.add_argument("--gaps", action="store_true", help="only show chapters missing coverage")
    args = ap.parse_args()

    root = Path(args.story)
    if not root.exists():
        print(f"!! story folder not found: {root}")
        return 1

    # Load each agent's notes once.
    notes = {}
    for label, rel in TRACKED:
        p = root / rel
        if p.exists():
            notes[label] = p.read_text(encoding="utf-8", errors="replace")
        else:
            notes[label] = None
            print(f"  (missing: {rel} — '{label}' column will read n/a)")

    chapters = []
    for f in sorted(root.glob("*.html")):
        if f.name in NON_CHAPTER:
            continue
        key = chapter_key(f.name)
        if key is None:
            continue
        html = f.read_text(encoding="utf-8", errors="replace")
        title = chapter_title(f.name)
        row = {"key": key, "sort": float(key), "name": f.name,
               "beats": html.count("IMAGE BEAT")}
        for label, _ in TRACKED:
            body = notes[label]
            if body is None:
                row[label] = None                       # notes file absent
            elif mentions_chapter(body, key):
                row[label] = "num"                      # filed against by number
            elif mentions_title(body, title):
                row[label] = "title"                    # mentioned in passing only
            else:
                row[label] = "none"
        chapters.append(row)
    chapters.sort(key=lambda c: c["sort"])

    def cell(v):
        return {None: "n/a", "num": " ok", "title": "  ~", "none": " --"}[v]

    print(f"\n{'='*74}")
    print(f"  STORY AGENT COVERAGE — {root.name}")
    print(f"{'='*74}")
    print(f"  {'chapter':<44}{'art':>5}{'cont':>6}{'rom':>6}{'rain':>6}")
    print(f"  {'-'*42}  {'-'*3}  {'-'*4}  {'-'*4}  {'-'*4}")

    shown = 0
    for c in chapters:
        missing = (c["beats"] == 0) or any(c[l] == "none" for l, _ in TRACKED)
        if args.gaps and not missing:
            continue
        shown += 1
        flag = "!" if missing else " "
        print(f" {flag}{c['name'][:43]:<44}{c['beats']:>5}"
              f"{cell(c['continuity']):>6}{cell(c['romance']):>6}{cell(c['river']):>6}")

    total = len(chapters)
    no_art = sum(1 for c in chapters if c["beats"] == 0)

    print(f"\n{'-'*74}")
    print(f"  {total} chapters" + (f"   ({shown} shown)" if args.gaps else ""))
    print(f"  legend:  ok = filed against by number   ~ = title mentioned only"
          f"   -- = absent\n")
    print(f"  no art beats placed        : {no_art:>3} / {total}")
    for label, _ in TRACKED:
        if notes[label] is None:
            print(f"  {label:<12} unfiled      : n/a (notes file missing)")
            continue
        none_n = sum(1 for c in chapters if c[label] == "none")
        title_n = sum(1 for c in chapters if c[label] == "title")
        print(f"  {label:<12} not filed    : {none_n:>3} / {total}"
              f"   (+{title_n} mentioned by title only)")
    print(f"{'-'*74}")
    print("  Read-only. Nothing was written, spawned, or changed.")
    print("  A gap here means an agent was never RUN for that chapter —")
    print("  not that it ran and failed. Silence is not success.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
