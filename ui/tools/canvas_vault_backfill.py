"""ANAM GUIDE."""
from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH, VAULT_CANVAS_DIRS, VAULT_CANVAS_FALLBACK_DIR  # noqa: E402
from services.canvas_store import archive_canvas_to_vault  # noqa: E402

# Trailing _<canvas_id>.md is the uniqueness suffix canvas_vault_path appends.
_ID_SUFFIX = re.compile(r"_(\d+)\.md$")


def canvas_ids_on_disk() -> set[int]:
    """Every canvas id that already has a file, counting the shared fallback
    bucket. Missing this bucket is exactly the blind spot in the docstring."""
    found: set[int] = set()
    for folder in list(VAULT_CANVAS_DIRS.values()) + [VAULT_CANVAS_FALLBACK_DIR]:
        if not folder.exists():
            continue
        for f in folder.glob("*.md"):
            m = _ID_SUFFIX.search(f.name)
            if m:
                found.add(int(m.group(1)))
    return found


def rows_missing_a_file() -> list[sqlite3.Row]:
    on_disk = canvas_ids_on_disk()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, identity, conversation_id, title, content, created_at "
            "FROM canvases ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [r for r in rows if r["id"] not in on_disk]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill missing canvas Vault mirrors.")
    ap.add_argument("--write", action="store_true",
                    help="actually write files (default is a dry run)")
    ap.add_argument("--only", type=int, nargs="*", default=None,
                    help="restrict to specific canvas ids")
    args = ap.parse_args()

    missing = rows_missing_a_file()
    if args.only:
        wanted = set(args.only)
        missing = [r for r in missing if r["id"] in wanted]

    if not missing:
        print("Every canvas row has a Vault file. Nothing to backfill.")
        return 0

    print(f"{len(missing)} canvas row(s) with no Vault file:\n")
    for r in missing:
        print(f"  #{r['id']:>4}  {r['identity']:<10} {r['created_at'][:10]}  "
              f"{(r['title'] or '')[:56]}")

    if not args.write:
        print("\nDRY RUN -- nothing written. Re-run with --write to backfill.")
        return 0

    print()
    ok = failed = 0
    for r in missing:
        # created_at is the truth about WHEN; using today's date would stamp
        # a false birthday onto the filename and sort it wrong forever.
        from datetime import datetime, timezone
        try:
            when = datetime.fromisoformat(r["created_at"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except Exception:
            when = datetime.now(timezone.utc)

        path = await archive_canvas_to_vault(
            identity=r["identity"],
            title=r["title"] or "Canvas",
            content=r["content"] or "",
            canvas_id=r["id"],
            conversation_id=r["conversation_id"],
            created_at=r["created_at"],
            when=when,
        )
        if path is None:
            failed += 1
            print(f"  FAILED  #{r['id']} ({r['identity']}) -- see log for why")
        else:
            ok += 1
            print(f"  wrote   #{r['id']} -> {path.name}")

    print(f"\nBackfilled {ok}, failed {failed}.")
    if failed:
        print("A repeat failure here is the REAL finding -- it reproduces the "
              "silent drop under a hand that is watching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
