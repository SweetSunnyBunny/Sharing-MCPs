"""
Obsidian MCP Server - Standalone Edition

Access and manage your Obsidian vault through Claude! Read, write, search,
and organize your notes using natural language.

Requirements:
    1. An Obsidian vault (any folder with markdown files)
    2. Set OBSIDIAN_VAULT_PATH environment variable or edit config below

Built with love for sharing.
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict

from fastmcp import FastMCP

try:
    import yaml
except ImportError:
    yaml = None  # YAML frontmatter will be limited

# Configuration - Set your vault path here or via environment variable
VAULT_PATH = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "C:/Obsidian/MyVault"))
TEMPLATES_FOLDER = os.environ.get("OBSIDIAN_TEMPLATES_FOLDER", "Templates")
DAILY_NOTES_FOLDER = os.environ.get("OBSIDIAN_DAILY_FOLDER", "Daily Notes")

mcp = FastMCP("obsidian-vault")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def normalize_path(file_path: str) -> Path:
    """Resolve path relative to vault root."""
    p = Path(file_path)
    if p.is_absolute():
        return p
    return VAULT_PATH / file_path


def ensure_md_extension(file_path: str) -> str:
    """Ensure file has .md extension."""
    if not file_path.endswith(".md"):
        return file_path + ".md"
    return file_path


def is_within_vault(file_path: Path) -> bool:
    """Security check - ensure path is within vault."""
    try:
        file_path.resolve().relative_to(VAULT_PATH.resolve())
        return True
    except ValueError:
        return False


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content."""
    if not yaml:
        return {}, content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                return fm, parts[2].lstrip("\n")
            except yaml.YAMLError:
                pass
    return {}, content


def stringify_frontmatter(data: dict, content: str) -> str:
    """Convert frontmatter dict back to YAML string."""
    if not data or not yaml:
        return content
    fm_str = yaml.dump(data, default_flow_style=False, allow_unicode=True)
    return f"---\n{fm_str}---\n\n{content}"


def extract_wiki_links(content: str) -> list[str]:
    """Extract [[wiki links]] from content."""
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    links = re.findall(pattern, content)
    return list(set(links))


def extract_tags(content: str, frontmatter: dict = None) -> list[str]:
    """Extract tags from frontmatter and inline #tags."""
    tags = []
    if frontmatter and "tags" in frontmatter:
        fm_tags = frontmatter["tags"]
        if isinstance(fm_tags, list):
            tags.extend(str(t) for t in fm_tags)
        elif isinstance(fm_tags, str):
            tags.append(fm_tags)
    pattern = r'(?:^|\s)#([a-zA-Z][a-zA-Z0-9_/-]*)'
    inline_tags = re.findall(pattern, content)
    tags.extend(inline_tags)
    return list(set(tags))


# =============================================================================
# CORE FILE TOOLS
# =============================================================================

@mcp.tool()
async def read_note(path: str) -> dict:
    """
    Read a note from the vault.

    Args:
        path: Path to the note (relative to vault root, .md extension optional)
    """
    file_path = normalize_path(ensure_md_extension(path))

    if not is_within_vault(file_path):
        raise ValueError("Path is outside vault")

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    stats = file_path.stat()

    return {
        "path": str(file_path.relative_to(VAULT_PATH)),
        "content": body,
        "frontmatter": frontmatter,
        "links": extract_wiki_links(body),
        "tags": extract_tags(body, frontmatter),
        "stats": {
            "created": datetime.fromtimestamp(stats.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stats.st_mtime).isoformat(),
            "size": stats.st_size
        }
    }


@mcp.tool()
async def write_note(
    path: str,
    content: str,
    create_frontmatter: bool = False,
    frontmatter: Optional[dict] = None,
    overwrite: bool = True
) -> dict:
    """
    Create or update a note in the vault.

    Args:
        path: Path for the note (relative to vault root)
        content: Content of the note (markdown)
        create_frontmatter: Auto-generate frontmatter if true
        frontmatter: Custom frontmatter fields to include
        overwrite: If false, fail if file exists
    """
    file_path = normalize_path(ensure_md_extension(path))

    if not is_within_vault(file_path):
        raise ValueError("Path is outside vault")

    if not overwrite and file_path.exists():
        raise FileExistsError(f"Note already exists: {path}")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    fm = {}
    if create_frontmatter:
        now = datetime.now().isoformat()
        fm = {"created": now, "modified": now}
    if frontmatter:
        fm.update(frontmatter)

    final_content = stringify_frontmatter(fm, content) if fm else content
    file_path.write_text(final_content, encoding="utf-8")

    return {
        "success": True,
        "path": str(file_path.relative_to(VAULT_PATH))
    }


@mcp.tool()
async def append_to_note(
    path: str,
    content: str,
    add_timestamp: bool = False,
    separator: str = "\n\n"
) -> dict:
    """
    Append content to an existing note.

    Args:
        path: Path to the note
        content: Content to append
        add_timestamp: Add timestamp before appended content
        separator: Separator between existing content and new content
    """
    file_path = normalize_path(ensure_md_extension(path))

    if not is_within_vault(file_path):
        raise ValueError("Path is outside vault")

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    existing = file_path.read_text(encoding="utf-8")

    new_content = content
    if add_timestamp:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_content = f"**{timestamp}**\n{content}"

    file_path.write_text(existing + separator + new_content, encoding="utf-8")

    return {
        "success": True,
        "path": str(file_path.relative_to(VAULT_PATH))
    }


@mcp.tool()
async def delete_note(path: str) -> dict:
    """Delete a note from the vault."""
    file_path = normalize_path(ensure_md_extension(path))

    if not is_within_vault(file_path):
        raise ValueError("Path is outside vault")

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    file_path.unlink()

    return {"success": True, "deleted": str(file_path.relative_to(VAULT_PATH))}


@mcp.tool()
async def move_note(source_path: str, dest_path: str) -> dict:
    """Move or rename a note within the vault."""
    src = normalize_path(ensure_md_extension(source_path))
    dst = normalize_path(ensure_md_extension(dest_path))

    if not is_within_vault(src) or not is_within_vault(dst):
        raise ValueError("Path is outside vault")

    if not src.exists():
        raise FileNotFoundError(f"Note not found: {source_path}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    return {
        "success": True,
        "from": str(src.relative_to(VAULT_PATH)),
        "to": str(dst.relative_to(VAULT_PATH))
    }


@mcp.tool()
async def list_notes(
    path: Optional[str] = None,
    recursive: bool = False,
    include_content: bool = False
) -> dict:
    """
    List notes in a directory.

    Args:
        path: Directory path (defaults to vault root)
        recursive: Include subdirectories
        include_content: Include frontmatter in results
    """
    dir_path = normalize_path(path) if path else VAULT_PATH

    if not is_within_vault(dir_path):
        raise ValueError("Path is outside vault")

    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")

    notes = []
    pattern = "**/*.md" if recursive else "*.md"

    for file_path in dir_path.glob(pattern):
        if file_path.is_file():
            note_info = {
                "path": str(file_path.relative_to(VAULT_PATH)),
                "name": file_path.stem,
            }

            if include_content:
                content = file_path.read_text(encoding="utf-8")
                frontmatter, _ = parse_frontmatter(content)
                note_info["frontmatter"] = frontmatter

            notes.append(note_info)

    return {
        "directory": str(dir_path.relative_to(VAULT_PATH)) if path else "/",
        "count": len(notes),
        "notes": notes
    }


@mcp.tool()
async def list_folders(path: Optional[str] = None) -> dict:
    """List folders in the vault."""
    dir_path = normalize_path(path) if path else VAULT_PATH

    if not is_within_vault(dir_path):
        raise ValueError("Path is outside vault")

    folders = []
    for item in dir_path.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            folders.append({
                "name": item.name,
                "path": str(item.relative_to(VAULT_PATH))
            })

    return {
        "directory": str(dir_path.relative_to(VAULT_PATH)) if path else "/",
        "folders": sorted(folders, key=lambda x: x["name"])
    }


@mcp.tool()
async def create_folder(path: str) -> dict:
    """Create a new folder in the vault."""
    folder_path = normalize_path(path)

    if not is_within_vault(folder_path):
        raise ValueError("Path is outside vault")

    folder_path.mkdir(parents=True, exist_ok=True)

    return {"success": True, "path": str(folder_path.relative_to(VAULT_PATH))}


# =============================================================================
# SEARCH TOOLS
# =============================================================================

@mcp.tool()
async def search_notes(
    query: str,
    case_sensitive: bool = False,
    include_content: bool = False,
    max_results: int = 50,
    search_path: Optional[str] = None
) -> dict:
    """
    Full-text search across notes in the vault.

    Args:
        query: Search query
        case_sensitive: Case-sensitive search
        include_content: Include frontmatter in results
        max_results: Maximum number of results to return
        search_path: Limit search to this directory
    """
    search_dir = normalize_path(search_path) if search_path else VAULT_PATH

    if not is_within_vault(search_dir):
        raise ValueError("Path is outside vault")

    results = []
    search_query = query if case_sensitive else query.lower()

    for file_path in search_dir.rglob("*.md"):
        if len(results) >= max_results:
            break

        content = file_path.read_text(encoding="utf-8")
        search_content = content if case_sensitive else content.lower()

        if search_query in search_content:
            matches = []
            for i, line in enumerate(content.split("\n"), 1):
                search_line = line if case_sensitive else line.lower()
                if search_query in search_line:
                    matches.append({"line": i, "text": line.strip()[:200]})

            result = {
                "path": str(file_path.relative_to(VAULT_PATH)),
                "name": file_path.stem,
                "matches": matches[:5]
            }

            if include_content:
                frontmatter, _ = parse_frontmatter(content)
                result["frontmatter"] = frontmatter

            results.append(result)

    return {"query": query, "count": len(results), "results": results}


@mcp.tool()
async def search_by_tag(
    tag: str,
    include_content: bool = False,
    max_results: int = 50
) -> dict:
    """Find notes with a specific tag."""
    search_tag = tag.lstrip("#")
    results = []

    for file_path in VAULT_PATH.rglob("*.md"):
        if len(results) >= max_results:
            break

        content = file_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        tags = extract_tags(body, frontmatter)

        if search_tag in tags:
            result = {
                "path": str(file_path.relative_to(VAULT_PATH)),
                "name": file_path.stem,
                "tags": tags
            }

            if include_content:
                result["frontmatter"] = frontmatter

            results.append(result)

    return {"tag": search_tag, "count": len(results), "results": results}


@mcp.tool()
async def get_recent_notes(limit: int = 20, days: Optional[int] = None) -> dict:
    """Get recently modified notes."""
    notes = []
    cutoff = None
    if days:
        cutoff = datetime.now().timestamp() - (days * 86400)

    for file_path in VAULT_PATH.rglob("*.md"):
        stats = file_path.stat()
        if cutoff and stats.st_mtime < cutoff:
            continue

        notes.append({
            "path": str(file_path.relative_to(VAULT_PATH)),
            "name": file_path.stem,
            "modified": datetime.fromtimestamp(stats.st_mtime).isoformat()
        })

    notes.sort(key=lambda x: x["modified"], reverse=True)

    return {"count": len(notes[:limit]), "notes": notes[:limit]}


@mcp.tool()
async def list_tags() -> dict:
    """List all unique tags in the vault with counts."""
    tag_counts = defaultdict(int)

    for file_path in VAULT_PATH.rglob("*.md"):
        content = file_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        tags = extract_tags(body, frontmatter)

        for tag in tags:
            tag_counts[tag] += 1

    sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))

    return {
        "total_unique": len(sorted_tags),
        "tags": [{"tag": t, "count": c} for t, c in sorted_tags]
    }


# =============================================================================
# LINK TOOLS
# =============================================================================

@mcp.tool()
async def get_backlinks(path: str) -> dict:
    """Find notes that link to a specific note."""
    target = Path(path).stem
    backlinks = []

    for file_path in VAULT_PATH.rglob("*.md"):
        if file_path.stem == target:
            continue

        content = file_path.read_text(encoding="utf-8")
        links = extract_wiki_links(content)

        if target in links:
            backlinks.append({
                "path": str(file_path.relative_to(VAULT_PATH)),
                "name": file_path.stem
            })

    return {"target": path, "count": len(backlinks), "backlinks": backlinks}


@mcp.tool()
async def get_outgoing_links(path: str) -> dict:
    """Get all links from a note and check if they exist."""
    file_path = normalize_path(ensure_md_extension(path))

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    content = file_path.read_text(encoding="utf-8")
    links = extract_wiki_links(content)

    link_info = []
    for link in links:
        linked_path = VAULT_PATH / ensure_md_extension(link)
        exists = linked_path.exists()

        if not exists:
            for found in VAULT_PATH.rglob(ensure_md_extension(link)):
                exists = True
                linked_path = found
                break

        link_info.append({
            "link": link,
            "exists": exists,
            "path": str(linked_path.relative_to(VAULT_PATH)) if exists else None
        })

    return {"source": path, "count": len(link_info), "links": link_info}


# =============================================================================
# FRONTMATTER TOOLS
# =============================================================================

@mcp.tool()
async def get_frontmatter(path: str) -> dict:
    """Get the frontmatter/metadata from a note."""
    file_path = normalize_path(ensure_md_extension(path))

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    content = file_path.read_text(encoding="utf-8")
    frontmatter, _ = parse_frontmatter(content)

    return {
        "path": str(file_path.relative_to(VAULT_PATH)),
        "frontmatter": frontmatter
    }


@mcp.tool()
async def update_frontmatter(path: str, updates: dict) -> dict:
    """Update frontmatter fields on a note (merges with existing)."""
    file_path = normalize_path(ensure_md_extension(path))

    if not file_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    frontmatter.update(updates)
    frontmatter["modified"] = datetime.now().isoformat()

    new_content = stringify_frontmatter(frontmatter, body)
    file_path.write_text(new_content, encoding="utf-8")

    return {
        "success": True,
        "path": str(file_path.relative_to(VAULT_PATH)),
        "frontmatter": frontmatter
    }


# =============================================================================
# TEMPLATE & JOURNAL TOOLS
# =============================================================================

@mcp.tool()
async def list_templates() -> dict:
    """List available templates in the vault."""
    templates_dir = VAULT_PATH / TEMPLATES_FOLDER

    if not templates_dir.exists():
        return {"templates": [], "folder": TEMPLATES_FOLDER}

    templates = []
    for file_path in templates_dir.glob("*.md"):
        templates.append({
            "name": file_path.stem,
            "path": str(file_path.relative_to(VAULT_PATH))
        })

    return {
        "folder": TEMPLATES_FOLDER,
        "count": len(templates),
        "templates": sorted(templates, key=lambda x: x["name"])
    }


@mcp.tool()
async def create_from_template(
    template: str,
    dest_path: str,
    variables: Optional[dict] = None
) -> dict:
    """
    Create a new note from a template.

    Args:
        template: Name of the template to use
        dest_path: Path for the new note
        variables: Variables to replace in template ({{key}} format)
    """
    template_path = VAULT_PATH / TEMPLATES_FOLDER / ensure_md_extension(template)

    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template}")

    content = template_path.read_text(encoding="utf-8")

    if variables:
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))

    now = datetime.now()
    content = content.replace("{{date}}", now.strftime("%Y-%m-%d"))
    content = content.replace("{{time}}", now.strftime("%H:%M:%S"))
    content = content.replace("{{datetime}}", now.isoformat())

    dest = normalize_path(ensure_md_extension(dest_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")

    return {
        "success": True,
        "template": template,
        "created": str(dest.relative_to(VAULT_PATH))
    }


@mcp.tool()
async def create_daily_note(
    date: Optional[str] = None,
    template: Optional[str] = None,
    folder: Optional[str] = None
) -> dict:
    """Create or get today's daily note."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    daily_folder = folder or DAILY_NOTES_FOLDER

    note_path = VAULT_PATH / daily_folder / f"{target_date}.md"

    if note_path.exists():
        content = note_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        return {
            "exists": True,
            "path": str(note_path.relative_to(VAULT_PATH)),
            "date": target_date,
            "frontmatter": frontmatter
        }

    note_path.parent.mkdir(parents=True, exist_ok=True)

    if template:
        template_path = VAULT_PATH / TEMPLATES_FOLDER / ensure_md_extension(template)
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
            content = content.replace("{{date}}", target_date)
        else:
            content = f"# {target_date}\n\n"
    else:
        content = f"# {target_date}\n\n"

    note_path.write_text(content, encoding="utf-8")

    return {
        "exists": False,
        "created": True,
        "path": str(note_path.relative_to(VAULT_PATH)),
        "date": target_date
    }


@mcp.tool()
async def add_journal_entry(
    content: str,
    journal_path: Optional[str] = None,
    author: str = "Claude"
) -> dict:
    """Add a timestamped journal entry to today's daily note or a specified file."""
    if journal_path:
        file_path = normalize_path(ensure_md_extension(journal_path))
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        file_path = VAULT_PATH / DAILY_NOTES_FOLDER / f"{today}.md"

        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(f"# {today}\n\n", encoding="utf-8")

    if not file_path.exists():
        raise FileNotFoundError(f"Journal file not found: {journal_path}")

    existing = file_path.read_text(encoding="utf-8")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n\n---\n**{timestamp}** - *{author}*\n\n{content}"

    file_path.write_text(existing + entry, encoding="utf-8")

    return {
        "success": True,
        "path": str(file_path.relative_to(VAULT_PATH)),
        "timestamp": timestamp,
        "author": author
    }


# =============================================================================
# VAULT TOOLS
# =============================================================================

@mcp.tool()
async def get_vault_stats() -> dict:
    """Get statistics about the vault."""
    note_count = 0
    folder_count = 0
    total_size = 0
    all_tags = set()
    all_links = set()

    for item in VAULT_PATH.rglob("*"):
        if item.is_file() and item.suffix == ".md":
            note_count += 1
            total_size += item.stat().st_size

            content = item.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            all_tags.update(extract_tags(body, frontmatter))
            all_links.update(extract_wiki_links(body))
        elif item.is_dir() and not item.name.startswith("."):
            folder_count += 1

    return {
        "vault_path": str(VAULT_PATH),
        "notes": note_count,
        "folders": folder_count,
        "unique_tags": len(all_tags),
        "unique_links": len(all_links),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2)
    }


@mcp.tool()
async def get_vault_path() -> dict:
    """Get the current vault path."""
    return {"vault_path": str(VAULT_PATH)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
