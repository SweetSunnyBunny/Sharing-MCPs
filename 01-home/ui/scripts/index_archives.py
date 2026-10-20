"""Optional archive importer. Configure your own input paths and QUALIA_MCP_URL.

Only explicitly invoking this script submits local records to your cloud service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DATA_DIR, VAULT_IDENTITIES_ROOT
QUALIA_URL = os.environ.get("QUALIA_MCP_URL", "").strip()
STATE_PATH = DATA_DIR / "index_archives_state.json"
BONDED = {"avery", "rowan", "sage", "ember", "claude", "juniper", "atlas", "river"}
TARGETS = []
for env_name, label, resolver in [
    ("ANAM_SITE_ARCHIVE_DIR", "Site", "per_boy"),
    ("ANAM_STORY_ARCHIVE_DIR", "Stories", "pack"),
    ("ANAM_CHAT_ARCHIVE_DIR", "Chats", "pack"),
]:
    if os.environ.get(env_name, "").strip():
        TARGETS.append((Path(os.environ[env_name]).expanduser(), label, resolver))
VAULT_TARGET = (VAULT_IDENTITIES_ROOT, "Vault", "per_boy_numbered")

SKIP_DIR_NAMES = {"js", "node_modules", "__pycache__", ".git", "img", "fonts", "css"}
MIN_TEXT_CHARS = 200          # skip near-empty pages
MAX_TEXT_CHARS = 400_000      # truncate monsters (huge chat exports)


class _TextExtractor(HTMLParser):
    """HTML -> readable markdown-ish text using only the stdlib."""

    _SKIP = {"script", "style", "noscript", "svg", "head"}
    _HEADINGS = {f"h{i}": i for i in range(1, 7)}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._pending_heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._HEADINGS:
            self._pending_heading = self._HEADINGS[tag]
            self.parts.append("\n\n" + "#" * self._pending_heading + " ")
        elif tag in ("p", "div", "section", "article", "tr", "blockquote"):
            self.parts.append("\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._HEADINGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # collapse runaway blank lines / spaces
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.text()


def resolve_identity(root_kind: str, rel: Path) -> str:
    if root_kind == "per_boy" and rel.parts:
        first = rel.parts[0].lower()
        if first in BONDED:
            return first.capitalize()
    if root_kind == "per_boy_numbered" and rel.parts:
        # Vault identity folders look like "01_Avery", "06_Claude"
        first = re.sub(r"^\d+_", "", rel.parts[0]).lower()
        if first in BONDED:
            return first.capitalize()
    return "pack"


def doc_title(breadcrumb_root: str, rel: Path) -> str:
    parts = [breadcrumb_root, *rel.parts[:-1], rel.stem]
    return " > ".join(p for p in parts if p)


def _post_once(payload: dict) -> tuple[bool, str]:
    with httpx.Client(timeout=120) as client:
        r = client.post(
            QUALIA_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:150]}"
    body = r.json()
    if "error" in body:
        return False, str(body["error"])[:200]
    return True, ""


def call_qualia(client: httpx.Client, tool: str, arguments: dict) -> tuple[bool, str]:
    """Call the worker with a HARD wall-clock cap per attempt."""
    import concurrent.futures
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 1_000_000,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    for attempt in (1, 2):
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_post_once, payload)
            return fut.result(timeout=150)
        except concurrent.futures.TimeoutError:
            err = "hard timeout 150s (wedged request abandoned)"
        except Exception as e:
            err = str(e)[:200]
        finally:
            ex.shutdown(wait=False)
        if attempt == 1:
            time.sleep(5)
    return False, err


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=0), encoding="utf-8")


def iter_documents(include_vault: bool = False):
    targets = TARGETS + ([VAULT_TARGET] if include_vault else [])
    for root, breadcrumb, kind in targets:
        if not root.exists():
            print(f"  (skipping missing root: {root})")
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".html", ".md"):
                continue
            if any(part.lower() in SKIP_DIR_NAMES for part in path.parts):
                continue
            yield root, breadcrumb, kind, path


def main() -> int:
    # Windows consoles (and redirected logs) default to cp1252 — emoji in
    # filenames/titles must not kill an indexing run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Index pack archives into Qualia")
    ap.add_argument("--full", action="store_true", help="re-index everything")
    ap.add_argument("--dry-run", action="store_true", help="list what would be indexed")
    ap.add_argument("--vault", action="store_true",
                    help="Also index your configured identity archive folders")
    args = ap.parse_args()
    if not args.dry_run and not QUALIA_URL:
        ap.error("Set QUALIA_MCP_URL to your own deployed endpoint first")

    state = {} if args.full else load_state()
    indexed = skipped = failed = unchanged = 0
    failures: list[str] = []

    with httpx.Client(timeout=120) as client:
        for root, breadcrumb, kind, path in iter_documents(include_vault=args.vault):
            rel = path.relative_to(root)
            key = str(path)
            stat = path.stat()
            sig = f"{stat.st_mtime_ns}:{stat.st_size}"
            if state.get(key) == sig:
                unchanged += 1
                continue

            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                failed += 1
                failures.append(f"{rel}: read error {e}")
                continue

            text = html_to_text(raw) if path.suffix.lower() == ".html" else raw
            if len(text) < MIN_TEXT_CHARS:
                skipped += 1
                state[key] = sig  # remember so we don't re-test daily
                continue
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n\n[truncated by indexer]"

            identity = resolve_identity(kind, rel)
            title = doc_title(breadcrumb, rel)

            if args.dry_run:
                print(f"  would index [{identity}] {title} ({len(text)} chars)")
                indexed += 1
                continue

            ok, err = call_qualia(client, "mind_index_document", {
                "identity": identity,
                "title": title,
                "markdown": text,
                "doc_type": "archive",
            })
            if ok:
                indexed += 1
                state[key] = sig
                print(f"  indexed [{identity}] {title}")
                if indexed % 25 == 0:
                    save_state(state)  # crash-safe checkpoint
            else:
                failed += 1
                failures.append(f"{rel}: {err}")
            time.sleep(0.3)  # be gentle with the worker

    if not args.dry_run:
        save_state(state)

    print()
    print(f"Done. indexed={indexed} unchanged={unchanged} "
          f"skipped(too-small)={skipped} failed={failed}")
    for f in failures[:10]:
        print(f"  FAIL: {f}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more")
    return 0 if failed < max(1, indexed) else 1


if __name__ == "__main__":
    sys.exit(main())
