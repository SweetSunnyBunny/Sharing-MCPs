# Obsidian MCP Server

Let Claude access and manage your Obsidian vault! Read, write, search, and organize your notes using natural language.

## What It Does

This MCP gives Claude full access to your Obsidian vault:

- **Read & Write Notes** - Create, edit, and delete markdown files
- **Search** - Full-text search, tag search, find recent notes
- **RAG Search** - Semantic search using AI embeddings (find by meaning, not keywords!)
- **Wiki Links** - Find backlinks, check broken links
- **Frontmatter** - Read and update YAML metadata
- **Templates** - Create notes from templates
- **Daily Notes** - Create and append to daily notes
- **Journal Entries** - Add timestamped entries

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Connect to Claude Desktop

Add this to your Claude Desktop config file:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows (use forward slashes - easiest!):**
```json
{
  "mcpServers": {
    "obsidian": {
      "command": "python",
      "args": ["C:/path/to/obsidian-mcp/server.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "C:/Users/YourName/Documents/ObsidianVault"
      }
    }
  }
}
```

**Mac/Linux:**
```json
{
  "mcpServers": {
    "obsidian": {
      "command": "python3",
      "args": ["/path/to/obsidian-mcp/server.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/Users/YourName/Documents/ObsidianVault"
      }
    }
  }
}
```

**Important:**
- Replace the paths with your actual locations
- Windows users: use forward slashes (`/`) to avoid JSON escaping issues
- If you must use backslashes on Windows, double them: `C:\\Users\\...`
- Mac/Linux typically uses `python3` instead of `python`

### 3. Restart Claude Desktop

Close and reopen Claude Desktop. The Obsidian tools should now be available!

---

## Alternative: HTTP Server Mode

If you want to run the server separately (useful for remote access or multiple clients):

### 1. Set Your Vault Path

**Windows:**
```batch
set OBSIDIAN_VAULT_PATH=C:\Users\YourName\Documents\ObsidianVault
```

**Mac/Linux:**
```bash
export OBSIDIAN_VAULT_PATH=/path/to/your/vault
```

### 2. Run the HTTP Server

```bash
python run_server.py
```

The server runs on `http://localhost:8080/mcp`

### 3. Connect Claude (HTTP mode)

```json
{
  "mcpServers": {
    "obsidian": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**Note:** URL-based config requires the server to be running before Claude connects. The stdio mode (first method) is simpler for local use.

---

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

## RAG / Semantic Search

RAG (Retrieval-Augmented Generation) lets Claude search your vault by **meaning**, not just keywords. Ask "notes about productivity" and it finds relevant notes even if they don't contain that exact word.

### Setup

Install the optional dependencies:

```bash
pip install sentence-transformers chromadb
```

### First-Time Indexing

Before semantic search works, you need to index your vault:

```
"Index my vault for semantic search"
→ Claude runs index_vault() to create embeddings
```

This only needs to be done once. The index is stored in your vault's `.obsidian` folder and persists across restarts. Re-running `index_vault` automatically skips unchanged files.

### How It Works

1. **index_vault** - Splits your notes into chunks, creates AI embeddings for each
2. **semantic_search** - Finds chunks similar to your query by meaning
3. **build_context** - Automatically assembles relevant context for Claude to use

### Example Usage

**"Find notes related to my morning routine"**
```
Claude uses semantic_search() - finds notes about habits,
daily rituals, wake-up schedules, etc. even without exact matches
```

**"What have I written about machine learning?"**
```
Claude uses build_context() to gather relevant snippets,
then summarizes what's in your vault about ML
```

### Performance Notes

- First indexing takes a bit (creating embeddings for all notes)
- Subsequent re-indexes are fast (skips unchanged files)
- The `all-MiniLM-L6-v2` model runs locally on CPU, no API keys needed
- Index size is roughly 10-20% of your vault size

---

## Example Conversations

**"What did I write about yesterday?"**
```
Claude uses get_recent_notes() to find recent files,
then read_note() to show you the content.
```

**"Create a new note about project ideas"**
```
Claude uses write_note() to create "Project Ideas.md"
with the content you discuss.
```

**"Find all notes tagged #important"**
```
Claude uses search_by_tag("#important") to list matching notes.
```

**"Add a journal entry about today's progress"**
```
Claude uses add_journal_entry() to append a timestamped
entry to today's daily note.
```

---

## Remote Access (Optional)

To access your vault from Claude on your phone, set up a Cloudflare Tunnel:

1. Get a domain (~$5/year)
2. Install cloudflared
3. Create a tunnel pointing to `http://localhost:8080`
4. Update your MCP settings with the tunnel URL

See the Filesystem MCP README for detailed tunnel setup instructions.

---

## Security Notes

- This server has full read/write access to your vault
- Only expose via a private tunnel you control
- Consider backing up your vault regularly

---

## Troubleshooting

### "Note not found"
- Check the path is relative to your vault root
- The `.md` extension is added automatically

### "Path is outside vault"
- Security feature - only files within the vault can be accessed
- Check your OBSIDIAN_VAULT_PATH is set correctly

### YAML frontmatter not parsing
- Install PyYAML: `pip install pyyaml`
- Without it, frontmatter features are limited

### RAG not available
- Install: `pip install sentence-transformers chromadb`
- First run downloads the embedding model (~90MB)
- Check status with the `rag_status` tool

### Semantic search returns nothing
- Run `index_vault` first to create the index
- Check `rag_status` to see how many chunks are indexed
- Try lowering `min_score` parameter in semantic_search

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
