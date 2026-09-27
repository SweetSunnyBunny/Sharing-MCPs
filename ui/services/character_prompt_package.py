"""Selected character packages: one identity file, live state, and turn anchor.

Only configured character identities opt in. Providers share the selector so
changing engines cannot silently change story continuity. Files are read fresh;
the bridge's existing identity receipt hashes handle identity refresh delivery.
"""

from pathlib import Path

from config import IDENTITIES, PROMPTS_DIR


def package_dir(identity: str, prompts_dir: Path = PROMPTS_DIR) -> Path | None:
    relative = IDENTITIES.get(identity, {}).get("prompt_package")
    if not relative:
        return None
    root = prompts_dir.resolve()
    selected = (root / relative).resolve()
    if not selected.is_relative_to(root):
        raise ValueError("Character prompt package must be inside prompts directory")
    return selected


def identity_prompt_file(identity: str, prompts_dir: Path = PROMPTS_DIR) -> Path:
    selected = package_dir(identity, prompts_dir)
    return selected / "IDENTITY.md" if selected else prompts_dir / f"{identity.lower()}.md"


def read_package_file(identity: str, name: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    selected = package_dir(identity, prompts_dir)
    if selected is None:
        return ""
    if name not in {"IDENTITY.md", "CURRENT_STATE.md", "TURN_ANCHOR.md"}:
        raise ValueError("Unknown character prompt component")
    text = (selected / name).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Empty character prompt component: {selected / name}")
    return text


def build_turn_packet(identity: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    """Complete, untruncated checkpoint and voice reminder for EACH reply."""
    selected = package_dir(identity, prompts_dir)
    if selected is None:
        return ""
    state = read_package_file(identity, "CURRENT_STATE.md", prompts_dir)
    anchor = read_package_file(identity, "TURN_ANCHOR.md", prompts_dir)
    return (
        f"[LIVE ROLEPLAY PACKET — {identity}]\n"
        "This is the selected story's saved checkpoint and writing guidance. "
        "It establishes continuity when this package first enters a thread; "
        "older retained history does not override that starting checkpoint. "
        "Newer live events and Owner's current corrections take precedence. "
        "Continue the actual latest turn, not an older stopping point.\n\n"
        f"{state}\n\n{anchor}\n\n"
        f"State source: {selected / 'CURRENT_STATE.md'}\n"
        f"Voice source: {selected / 'TURN_ANCHOR.md'}\n"
        "[/LIVE ROLEPLAY PACKET]"
    )
