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

# RAG dependencies (optional)
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    chromadb = None
    CHROMADB_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    EMBEDDINGS_AVAILABLE = False

RAG_AVAILABLE = CHROMADB_AVAILABLE and EMBEDDINGS_AVAILABLE

# Configuration - Set your vault path here or via environment variable
VAULT_PATH = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "C:/Obsidian/MyVault"))
TEMPLATES_FOLDER = os.environ.get("OBSIDIAN_TEMPLATES_FOLDER", "Templates")
DAILY_NOTES_FOLDER = os.environ.get("OBSIDIAN_DAILY_FOLDER", "Daily Notes")

# RAG Configuration
RAG_INDEX_PATH = Path(os.environ.get("OBSIDIAN_RAG_INDEX", str(VAULT_PATH / ".obsidian" / "rag_index")))
EMBEDDING_MODEL = os.environ.get("OBSIDIAN_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
CHUNK_SIZE = int(os.environ.get("OBSIDIAN_CHUNK_SIZE", "500"))  # characters
CHUNK_OVERLAP = int(os.environ.get("OBSIDIAN_CHUNK_OVERLAP", "50"))  # characters

# Global RAG components (lazy loaded)
_embedding_model = None
_chroma_client = None
_chroma_collection = None

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
# RAG UTILITY FUNCTIONS
# =============================================================================

def get_embedding_model():
    """Lazy load the embedding model."""
    global _embedding_model
    if not EMBEDDINGS_AVAILABLE:
        return None
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embedding_model


def get_chroma_collection():
    """Lazy load the ChromaDB collection."""
    global _chroma_client, _chroma_collection
    if not CHROMADB_AVAILABLE:
        return None
    if _chroma_collection is None:
        RAG_INDEX_PATH.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=str(RAG_INDEX_PATH),
            settings=Settings(anonymized_telemetry=False)
        )
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="obsidian_vault",
            metadata={"hnsw:space": "cosine"}
        )
    return _chroma_collection


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    Split text into overlapping chunks, preferring natural boundaries.
    Returns list of dicts with 'text' and 'start_char' keys.
    """
    if not text.strip():
        return []

    chunks = []

    # Try to split by headers first
    header_pattern = r'^(#{1,6}\s+.+)$'
    sections = re.split(header_pattern, text, flags=re.MULTILINE)

    current_section = ""
    current_header = ""

    for i, part in enumerate(sections):
        if re.match(header_pattern, part):
            # This is a header
            if current_section.strip():
                # Save previous section
                chunks.extend(_chunk_section(current_section, current_header, chunk_size, overlap))
            current_header = part.strip()
            current_section = part + "\n"
        else:
            current_section += part

    # Don't forget the last section
    if current_section.strip():
        chunks.extend(_chunk_section(current_section, current_header, chunk_size, overlap))

    # If no headers found, chunk the whole text
    if not chunks:
        chunks = _chunk_section(text, "", chunk_size, overlap)

    return chunks


def _chunk_section(text: str, header: str, chunk_size: int, overlap: int) -> list[dict]:
    """Chunk a section of text with overlap."""
    chunks = []

    # If section is small enough, return as single chunk
    if len(text) <= chunk_size:
        if text.strip():
            chunks.append({"text": text.strip(), "header": header})
        return chunks

    # Split by paragraphs first
    paragraphs = re.split(r'\n\s*\n', text)

    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk += ("\n\n" if current_chunk else "") + para
        else:
            if current_chunk:
                chunks.append({"text": current_chunk, "header": header})

            # If paragraph itself is too long, split it
            if len(para) > chunk_size:
                words = para.split()
                current_chunk = ""
                for word in words:
                    if len(current_chunk) + len(word) + 1 <= chunk_size:
                        current_chunk += (" " if current_chunk else "") + word
                    else:
                        if current_chunk:
                            chunks.append({"text": current_chunk, "header": header})
                        current_chunk = word
            else:
                current_chunk = para

    if current_chunk:
        chunks.append({"text": current_chunk, "header": header})

    return chunks


def get_file_hash(file_path: Path) -> str:
    """Get a hash based on file path and modification time."""
    stats = file_path.stat()
    return f"{file_path}:{stats.st_mtime}:{stats.st_size}"


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
# RAG SEARCH TOOLS
# =============================================================================

@mcp.tool()
async def rag_status() -> dict:
    """
    Check the status of RAG (semantic search) capabilities.

    Returns availability of RAG features, index status, and configuration.
    """
    result = {
        "rag_available": RAG_AVAILABLE,
        "chromadb_installed": CHROMADB_AVAILABLE,
        "embeddings_installed": EMBEDDINGS_AVAILABLE,
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "index_path": str(RAG_INDEX_PATH)
    }

    if not RAG_AVAILABLE:
        result["install_hint"] = "pip install sentence-transformers chromadb"
        return result

    collection = get_chroma_collection()
    if collection:
        result["indexed_chunks"] = collection.count()
        # Get unique documents
        if collection.count() > 0:
            all_meta = collection.get(include=["metadatas"])
            unique_docs = set(m.get("path", "") for m in all_meta["metadatas"])
            result["indexed_notes"] = len(unique_docs)
        else:
            result["indexed_notes"] = 0

    return result


@mcp.tool()
async def index_vault(
    force_reindex: bool = False,
    paths: Optional[list[str]] = None
) -> dict:
    """
    Index vault notes for semantic search (RAG).

    Creates vector embeddings for all notes, enabling semantic search.
    Automatically skips unchanged files unless force_reindex is True.

    Args:
        force_reindex: If true, reindex all notes even if unchanged
        paths: Optional list of specific paths to index (relative to vault)
    """
    if not RAG_AVAILABLE:
        return {
            "success": False,
            "error": "RAG not available. Install: pip install sentence-transformers chromadb"
        }

    model = get_embedding_model()
    collection = get_chroma_collection()

    if not model or not collection:
        return {"success": False, "error": "Failed to initialize RAG components"}

    # Track what's in the index
    existing_hashes = {}
    if not force_reindex and collection.count() > 0:
        all_data = collection.get(include=["metadatas"])
        for meta in all_data["metadatas"]:
            if "file_hash" in meta:
                existing_hashes[meta.get("path", "")] = meta["file_hash"]

    # Determine which files to process
    if paths:
        files_to_process = [normalize_path(ensure_md_extension(p)) for p in paths]
    else:
        files_to_process = list(VAULT_PATH.rglob("*.md"))

    indexed = 0
    skipped = 0
    chunks_added = 0
    errors = []

    for file_path in files_to_process:
        if not is_within_vault(file_path) or not file_path.exists():
            continue

        rel_path = str(file_path.relative_to(VAULT_PATH))
        file_hash = get_file_hash(file_path)

        # Skip if unchanged
        if not force_reindex and existing_hashes.get(rel_path) == file_hash:
            skipped += 1
            continue

        try:
            # Remove old chunks for this file
            try:
                collection.delete(where={"path": rel_path})
            except Exception:
                pass  # Collection might be empty

            # Read and parse file
            content = file_path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            tags = extract_tags(body, frontmatter)

            # Chunk the content
            chunks = chunk_text(body)

            if not chunks:
                continue

            # Create embeddings
            chunk_texts = [c["text"] for c in chunks]
            embeddings = model.encode(chunk_texts, show_progress_bar=False)

            # Add to collection
            ids = [f"{rel_path}:chunk:{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "path": rel_path,
                    "name": file_path.stem,
                    "chunk_index": i,
                    "header": chunks[i].get("header", ""),
                    "tags": json.dumps(tags),
                    "file_hash": file_hash
                }
                for i in range(len(chunks))
            ]

            collection.add(
                ids=ids,
                embeddings=embeddings.tolist(),
                documents=chunk_texts,
                metadatas=metadatas
            )

            indexed += 1
            chunks_added += len(chunks)

        except Exception as e:
            errors.append({"path": rel_path, "error": str(e)})

    return {
        "success": True,
        "indexed_notes": indexed,
        "skipped_unchanged": skipped,
        "chunks_created": chunks_added,
        "errors": errors[:10] if errors else []
    }


@mcp.tool()
async def semantic_search(
    query: str,
    limit: int = 10,
    min_score: float = 0.3,
    filter_tags: Optional[list[str]] = None,
    filter_path: Optional[str] = None
) -> dict:
    """
    Search notes by semantic meaning using AI embeddings.

    Unlike keyword search, this finds notes based on conceptual similarity.
    For example, searching "project planning" might find notes about
    "task management" or "roadmap" even without exact keyword matches.

    Args:
        query: Natural language search query
        limit: Maximum results to return
        min_score: Minimum similarity score (0-1, higher = more similar)
        filter_tags: Only include notes with these tags
        filter_path: Only search within this directory
    """
    if not RAG_AVAILABLE:
        return {
            "success": False,
            "error": "RAG not available. Install: pip install sentence-transformers chromadb"
        }

    model = get_embedding_model()
    collection = get_chroma_collection()

    if not model or not collection:
        return {"success": False, "error": "Failed to initialize RAG components"}

    if collection.count() == 0:
        return {
            "success": False,
            "error": "Vault not indexed. Run index_vault first."
        }

    # Create query embedding
    query_embedding = model.encode([query], show_progress_bar=False)[0]

    # Build filter
    where_filter = None
    if filter_path:
        where_filter = {"path": {"$contains": filter_path}}

    # Search
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=limit * 2,  # Get extra to filter by score
        where=where_filter,
        include=["documents", "metadatas", "distances"]
    )

    # Process results
    processed = []
    seen_paths = set()

    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            # ChromaDB returns L2 distance, convert to similarity score
            distance = results["distances"][0][i]
            # For cosine distance: similarity = 1 - distance (when using cosine space)
            score = 1 - distance

            if score < min_score:
                continue

            meta = results["metadatas"][0][i]
            path = meta.get("path", "")

            # Apply tag filter
            if filter_tags:
                note_tags = json.loads(meta.get("tags", "[]"))
                if not any(t in note_tags for t in filter_tags):
                    continue

            # Group by file, show best chunk per file
            if path in seen_paths:
                continue
            seen_paths.add(path)

            processed.append({
                "path": path,
                "name": meta.get("name", ""),
                "score": round(score, 3),
                "matched_section": meta.get("header", ""),
                "snippet": results["documents"][0][i][:300] + "..." if len(results["documents"][0][i]) > 300 else results["documents"][0][i],
                "tags": json.loads(meta.get("tags", "[]"))
            })

            if len(processed) >= limit:
                break

    return {
        "query": query,
        "count": len(processed),
        "results": processed
    }


@mcp.tool()
async def build_context(
    query: str,
    max_chunks: int = 5,
    max_tokens: int = 2000,
    include_metadata: bool = True
) -> dict:
    """
    Build relevant context from the vault for a given query (RAG retrieval).

    This is the main RAG tool - it finds the most relevant note sections
    for a query and formats them as context for AI consumption.

    Args:
        query: The question or topic to find context for
        max_chunks: Maximum number of text chunks to include
        max_tokens: Approximate token limit (chars / 4)
        include_metadata: Include source paths and headers
    """
    if not RAG_AVAILABLE:
        return {
            "success": False,
            "error": "RAG not available. Install: pip install sentence-transformers chromadb"
        }

    model = get_embedding_model()
    collection = get_chroma_collection()

    if not model or not collection:
        return {"success": False, "error": "Failed to initialize RAG components"}

    if collection.count() == 0:
        return {
            "success": False,
            "error": "Vault not indexed. Run index_vault first."
        }

    # Create query embedding
    query_embedding = model.encode([query], show_progress_bar=False)[0]

    # Search for relevant chunks
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=max_chunks * 2,
        include=["documents", "metadatas", "distances"]
    )

    # Build context string
    context_parts = []
    sources = []
    total_chars = 0
    max_chars = max_tokens * 4  # Rough approximation

    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            if len(context_parts) >= max_chunks:
                break

            distance = results["distances"][0][i]
            score = 1 - distance

            # Skip low-relevance results
            if score < 0.25:
                continue

            meta = results["metadatas"][0][i]
            text = results["documents"][0][i]

            # Check token limit
            if total_chars + len(text) > max_chars:
                # Try to fit partial
                remaining = max_chars - total_chars
                if remaining > 100:
                    text = text[:remaining] + "..."
                else:
                    break

            if include_metadata:
                header = meta.get("header", "")
                source_line = f"[Source: {meta.get('path', 'unknown')}"
                if header:
                    source_line += f" - {header}"
                source_line += "]"
                context_parts.append(f"{source_line}\n{text}")
            else:
                context_parts.append(text)

            sources.append({
                "path": meta.get("path", ""),
                "section": meta.get("header", ""),
                "relevance": round(score, 3)
            })

            total_chars += len(text)

    context = "\n\n---\n\n".join(context_parts)

    return {
        "success": True,
        "query": query,
        "context": context,
        "sources": sources,
        "chunks_used": len(context_parts),
        "approximate_tokens": total_chars // 4
    }


@mcp.tool()
async def clear_index() -> dict:
    """
    Clear the RAG index completely.

    Use this if you want to rebuild the index from scratch.
    """
    if not RAG_AVAILABLE:
        return {
            "success": False,
            "error": "RAG not available"
        }

    global _chroma_collection, _chroma_client

    try:
        if _chroma_client:
            _chroma_client.delete_collection("obsidian_vault")
            _chroma_collection = None

        # Recreate empty collection
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="obsidian_vault",
            metadata={"hnsw:space": "cosine"}
        )

        return {"success": True, "message": "Index cleared"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
