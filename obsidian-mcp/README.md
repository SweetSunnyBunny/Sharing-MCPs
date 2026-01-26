# Obsidian MCP Server

Let Claude access and manage your Obsidian vault! Read, write, search, and organize your notes using natural language.

## What It Does

This MCP gives Claude full access to your Obsidian vault:

- **Read & Write Notes** - Create, edit, and delete markdown files
- **Search** - Full-text search, tag search, find recent notes
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

### 2. Set Your Vault Path

Either edit `server.py` or set an environment variable:

**Windows:**
```batch
set OBSIDIAN_VAULT_PATH=C:\Users\YourName\Documents\ObsidianVault
```

**Mac/Linux:**
```bash
export OBSIDIAN_VAULT_PATH=/path/to/your/vault
```

### 3. Run the Server

```bash
python run_server.py
```

The server runs on `http://localhost:8080/mcp`

### 4. Connect Claude

Add to your MCP settings:

```json
{
  "mcpServers": {
    "obsidian": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

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

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
