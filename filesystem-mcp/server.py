"""
Filesystem MCP Server - Standalone Edition

Provides remote file access for Claude across all contexts.
Access your computer's files from anywhere - phone, other computers, etc.

Setup:
    1. Install cloudflared and set up a tunnel (see README)
    2. Run: python run_server.py
    3. Connect via your tunnel URL

Built with love for sharing.
"""

import json
import os
import shutil
import base64
import mimetypes
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from pydantic import Field

# Initialize FastMCP server
mcp = FastMCP("filesystem")

# File extensions that should be read as text
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.yaml', '.yml',
    '.xml', '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.c', '.cpp', '.h', '.hpp', '.cs', '.java', '.go', '.rs', '.rb', '.php',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
    '.sql', '.graphql', '.toml', '.ini', '.cfg', '.conf', '.env',
    '.gitignore', '.dockerignore', '.editorconfig',
    '.csv', '.tsv', '.log', '.svg',
}

# Image extensions (will be returned for visual viewing)
IMAGE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.tiff', '.tif'
}

# Max file size for reading (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


def get_file_type(path: Path) -> str:
    """Determine if file is text, image, or binary."""
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return "text"
    elif suffix in IMAGE_EXTENSIONS:
        return "image"
    else:
        return "binary"


def format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(timestamp: float) -> str:
    """Format timestamp as readable date."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# DIRECTORY TOOLS
# =============================================================================

@mcp.tool(name="fs_list_directory")
async def list_directory(
    path: str = Field(..., description="Directory path to list (e.g., 'C:\\Users' or '/home')"),
    show_hidden: bool = Field(False, description="Show hidden files (starting with .)"),
    recursive: bool = Field(False, description="List recursively (max 2 levels)"),
    pattern: Optional[str] = Field(None, description="Filter by glob pattern (e.g., '*.py')")
) -> Dict[str, Any]:
    """
    List contents of a directory.
    """
    try:
        dir_path = Path(path)

        if not dir_path.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        if not dir_path.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        items = []

        if pattern:
            if recursive:
                entries = list(dir_path.rglob(pattern))[:500]
            else:
                entries = list(dir_path.glob(pattern))[:500]
        else:
            if recursive:
                entries = []
                for root, dirs, files in os.walk(dir_path):
                    depth = str(root).count(os.sep) - str(dir_path).count(os.sep)
                    if depth > 2:
                        continue
                    for name in dirs + files:
                        entries.append(Path(root) / name)
                entries = entries[:500]
            else:
                entries = list(dir_path.iterdir())[:500]

        for entry in entries:
            try:
                if not show_hidden and entry.name.startswith('.'):
                    continue

                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "size": format_size(stat.st_size) if not entry.is_dir() else None,
                    "size_bytes": stat.st_size if not entry.is_dir() else None,
                    "modified": format_time(stat.st_mtime),
                    "type": get_file_type(entry) if not entry.is_dir() else "directory",
                })
            except (PermissionError, OSError):
                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "error": "Permission denied",
                })

        items.sort(key=lambda x: (not x.get('is_dir', False), x.get('name', '').lower()))

        return {
            "success": True,
            "path": str(dir_path.resolve()),
            "count": len(items),
            "items": items,
        }
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_create_directory")
async def create_directory(
    path: str = Field(..., description="Directory path to create"),
    parents: bool = Field(True, description="Create parent directories if needed")
) -> Dict[str, Any]:
    """Create a new directory."""
    try:
        dir_path = Path(path)
        dir_path.mkdir(parents=parents, exist_ok=True)
        return {
            "success": True,
            "path": str(dir_path.resolve()),
            "message": "Directory created",
        }
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# FILE READING TOOLS
# =============================================================================

@mcp.tool(name="fs_read_file")
async def read_file(
    path: str = Field(..., description="File path to read"),
    encoding: str = Field("utf-8", description="Text encoding (for text files)"),
    start_line: Optional[int] = Field(None, description="Start from this line (1-indexed)"),
    num_lines: Optional[int] = Field(None, description="Number of lines to read")
) -> Dict[str, Any]:
    """
    Read a file's contents. Text files return content directly.
    Images are displayed visually. Binary files return metadata only.
    """
    try:
        file_path = Path(path)

        if not file_path.exists():
            return {"success": False, "error": f"File does not exist: {path}"}

        if not file_path.is_file():
            return {"success": False, "error": f"Not a file: {path}"}

        stat = file_path.stat()
        file_type = get_file_type(file_path)

        if stat.st_size > MAX_FILE_SIZE:
            return {
                "success": False,
                "error": f"File too large: {format_size(stat.st_size)} (max {format_size(MAX_FILE_SIZE)})"
            }

        result = {
            "success": True,
            "path": str(file_path.resolve()),
            "name": file_path.name,
            "type": file_type,
            "size": format_size(stat.st_size),
            "size_bytes": stat.st_size,
            "modified": format_time(stat.st_mtime),
        }

        if file_type == "text":
            with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                if start_line or num_lines:
                    lines = f.readlines()
                    start = (start_line - 1) if start_line else 0
                    end = (start + num_lines) if num_lines else None
                    content = ''.join(lines[start:end])
                    result["total_lines"] = len(lines)
                    result["showing_lines"] = f"{start+1}-{min(end or len(lines), len(lines))}"
                else:
                    content = f.read()
                result["content"] = content

        elif file_type == "image":
            # Return as FastMCP Image so Claude can actually see it
            return Image(path=str(file_path.resolve()))

        else:  # binary
            result["message"] = "Binary file - content not displayed"
            with open(file_path, 'rb') as f:
                header = f.read(32)
            result["header_hex"] = header.hex()

        return result
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except UnicodeDecodeError as e:
        return {"success": False, "error": f"Encoding error: {e}. Try a different encoding."}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_read_image")
async def read_image(
    path: str = Field(..., description="Image file path to read")
):
    """
    Read an image file and display it visually.
    This returns the actual image so Claude can see it.
    """
    try:
        file_path = Path(path)

        if not file_path.exists():
            return {"success": False, "error": f"File does not exist: {path}"}

        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return {"success": False, "error": f"Not a recognized image format: {file_path.suffix}"}

        stat = file_path.stat()

        if stat.st_size > MAX_FILE_SIZE:
            return {"success": False, "error": f"Image too large: {format_size(stat.st_size)}"}

        return Image(path=str(file_path.resolve()))
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_get_file_info")
async def get_file_info(
    path: str = Field(..., description="File or directory path")
) -> Dict[str, Any]:
    """Get detailed information about a file or directory."""
    try:
        target = Path(path)

        if not target.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        stat = target.stat()

        result = {
            "success": True,
            "path": str(target.resolve()),
            "name": target.name,
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
            "size": format_size(stat.st_size),
            "size_bytes": stat.st_size,
            "created": format_time(stat.st_ctime),
            "modified": format_time(stat.st_mtime),
            "accessed": format_time(stat.st_atime),
        }

        if target.is_file():
            result["type"] = get_file_type(target)
            result["extension"] = target.suffix
            mime = mimetypes.guess_type(str(target))[0]
            if mime:
                result["mime_type"] = mime

        if target.is_dir():
            try:
                contents = list(target.iterdir())
                result["item_count"] = len(contents)
                result["subdirs"] = sum(1 for c in contents if c.is_dir())
                result["files"] = sum(1 for c in contents if c.is_file())
            except PermissionError:
                result["item_count"] = "unknown (permission denied)"

        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# FILE WRITING TOOLS
# =============================================================================

@mcp.tool(name="fs_write_file")
async def write_file(
    path: str = Field(..., description="File path to write"),
    content: str = Field(..., description="Content to write"),
    encoding: str = Field("utf-8", description="Text encoding"),
    create_dirs: bool = Field(True, description="Create parent directories if needed"),
    append: bool = Field(False, description="Append to file instead of overwriting")
) -> Dict[str, Any]:
    """Write content to a file."""
    try:
        file_path = Path(path)

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        mode = 'a' if append else 'w'
        with open(file_path, mode, encoding=encoding) as f:
            f.write(content)

        stat = file_path.stat()

        return {
            "success": True,
            "path": str(file_path.resolve()),
            "size": format_size(stat.st_size),
            "action": "appended" if append else "written",
        }
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_write_binary")
async def write_binary(
    path: str = Field(..., description="File path to write"),
    base64_content: str = Field(..., description="Base64 encoded binary content"),
    create_dirs: bool = Field(True, description="Create parent directories if needed")
) -> Dict[str, Any]:
    """Write binary content (like images) to a file from base64."""
    try:
        file_path = Path(path)

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        binary_data = base64.b64decode(base64_content)

        with open(file_path, 'wb') as f:
            f.write(binary_data)

        stat = file_path.stat()

        return {
            "success": True,
            "path": str(file_path.resolve()),
            "size": format_size(stat.st_size),
            "action": "written",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# FILE OPERATIONS
# =============================================================================

@mcp.tool(name="fs_copy")
async def copy_file(
    source: str = Field(..., description="Source file or directory path"),
    destination: str = Field(..., description="Destination path")
) -> Dict[str, Any]:
    """Copy a file or directory."""
    try:
        src = Path(source)
        dst = Path(destination)

        if not src.exists():
            return {"success": False, "error": f"Source does not exist: {source}"}

        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        return {
            "success": True,
            "source": str(src.resolve()),
            "destination": str(dst.resolve()),
            "action": "copied",
        }
    except PermissionError:
        return {"success": False, "error": "Permission denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_move")
async def move_file(
    source: str = Field(..., description="Source file or directory path"),
    destination: str = Field(..., description="Destination path")
) -> Dict[str, Any]:
    """Move a file or directory."""
    try:
        src = Path(source)
        dst = Path(destination)

        if not src.exists():
            return {"success": False, "error": f"Source does not exist: {source}"}

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

        return {
            "success": True,
            "source": str(src),
            "destination": str(dst.resolve()),
            "action": "moved",
        }
    except PermissionError:
        return {"success": False, "error": "Permission denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_delete")
async def delete_file(
    path: str = Field(..., description="File or directory path to delete"),
    recursive: bool = Field(False, description="Delete directory contents recursively")
) -> Dict[str, Any]:
    """Delete a file or directory."""
    try:
        target = Path(path)

        if not target.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()

        return {
            "success": True,
            "path": str(target),
            "action": "deleted",
        }
    except OSError as e:
        if "not empty" in str(e).lower():
            return {"success": False, "error": "Directory not empty. Use recursive=True to delete."}
        return {"success": False, "error": str(e)}
    except PermissionError:
        return {"success": False, "error": "Permission denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# SEARCH TOOLS
# =============================================================================

@mcp.tool(name="fs_search")
async def search_files(
    path: str = Field(..., description="Directory to search in"),
    pattern: str = Field(..., description="Glob pattern (e.g., '*.py', '**/*.jpg')"),
    max_results: int = Field(100, description="Maximum results to return")
) -> Dict[str, Any]:
    """Search for files matching a glob pattern."""
    try:
        search_path = Path(path)

        if not search_path.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        if not search_path.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        matches = []
        for match in search_path.glob(pattern):
            if len(matches) >= max_results:
                break
            try:
                stat = match.stat()
                matches.append({
                    "name": match.name,
                    "path": str(match),
                    "is_dir": match.is_dir(),
                    "size": format_size(stat.st_size) if not match.is_dir() else None,
                    "modified": format_time(stat.st_mtime),
                    "type": get_file_type(match) if not match.is_dir() else "directory",
                })
            except (PermissionError, OSError):
                continue

        return {
            "success": True,
            "search_path": str(search_path.resolve()),
            "pattern": pattern,
            "count": len(matches),
            "truncated": len(matches) >= max_results,
            "matches": matches,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_search_content")
async def search_content(
    path: str = Field(..., description="Directory to search in"),
    text: str = Field(..., description="Text to search for"),
    file_pattern: str = Field("*", description="File pattern to search in (e.g., '*.py')"),
    case_sensitive: bool = Field(False, description="Case-sensitive search"),
    max_results: int = Field(50, description="Maximum files to return")
) -> Dict[str, Any]:
    """Search for text content within files."""
    try:
        search_path = Path(path)

        if not search_path.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        search_text = text if case_sensitive else text.lower()
        results = []
        files_searched = 0

        for file_path in search_path.rglob(file_pattern):
            if len(results) >= max_results:
                break

            if not file_path.is_file():
                continue

            if get_file_type(file_path) != "text":
                continue

            try:
                files_searched += 1
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                compare_content = content if case_sensitive else content.lower()

                if search_text in compare_content:
                    lines = content.split('\n')
                    matches = []
                    for i, line in enumerate(lines):
                        compare_line = line if case_sensitive else line.lower()
                        if search_text in compare_line:
                            matches.append({
                                "line_number": i + 1,
                                "content": line[:200],
                            })
                            if len(matches) >= 5:
                                break

                    results.append({
                        "path": str(file_path),
                        "name": file_path.name,
                        "match_count": len(matches),
                        "matches": matches,
                    })
            except (PermissionError, OSError):
                continue

        return {
            "success": True,
            "search_path": str(search_path.resolve()),
            "search_text": text,
            "files_searched": files_searched,
            "files_matched": len(results),
            "truncated": len(results) >= max_results,
            "results": results,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# SPECIAL TOOLS
# =============================================================================

@mcp.tool(name="fs_list_drives")
async def list_drives() -> Dict[str, Any]:
    """List available drives (Windows) or mount points (Linux/Mac)."""
    try:
        import string
        drives = []

        # Windows drives
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                try:
                    total, used, free = shutil.disk_usage(drive)
                    drives.append({
                        "drive": f"{letter}:",
                        "path": drive,
                        "total": format_size(total),
                        "used": format_size(used),
                        "free": format_size(free),
                        "percent_used": f"{(used/total)*100:.1f}%",
                    })
                except (PermissionError, OSError):
                    drives.append({
                        "drive": f"{letter}:",
                        "path": drive,
                        "error": "Unable to read drive info",
                    })

        # If no Windows drives found, try Unix root
        if not drives and os.path.exists('/'):
            try:
                total, used, free = shutil.disk_usage('/')
                drives.append({
                    "drive": "/",
                    "path": "/",
                    "total": format_size(total),
                    "used": format_size(used),
                    "free": format_size(free),
                    "percent_used": f"{(used/total)*100:.1f}%",
                })
            except (PermissionError, OSError):
                pass

        return {
            "success": True,
            "drives": drives,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="fs_get_recent_files")
async def get_recent_files(
    path: str = Field(..., description="Directory to search"),
    hours: int = Field(24, description="Files modified in the last N hours"),
    pattern: str = Field("*", description="File pattern to match"),
    max_results: int = Field(50, description="Maximum results")
) -> Dict[str, Any]:
    """Find recently modified files in a directory."""
    try:
        search_path = Path(path)

        if not search_path.exists():
            return {"success": False, "error": f"Path does not exist: {path}"}

        cutoff = datetime.now().timestamp() - (hours * 3600)
        recent = []

        for file_path in search_path.rglob(pattern):
            if not file_path.is_file():
                continue

            try:
                stat = file_path.stat()
                if stat.st_mtime >= cutoff:
                    recent.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "size": format_size(stat.st_size),
                        "modified": format_time(stat.st_mtime),
                        "modified_timestamp": stat.st_mtime,
                        "type": get_file_type(file_path),
                    })
            except (PermissionError, OSError):
                continue

        recent.sort(key=lambda x: x.get('modified_timestamp', 0), reverse=True)
        recent = recent[:max_results]

        for item in recent:
            del item['modified_timestamp']

        return {
            "success": True,
            "search_path": str(search_path.resolve()),
            "hours_back": hours,
            "count": len(recent),
            "files": recent,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
