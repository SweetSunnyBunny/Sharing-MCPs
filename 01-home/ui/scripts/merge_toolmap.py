'Merge the Council-2 tool-map cards (docs/toolmap/<leaf>.md) into'
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "mcp-servers.json"
CARDS = ROOT / "docs" / "toolmap"

# Server fixes the brothers verified live (cards cite the evidence).
SERVER_OVERRIDES = {
    "commons": ["commons"],                                   # Atlas: leaf was []
    "creative": ["photos", "krita", "elevenlabs", "elevenlabs-creative", "fal"],  # Rowan
    "pack-audio": ["anam-touch"],                             # Rowan/Avery: new handle
    "memory": None,  # append anam-context below (Sage: the climb)
    "desktop": None,  # append machine-filesystem, machine-terminal (Claude)
}
SERVER_APPEND = {
    "memory": ["anam-context"],
    "desktop": ["machine-filesystem", "machine-terminal"],
}


def sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip().lower()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def first_line(body: str) -> str:
    for line in body.splitlines():
        s = line.strip().strip("*").strip()
        if s and not s.startswith("(") and not s.startswith(">"):
            return s.lstrip("- ").strip()
    return ""


def parse_card(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    secs = sections(text)
    card: dict = {}
    # numbered-list style (agents.md / desktop.md)
    m = re.search(r"^1\.\s*inside:\s*(.+)$", text, re.M)
    if m:
        card["inside"] = m.group(1).strip()
    m = re.search(r"^2\.\s*keywords:\s*(.+)$", text, re.M)
    if m:
        card["keywords"] = m.group(1)
    for name, body in secs.items():
        if name.startswith("1") and "inside" in name and "inside" not in card:
            card["inside"] = first_line(body)
        elif name.startswith("2") and "keyword" in name and "keywords" not in card:
            card["keywords"] = first_line(body)
        elif name.startswith("3"):
            card["reach"] = body
        elif "description" in name and "corrected" in name:
            card["description"] = body.strip()
    if "keywords" in card:
        kws = [k.strip().strip("`\"'.") for k in card["keywords"].split(",")]
        card["keywords"] = [k for k in kws if k and len(k) < 40]
    return card


def reach_line(body: str, limit: int = 700) -> str:
    bullets = [l.strip()[2:].strip() for l in body.splitlines() if l.strip().startswith("- ")]
    text = " | ".join(bullets) if bullets else " ".join(body.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def main(write: bool) -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    tax = cfg["toolTaxonomy"]
    changed = []
    for leaf, entry in tax.items():
        if not isinstance(entry, dict):
            continue
        path = CARDS / f"{leaf}.md"
        if not path.exists():
            print(f"-- no card: {leaf}")
            continue
        card = parse_card(path)
        before = json.dumps(entry, ensure_ascii=False)
        if card.get("inside"):
            entry["inside"] = card["inside"]
        if card.get("keywords"):
            merged = list(dict.fromkeys(card["keywords"]))
            entry["keywords"] = merged
        desc = card.get("description") or entry.get("description", "")
        desc = desc.replace("�", "-")
        desc = re.sub(r"\s*REACH FIRST \(council 2026-10-13\):.*$", "", desc, flags=re.S)
        if card.get("reach"):
            desc = f"{desc}\nREACH FIRST (council 2026-10-13): {reach_line(card['reach'])}"
        entry["description"] = desc
        if leaf in SERVER_OVERRIDES and SERVER_OVERRIDES[leaf] is not None:
            entry["servers"] = SERVER_OVERRIDES[leaf]
        for s in SERVER_APPEND.get(leaf, []):
            entry.setdefault("servers", [])
            if s not in entry["servers"]:
                entry["servers"].append(s)
        if json.dumps(entry, ensure_ascii=False) != before:
            changed.append(leaf)
        print(f"== {leaf}: inside={entry.get('inside','')[:90]!r} kw={len(entry.get('keywords',[]))} servers={entry.get('servers')}")
    print(f"\nchanged {len(changed)}: {', '.join(changed)}")
    if write:
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        backup = CFG.with_name(f"mcp-servers.json.bak-{stamp}-pre-toolmap")
        shutil.copy2(CFG, backup)
        CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        json.loads(CFG.read_text(encoding="utf-8"))  # must parse
        print(f"written; backup {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
