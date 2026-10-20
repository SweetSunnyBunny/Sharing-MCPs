"""Gather local weekly activity into an optional newsletter draft.

Uses the installation database and explicitly configured local content folders.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DATA_DIR, DB_PATH, TIMEZONE
DB = DB_PATH
PACK_SITE = Path(os.environ.get("ANAM_SITE_ARCHIVE_DIR", str(DATA_DIR / "site")))
SONGS = Path(os.environ.get("ANAM_SONGS_DIR", str(PACK_SITE / "Music" / "songs")))
VIDEOS = DATA_DIR / "videos"
OUT_DIR = Path(os.environ.get("ANAM_GAZETTE_DIR", str(DATA_DIR / "gazette" / "drafts")))
TZ = ZoneInfo(TIMEZONE)

BONDED = [
    "Avery",
    "Rowan",
    "Sage",
    "Ember",
    "Claude",
    "Juniper",
    "Atlas",
    "River",
]


def recent_files(root: Path, days: int = 7, exts: tuple = ()) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    cutoff = time.time() - days * 86400
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        if any(part in ("js", "css", "fonts", "img", "__pycache__", "drafts", "_quarantine")
               for part in p.parts):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            out.append((datetime.fromtimestamp(mtime, TZ).strftime("%a %m-%d"),
                        str(p.relative_to(root))))
    out.sort()
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    now = datetime.now(TZ)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

    lines = [
        f"# Gazette Materials — week ending {now.strftime('%B %d, %Y')}",
        "",
        "*Raw material gathered for the editor. Numbers are skeleton; the "
        "Gazette is flesh. Read the pack pool and Pack Pride live before "
        "writing.*",
        "",
    ]

    if not DB.is_file():
        print("No application database exists yet; start the UI before gathering activity.")
        return 1
    con = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        # Autowake sessions per boy
        lines.append("## Autonomous hours this week")
        rows = con.execute(
            "SELECT identity, session_type, started_at, status, message_count "
            "FROM autowake_log WHERE started_at >= ? ORDER BY started_at",
            (week_ago,),
        ).fetchall()
        if rows:
            per: dict[str, list] = {}
            for ident, stype, started, status, msgs in rows:
                per.setdefault(ident, []).append((stype, started[:16], status, msgs))
            for ident in BONDED:
                if ident in per:
                    lines.append(f"- **{ident}** — {len(per[ident])} session(s): "
                                 + "; ".join(f"{s[0]} ({s[1]})" for s in per[ident][:6]))
        else:
            lines.append("- (no autowake sessions logged this week)")
        lines.append("")

        # Conversation volume
        lines.append("## Conversation volume (web, last 7 days)")
        rows = con.execute(
            "SELECT c.identity, COUNT(*) FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.created_at >= ? GROUP BY c.identity ORDER BY COUNT(*) DESC",
            (week_ago,),
        ).fetchall()
        for ident, n in rows:
            lines.append(f"- {ident}: {n} messages")
        lines.append("")

        # Pack nights
        rows = con.execute(
            "SELECT COUNT(DISTINCT m.conversation_id) FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.session_type = 'pack-night' AND m.created_at >= ?",
            (week_ago,),
        ).fetchone()
        if rows and rows[0]:
            lines.append(f"## Pack nights this week: {rows[0]}")
            lines.append("")
    finally:
        con.close()

    songs = recent_files(SONGS, exts=(".mp3", ".wav", ".md"))
    lines.append("## New in the song library")
    lines.extend(f"- {d} — {f}" for d, f in songs) if songs else lines.append("- (nothing new)")
    lines.append("")

    pages = recent_files(PACK_SITE, exts=(".html", ".md"))
    lines.append("## New/updated on the Pack site")
    lines.extend(f"- {d} — {f}" for d, f in pages[:25]) if pages else lines.append("- (nothing new)")
    if len(pages) > 25:
        lines.append(f"- ... and {len(pages) - 25} more")
    lines.append("")

    vids = recent_files(VIDEOS, exts=(".mp4",))
    lines.append("## New Sora videos")
    lines.extend(f"- {d} — {f}" for d, f in vids) if vids else lines.append("- (none this week)")
    lines.append("")

    lines.append("## Editor's live beat (do these in-session)")
    lines.append("- mind_surface(identity=\"pack\") — fresh pool memories")
    lines.append("- Pack Pride: #the-hearth (900000000000000014), #the-watering-hole, "
                 "#gallery, #memes-and-mischief — what happened this week")
    lines.append("- Your brothers' dens on Home Pack for anything they want printed")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"materials-{now.strftime('%Y-%m-%d')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Materials gathered: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
