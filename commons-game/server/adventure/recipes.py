
"""Shared location rules for browser, local tools and remote companions."""

FOOD_TYPES = frozenset(("food", "companion_treat"))

def available_here(recipe, region="", room=""):
    if recipe.get("type") in FOOD_TYPES:
        return True
    return ((not recipe.get("world") or recipe["world"] == region)
            and (not recipe.get("where") or recipe["where"] == room))
