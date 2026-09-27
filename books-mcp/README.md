# Books MCP

Read unencrypted EPUB books chapter by chapter, search text, and save reading notes and bookmarks locally. No account or API key is needed.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\books-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\books-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Add a book

```powershell
New-Item -ItemType Directory -Force .\library
```

Copy an EPUB you have permission to read into `library`. Use a real `.epub` file, not a renamed PDF or a DRM-protected store download. An empty library is valid; it simply returns no books. Bookmarks, notes and progress are created beside the server and should remain private.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\run_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "books-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/books-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/books-mcp/run_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `list_books`. Your EPUB should appear. Next call `get_book_info` with the returned book ID, then `read_chapter` for chapter 1.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Book absent:** confirm it is directly inside this package's `library` directory and has an `.epub` extension.
- **Unreadable chapters:** try a small unencrypted EPUB first; DRM-protected or unusually structured books may not parse.
- **Progress does not save:** use a writable folder rather than Program Files.

## Optional local HTTP mode

For a client supporting Streamable HTTP, leave this terminal running and use URL `http://127.0.0.1:8770/mcp`:

```powershell
.\.venv\Scripts\python.exe .\run_server.py --transport streamable-http --host 127.0.0.1 --port 8770
```

A browser is not an MCP client; a plain browser request may return an error even when the server works.

## Available Tools

| Tool | Description |
|------|-------------|
| `list_books` | List all EPUBs in the library |
| `get_book_info` | Get metadata, TOC, and progress for a book |
| `read_chapter` | Read a chapter (auto-continues from last position) |
| `search_book` | Search within a book for text |
| `add_bookmark` | Bookmark a chapter with an optional note |
| `add_reading_note` | Save thoughts or observations about a chapter |
| `get_reading_notes` | Review all your reading notes |
| `summarize_chapter` | Break a chapter into sections for discussion |

---

## Data Storage

Reading progress, bookmarks, and notes are stored as JSON files alongside the server:

- `reading_progress.json` - Which chapter you're on per book
- `bookmarks.json` - Your bookmarked chapters
- `reading_notes.json` - Your notes and observations

These persist across sessions so you always pick up where you left off.

---
