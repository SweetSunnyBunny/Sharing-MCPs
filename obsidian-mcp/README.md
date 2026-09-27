# Obsidian MCP

Read, write and search a local Obsidian vault. A vault is an ordinary folder of Markdown notes. Obsidian does not need to stay open; no Obsidian plugin, cloud account, or API key is required.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\obsidian-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\obsidian-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Point it at a vault

Find the real folder containing your vault's `.md` files. For an empty practice vault:

```powershell
New-Item -ItemType Directory -Force "C:\Notes\PracticeVault"
Set-Content -LiteralPath "C:\Notes\PracticeVault\Welcome.md" -Value "This is a practice note."
$env:OBSIDIAN_VAULT_PATH = "C:\Notes\PracticeVault"
```

Use your real vault path in both this command and the client JSON below. Do not choose the Obsidian application folder or its `.obsidian` settings directory. This package does not load a `.env` file automatically.

Installing `requirements.txt` includes the optional semantic-search libraries as well as ordinary note tools. Initial semantic indexing may download model weights and take time. For ordinary note tools alone, the minimum install is `fastmcp pyyaml`; semantic tools then report unavailable until their extra libraries are installed.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "obsidian-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/obsidian-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/obsidian-mcp/server.py"
      ],
      "env": {
        "OBSIDIAN_VAULT_PATH": "C:/Notes/PracticeVault"
      }
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `get_vault_path`, then `list_notes`. Confirm the folder is correct and `Welcome.md` appears. `read_note` takes a path relative to the vault, for example `Welcome.md`.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Wrong/empty vault:** correct `OBSIDIAN_VAULT_PATH` in the client `env`, then restart the client.
- **Path outside vault:** tool note paths are relative to the configured vault; do not use `..` to reach elsewhere.
- **Semantic search empty:** call `rag_status`, then `index_vault` before `semantic_search`. Normal text search does not need the index.
- **Model download fails:** check internet/storage; regular read/write tools can still be used independently.

## Available Tools

### Notes
| Tool | Description |
|------|-------------|
| `read_note` | Read a note with content, links, and tags |
| `write_note` | Create or update a note |
| `append_to_note` | Append content to existing note |
| `delete_note` | Delete a note |
| `move_note` | Move or rename a note |
| `list_notes` | List notes in a directory |

### Folders
| Tool | Description |
|------|-------------|
| `list_folders` | List folders in the vault |
| `create_folder` | Create a new folder |

### Search
| Tool | Description |
|------|-------------|
| `search_notes` | Full-text search |
| `search_by_tag` | Find notes with a tag |
| `get_recent_notes` | Recently modified notes |
| `list_tags` | All tags with counts |

### RAG / Semantic Search
| Tool | Description |
|------|-------------|
| `rag_status` | Check RAG availability and index status |
| `index_vault` | Build embeddings for semantic search |
| `semantic_search` | Search by meaning, not just keywords |
| `build_context` | Auto-retrieve relevant context for a query |
| `clear_index` | Reset the RAG index |

### Links
| Tool | Description |
|------|-------------|
| `get_backlinks` | Notes linking to a note |
| `get_outgoing_links` | Links from a note |

### Frontmatter
| Tool | Description |
|------|-------------|
| `get_frontmatter` | Get note metadata |
| `update_frontmatter` | Update metadata fields |

### Templates & Daily Notes
| Tool | Description |
|------|-------------|
| `list_templates` | Available templates |
| `create_from_template` | Create note from template |
| `create_daily_note` | Create/get daily note |
| `add_journal_entry` | Add timestamped entry |

### Vault
| Tool | Description |
|------|-------------|
| `get_vault_stats` | Vault statistics |
| `get_vault_path` | Current vault path |

---

## Configuration

You can configure these via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSIDIAN_VAULT_PATH` | `C:/Obsidian/MyVault` | Path to your vault |
| `OBSIDIAN_TEMPLATES_FOLDER` | `Templates` | Templates folder name |
| `OBSIDIAN_DAILY_FOLDER` | `Daily Notes` | Daily notes folder name |
| `OBSIDIAN_RAG_INDEX` | `{vault}/.obsidian/rag_index` | Where to store the search index |
| `OBSIDIAN_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence transformer model |
| `OBSIDIAN_CHUNK_SIZE` | `500` | Characters per chunk |
| `OBSIDIAN_CHUNK_OVERLAP` | `50` | Overlap between chunks |

---
