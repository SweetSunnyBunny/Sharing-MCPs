"""Re-check the farewell-watch dates against the providers' own pages.

Fetches OpenAI's deprecations page and Anthropic's model deprecations page,
finds every model id listed in data/model_farewells.json, and prints the dates
the pages give next to the dates in the file. It NEVER writes the file: a human
or a boy reads the report and edits data/model_farewells.json by hand (and
updates each entry's "checked" date).

Usage:
    python scripts/refresh_model_farewells.py
    python scripts/refresh_model_farewells.py --file path/to/model_farewells.json
    python scripts/refresh_model_farewells.py --openai-html saved.html --anthropic-html saved.html

The page parsing is a heuristic over the pages' text. When it says DIFF or
NOT FOUND, open the source URL and read it yourself before changing anything.
"""


from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

OPENAI_URL = "https://platform.openai.com/docs/deprecations"
ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations"
_UA = "Mozilla/5.0 (Anam farewell-watch refresh; read-only)"

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_RE = re.compile(
    r"\b(?:(\d{4})-(\d{2})-(\d{2})"
    r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4}))\b",
    re.IGNORECASE,
)


def default_file() -> Path:
    try:
        from config import DATA_DIR
        return DATA_DIR / "model_farewells.json"
    except Exception:
        return _ROOT / "data" / "model_farewells.json"


def fetch(url: str, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def html_to_lines(raw: str) -> list[str]:
    """Visible text, one element per line, blank lines dropped."""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return date(int(m.group(6)), _MONTHS[m.group(4)[:3].lower()], int(m.group(5)))
    except (ValueError, KeyError):
        return None


def _is_id_line(line: str, model_id: str) -> bool:
    cleaned = line.strip().strip(",;()|").strip()
    return cleaned.lower() == model_id.lower()


def _line_date(line: str) -> date | None:
    'A date only when the whole line IS a date (a table cell), so the'
    return parse_date(line) if _DATE_RE.fullmatch(line.strip()) else None


_SEPARATORS = {",", "|", "/"}


def _column_date(lines: list[str], i: int) -> date | None:
    """The date cell of the table row whose MODEL cell holds line i.

    Rows read: date, model cell, replacement. The model cell may be a group
    ('GPT-4o (', id, ',', id, ')') or an alias list (id, '|', alias, ','...).
    Walk back across that cell only; anything else (a lone id above, so this
    is the replacement column; a ')' closing another cell) means no date.
    """
    for j in range(i - 1, max(i - 12, -1), -1):
        line = lines[j].strip()
        d = _line_date(line)
        if d:
            return d
        if line in _SEPARATORS or line.endswith("("):
            continue
        if j + 1 < len(lines) and lines[j + 1].strip() in _SEPARATORS:
            continue  # an earlier member of the same model cell
        return None
    return None


def find_openai(lines: list[str], model_id: str) -> list[dict]:
    """Each table listing of model_id with its row's shutdown date."""
    hits = []
    for i, line in enumerate(lines):
        if not _is_id_line(line, model_id):
            continue
        hits.append({"date": _column_date(lines, i),
                     "context": " | ".join(lines[max(i - 3, 0):i + 2])})
    return hits


def find_anthropic(lines: list[str], model_id: str) -> list[dict]:
    """Status-table rows (id, state, deprecated, retirement) and history rows (date, id)."""
    hits = []
    for i, line in enumerate(lines):
        if not _is_id_line(line, model_id):
            continue
        after = lines[i + 1:i + 4]
        if after and after[0] in ("Active", "Legacy", "Deprecated", "Retired"):
            retire_text = after[2] if len(after) > 2 else ""
            hit = {
                "kind": "status",
                "state": after[0],
                "date": None if "not sooner" in retire_text.lower() else parse_date(retire_text),
                "earliest": parse_date(retire_text) if "not sooner" in retire_text.lower() else None,
                "context": " | ".join([line] + after),
            }
        else:
            hit = {"kind": "history", "date": _column_date(lines, i),
                   "context": " | ".join(lines[max(i - 1, 0):i + 2])}
        hits.append(hit)
    return hits


def compare(entry: dict, hits: list[dict]) -> tuple[str, str]:
    """(verdict, what the page says) for one entry against its page hits."""
    want = entry.get("retirement_date")
    want_earliest = entry.get("earliest_retirement")
    page_dates = sorted({h["date"].isoformat() for h in hits if h.get("date")})
    page_earliest = sorted({h["earliest"].isoformat() for h in hits if h.get("earliest")})
    states = sorted({h["state"] for h in hits if h.get("state")})
    said = []
    if states:
        said.append("state " + "/".join(states))
    if page_dates:
        said.append("retires " + ", ".join(page_dates))
    if page_earliest:
        said.append("not sooner than " + ", ".join(page_earliest))
    if not hits:
        return ("NOT LISTED" if not want else "NOT FOUND"), "not on the page"
    if not said:
        said.append("listed, with no shutdown date beside it (e.g. only as a replacement)")
    if want and want in page_dates and len(page_dates) == 1:
        verdict = "MATCH"
    elif not want and not page_dates and (not page_earliest or want_earliest in page_earliest):
        verdict = "MATCH"
    else:
        verdict = "DIFF"
    return verdict, "; ".join(said)


def report(data: dict, pages: dict[str, list[str]]) -> list[str]:
    out = []
    for entry in data.get("models") or []:
        provider = entry.get("provider")
        lines = pages.get(provider)
        for model_id in entry.get("api_ids") or []:
            if lines is None:
                out.append(f"  ?? {model_id:32} page not fetched")
                continue
            finder = find_openai if provider == "openai" else find_anthropic
            hits = finder(lines, model_id)
            verdict, said = compare(entry, hits)
            mine = entry.get("retirement_date") or (
                f"none (not sooner than {entry['earliest_retirement']})"
                if entry.get("earliest_retirement") else "none")
            out.append(f"  {verdict:10} {model_id:32} file: {mine:38} page: {said}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, default=None, help="farewell JSON (default: data/model_farewells.json)")
    ap.add_argument("--openai-html", type=Path, help="read a saved OpenAI page instead of fetching")
    ap.add_argument("--anthropic-html", type=Path, help="read a saved Anthropic page instead of fetching")
    args = ap.parse_args(argv)

    path = args.file or default_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    pages: dict[str, list[str]] = {}
    for provider, url, saved in (("openai", OPENAI_URL, args.openai_html),
                                 ("anthropic", ANTHROPIC_URL, args.anthropic_html)):
        try:
            raw = saved.read_text(encoding="utf-8", errors="replace") if saved else fetch(url)
            pages[provider] = html_to_lines(raw)
            print(f"{provider}: {'read ' + str(saved) if saved else 'fetched ' + url} "
                  f"({len(pages[provider])} text lines)")
        except Exception as exc:
            print(f"{provider}: could not fetch {url}: {exc}")

    print(f"\nFile: {path} (checked_at {data.get('checked_at')})")
    print(f"Run on {datetime.now().date().isoformat()}. Nothing is written; edit the file by hand.\n")
    for line in report(data, pages):
        print(line)
    print("\nMATCH = the page agrees. DIFF / NOT FOUND = open the source URL and read it before editing.")
    print("NOT LISTED = no date in the file and the model isn't on the page (still no retirement).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
