
"""Commons runtime helper."""
import json
from pathlib import Path

def _key(name):
    return str(name or "").strip().casefold()

def build(crops=None, fish=None, shinies=None, forage=None, hunting=None):
    """Commons runtime helper."""
    out = {}

    def put(name, category, default=""):
        k = _key(name)
        cat = str(category or "").strip() or default
        if k and cat:
            out.setdefault(k, cat)

    for name, rec in ((crops or {}).get("crops") or {}).items():
        put(name, (rec or {}).get("yields"))
    for name, rec in ((fish or {}).get("fish") or {}).items():
        put(name, (rec or {}).get("yields"), "fish")
    for name, rec in ((shinies or {}).get("shinies") or {}).items():
        put(name, (rec or {}).get("yields"), "shiny")
    for name, rec in ((forage or {}).get("finds") or {}).items():
        put(name, (rec or {}).get("yields"), "forage")
    for _quarry, rec in ((hunting or {}).get("quarry") or {}).items():
        for name, spec in ((rec or {}).get("loot") or {}).items():
            put(name, (spec or {}).get("yields"), "game")
    return out

def category(yields, name):
    return yields.get(_key(name), "")

def by_category(picked, yields):
    """Fold {thing: n} into {category: n}."""
    out = {}
    for name, n in (picked or {}).items():
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        cat = category(yields, name)
        if n > 0 and cat:
            out[cat] = out.get(cat, 0) + n
    return out

def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig")) or {}
    except (OSError, ValueError):
        return {}

def load(root):
    """The same five catalogs commons_server reads, straight from world/."""
    world = Path(root) / "world"
    return build(*(_read(world / f) for f in
                   ("crops.json", "fish.json", "shinies.json", "forage.json", "hunting.json")))
