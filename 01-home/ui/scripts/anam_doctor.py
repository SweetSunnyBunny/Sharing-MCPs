"""Read-only Anam diagnostic report. Safe to run while the live app is active."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (
    DATA_DIR,
    DB_PATH,
    HERMES_SKILLS_ALLOWLIST,
    HERMES_SKILLS_DIR,
    PATH_CONFIG,
    PORT,
)
from services.skill_usage import get_skill_usage, list_pending_improvements


def _check_database() -> dict:
    if not DB_PATH.exists():
        return {"status": "error", "path": str(DB_PATH), "error": "database missing"}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH.resolve().as_posix()}?mode=ro", uri=True)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT count(*) FROM timers WHERE status IN ('pending','running')"
            ).fetchone()[0]
        finally:
            conn.close()
        return {
            "status": "ok" if quick == "ok" else "error",
            "path": str(DB_PATH),
            "quick_check": quick,
            "tables": tables,
            "active_timers": pending,
        }
    except Exception as exc:
        return {"status": "error", "path": str(DB_PATH), "error": str(exc)}


def _check_http(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "status": payload.get("status", "unknown"),
            "http_status": response.status,
            "url": url,
            "ready": payload.get("ready"),
            "scheduler": payload.get("scheduler"),
            "mcp": payload.get("mcp"),
            "process": payload.get("process"),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "error", "url": url, "http_status": exc.code}
    except Exception as exc:
        return {"status": "offline", "url": url, "error": str(exc)}


def build_report(base_url: str) -> dict:
    usage = shutil.disk_usage(DATA_DIR.resolve().anchor or DATA_DIR)
    paths = {
        name: {"path": cfg["path"], "exists": Path(cfg["path"]).exists(), "source": cfg["source"]}
        for name, cfg in sorted(PATH_CONFIG.items())
    }
    available_hermes = []
    if HERMES_SKILLS_DIR.exists():
        for skill_file in HERMES_SKILLS_DIR.rglob("SKILL.md"):
            if skill_file.parent.name.lower() in HERMES_SKILLS_ALLOWLIST:
                available_hermes.append(skill_file.parent.name)
    return {
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "commands": {
            "claude": shutil.which("claude") or shutil.which("claude.cmd"),
            "codex": shutil.which("codex") or shutil.which("codex.cmd"),
        },
        "database": _check_database(),
        "server": _check_http(base_url),
        "disk": {
            "free_gb": round(usage.free / 1024**3, 2),
            "total_gb": round(usage.total / 1024**3, 2),
        },
        "skills": {
            "hermes_root": str(HERMES_SKILLS_DIR),
            "hermes_root_exists": HERMES_SKILLS_DIR.exists(),
            "allowlisted": sorted(HERMES_SKILLS_ALLOWLIST),
            "available_allowlisted": sorted(available_hermes),
            "usage_entries": len(get_skill_usage()),
            "pending_improvements": len(list_pending_improvements()),
        },
        "configured_paths": paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=f"http://127.0.0.1:{PORT}")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    report = build_report(args.url)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("Anam doctor (read-only)")
        print(json.dumps(report, indent=2, ensure_ascii=False))
    severe = report["database"]["status"] == "error"
    return 1 if severe else 0


if __name__ == "__main__":
    raise SystemExit(main())
