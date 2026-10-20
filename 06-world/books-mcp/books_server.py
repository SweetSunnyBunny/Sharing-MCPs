"""
Books MCP Server
Read and explore EPUB books with Claude.
Tracks reading progress, bookmarks, and notes across sessions.
"""

from fastmcp import FastMCP
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import json
import zipfile
import re
from html.parser import HTMLParser

# Create the MCP Server
mcp = FastMCP("Books")

# Library directory for EPUBs
LIBRARY_DIR = Path(__file__).parent / "library"
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

# Reading progress and notes
PROGRESS_FILE = Path(__file__).parent / "reading_progress.json"
NOTES_FILE = Path(__file__).parent / "reading_notes.json"
BOOKMARKS_FILE = Path(__file__).parent / "bookmarks.json"


class HTMLStripper(HTMLParser):
    """Simple HTML to text converter."""
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
        self.in_body = False
        self.skip_tags = {'script', 'style', 'head', 'title'}
        self.current_skip = None

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.current_skip = tag
        if tag == 'body':
            self.in_body = True
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
            self.fed.append('\n')

    def handle_endtag(self, tag):
        if tag == self.current_skip:
            self.current_skip = None
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.fed.append('\n')

    def handle_data(self, data):
        if self.current_skip is None:
            self.fed.append(data)

    def get_text(self):
        text = ''.join(self.fed)
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()


def strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    stripper = HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def load_json_file(path: Path) -> Dict:
    """Load a JSON file or return empty dict."""
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {}


def save_json_file(path: Path, data: Dict):
    """Save data to JSON file."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_book_id(filename: str) -> str:
    """Generate a simple ID from filename."""
    return Path(filename).stem.lower().replace(' ', '_').replace('-', '_')


class EPUBReader:
    """Read and parse EPUB files."""

    def __init__(self, epub_path: Path):
        self.path = epub_path
        self.zipfile = None
        self.spine = []  # Ordered list of content files
        self.toc = []    # Table of contents
        self.metadata = {}

    def open(self):
        """Open the EPUB file."""
        self.zipfile = zipfile.ZipFile(self.path, 'r')
        self._parse_container()
        self._parse_opf()

    def close(self):
        """Close the EPUB file."""
        if self.zipfile:
            self.zipfile.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def _parse_container(self):
        """Find the OPF file location from container.xml."""
        try:
            container = self.zipfile.read('META-INF/container.xml').decode('utf-8')
            match = re.search(r'full-path="([^"]+)"', container)
            if match:
                self.opf_path = match.group(1)
                self.content_dir = str(Path(self.opf_path).parent)
                if self.content_dir == '.':
                    self.content_dir = ''
        except:
            # Try common locations
            for path in ['OEBPS/content.opf', 'content.opf', 'EPUB/package.opf']:
                if path in self.zipfile.namelist():
                    self.opf_path = path
                    self.content_dir = str(Path(path).parent)
                    if self.content_dir == '.':
                        self.content_dir = ''
                    break

    def _parse_opf(self):
        """Parse the OPF file for metadata and spine."""
        try:
            opf = self.zipfile.read(self.opf_path).decode('utf-8')

            # Extract metadata
            title_match = re.search(r'<dc:title[^>]*>([^<]+)</dc:title>', opf, re.IGNORECASE)
            if title_match:
                self.metadata['title'] = title_match.group(1).strip()

            author_match = re.search(r'<dc:creator[^>]*>([^<]+)</dc:creator>', opf, re.IGNORECASE)
            if author_match:
                self.metadata['author'] = author_match.group(1).strip()

            # Extract spine (reading order)
            spine_match = re.search(r'<spine[^>]*>(.*?)</spine>', opf, re.DOTALL | re.IGNORECASE)
            if spine_match:
                spine_content = spine_match.group(1)
                itemrefs = re.findall(r'idref="([^"]+)"', spine_content)

                # Build manifest lookup
                manifest = {}
                for match in re.finditer(r'<item[^>]+id="([^"]+)"[^>]+href="([^"]+)"', opf, re.IGNORECASE):
                    manifest[match.group(1)] = match.group(2)
                # Also try href before id
                for match in re.finditer(r'<item[^>]+href="([^"]+)"[^>]+id="([^"]+)"', opf, re.IGNORECASE):
                    manifest[match.group(2)] = match.group(1)

                # Build spine from itemrefs
                for idref in itemrefs:
                    if idref in manifest:
                        self.spine.append(manifest[idref])

            # Try to extract TOC
            self._parse_toc(opf)

        except Exception as e:
            self.metadata['parse_error'] = str(e)

    def _parse_toc(self, opf: str):
        """Try to parse table of contents."""
        try:
            # Look for NCX file in manifest
            ncx_match = re.search(r'<item[^>]+href="([^"]+\.ncx)"', opf, re.IGNORECASE)
            if ncx_match:
                ncx_path = ncx_match.group(1)
                if self.content_dir:
                    ncx_path = f"{self.content_dir}/{ncx_path}"

                ncx = self.zipfile.read(ncx_path).decode('utf-8')

                # Extract nav points
                for match in re.finditer(r'<navPoint[^>]*>.*?<navLabel>.*?<text>([^<]+)</text>.*?<content[^>]+src="([^"]+)"', ncx, re.DOTALL):
                    self.toc.append({
                        'title': match.group(1).strip(),
                        'href': match.group(2).split('#')[0]  # Remove fragment
                    })
        except:
            pass

    def get_chapter_count(self) -> int:
        """Get total number of chapters/sections."""
        return len(self.spine)

    def get_chapter(self, index: int) -> Optional[str]:
        """Get chapter content by index (0-based)."""
        if index < 0 or index >= len(self.spine):
            return None

        try:
            href = self.spine[index]
            if self.content_dir:
                href = f"{self.content_dir}/{href}"

            content = self.zipfile.read(href).decode('utf-8')
            return strip_html(content)
        except:
            return None

    def get_chapter_title(self, index: int) -> str:
        """Try to get a chapter title."""
        if index < len(self.toc):
            return self.toc[index]['title']
        return f"Chapter {index + 1}"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search all chapters for a query."""
        results = []
        query_lower = query.lower()

        for i, href in enumerate(self.spine):
            content = self.get_chapter(i)
            if content and query_lower in content.lower():
                # Find the matching context
                idx = content.lower().find(query_lower)
                start = max(0, idx - 100)
                end = min(len(content), idx + len(query) + 100)
                context = content[start:end]
                if start > 0:
                    context = "..." + context
                if end < len(content):
                    context = context + "..."

                results.append({
                    'chapter': i + 1,
                    'chapter_title': self.get_chapter_title(i),
                    'context': context
                })

                if len(results) >= max_results:
                    break

        return results


@mcp.tool()
def list_books() -> Dict[str, Any]:
    """
    List all EPUB books available in the library.

    Returns:
        List of books with titles, authors, and reading progress
    """
    books = []
    progress_data = load_json_file(PROGRESS_FILE)

    for epub_file in LIBRARY_DIR.glob("*.epub"):
        book_id = get_book_id(epub_file.name)

        try:
            with EPUBReader(epub_file) as reader:
                book_info = {
                    'id': book_id,
                    'filename': epub_file.name,
                    'title': reader.metadata.get('title', epub_file.stem),
                    'author': reader.metadata.get('author', 'Unknown'),
                    'chapters': reader.get_chapter_count(),
                    'has_toc': len(reader.toc) > 0
                }

                # Add reading progress
                if book_id in progress_data:
                    prog = progress_data[book_id]
                    book_info['current_chapter'] = prog.get('chapter', 1)
                    book_info['last_read'] = prog.get('last_read', '')
                    book_info['percent_complete'] = round(
                        (prog.get('chapter', 1) / reader.get_chapter_count()) * 100
                    ) if reader.get_chapter_count() > 0 else 0

                books.append(book_info)
        except Exception as e:
            books.append({
                'id': book_id,
                'filename': epub_file.name,
                'error': f"Could not read: {str(e)}"
            })

    return {
        'success': True,
        'book_count': len(books),
        'library_path': str(LIBRARY_DIR),
        'books': books
    }


@mcp.tool()
def get_book_info(book: str) -> Dict[str, Any]:
    """
    Get detailed information about a book.

    Args:
        book: Book filename or ID

    Returns:
        Book metadata, table of contents, and reading progress
    """
    # Find the book file
    epub_file = None
    for f in LIBRARY_DIR.glob("*.epub"):
        if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
            epub_file = f
            break

    if not epub_file:
        return {
            'success': False,
            'error': f"Book not found: {book}",
            'available': [f.name for f in LIBRARY_DIR.glob("*.epub")]
        }

    try:
        with EPUBReader(epub_file) as reader:
            book_id = get_book_id(epub_file.name)
            progress_data = load_json_file(PROGRESS_FILE)
            notes_data = load_json_file(NOTES_FILE)
            bookmarks_data = load_json_file(BOOKMARKS_FILE)

            result = {
                'success': True,
                'id': book_id,
                'filename': epub_file.name,
                'title': reader.metadata.get('title', epub_file.stem),
                'author': reader.metadata.get('author', 'Unknown'),
                'chapters': reader.get_chapter_count(),
                'table_of_contents': reader.toc[:20] if reader.toc else []
            }

            # Add progress
            if book_id in progress_data:
                result['reading_progress'] = progress_data[book_id]

            # Add notes count
            if book_id in notes_data:
                result['notes_count'] = len(notes_data[book_id])

            # Add bookmarks
            if book_id in bookmarks_data:
                result['bookmarks'] = bookmarks_data[book_id]

            return result

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@mcp.tool()
def read_chapter(
    book: str,
    chapter: int = None,
    continue_reading: bool = True
) -> Dict[str, Any]:
    """
    Read a chapter from a book.

    Args:
        book: Book filename or ID
        chapter: Chapter number (1-based). If not specified, continues from last position.
        continue_reading: If True and no chapter specified, picks up where you left off

    Returns:
        The chapter content, title, and navigation info
    """
    # Find the book file
    epub_file = None
    for f in LIBRARY_DIR.glob("*.epub"):
        if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
            epub_file = f
            break

    if not epub_file:
        return {
            'success': False,
            'error': f"Book not found: {book}"
        }

    try:
        with EPUBReader(epub_file) as reader:
            book_id = get_book_id(epub_file.name)
            progress_data = load_json_file(PROGRESS_FILE)

            # Determine which chapter to read
            if chapter is None and continue_reading and book_id in progress_data:
                chapter = progress_data[book_id].get('chapter', 1)
            elif chapter is None:
                chapter = 1

            # Convert to 0-based index
            chapter_idx = chapter - 1
            total_chapters = reader.get_chapter_count()

            if chapter_idx < 0 or chapter_idx >= total_chapters:
                return {
                    'success': False,
                    'error': f"Chapter {chapter} not found. Book has {total_chapters} chapters."
                }

            content = reader.get_chapter(chapter_idx)
            if not content:
                return {
                    'success': False,
                    'error': f"Could not read chapter {chapter}"
                }

            # Update reading progress
            progress_data[book_id] = {
                'chapter': chapter,
                'last_read': datetime.now().isoformat(),
                'book_title': reader.metadata.get('title', epub_file.stem)
            }
            save_json_file(PROGRESS_FILE, progress_data)

            return {
                'success': True,
                'book_id': book_id,
                'title': reader.metadata.get('title', epub_file.stem),
                'author': reader.metadata.get('author', 'Unknown'),
                'chapter': chapter,
                'chapter_title': reader.get_chapter_title(chapter_idx),
                'total_chapters': total_chapters,
                'has_previous': chapter > 1,
                'has_next': chapter < total_chapters,
                'content': content,
                'word_count': len(content.split())
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@mcp.tool()
def search_book(book: str, query: str) -> Dict[str, Any]:
    """
    Search within a book for specific text.

    Useful for finding passages, quotes, or returning to something mentioned earlier.

    Args:
        book: Book filename or ID
        query: Text to search for

    Returns:
        Matching passages with chapter locations
    """
    # Find the book file
    epub_file = None
    for f in LIBRARY_DIR.glob("*.epub"):
        if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
            epub_file = f
            break

    if not epub_file:
        return {
            'success': False,
            'error': f"Book not found: {book}"
        }

    try:
        with EPUBReader(epub_file) as reader:
            results = reader.search(query)

            return {
                'success': True,
                'book': reader.metadata.get('title', epub_file.stem),
                'query': query,
                'match_count': len(results),
                'matches': results
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@mcp.tool()
def add_bookmark(
    book: str,
    chapter: int,
    note: str = None
) -> Dict[str, Any]:
    """
    Bookmark a chapter in a book.

    Args:
        book: Book filename or ID
        chapter: Chapter number to bookmark
        note: Optional note about why this is bookmarked

    Returns:
        Confirmation of bookmark
    """
    # Find the book
    epub_file = None
    for f in LIBRARY_DIR.glob("*.epub"):
        if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
            epub_file = f
            break

    if not epub_file:
        return {
            'success': False,
            'error': f"Book not found: {book}"
        }

    book_id = get_book_id(epub_file.name)
    bookmarks_data = load_json_file(BOOKMARKS_FILE)

    if book_id not in bookmarks_data:
        bookmarks_data[book_id] = []

    bookmark = {
        'chapter': chapter,
        'added': datetime.now().isoformat()
    }
    if note:
        bookmark['note'] = note

    # Check if already bookmarked
    existing = next((b for b in bookmarks_data[book_id] if b['chapter'] == chapter), None)
    if existing:
        existing.update(bookmark)
    else:
        bookmarks_data[book_id].append(bookmark)

    save_json_file(BOOKMARKS_FILE, bookmarks_data)

    return {
        'success': True,
        'book_id': book_id,
        'bookmark': bookmark,
        'total_bookmarks': len(bookmarks_data[book_id])
    }


@mcp.tool()
def add_reading_note(
    book: str,
    chapter: int,
    note: str,
    quote: str = None
) -> Dict[str, Any]:
    """
    Add a note about something in the book.

    Capture thoughts, reactions, or discussion points while reading.

    Args:
        book: Book filename or ID
        chapter: Chapter the note is about
        note: Your thoughts or observations
        quote: Optional quote from the book this relates to

    Returns:
        Confirmation of note saved
    """
    # Find the book
    epub_file = None
    for f in LIBRARY_DIR.glob("*.epub"):
        if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
            epub_file = f
            break

    if not epub_file:
        return {
            'success': False,
            'error': f"Book not found: {book}"
        }

    book_id = get_book_id(epub_file.name)
    notes_data = load_json_file(NOTES_FILE)

    if book_id not in notes_data:
        notes_data[book_id] = []

    reading_note = {
        'chapter': chapter,
        'note': note,
        'added': datetime.now().isoformat()
    }
    if quote:
        reading_note['quote'] = quote

    notes_data[book_id].append(reading_note)
    save_json_file(NOTES_FILE, notes_data)

    return {
        'success': True,
        'book_id': book_id,
        'note': reading_note,
        'total_notes': len(notes_data[book_id])
    }


@mcp.tool()
def get_reading_notes(book: str = None) -> Dict[str, Any]:
    """
    Get all reading notes, optionally for a specific book.

    Args:
        book: Optional book filename or ID to filter by

    Returns:
        All notes, organized by book and chapter
    """
    notes_data = load_json_file(NOTES_FILE)

    if book:
        # Find matching book
        book_id = None
        for f in LIBRARY_DIR.glob("*.epub"):
            if book.lower() in f.name.lower() or book.lower() == get_book_id(f.name):
                book_id = get_book_id(f.name)
                break

        if book_id and book_id in notes_data:
            return {
                'success': True,
                'book_id': book_id,
                'notes': notes_data[book_id]
            }
        else:
            return {
                'success': True,
                'book_id': book_id,
                'notes': []
            }
    else:
        # Return all notes
        return {
            'success': True,
            'books_with_notes': list(notes_data.keys()),
            'all_notes': notes_data
        }


@mcp.tool()
def summarize_chapter(book: str, chapter: int) -> Dict[str, Any]:
    """
    Get a chapter's content broken into sections for discussion.

    Args:
        book: Book filename or ID
        chapter: Chapter number

    Returns:
        Chapter content broken into digestible sections
    """
    # Get the chapter content first
    result = read_chapter(book, chapter, continue_reading=False)

    if not result.get('success'):
        return result

    content = result['content']

    # Break into paragraphs/sections
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

    # Group into sections of ~500 words each
    sections = []
    current_section = []
    current_words = 0
    section_limit = 500

    for para in paragraphs:
        para_words = len(para.split())
        if current_words + para_words > section_limit and current_section:
            sections.append({
                'section': len(sections) + 1,
                'text': '\n\n'.join(current_section),
                'word_count': current_words
            })
            current_section = [para]
            current_words = para_words
        else:
            current_section.append(para)
            current_words += para_words

    # Add final section
    if current_section:
        sections.append({
            'section': len(sections) + 1,
            'text': '\n\n'.join(current_section),
            'word_count': current_words
        })

    return {
        'success': True,
        'book_id': result['book_id'],
        'title': result['title'],
        'chapter': chapter,
        'chapter_title': result['chapter_title'],
        'total_sections': len(sections),
        'total_words': result['word_count'],
        'sections': sections
    }


if __name__ == "__main__":
    mcp.run()
