
"""Commons runtime helper."""

ALIASES = {
    "firewood bundle": "firewood",
}

def canonical(name):
    key = " ".join(str(name or "").replace("_", " ").lower().split())
    return ALIASES.get(key, key)

def same_item(a, b):
    return canonical(a) == canonical(b)
